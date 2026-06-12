"""
Emovils OPC — Airtable API (CRM + Dashboard)
Tablas: Leads, Reservas, Metricas, Contenido
"""
import requests
import logging
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
# LEADS (Prospectos)
# ─────────────────────────────────────────────
class LeadStatus:
    NUEVO = "nuevo"
    COTIZADO = "cotizado"
    RESERVADO = "reservado"
    PERDIDO = "perdido"
    NO_RESPONDE = "no_responde"


def create_lead(
    whatsapp: str,
    nombre: str = "",
    canal_origen: str = "whatsapp",
    producto: str = "airport",
    notas: str = ""
) -> dict:
    """Crea un nuevo lead en Airtable."""
    data = {
        "fields": {
            "WhatsApp": whatsapp,
            "Nombre": nombre,
            "Status": LeadStatus.NUEVO,
            "Canal_Origen": canal_origen,
            "Producto": producto,
            "Fecha_Contacto": datetime.now().isoformat(),
            "Notas": notas
        }
    }
    resp = requests.post(f"{BASE_URL}/{AIRTABLE_LEADS_TABLE}", json=data, headers=HEADERS)
    resp.raise_for_status()
    logger.info(f"Lead creado: {whatsapp} — {nombre}")
    return resp.json()


def update_lead_status(record_id: str, status: str, notas: str = "") -> dict:
    """Actualiza el estado de un lead."""
    data = {
        "fields": {
            "Status": status,
            "Ultima_Actualizacion": datetime.now().isoformat(),
        }
    }
    if notas:
        data["fields"]["Notas"] = notas
    resp = requests.patch(f"{BASE_URL}/{AIRTABLE_LEADS_TABLE}/{record_id}", json=data, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def get_lead_by_whatsapp(whatsapp: str) -> Optional[dict]:
    """Busca un lead por número de WhatsApp."""
    params = {"filterByFormula": f"{{WhatsApp}} = '{whatsapp}'"}
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
# RESERVAS
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
    data = {
        "fields": {
            "Lead_ID": [lead_id],
            "Nombre_Pasajero": nombre_pasajero,
            "WhatsApp": whatsapp,
            "Fecha_Viaje": fecha_viaje,
            "Hora_Viaje": hora_viaje,
            "Vuelo": vuelo,
            "Aerolinea": aerolinea,
            "Origen": origen,
            "Destino": destino,
            "Pasajeros": pasajeros,
            "Maletas": maletas,
            "Precio_USD": precio_usd,
            "Forma_Pago": forma_pago,
            "Tipo_Servicio": tipo_servicio,
            "Producto": producto,
            "Status": "pendiente_pago",
            "Fecha_Reserva": datetime.now().isoformat()
        }
    }
    resp = requests.post(f"{BASE_URL}/{AIRTABLE_RESERVAS_TABLE}", json=data, headers=HEADERS)
    resp.raise_for_status()
    logger.info(f"Reserva creada: {nombre_pasajero} — {fecha_viaje}")
    return resp.json()


def confirm_payment(record_id: str, stripe_payment_id: str) -> dict:
    """Confirma el pago de una reserva."""
    data = {
        "fields": {
            "Status": "pagado",
            "Stripe_Payment_ID": stripe_payment_id,
            "Fecha_Pago": datetime.now().isoformat()
        }
    }
    resp = requests.patch(f"{BASE_URL}/{AIRTABLE_RESERVAS_TABLE}/{record_id}", json=data, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def assign_driver(record_id: str, nombre_chofer: str, vehiculo: str, tel_chofer: str) -> dict:
    """Asigna un chofer a una reserva."""
    data = {
        "fields": {
            "Chofer_Nombre": nombre_chofer,
            "Chofer_Vehiculo": vehiculo,
            "Chofer_Telefono": tel_chofer,
            "Status": "confirmado",
            "Fecha_Asignacion": datetime.now().isoformat()
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
# ALERTA CPA PARA EL DUEÑO
# ─────────────────────────────────────────────
def create_cpa_alert(cpa: float, action: str, message: str) -> dict:
    """Crea una alerta de CPA visible para el dueño."""
    data = {
        "fields": {
            "Fecha": datetime.now().isoformat(),
            "Tipo": "ALERTA_CPA",
            "CPA_Actual": cpa,
            "Mensaje": f"⚠️ PAUSA: CPA = ${cpa} (máximo $6) — {message}",
            "Accion_Requerida": action,
            "Status": "pendiente_revision"
        }
    }
    resp = requests.post(f"{BASE_URL}/{AIRTABLE_METRICAS_TABLE}", json=data, headers=HEADERS)
    resp.raise_for_status()
    logger.warning(f"ALERTA CPA creada: ${cpa}")
    return resp.json()
