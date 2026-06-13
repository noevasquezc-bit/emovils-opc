"""
Emovils OPC — Agent Marketing (Media Buyer IA)
Responsabilidad: Propone estructura de campañas, públicos,
presupuesto, creativos y optimización de Meta Ads.
Safeguard integrado: CPA máximo $6, presupuesto $4/día.
"""
import anthropic
import logging
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from config.safeguards import CPAEvaluator, BudgetGuardian, CPA_LIMITS, CPAStatus
from lib.meta_api import (
    get_active_campaigns, get_campaign_insights,
    pause_campaign, resume_campaign, get_total_spend_today,
    create_airport_campaign
)
from lib.airtable_api import get_pilot_totals

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
cpa_evaluator = CPAEvaluator()
budget_guardian = BudgetGuardian()

SYSTEM_PROMPT = """Eres el Media Buyer IA de Emovils OPC, empresa de movilidad privada en República Dominicana.

TU FUNCIÓN:
- Proponer estructura de campañas de Meta Ads (Facebook/Instagram)
- Definir públicos objetivos (targeting)
- Recomendar creativos (imágenes, videos, copy)
- Gestionar presupuesto dentro de los límites del piloto
- Optimizar campañas según el CPA

PRODUCTO QUE PROMOCIONAS: Emovils Airport
- Servicio: Traslados privados AILA/SDQ (Santo Domingo)
- Precio: USD $25 sencillo
- Objetivo del anuncio: Generar mensajes en WhatsApp (objetivo MESSAGES)
- Mensaje central: "Llegas a Santo Domingo y tu transporte ya está resuelto"

RESTRICCIONES DE PRESUPUESTO (INVIOLABLES):
- Presupuesto diario máximo: $4/día (NUNCA más sin autorización)
- Presupuesto total piloto: $100 en 21 días
- CPA objetivo: < $6 USD por cliente
- Si CPA > $8: PAUSAR INMEDIATAMENTE

AUDIENCIAS PRIORITARIAS (de mayor a menor potencial):
1. Diáspora dominicana en USA — NYC, NJ, Boston, Miami, Orlando, Puerto Rico, España
   └─ Intereses: Dominican Republic, "vuelos Santo Domingo", familia en RD
2. Turistas que viajan a SDQ, Punta Cana, Bávaro, La Romana, Samaná
   └─ Intereses: travel, Caribbean, Dominican Republic tourism
3. Ejecutivos y viajeros de negocios
   └─ Intereses: business travel, executive transportation
4. Familias con niños o adultos mayores que viajan
   └─ Comportamiento: frecuent travelers, family travelers

ÁNGULOS DE CREATIVOS QUE FUNCIONAN:
- Seguridad: "Tu chofer esperándote con tu nombre. Sin negociar."
- Familia: "Tu familia llega y el transporte ya está resuelto."
- Cansancio: "Después de un vuelo largo, no improvises."
- Precio claro: "Sabes cuánto pagas antes de aterrizar."
- Ejecutivo: "Traslado puntual para viajeros que no dejan nada al azar."

ESTRUCTURA DE CAMPAÑA RECOMENDADA (Piloto):
- 1 campaña activa (objetivo: MESSAGES → WhatsApp)
- 2-3 Ad Sets (una por audiencia principal)
- 2-3 ads por Ad Set (variaciones de copy/imagen)
- Presupuesto: $4/día distribuido entre los Ad Sets activos

Siempre responde en español. Sé específico con números, CPA y presupuesto."""


