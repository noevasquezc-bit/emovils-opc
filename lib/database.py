"""
Emovils — Base de Datos Central de Reservas
Almacenamiento en memoria con sincronizacion a Airtable.
Unico punto de verdad para todas las reservas, conductores y vehiculos.
"""
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Base de datos en memoria (persiste mientras el servidor corre) ──
RESERVAS = {}    # booking_id -> dict
VEHICULOS = {}   # vehicle_id -> dict
CONDUCTORES = {} # driver_id -> dict


def _gen_id(prefix: str) -> str:
    fecha = datetime.now().strftime("%y%m%d")
    codigo = secrets.token_hex(3).upper()
    return f"{prefix}-{fecha}-{codigo}"


def _gen_token() -> str:
    return secrets.token_urlsafe(24)


# ─────────────────────────────────────────────
# RESERVAS
# ─────────────────────────────────────────────

def crear_reserva(datos: dict) -> dict:
    """
    Crea una reserva nueva y genera los tokens QR del cliente.
    Retorna el dict completo de la reserva.
    """
    booking_id = _gen_id("EMV")
    token_cliente = _gen_token()
    ahora = datetime.now()

    reserva = {
        "booking_id": booking_id,
        # Cliente
        "cliente_nombre": datos.get("nombre", ""),
        "cliente_telefono": datos.get("telefono", ""),
        "cliente_whatsapp": datos.get("whatsapp", ""),
        # Servicio
        "origen": datos.get("origen", ""),
        "destino": datos.get("destino", ""),
        "fecha": datos.get("fecha", ""),
        "hora": datos.get("hora", ""),
        "pasajeros": datos.get("pasajeros", 1),
        "vehiculo_tipo": datos.get("vehiculo", "sedan"),
        # Precio y pago
        "precio_rd": datos.get("precio", 0),
        "forma_pago": datos.get("forma_pago", "efectivo"),
        "estado_pago": "pendiente",
        # Estado de la reserva
        "estado": "confirmada",  # confirmada / conductor_asignado / en_camino / completada / cancelada
        # Conductor y vehiculo (se asigna despues)
        "conductor_id": None,
        "conductor_nombre": None,
        "conductor_telefono": None,
        "conductor_foto": None,
        "vehiculo_id": None,
        "vehiculo_placa": None,
        "vehiculo_color": None,
        "vehiculo_modelo": None,
        # QR del cliente (para que conductor escanee al abordar)
        "token_cliente": token_cliente,
        "qr_cliente_usado": False,
        "qr_cliente_usado_en": None,
        # Timestamps
        "creada_en": ahora.isoformat(),
        "actualizada_en": ahora.isoformat(),
        "abordaje_confirmado": False,
        "abordaje_confirmado_en": None,
    }

    RESERVAS[booking_id] = reserva
    logger.info("Reserva creada: %s para %s", booking_id, reserva["cliente_nombre"])
    return reserva


def obtener_reserva(booking_id: str) -> Optional[dict]:
    return RESERVAS.get(booking_id)


def actualizar_reserva(booking_id: str, cambios: dict) -> Optional[dict]:
    if booking_id not in RESERVAS:
        return None
    RESERVAS[booking_id].update(cambios)
    RESERVAS[booking_id]["actualizada_en"] = datetime.now().isoformat()
    return RESERVAS[booking_id]


def asignar_conductor(booking_id: str, conductor: dict, vehiculo: dict) -> Optional[dict]:
    """
    Asigna conductor y vehiculo a una reserva.
    conductor: {id, nombre, telefono, foto_url}
    vehiculo: {id, placa, color, modelo}
    """
    cambios = {
        "conductor_id": conductor.get("id"),
        "conductor_nombre": conductor.get("nombre"),
        "conductor_telefono": conductor.get("telefono"),
        "conductor_foto": conductor.get("foto_url", ""),
        "vehiculo_id": vehiculo.get("id"),
        "vehiculo_placa": vehiculo.get("placa"),
        "vehiculo_color": vehiculo.get("color"),
        "vehiculo_modelo": vehiculo.get("modelo"),
        "estado": "conductor_asignado",
    }
    return actualizar_reserva(booking_id, cambios)


def confirmar_abordaje(booking_id: str, driver_id: str) -> dict:
    """El conductor escanea el QR del cliente y confirma el abordaje."""
    reserva = obtener_reserva(booking_id)
    if not reserva:
        return {"ok": False, "error": "Reserva no encontrada"}

    if reserva.get("qr_cliente_usado"):
        return {"ok": False, "error": "Este QR ya fue utilizado"}

    if reserva.get("conductor_id") and reserva["conductor_id"] != driver_id:
        return {"ok": False, "error": "Este conductor no está asignado a esta reserva"}

    actualizar_reserva(booking_id, {
        "qr_cliente_usado": True,
        "qr_cliente_usado_en": datetime.now().isoformat(),
        "abordaje_confirmado": True,
        "abordaje_confirmado_en": datetime.now().isoformat(),
        "estado": "en_camino",
    })
    return {"ok": True, "reserva": RESERVAS[booking_id]}


def listar_reservas_activas() -> list:
    activos = ["confirmada", "conductor_asignado", "en_camino"]
    return [r for r in RESERVAS.values() if r.get("estado") in activos]


# ─────────────────────────────────────────────
# VEHICULOS
# ─────────────────────────────────────────────

def registrar_vehiculo(datos: dict) -> dict:
    """Registra un vehiculo y genera su token QR permanente."""
    vehicle_id = datos.get("id") or _gen_id("VH")
    token = _gen_token()

    vehiculo = {
        "vehicle_id": vehicle_id,
        "placa": datos.get("placa", ""),
        "modelo": datos.get("modelo", ""),
        "color": datos.get("color", ""),
        "foto_url": datos.get("foto_url", ""),
        "capacidad": datos.get("capacidad", 4),
        "tipo": datos.get("tipo", "sedan"),
        "token_qr": token,
        "activo": True,
        "registrado_en": datetime.now().isoformat(),
    }

    VEHICULOS[vehicle_id] = vehiculo
    logger.info("Vehiculo registrado: %s (%s)", vehicle_id, vehiculo["placa"])
    return vehiculo


def obtener_vehiculo(vehicle_id: str) -> Optional[dict]:
    return VEHICULOS.get(vehicle_id)


def obtener_reserva_activa_de_vehiculo(vehicle_id: str) -> Optional[dict]:
    """Busca si hay una reserva activa asignada a este vehiculo."""
    for reserva in listar_reservas_activas():
        if reserva.get("vehiculo_id") == vehicle_id:
            return reserva
    return None
