"""
Emovils OPC — Meta Ads API (Facebook/Instagram)
Gestión de campañas, presupuesto y monitoreo de CPA.
Safeguard: PAUSA AUTOMÁTICA si CPA > $6.
"""
import requests
import logging
from typing import Optional
from config.settings import META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_APP_ID
from config.safeguards import CPAEvaluator, BudgetGuardian, CPA_LIMITS

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com/v19.0"
cpa_evaluator = CPAEvaluator()
budget_guardian = BudgetGuardian()


# ─────────────────────────────────────────────
# CAMPAÑAS
# ─────────────────────────────────────────────
def get_active_campaigns() -> list:
    """Lista las campañas activas de la cuenta publicitaria."""
    url = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/campaigns"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": "id,name,status,daily_budget,lifetime_budget,objective",
        "filtering": '[{"field":"effective_status","operator":"IN","value":["ACTIVE"]}]'
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_campaign_insights(campaign_id: str, date_preset: str = "last_7d") -> dict:
    """Obtiene métricas de una campaña: gasto, alcance, clics, leads."""
    url = f"{BASE_URL}/{campaign_id}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": "spend,impressions,clicks,ctr,cpc,actions,cost_per_action_type",
        "date_preset": date_preset
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json().get("data", [{}])
    return data[0] if data else {}


def pause_campaign(campaign_id: str) -> dict:
    """Pausa una campaña de Meta Ads."""
    url = f"{BASE_URL}/{campaign_id}"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "status": "PAUSED"
    }
    resp = requests.post(url, params=params)
    resp.raise_for_status()
    logger.warning(f"Campaña pausada: {campaign_id}")
    return resp.json()


def resume_campaign(campaign_id: str) -> dict:
    """Reactiva una campaña pausada."""
    url = f"{BASE_URL}/{campaign_id}"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "status": "ACTIVE"
    }
    resp = requests.post(url, params=params)
    resp.raise_for_status()
    logger.info(f"Campaña reactivada: {campaign_id}")
    return resp.json()


def set_daily_budget(adset_id: str, daily_budget_cents: int) -> dict:
    """
    Actualiza el presupuesto diario de un Ad Set.
    daily_budget_cents: presupuesto en centavos (400 = $4.00)
    Safeguard: Verifica límites antes de actualizar.
    """
    daily_usd = daily_budget_cents / 100
    check = budget_guardian.check_daily_spend(daily_usd)

    if check["allowed"] is False:
        logger.error(f"Presupuesto BLOQUEADO: ${daily_usd}/día supera límite máximo")
        raise ValueError(check["message"])

    if check.get("requires_confirmation"):
        logger.warning(f"Presupuesto requiere confirmación Tier 3: ${daily_usd}/día")
        # En producción, envía notificación al dueño para aprobación
        raise PermissionError(check["message"])

    url = f"{BASE_URL}/{adset_id}"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "daily_budget": daily_budget_cents
    }
    resp = requests.post(url, params=params)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────
# SAFEGUARD — EVALUADOR CPA AUTOMÁTICO
# ─────────────────────────────────────────────
def evaluate_and_enforce_cpa(total_spent: float, total_clients: int) -> dict:
    """
    EL GUARDIÁN DEL $6.
    Evalúa el CPA actual y actúa automáticamente:
    - OK: continúa
    - Alerta amarilla: notifica al dueño
    - Alerta roja: PAUSA TODOS LOS ANUNCIOS
    """
    result = cpa_evaluator.evaluate(total_spent, total_clients)
    logger.info(f"CPA Evaluado: {result['emoji']} ${result['cpa']} — {result['status'].value}")

    if result["pause_ads"]:
        campaigns = get_active_campaigns()
        paused = []
        for campaign in campaigns:
            try:
                pause_campaign(campaign["id"])
                paused.append(campaign["id"])
            except Exception as e:
                logger.error(f"Error pausando campaña {campaign['id']}: {e}")

        result["campaigns_paused"] = paused
        logger.warning(f"PAUSA AUTOMÁTICA — {len(paused)} campaña(s) pausada(s). CPA: ${result['cpa']}")

    return result


def get_total_spend_today() -> float:
    """Retorna el gasto total de hoy en la cuenta publicitaria."""
    url = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": "spend",
        "date_preset": "today"
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json().get("data", [{}])
    return float(data[0].get("spend", 0)) if data else 0.0


def get_leads_from_ads(date_preset: str = "last_7d") -> list:
    """Obtiene leads generados por anuncios de Meta."""
    url = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/leadgen_forms"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": "id,name,leads_count"
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json().get("data", [])


# ─────────────────────────────────────────────
# CREACIÓN DE CAMPAÑA (Piloto Emovils Airport)
# ─────────────────────────────────────────────
def create_airport_campaign(
    name: str = "Emovils Airport — Piloto 21 días",
    daily_budget_cents: int = 400,  # $4/día
    targeting_location: str = "US",
    targeting_age_min: int = 25,
    targeting_age_max: int = 65
) -> dict:
    """
    Crea la campaña base para el piloto Airport.
    Presupuesto blindado: $4/día máximo.
    Objetivo: mensajes a WhatsApp.
    """
    # Verificar presupuesto antes de crear
    check = budget_guardian.check_daily_spend(daily_budget_cents / 100)
    if check["allowed"] is False:
        raise ValueError(f"Presupuesto bloqueado: {check['message']}")

    url = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/campaigns"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "name": name,
        "objective": "MESSAGES",
        "status": "PAUSED",  # Inicia pausado, dueño activa manualmente
        "special_ad_categories": []
    }
    resp = requests.post(url, params=params)
    resp.raise_for_status()
    campaign = resp.json()
    logger.info(f"Campaña creada (pausada): {campaign.get('id')} — {name}")
    return campaign
