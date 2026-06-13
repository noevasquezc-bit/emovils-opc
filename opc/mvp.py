"""
Emovils OPC — MVP (Minimal Viable Product)

Modulo enfocado SOLO en el flujo critico:
  cliente WhatsApp -> cotizar -> confirmar -> pago -> nombre -> tel -> reserva
  -> QR cliente -> asignar conductor/vehiculo -> cliente escanea QR vehiculo
  -> check verde -> conductor escanea QR cliente -> in_progress

No depende de despachador complejo, NCF, reportes, social, etc.
Solo Airtable base prod (app9CGq0LCsEk09r7).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CONFIG MVP — todo configurable, NO hardcoded mas alla del default
# ═══════════════════════════════════════════════════════════════

MINIMUM_FARE_DOP = 300
KM_INCLUIDOS_BASE = 3
CIUDAD_POR_KM_ADICIONAL = 60
RECARGO_NOCTURNO_PORCENTAJE = 20
HORARIO_NOCTURNO_INICIO = 23  # 23:00
HORARIO_NOCTURNO_FIN = 6       # 06:00
SEDAN_CAPACITY = 4
VAN_CAPACITY = 7
VAN_MULTIPLIER = 1.40

# Token HMAC firma QR (deberia estar en env var)
QR_SIGNING_KEY = os.getenv("QR_SIGNING_KEY", "emovils-mvp-2026-default-CHANGE-IN-PROD")

# Base URL del sistema (para verification_url en QRs)
PUBLIC_BASE_URL = os.getenv("BACKEND_URL", "https://emovils-opc-production.up.railway.app")

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "app9CGq0LCsEk09r7")
AT_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json",
}
AT_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"


# ═══════════════════════════════════════════════════════════════
# TARIFARIO MVP
# ═══════════════════════════════════════════════════════════════

@dataclass
class Cotizacion:
    origen: str
    destino: str
    pasajeros: int
    km_estimados: float
    vehiculo_recomendado: str  # "Sedan", "Van Caravan", "supervisor"
    precio_rd: int
    es_nocturno: bool
    requiere_supervisor: bool
    razon_supervisor: str = ""
    moneda: str = "RD$"


def _es_hora_nocturna(hora: int) -> bool:
    """11pm-6am"""
    return hora >= HORARIO_NOCTURNO_INICIO or hora < HORARIO_NOCTURNO_FIN


def cotizar(origen: str, destino: str, pasajeros: int, hora: int, km_estimados: float = 10.0) -> Cotizacion:
    """Cotizacion MVP simple. Solo servicios urbanos directos."""
    # Validar pasajeros
    if pasajeros > VAN_CAPACITY:
        return Cotizacion(
            origen=origen, destino=destino, pasajeros=pasajeros,
            km_estimados=km_estimados, vehiculo_recomendado="supervisor",
            precio_rd=0, es_nocturno=False, requiere_supervisor=True,
            razon_supervisor=f"Mas de {VAN_CAPACITY} pasajeros — requiere coordinacion especial",
        )

    if pasajeros <= 0:
        pasajeros = 1

    es_nocturno = _es_hora_nocturna(hora)

    # Calculo base
    base = MINIMUM_FARE_DOP
    km_extra = max(0, km_estimados - KM_INCLUIDOS_BASE)
    precio = base + (km_extra * CIUDAD_POR_KM_ADICIONAL)

    # Vehiculo segun pasajeros
    if pasajeros <= SEDAN_CAPACITY:
        vehiculo = "Sedan"
        multiplicador = 1.0
    else:  # 5-7
        vehiculo = "Van Caravan"
        multiplicador = VAN_MULTIPLIER

    precio *= multiplicador

    # Recargo nocturno
    if es_nocturno:
        precio *= (1 + RECARGO_NOCTURNO_PORCENTAJE / 100)

    # Minimo
    precio = max(precio, MINIMUM_FARE_DOP)
    precio_redondeado = int(round(precio / 10) * 10)

    return Cotizacion(
        origen=origen, destino=destino, pasajeros=pasajeros,
        km_estimados=km_estimados, vehiculo_recomendado=vehiculo,
        precio_rd=precio_redondeado, es_nocturno=es_nocturno,
        requiere_supervisor=False,
    )


# ═══════════════════════════════════════════════════════════════
# QR — generacion y validacion
# ═══════════════════════════════════════════════════════════════

def _firmar(payload: str) -> str:
    return hmac.new(
        QR_SIGNING_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:24]


def generar_qr_cliente(booking_id: str) -> tuple[str, str]:
    """Devuelve (token, url) para el QR del cliente.
    El QR contiene solo: booking_id + token + URL de verificacion."""
    raw = secrets.token_urlsafe(18)
    token = f"{raw}.{_firmar(booking_id + ':' + raw)}"
    url = f"{PUBLIC_BASE_URL}/qr/cliente/{booking_id}?t={token}"
    return token, url


def generar_qr_vehiculo(vehicle_id: str) -> tuple[str, str]:
    """Devuelve (token, url) para el QR fisico del vehiculo."""
    raw = secrets.token_urlsafe(18)
    token = f"{raw}.{_firmar(vehicle_id + ':' + raw)}"
    url = f"{PUBLIC_BASE_URL}/vehicle/verify/{vehicle_id}?t={token}"
    return token, url


def validar_token_cliente(booking_id: str, token: str) -> bool:
    """Verifica firma HMAC del token del cliente."""
    try:
        raw, firma = token.rsplit(".", 1)
        return hmac.compare_digest(firma, _firmar(booking_id + ':' + raw))
    except Exception:
        return False


def validar_token_vehiculo(vehicle_id: str, token: str) -> bool:
    try:
        raw, firma = token.rsplit(".", 1)
        return hmac.compare_digest(firma, _firmar(vehicle_id + ':' + raw))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# AIRTABLE CRUD (directo, sin dependencia de modulos viejos)
# ═══════════════════════════════════════════════════════════════

def _at_get(tabla: str, formula: str = "", max_records: int = 100) -> list[dict]:
    params = {"maxRecords": max_records}
    if formula:
        params["filterByFormula"] = formula
    r = requests.get(f"{AT_URL}/{tabla}", headers=AT_HEADERS, params=params, timeout=15)
    if not r.ok:
        logger.warning("Airtable GET %s: %s", tabla, r.text[:200])
        return []
    return r.json().get("records", [])


def _at_create(tabla: str, fields: dict) -> dict:
    r = requests.post(
        f"{AT_URL}/{tabla}",
        headers=AT_HEADERS,
        json={"records": [{"fields": fields}]},
        timeout=15,
    )
    if not r.ok:
        logger.error("Airtable CREATE %s: %s", tabla, r.text[:300])
        raise RuntimeError(f"Airtable create fallo: {r.status_code}")
    return r.json()["records"][0]


def _at_update(tabla: str, record_id: str, fields: dict) -> dict:
    r = requests.patch(
        f"{AT_URL}/{tabla}/{record_id}",
        headers=AT_HEADERS,
        json={"fields": fields},
        timeout=15,
    )
    if not r.ok:
        logger.error("Airtable UPDATE %s: %s", tabla, r.text[:300])
        raise RuntimeError(f"Airtable update fallo: {r.status_code}")
    return r.json()


# ═══════════════════════════════════════════════════════════════
# RESERVAR + ASIGNAR
# ═══════════════════════════════════════════════════════════════

def crear_reserva(
    customer_name: str,
    customer_phone: str,
    origin: str,
    destination: str,
    passengers: int,
    final_price: int,
    payment_method: str,
    service_date: Optional[str] = None,
    service_time: Optional[str] = None,
    vehicle_type: str = "Sedan",
) -> dict:
    """Crea booking en Airtable. Devuelve {booking_id, record_id, qr_url, qr_token}."""
    booking_id = "EMV-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2).upper()

    pm = payment_method.lower()
    if pm == "cash":
        payment_status = "cash_pending"
        booking_status = "confirmed"
    elif pm == "card":
        payment_status = "card_pending"
        booking_status = "confirmed"
    elif pm == "online":
        payment_status = "pending"
        booking_status = "pending_payment"
    else:
        payment_status = "pending"
        booking_status = "supervisor_review"

    qr_token, qr_url = generar_qr_cliente(booking_id)
    now = datetime.now().isoformat()

    fields = {
        "Booking_ID": booking_id,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "Pickup_Location": origin,
        "Dropoff_Location": destination,
        "Passengers": passengers,
        "final_price": final_price,
        "currency": "RD$",
        "payment_method": pm,
        "payment_status": payment_status,
        "booking_status": booking_status,
        "vehicle_type_mvp": vehicle_type,
        "customer_qr_token": qr_token,
        "customer_qr_url": qr_url,
        "customer_qr_status": "active",
        "vehicle_verification_status": "not_started",
        "pickup_confirmed": False,
        "service_time": service_time or now,
        "Travel_Date": service_date or datetime.now().date().isoformat(),
        "Created_At": now,
    }
    rec = _at_create("Bookings", fields)
    logger.info("✓ Booking creado: %s (record %s)", booking_id, rec["id"])
    return {
        "booking_id": booking_id,
        "record_id": rec["id"],
        "qr_url": qr_url,
        "qr_token": qr_token,
        "booking_status": booking_status,
    }


def asignar_conductor_y_vehiculo(booking_id: str, vehicle_type: str) -> dict:
    """Encuentra el primer vehiculo activo del tipo solicitado con chofer disponible."""
    # Buscar vehiculo activo del tipo
    vehiculos = _at_get(
        "Vehicles",
        formula=f"AND({{vehicle_type}}='{vehicle_type}', {{vehicle_status}}='active')",
        max_records=10,
    )
    if not vehiculos:
        return {"asignado": False, "razon": f"No hay vehiculos {vehicle_type} disponibles"}

    for v in vehiculos:
        vf = v["fields"]
        driver_id = vf.get("assigned_driver_id", "")
        if not driver_id:
            continue
        # Verificar driver disponible
        drivers = _at_get(
            "Drivers",
            formula=f"AND({{driver_id}}='{driver_id}', {{driver_status}}='available')",
            max_records=1,
        )
        if not drivers:
            continue

        d = drivers[0]
        df = d["fields"]
        # Asignar a la reserva
        booking = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
        if not booking:
            return {"asignado": False, "razon": f"Booking {booking_id} no existe"}

        _at_update("Bookings", booking[0]["id"], {
            "driver_id": driver_id,
            "vehicle_id": vf.get("vehicle_id", ""),
            "Driver_Name": df.get("driver_name", ""),
            "Driver_Phone": df.get("driver_phone", ""),
            "Driver_Vehicle": f"{vf.get('vehicle_brand','')} {vf.get('vehicle_model','')} {vf.get('vehicle_plate','')}",
        })
        # Marcar driver y vehicle como busy
        _at_update("Drivers", d["id"], {"driver_status": "busy"})
        return {
            "asignado": True,
            "driver_id": driver_id,
            "driver_name": df.get("driver_name", ""),
            "driver_phone": df.get("driver_phone", ""),
            "vehicle_id": vf.get("vehicle_id", ""),
            "vehicle_plate": vf.get("vehicle_plate", ""),
            "vehicle_color": vf.get("vehicle_color", ""),
            "vehicle_brand": vf.get("vehicle_brand", ""),
            "vehicle_model": vf.get("vehicle_model", ""),
        }

    return {"asignado": False, "razon": "Sin choferes disponibles para el vehiculo solicitado"}


# ═══════════════════════════════════════════════════════════════
# VERIFICACION QR (cliente y vehiculo)
# ═══════════════════════════════════════════════════════════════

def verificar_qr_vehiculo(vehicle_id: str, token: str) -> dict:
    """Cliente escanea QR fisico del vehiculo. Devuelve {color: green/red/yellow, ...}"""
    if not validar_token_vehiculo(vehicle_id, token):
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "red",
                          notes="Token invalido")
        return {"color": "red", "razon": "QR invalido"}

    vehiculos = _at_get("Vehicles", formula=f"{{vehicle_id}}='{vehicle_id}'", max_records=1)
    if not vehiculos:
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "red",
                          notes="Vehiculo no existe")
        return {"color": "red", "razon": "Vehiculo no registrado en Emovils"}

    v = vehiculos[0]["fields"]
    if v.get("vehicle_status") != "active":
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "yellow",
                          notes=f"Vehiculo {v.get('vehicle_status')}")
        return {"color": "yellow", "razon": "Vehiculo no esta activo"}

    # Buscar la reserva confirmada actual de este vehiculo
    bookings = _at_get(
        "Bookings",
        formula=f"AND({{vehicle_id}}='{vehicle_id}', OR({{booking_status}}='confirmed', {{booking_status}}='in_progress'))",
        max_records=5,
    )
    if not bookings:
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "yellow",
                          notes="Sin reserva activa")
        return {
            "color": "yellow",
            "razon": "Este vehiculo no tiene reservas activas",
            "vehicle": {
                "plate": v.get("vehicle_plate", ""),
                "brand": v.get("vehicle_brand", ""),
                "model": v.get("vehicle_model", ""),
                "color": v.get("vehicle_color", ""),
            },
        }

    booking = bookings[0]
    bf = booking["fields"]
    driver_id = bf.get("driver_id", "")
    if not driver_id:
        _log_verification(bf.get("Booking_ID"), vehicle_id, None, "client_scans_vehicle", "yellow",
                          notes="Sin chofer asignado")
        return {"color": "yellow", "razon": "Sin chofer asignado a la reserva"}

    drivers = _at_get("Drivers", formula=f"{{driver_id}}='{driver_id}'", max_records=1)
    if not drivers:
        return {"color": "red", "razon": "Chofer no registrado"}
    df = drivers[0]["fields"]

    # Marcar verificacion en booking
    _at_update("Bookings", booking["id"], {
        "vehicle_verification_status": "green",
    })

    _log_verification(bf.get("Booking_ID"), vehicle_id, driver_id, "client_scans_vehicle", "green",
                      notes="OK")

    return {
        "color": "green",
        "vehicle": {
            "plate": v.get("vehicle_plate", ""),
            "brand": v.get("vehicle_brand", ""),
            "model": v.get("vehicle_model", ""),
            "color": v.get("vehicle_color", ""),
            "type": v.get("vehicle_type", ""),
        },
        "driver": {
            "name": df.get("driver_name", ""),
            "phone": df.get("driver_phone", ""),
            "rating": df.get("rating_avg", 0),
        },
        "booking": {
            "code": bf.get("Booking_ID", ""),
            "origen": bf.get("Pickup_Location", ""),
            "destino": bf.get("Dropoff_Location", ""),
            "customer": bf.get("customer_name", ""),
        },
    }


def verificar_qr_cliente(booking_id: str, token: str, driver_id: str) -> dict:
    """Conductor escanea QR del cliente. Confirma recogida → in_progress."""
    if not validar_token_cliente(booking_id, token):
        _log_verification(booking_id, None, driver_id, "driver_scans_client", "red",
                          notes="Token invalido")
        return {"ok": False, "razon": "QR invalido o falsificado"}

    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
    if not bookings:
        return {"ok": False, "razon": "Reserva no encontrada"}
    booking = bookings[0]
    bf = booking["fields"]

    # Verificar QR no usado
    if bf.get("customer_qr_status") == "used":
        return {"ok": False, "razon": "QR ya utilizado anteriormente"}

    # Verificar chofer asignado coincide
    if bf.get("driver_id") != driver_id:
        _log_verification(booking_id, bf.get("vehicle_id"), driver_id, "driver_scans_client", "red",
                          notes="Chofer no asignado a esta reserva")
        return {"ok": False, "razon": "Esta reserva no esta asignada a este chofer"}

    # Verificar status confirmado
    if bf.get("booking_status") not in ("confirmed",):
        return {"ok": False, "razon": f"Reserva en estado {bf.get('booking_status')}"}

    now = datetime.now().isoformat()
    _at_update("Bookings", booking["id"], {
        "customer_qr_status": "used",
        "pickup_confirmed": True,
        "pickup_confirmed_at": now,
        "pickup_confirmed_by_driver_id": driver_id,
        "booking_status": "in_progress",
    })

    _log_verification(booking_id, bf.get("vehicle_id"), driver_id, "driver_scans_client", "green",
                      notes="Recogida confirmada")

    return {
        "ok": True,
        "booking_id": booking_id,
        "customer_name": bf.get("customer_name", ""),
        "origen": bf.get("Pickup_Location", ""),
        "destino": bf.get("Dropoff_Location", ""),
        "passengers": bf.get("Passengers", 0),
        "payment_method": bf.get("payment_method", ""),
        "payment_status": bf.get("payment_status", ""),
        "final_price": bf.get("final_price", 0),
    }


def _log_verification(booking_id, vehicle_id, driver_id, verification_type, result, notes=""):
    try:
        _at_create("Verification_Logs", {
            "verification_id": secrets.token_hex(8),
            "booking_id": booking_id or "",
            "vehicle_id": vehicle_id or "",
            "driver_id": driver_id or "",
            "verification_type": verification_type,
            "verification_result": result,
            "verified_at": datetime.now().isoformat(),
            "notes": notes,
        })
    except Exception as exc:
        logger.warning("No se pudo loggear verificacion: %s", exc)


# ═══════════════════════════════════════════════════════════════
# PANEL CONDUCTOR — consulta reservas
# ═══════════════════════════════════════════════════════════════

def reservas_conductor(driver_id: str) -> list[dict]:
    """Reservas asignadas a un chofer en estado activo."""
    bookings = _at_get(
        "Bookings",
        formula=f"AND({{driver_id}}='{driver_id}', OR({{booking_status}}='confirmed', {{booking_status}}='in_progress'))",
        max_records=20,
    )
    out = []
    for b in bookings:
        bf = b["fields"]
        out.append({
            "booking_id": bf.get("Booking_ID", ""),
            "customer_name": bf.get("customer_name", ""),
            "customer_phone": bf.get("customer_phone", ""),
            "origen": bf.get("Pickup_Location", ""),
            "destino": bf.get("Dropoff_Location", ""),
            "service_time": bf.get("service_time", ""),
            "passengers": bf.get("Passengers", 0),
            "vehicle_type": bf.get("vehicle_type_mvp", ""),
            "payment_method": bf.get("payment_method", ""),
            "payment_status": bf.get("payment_status", ""),
            "final_price": bf.get("final_price", 0),
            "booking_status": bf.get("booking_status", ""),
            "vehicle_verification_status": bf.get("vehicle_verification_status", "not_started"),
            "pickup_confirmed": bf.get("pickup_confirmed", False),
        })
    return out


def obtener_reserva(booking_id: str) -> Optional[dict]:
    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
    if not bookings:
        return None
    bf = bookings[0]["fields"]
    return {
        "booking_id": bf.get("Booking_ID", ""),
        "customer_name": bf.get("customer_name", ""),
        "customer_phone": bf.get("customer_phone", ""),
        "origen": bf.get("Pickup_Location", ""),
        "destino": bf.get("Dropoff_Location", ""),
        "passengers": bf.get("Passengers", 0),
        "vehicle_type": bf.get("vehicle_type_mvp", ""),
        "final_price": bf.get("final_price", 0),
        "currency": bf.get("currency", "RD$"),
        "payment_method": bf.get("payment_method", ""),
        "payment_status": bf.get("payment_status", ""),
        "booking_status": bf.get("booking_status", ""),
        "driver_id": bf.get("driver_id", ""),
        "vehicle_id": bf.get("vehicle_id", ""),
        "customer_qr_url": bf.get("customer_qr_url", ""),
        "vehicle_verification_status": bf.get("vehicle_verification_status", ""),
        "pickup_confirmed": bf.get("pickup_confirmed", False),
        "service_time": bf.get("service_time", ""),
    }


# ═══════════════════════════════════════════════════════════════
# CLI test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Test MVP")
    c = cotizar("AILA", "Hotel Embajador", pasajeros=2, hora=18, km_estimados=25)
    print(f"Cotizacion: {c}")
    c2 = cotizar("Punta Cana", "Casa Campo", pasajeros=10, hora=14, km_estimados=80)
    print(f"Cotizacion 10pax: {c2}")