# ─────────────────────────────────────────────
# FUNCIÓN PRINCIPAL: MONITOREO Y OPTIMIZACIÓN
# ─────────────────────────────────────────────
def run_daily_optimization() -> dict:
    """
    Ejecuta la optimización diaria de campañas.
    Evalúa CPA, decide si pausar/continuar/ajustar.
    Retorna el plan de acción del día.
    """
    totals = get_pilot_totals()
    today_spend = get_total_spend_today()
    campaigns = get_active_campaigns()

    cpa_result = cpa_evaluator.evaluate(
        total_spent=totals["total_spent"],
        total_clients=totals["total_clients"]
    )

    budget_check = budget_guardian.check_daily_spend(today_spend)

    logger.info(f"Optimización diaria — CPA: {cpa_result['emoji']} ${cpa_result['cpa']} | Gasto hoy: ${today_spend}")

    # ACCIÓN AUTOMÁTICA según CPA
    action_taken = "ninguna"
    campaigns_affected = []

    if cpa_result["pause_ads"]:
        for campaign in campaigns:
            try:
                pause_campaign(campaign["id"])
                campaigns_affected.append(campaign["id"])
                action_taken = "pausa_automatica"
            except Exception as e:
                logger.error(f"Error pausando campaña {campaign['id']}: {e}")

    # Generar análisis y recomendaciones con IA
    analysis = generate_campaign_analysis(
        cpa_result=cpa_result,
        totals=totals,
        today_spend=today_spend,
        active_campaigns=len(campaigns)
    )

    return {
        "date": __import__("datetime").datetime.now().isoformat(),
        "cpa": cpa_result["cpa"],
        "cpa_status": cpa_result["status"].value,
        "cpa_emoji": cpa_result["emoji"],
        "today_spend": today_spend,
        "action_taken": action_taken,
        "campaigns_paused": campaigns_affected,
        "analysis": analysis,
        "totals": totals
    }


