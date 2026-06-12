"""
Emovils OPC — Airtable API (CRM + Dashboard)
Campos mapeados a la estructura real de las tablas en Airtable.
"""
import requests
import logging
import json
from datetime import datetime
from typing import Optional
from config.settings import (
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID,
    AIRTABLE_LEADS_TABLE, AIRTABLE_RESERVAS_TABLE,
    AIRTABLE_METRICAS_TABLE, AIRTABLE_CONTENIDO_TABLE
)

logger = logging.getLogger(__name__)

BASE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}


# ─────────────────────────────────────────────
# LEADS
# ─────────────────────────────────────────────
class LeadStatus:
    NUEVO = "Todo"
    COTIZADO = "In progress"
    RESERVADO = "Done"
    PERDIDO = "Todo"
    NO_RESPONDE = "Todo"

# Mapeo de canal origen a valores válidos de Airtable
SOURCE_MAP = {
    "whatsapp": "WhatsApp",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "referral": "Referral",
    "organico": "Orgánico",
}

# Mapeo de producto a valores válidos de Airtable
PRODUCT_MAP = {
    "airport": "Airport",
    "family": "Family",
    "medical": "Medical",
    "ejecutivo": "Ejecutivo",
    "by_hour": "By Hour",
}


def create_lead(
    whatsapp: str,
    nombre: str = "",
    canal_origen: str = "whatsapp",
    producto: str = "airport",
    notas: str = ""
) -> dict:
    """Crea un nuevo lead en Airtable."""
    source = SOURCE_MAP.get(canal_origen.lower(), "WhatsApp")
    service = PRODUCT_MAP.get(producto.lower(), "Airport")
    data = {
        "fields": {
            "Phone": whatsapp,
            "Name": nombre or whatsapp,
            "Status": LeadStatus.NUEVO,
            "Source": source,
            "Service_Interest": service,
            "Notes": notas
        }
    }
    resp = requests.post(f"{BASE_URL}/{AIRTABLE_LEADS_TABLE}", json=data, headers=HEADERS)
    if not resp.ok:
        logger.error(f"Airtable create_lead error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    logger.info(f"Lead creado: {whatsapp} — {nombre}")
    return resp.json()


def update_lead_status(record_id: str, status: str, notas: str = "") -> dict:
    """Actualiza el estado de un lead."""
    data = {
        "fields": {
            "Status": status,
            "Last_Contact": datetime.now().isoformat(),
        }
    }
    if notas:
        data["fields"]["Notes"] = notas
    resp = requests.patch(f"{BASE_URL}/{AIRTABLE_LEADS_TABLE}/{record_id}", json=data, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def get_lead_by_whatsapp(whatsapp: str) -> Optional[dict]:
    """Busca un lead por número de WhatsApp (campo Phone)."""
    params = {"filterByFormula": f"{{Phone}} = '{whatsapp}'"}
    resp = requests.get(f"{BASE_URL}/{AIRTABLE_LEADS_TABLE}", headers=HEADERS, params=params)
    resp.raise_for_status()
    records = resp.json().get("records", [])
    return records[0] if records else None


def list_leads_by_status(status: str) -> list:
    """Lista todos los leads por estado."""
    params = {"filterByFormula": f"{{Status}} = '{status}'"}
    resp = requests.get(f"{BASE_URL}/{AIRTABLE_LEADS_TABLE}", headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json().get("records", [])


# ─────────────────────────────────────────────
# RESERVAS (Bookings)
# ─────────────────────────────────────────────
def create_reserva(
    lead_id: str,
    nombre_pasajero: str,
    whatsapp: str,
    fecha_viaje: str,
    hora_viaje: str,
    vuelo: str,
    aerolinea: str,
    origen: str,
    destino: str,
    pasajeros: int,
    maletas: int,
    precio_usd: float,
    forma_pago: str,
    tipo_servicio: str = "ida",
    producto: str = "airport"
) -> dict:
    """Crea una reserva confirmada."""
    # Combinar fecha y hora en formato datetime
    try:
        travel_datetime = f"{fecha_viaje}T{hora_viaje}:00.000Z"
    except Exception:
        travel_datetime = fecha_viaje

    # Generar Booking_ID único
    booking_id = f"EMV-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    data = {
        "fields": {
            "Booking_ID": booking_id,
            "Lead_ID": lead_id,
            "Service": tipo_servicio,
            "Pickup_Location": origen,
            "Dropoff_Location": destino,
            "Travel_Date": travel_datetime,
            "Flight_Number": vuelo,
            "Passengers": pasajeros,
            "Quote_Price": precio_usd,
            "Status": "Pending",
            "Created_At": datetime.now().isoformat()
        }
    }
    resp = requests.post(f"{BASE_URL}/{AIRTABLE_RESERVAS_TABLE}", json=data, headers=HEADERS)
    resp.raise_for_status()
    logger.info(f"Reserva creada: {booking_id} — {nombre_pasajero} — {fecha_viaje}")
    return resp.json()


def confirm_payment(record_id: str, payment_id: str) -> dict:
    """Confirma el pago de una reserva."""
    data = {
        "fields": {
            "Status": "Completed",
            "Stripe_Session": payment_id,
            "Completed_At": datetime.now().isoformat()
        }
    }
    resp = requests.patch(f"{BASE_URL}/{AIRTABLE_RESERVAS_TABLE}/{record_id}", json=data, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def assign_driver(record_id: str, nombre_chofer: str, vehiculo: str, tel_chofer: str) -> dict:
    """Asigna un chofer a una reserva."""
    data = {
        "fields": {
            "Driver_Name": nombre_chofer,
            "Driver_Vehicle": vehiculo,
            "Driver_Phone": tel_chofer,
            "Status": "Confirmed"
        }
    }
    resp = requests.patch(f"{BASE_URL}/{AIRTABLE_RESERVAS_TABLE}/{record_id}", json=data, headers=HEADERS)
    resp.raise_for_status()
    logger.info(f"Chofer asignado a reserva {record_id}: {nombre_chofer}")
    return resp.json()


# ─────────────────────────────────────────────
# MÉTRICAS (Dashboard del dueño)
# ─────────────────────────────────────────────
def log_daily_metrics(
    fecha: str,
    gastado_ads: float,
    leads_nuevos: int,
    cotizaciones: int,
    reservas_pagadas: int,
    ingresos_usd: float,
    cpa: Optional[float],
    cpa_status: str,
    canal_origen: str = "meta_ads"
) -> dict:
    """Registra las métricas diarias del piloto."""
    data = {
        "fields": {
            "Fecha": fecha,
            "Gastado_Ads_USD": gastado_ads,
            "Leads_Nuevos": leads_nuevos,
            "Cotizaciones_Enviadas": cotizaciones,
            "Reservas_Pagadas": reservas_pagadas,
            "Ingresos_USD": ingresos_usd,
            "CPA": cpa,
            "CPA_Status": cpa_status,
            "Canal_Origen": canal_origen,
            "Margen_USD": round(ingresos_usd - gastado_ads, 2) if ingresos_usd else 0
        }
    }
    resp = requests.post(f"{BASE_URL}/{AIRTABLE_METRICAS_TABLE}", json=data, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def get_pilot_totals() -> dict:
    """Obtiene los totales acumulados del piloto para calcular CPA."""
    resp = requests.get(f"{BASE_URL}/{AIRTABLE_METRICAS_TABLE}", headers=HEADERS)
    resp.raise_for_status()
    records = resp.json().get("records", [])

    total_spent = sum(r["fields"].get("Gastado_Ads_USD", 0) for r in records)
    total_clients = sum(r["fields"].get("Reservas_Pagadas", 0) for r in records)
    total_revenue = sum(r["fields"].get("Ingresos_USD", 0) for r in records)
    total_leads = sum(r["fields"].get("Leads_Nuevos", 0) for r in records)

    return {
        "total_spent": round(total_spent, 2),
        "total_clients": total_clients,
        "total_revenue": round(total_revenue, 2),
        "total_leads": total_leads,
        "cpa": round(total_spent / total_clients, 2) if total_clients > 0 else None,
        "days_recorded": len(records)
    }


# ─────────────────────────────────────────────
# HISTORIAL DE CONVERSACIÓN (persistencia entre reinicios)
# ─────────────────────────────────────────────
def get_conversation_history(wa_number: str) -> list:
    """Carga el historial de conversación desde el campo Notes del lead."""
    try:
        lead = get_lead_by_whatsapp(wa_number)
        if not lead:
            return []
        notes = lead.get("fields", {}).get("Notes", "")
        if notes and notes.startswith("HISTORY:"):
            return json.loads(notes[8:])
    except Exception as e:
        logger.error(f"Error cargando historial de {wa_number[:6]}***: {e}")
    return []


def save_conversation_history(wa_number: str, history: list) -> None:
    """Guarda el historial de conversación en el campo Notes del lead."""
    try:
        lead = get_lead_by_whatsapp(wa_number)
        if not lead:
            return
        record_id = lead["id"]
        trimmed = history[-20:]  # Máximo 20 mensajes
        data = {"fields": {"Notes": "HISTORY:" + json.dumps(trimmed, ensure_ascii=False)}}
        resp = requests.patch(f"{BASE_URL}/{AIRTABLE_LEADS_TABLE}/{record_id}", json=data, headers=HEADERS)
        if not resp.ok:
            logger.error(f"Error guardando historial {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.error(f"Error guardando historial de {wa_number[:6]}***: {e}")


def create_cpa_alert(cpa: float, action: str, message: str) -> dict:
    """Crea una alerta de CPA visible para el dueño."""
    data = {
        "fields": {
            "Fecha": datetime.now().isoformat(),
            "CPA_Status": "ALERTA",
            "CPA": cpa,
            "Canal_Origen": f"PAUSA: CPA=${cpa} (max $6) — {message}"
        }
    }
    resp = requests.post(f"{BASE_URL}/{AIRTABLE_METRICAS_TABLE}", json=data, headers=HEADERS)
    resp.raise_for_status()
    logger.warning(f"ALERTA CPA creada: ${cpa}")
    return resp.json()
