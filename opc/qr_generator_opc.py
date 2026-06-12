"""
Emovils OPC — Generador de QR Bidireccional

Genera 2 tipos de QR firmados criptográficamente:

  1. QR del CLIENTE: contiene datos del servicio. Lo recibe al reservar.
                     El conductor lo escanea para verificar al cliente.

  2. QR del VEHÍCULO: lateral del carro. El cliente lo escanea para
                     verificar que es el vehículo asignado a su reserva.

Ambos QRs se firman con HMAC-SHA256 para prevenir falsificación.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_M

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────

# Secret para firmar QRs (se inyecta via env var en producción)
QR_SECRET = os.getenv("EMOVILS_QR_SECRET", "emovils-dev-secret-change-in-prod")

# Tiempo de vida de un QR de servicio (después expira)
QR_SERVICIO_TTL_HORAS = 24

# Path donde se guardan los PNG generados
QR_OUTPUT_DIR = Path(os.getenv(
    "EMOVILS_QR_DIR",
    "/Users/noevasquez/Desktop/PROYECTO OPC/emovils-opc/opc/qrs"
))
QR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# ESTRUCTURAS
# ─────────────────────────────────────────────────────────────

@dataclass
class DatosQRCliente:
    """Datos que viajan en el QR del cliente."""
    tipo: str = "CLIENTE"
    servicio_id: str = ""
    nombre_cliente: str = ""
    whatsapp_cliente: str = ""
    origen: str = ""
    destino: str = ""
    fecha_hora: str = ""
    pasajeros: int = 0
    monto_rd: int = 0
    estado_pago: str = ""
    conductor_nombre: str = ""
    conductor_whatsapp: str = ""
    vehiculo_placa: str = ""
    emitido_en: str = ""
    expira_en: str = ""


@dataclass
class DatosQRVehiculo:
    """Datos que viajan en el QR lateral del vehículo."""
    tipo: str = "VEHICULO"
    placa: str = ""
    tipo_vehiculo: str = ""
    color: str = ""
    marca: str = ""
    conductor_actual: str = ""
    conductor_whatsapp: str = ""
    foto_conductor_url: str = ""
    empresa: str = "Emovils — Transporte Ejecutivo"
    emitido_en: str = ""


# ─────────────────────────────────────────────────────────────
# FIRMA HMAC-SHA256
# ─────────────────────────────────────────────────────────────

def _firmar(payload: str, secret: str = QR_SECRET) -> str:
    """Firma HMAC-SHA256 y devuelve base64url corto."""
    firma = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(firma)[:16].decode()


def _verificar_firma(payload: str, firma: str, secret: str = QR_SECRET) -> bool:
    return hmac.compare_digest(_firmar(payload, secret), firma)


# ─────────────────────────────────────────────────────────────
# CONSTRUCCIÓN DEL CONTENIDO DEL QR
# ─────────────────────────────────────────────────────────────

def _construir_payload_cliente(datos: DatosQRCliente) -> str:
    """Convierte a JSON compacto y agrega firma."""
    cuerpo = json.dumps(asdict(datos), separators=(",", ":"), ensure_ascii=False)
    firma = _firmar(cuerpo)
    return f"EMV1|{cuerpo}|{firma}"


def _construir_payload_vehiculo(datos: DatosQRVehiculo) -> str:
    cuerpo = json.dumps(asdict(datos), separators=(",", ":"), ensure_ascii=False)
    firma = _firmar(cuerpo)
    return f"EMV1|{cuerpo}|{firma}"


def parsear_payload(payload: str) -> tuple[dict, bool]:
    """
    Parsea un payload recibido al escanear un QR.
    Devuelve (datos, firma_valida).
    """
    try:
        prefijo, cuerpo, firma = payload.split("|", 2)
        if prefijo != "EMV1":
            return {}, False
        valida = _verificar_firma(cuerpo, firma)
        return json.loads(cuerpo), valida
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Payload QR inválido: {e}")
        return {}, False


# ─────────────────────────────────────────────────────────────
# GENERACIÓN DEL PNG
# ─────────────────────────────────────────────────────────────

def _generar_imagen_qr(payload: str, nombre_archivo: str) -> Path:
    """Genera el PNG del QR y lo guarda. Devuelve el path."""
    qr = qrcode.QRCode(
        version=None,                  # auto-tamaño
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    path = QR_OUTPUT_DIR / nombre_archivo
    img.save(path)
    logger.info(f"QR guardado: {path}")
    return path


def generar_qr_cliente(
    servicio_id: str,
    nombre_cliente: str,
    whatsapp_cliente: str,
    origen: str,
    destino: str,
    fecha_hora: str,
    pasajeros: int,
    monto_rd: int,
    estado_pago: str,
    conductor_nombre: str = "",
    conductor_whatsapp: str = "",
    vehiculo_placa: str = "",
) -> tuple[Path, str]:
    """
    Genera el QR que recibe el cliente al reservar.
    Devuelve (path_png, payload_texto).
    """
    ahora = datetime.now()
    expira = ahora + timedelta(hours=QR_SERVICIO_TTL_HORAS)

    datos = DatosQRCliente(
        servicio_id=servicio_id,
        nombre_cliente=nombre_cliente,
        whatsapp_cliente=whatsapp_cliente,
        origen=origen,
        destino=destino,
        fecha_hora=fecha_hora,
        pasajeros=pasajeros,
        monto_rd=monto_rd,
        estado_pago=estado_pago,
        conductor_nombre=conductor_nombre,
        conductor_whatsapp=conductor_whatsapp,
        vehiculo_placa=vehiculo_placa,
        emitido_en=ahora.isoformat(timespec="seconds"),
        expira_en=expira.isoformat(timespec="seconds"),
    )
    payload = _construir_payload_cliente(datos)
    path = _generar_imagen_qr(payload, f"cliente_{servicio_id}.png")
    return path, payload


def generar_qr_vehiculo(
    placa: str,
    tipo_vehiculo: str,
    color: str,
    marca: str,
    conductor_actual: str = "",
    conductor_whatsapp: str = "",
    foto_conductor_url: str = "",
) -> tuple[Path, str]:
    """
    Genera el QR que va en los laterales del vehículo.
    Devuelve (path_png, payload_texto).
    Se regenera cuando cambia el conductor asignado.
    """
    datos = DatosQRVehiculo(
        placa=placa,
        tipo_vehiculo=tipo_vehiculo,
        color=color,
        marca=marca,
        conductor_actual=conductor_actual,
        conductor_whatsapp=conductor_whatsapp,
        foto_conductor_url=foto_conductor_url,
        emitido_en=datetime.now().isoformat(timespec="seconds"),
    )
    payload = _construir_payload_vehiculo(datos)
    path = _generar_imagen_qr(payload, f"vehiculo_{placa}.png")
    return path, payload


# ─────────────────────────────────────────────────────────────
# VERIFICACIÓN AL ESCANEAR
# ─────────────────────────────────────────────────────────────

@dataclass
class ResultadoVerificacion:
    match: bool
    firma_valida: bool
    expirado: bool
    tipo: str = ""
    mensaje: str = ""
    datos: dict | None = None


def verificar_qr_cliente_para_chofer(
    payload_escaneado: str,
    servicio_esperado_id: str,
) -> ResultadoVerificacion:
    """
    Conductor escaneó el QR del cliente. Validamos:
      1. Firma criptográfica válida
      2. No expirado
      3. El servicio coincide con el que tiene asignado
    """
    datos, firma_valida = parsear_payload(payload_escaneado)
    if not firma_valida:
        return ResultadoVerificacion(
            match=False, firma_valida=False, expirado=False,
            mensaje="⚠️ QR falsificado o corrupto",
        )

    if datos.get("tipo") != "CLIENTE":
        return ResultadoVerificacion(
            match=False, firma_valida=True, expirado=False,
            mensaje="⚠️ Este no es un QR de cliente",
        )

    expira_str = datos.get("expira_en", "")
    expirado = False
    if expira_str:
        try:
            expirado = datetime.now() > datetime.fromisoformat(expira_str)
        except ValueError:
            pass

    if expirado:
        return ResultadoVerificacion(
            match=False, firma_valida=True, expirado=True,
            tipo="CLIENTE", datos=datos,
            mensaje="⚠️ QR expirado. Pídele al cliente el QR actualizado.",
        )

    if datos.get("servicio_id") != servicio_esperado_id:
        return ResultadoVerificacion(
            match=False, firma_valida=True, expirado=False,
            tipo="CLIENTE", datos=datos,
            mensaje="⚠️ Este cliente NO corresponde a tu servicio asignado.",
        )

    return ResultadoVerificacion(
        match=True, firma_valida=True, expirado=False,
        tipo="CLIENTE", datos=datos,
        mensaje=f"✅ Cliente verificado: {datos.get('nombre_cliente', '')}. Inicia el servicio.",
    )


def verificar_qr_vehiculo_para_cliente(
    payload_escaneado: str,
    placa_esperada: str,
) -> ResultadoVerificacion:
    """
    Cliente escaneó el QR del lateral del vehículo. Validamos:
      1. Firma válida
      2. La placa coincide con la asignada a la reserva
    """
    datos, firma_valida = parsear_payload(payload_escaneado)
    if not firma_valida:
        return ResultadoVerificacion(
            match=False, firma_valida=False, expirado=False,
            mensaje="⚠️ Este QR no es válido. NO subas al vehículo.",
        )

    if datos.get("tipo") != "VEHICULO":
        return ResultadoVerificacion(
            match=False, firma_valida=True, expirado=False,
            mensaje="⚠️ QR incorrecto.",
        )

    if datos.get("placa", "").upper() != placa_esperada.upper():
        return ResultadoVerificacion(
            match=False, firma_valida=True, expirado=False,
            tipo="VEHICULO", datos=datos,
            mensaje=(
                f"⚠️ Este NO es tu vehículo asignado.\n"
                f"  Esperado: {placa_esperada}\n"
                f"  Encontrado: {datos.get('placa', 'desconocido')}\n"
                "Espera el vehículo correcto o llama al 829-861-0090."
            ),
        )

    return ResultadoVerificacion(
        match=True, firma_valida=True, expirado=False,
        tipo="VEHICULO", datos=datos,
        mensaje=(
            f"✅ Vehículo verificado.\n"
            f"  Conductor: {datos.get('conductor_actual', 'N/D')}\n"
            f"  Placa: {datos.get('placa', '')}\n"
            "Puedes abordar tranquilo."
        ),
    )


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 60)
    print("EMOVILS OPC — Test de Sistema QR Bidireccional")
    print("=" * 60)

    # 1. Generar QR cliente
    print("\n1. Generando QR del cliente para SVC-001...")
    path_cli, payload_cli = generar_qr_cliente(
        servicio_id="SVC-2026-001",
        nombre_cliente="María González",
        whatsapp_cliente="+18295551234",
        origen="Hotel El Embajador, Av. Sarasota",
        destino="AILA Terminal A",
        fecha_hora="2026-06-10 20:30",
        pasajeros=2,
        monto_rd=2940,
        estado_pago="PENDIENTE",
        conductor_nombre="Juan Pérez",
        conductor_whatsapp="+18295551111",
        vehiculo_placa="A123456",
    )
    print(f"  ✓ PNG: {path_cli}")
    print(f"  ✓ Payload: {payload_cli[:80]}...")

    # 2. Generar QR vehículo
    print("\n2. Generando QR del vehículo A123456...")
    path_veh, payload_veh = generar_qr_vehiculo(
        placa="A123456",
        tipo_vehiculo="Caravan",
        color="Blanco",
        marca="Hyundai",
        conductor_actual="Juan Pérez",
        conductor_whatsapp="+18295551111",
    )
    print(f"  ✓ PNG: {path_veh}")

    # 3. Simular conductor escaneando QR del cliente
    print("\n3. Conductor escanea QR del cliente...")
    r1 = verificar_qr_cliente_para_chofer(payload_cli, "SVC-2026-001")
    print(f"  {r1.mensaje}")

    # 4. Simular conductor con servicio EQUIVOCADO
    print("\n4. Conductor escanea pero su servicio es OTRO...")
    r2 = verificar_qr_cliente_para_chofer(payload_cli, "SVC-2026-999")
    print(f"  {r2.mensaje}")

    # 5. Cliente escanea QR del vehículo correcto
    print("\n5. Cliente escanea QR vehículo (placa correcta)...")
    r3 = verificar_qr_vehiculo_para_cliente(payload_veh, "A123456")
    print(f"  {r3.mensaje}")

    # 6. Cliente escanea pero su reserva tiene OTRA placa
    print("\n6. Cliente esperaba placa B999888...")
    r4 = verificar_qr_vehiculo_para_cliente(payload_veh, "B999888")
    print(f"  {r4.mensaje}")

    # 7. Detectar QR falsificado
    print("\n7. Intento de QR falsificado...")
    payload_falso = "EMV1|{\"tipo\":\"CLIENTE\",\"servicio_id\":\"FAKE\"}|firmafalsa"
    r5 = verificar_qr_cliente_para_chofer(payload_falso, "FAKE")
    print(f"  {r5.mensaje}")

    print()
    print("=" * 60)
    print("✓ Todos los casos cubiertos. QRs en:", QR_OUTPUT_DIR)