def generate_campaign_analysis(
    cpa_result: dict,
    totals: dict,
    today_spend: float,
    active_campaigns: int
) -> str:
    """Genera análisis inteligente de las campañas usando Claude."""

    prompt = f"""
    ESTADO DE LAS CAMPAÑAS DE META ADS — EMOVILS AIRPORT:

    MÉTRICAS ACTUALES:
    - CPA: {f"${cpa_result['cpa']}" if cpa_result['cpa'] else 'Sin datos'} ({cpa_result['emoji']} {cpa_result['status'].value})
    - Gastado total: ${totals['total_spent']}
    - Clientes confirmados: {totals['total_clients']}
    - Gasto de hoy: ${today_spend}
    - Presupuesto restante: ${CPA_LIMITS.TOTAL_PILOT_BUDGET - totals['total_spent']:.2f}
    - Campañas activas: {active_campaigns}
    - Leads totales: {totals['total_leads']}

    Analiza este estado y dame:
    1. Diagnóstico de las campañas (máximo 2 líneas)
    2. La principal hipótesis de por qué el CPA está en este nivel
    3. UNA sola acción de optimización para hoy (no 10, solo 1)
    4. Si las campañas deben continuar, ajustarse o pausarse

    Sé brutalmente directo. Nada de paja.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# ─────────────────────────────────────────────
# ESTRATEGIA DE CAMPAÑA
# ─────────────────────────────────────────────
def design_campaign_structure(week: int = 1) -> str:
    """
    Diseña la estructura completa de campaña para una semana específica.
    Basado en el presupuesto disponible y el CPA objetivo.
    """
    remaining_budget = CPA_LIMITS.TOTAL_PILOT_BUDGET
    daily_budget = CPA_LIMITS.DAILY_BUDGET_USD

    prompt = f"""
    Diseña la estructura de campaña de Meta Ads para Emovils Airport — Semana {week} del piloto.

    RESTRICCIONES INVIOLABLES:
    - Presupuesto diario: ${daily_budget} (máximo, no negociable)
    - CPA objetivo: < $6 USD
    - Objetivo de campaña: MESSAGES (conversaciones a WhatsApp)
    - Duración semana: 7 días

    ENTREGA:
    1. Estructura de campaña (nombre, objetivo, configuración)
    2. 3 Ad Sets con:
       - Nombre y audiencia específica
       - Edad, ubicación, intereses
       - Presupuesto asignado (del total $4/día)
    3. 2 variaciones de copy para los ads
    4. Qué imagen/video usar en cada ad
    5. KPIs mínimos para considerar que el ad "funciona"

    Sé muy específico con el targeting. Nada genérico.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def recommend_audience(current_cpa: float = None) -> str:
    """
    Recomienda el mejor público objetivo basado en el CPA actual.
    Si el CPA está alto, sugiere audiencias más específicas.
    """
    cpa_context = f"CPA actual: ${current_cpa}" if current_cpa else "Sin datos de CPA todavía"

    prompt = f"""
    {cpa_context}

    Recomienda la mejor audiencia de Meta Ads para Emovils Airport ahora mismo.

    Considera estos segmentos y evalúa cuál atacar primero:
    A) Dominicanos en USA (NYC, NJ, Miami, Boston) — viajan a SDQ frecuentemente
    B) Turistas internacionales — viajan a PUJ/SDQ por primera vez
    C) Ejecutivos dominicanos que viajan de negocios
    D) Familias con hijos en exterior coordinando traslados para sus padres en RD
    E) Lookalike audience basada en clientes existentes

    Para la audiencia recomendada dame:
    - País(es) y ciudades específicas
    - Rango de edad
    - Intereses exactos (los que escribirías en Meta Ads Manager)
    - Comportamientos
    - Exclusiones (quién NO debe ver el anuncio)
    - Tamaño estimado de audiencia
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def analyze_creative_performance(insights: dict) -> str:
    """Analiza el rendimiento de un creativo y recomienda si continuar o cambiar."""
    prompt = f"""
    Analiza el rendimiento de este creativo de Meta Ads para Emovils Airport:

    MÉTRICAS:
    - Gasto: ${insights.get('spend', 0)}
    - Impresiones: {insights.get('impressions', 0)}
    - Clics: {insights.get('clicks', 0)}
    - CTR: {insights.get('ctr', 0)}%
    - CPC: ${insights.get('cpc', 0)}

    Evalúa:
    1. ¿El CTR es bueno para este tipo de anuncio? (benchmark: >1% para tráfico frío)
    2. ¿El CPC es aceptable? (objetivo: < $0.30 para llegar a CPA $6)
    3. ¿Continuar con este creativo, modificarlo o pausarlo?
    4. Si cambias algo, ¿qué cambias primero?
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def create_retargeting_strategy() -> str:
    """Crea estrategia de retargeting para leads que no convirtieron."""
    prompt = """
    Crea una estrategia de retargeting para Emovils Airport.

    AUDIENCIAS DE RETARGETING POSIBLES:
    1. Personas que visitaron la landing page pero no escribieron a WhatsApp
    2. Personas que iniciaron conversación en WhatsApp pero no cotizaron
    3. Personas que recibieron cotización pero no pagaron
    4. Clientes anteriores (cross-sell / upsell)

    Para cada audiencia define:
    - Mensaje/ángulo del retargeting
    - Tiempo de la ventana (cuántos días después)
    - Presupuesto recomendado
    - Copy específico del anuncio
    - CTA

    Recuerda: presupuesto total del piloto es $100 — el retargeting no puede consumir más del 20%.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=900,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# ─────────────────────────────────────────────
# SETUP INICIAL DE CAMPAÑA
# ─────────────────────────────────────────────
def setup_pilot_campaign() -> dict:
    """
    Crea la campaña inicial del piloto en Meta Ads.
    Inicia pausada — el dueño la activa manualmente.
    Presupuesto blindado: $4/día.
    """
    logger.info("Creando campaña piloto Emovils Airport...")

    try:
        campaign = create_airport_campaign(
            name="Emovils Airport — Piloto 21 días | WhatsApp",
            daily_budget_cents=400,  # $4/día = 400 centavos
            targeting_location="US",
            targeting_age_min=25,
            targeting_age_max=65
        )

        logger.info(f"Campaña creada (pausada): {campaign.get('id')}")
        return {
            "status": "created",
            "campaign_id": campaign.get("id"),
            "message": "Campaña creada. Está PAUSADA. Revisa en Meta Ads Manager antes de activar.",
            "daily_budget": "$4.00",
            "cpa_limit": "$6.00"
        }
    except ValueError as e:
        logger.error(f"Presupuesto bloqueado: {e}")
        return {"status": "blocked", "error": str(e)}
    except Exception as e:
        logger.error(f"Error creando campaña: {e}")
        return {"status": "error", "error": str(e)}
