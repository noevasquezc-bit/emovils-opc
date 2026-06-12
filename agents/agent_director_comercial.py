"""
Emovils OPC — Agente 1: Director Comercial
Responsabilidad: Decide qué producto vender cada semana, define oferta,
prioridad, estrategia y métricas. Es el cerebro estratégico.
"""
import anthropic
import logging
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from config.products import get_active_product, PRODUCTS
from config.safeguards import CPAEvaluator, CPA_LIMITS
from lib.airtable_api import get_pilot_totals

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Eres el Director Comercial de Emovils OPC, empresa de movilidad privada en República Dominicana.

TU IDENTIDAD:
- Empresa: Emovils OPC — "No vendemos traslados. Vendemos certeza al llegar."
- Producto piloto: Emovils Airport (traslados privados desde/hacia AILA/SDQ)
- Precio: USD $25 por traslado sencillo
- Clientes: Diáspora dominicana (NYC, NJ, Boston, Miami), turistas, ejecutivos, familias

TU FUNCIÓN:
- Defines la estrategia comercial semanal
- Priorizas qué producto atacar
- Defines la oferta, el ángulo y el público
- Estableces metas medibles
- NO ejecutas. DECIDES y DELEGAS a los otros agentes.

RESTRICCIONES CRÍTICAS:
- CPA máximo permitido: $6 USD por cliente
- Presupuesto diario máximo: $4 USD (piloto 21 días, $100 total)
- Si CPA > $6: pausa automática de anuncios
- Métrica principal: RESERVAS PAGADAS (no likes, no alcance)
- Foco inicial: ventas orgánicas + WhatsApp (no pagar leads fríos)

CANALES PRIORITARIOS:
1. Clientes anteriores (reactivación)
2. Referidos de clientes satisfechos
3. Alianzas (hoteles pequeños, Airbnb hosts, clínicas, empresas)
4. Google Business Profile
5. Contenido orgánico en Instagram/Facebook
6. Publicidad pagada SOLO si CPA se mantiene < $6

MÉTRICAS QUE REPORTAS:
- Contactos realizados → Respuestas → Cotizaciones → Reservas pagadas → Ingresos → CPA → Margen

Siempre responde en español. Sé directo, práctico y orientado a resultados."""


def get_weekly_strategy() -> str:
    """
    El Director Comercial analiza el estado actual del piloto
    y define la estrategia para la semana.
    """
    totals = get_pilot_totals()
    product = get_active_product()
    evaluator = CPAEvaluator()

    cpa_result = evaluator.evaluate(
        total_spent=totals["total_spent"],
        total_clients=totals["total_clients"]
    )

    context = f"""
    ESTADO ACTUAL DEL PILOTO:
    - Gastado: ${totals['total_spent']}
    - Clientes: {totals['total_clients']}
    - Ingresos: ${totals['total_revenue']}
    - CPA: {f"${cpa_result['cpa']}" if cpa_result['cpa'] else 'Sin datos aún'}
    - Status: {cpa_result['emoji']} {cpa_result['status'].value}
    - Leads totales: {totals['total_leads']}
    - Días registrados: {totals['days_recorded']}
    - Presupuesto restante: ${CPA_LIMITS.TOTAL_PILOT_BUDGET - totals['total_spent']:.2f}
    """

    prompt = f"""
    {context}

    Analiza este estado del piloto y dame:
    1. Evaluación del momento (¿estamos bien, alerta, o mal?)
    2. Las 3 acciones prioritarias para esta semana
    3. El canal que más debemos atacar ahora mismo
    4. Si la publicidad pagada debe continuar, pausarse o ajustarse
    5. Un mensaje corto para el dueño (máximo 3 líneas, directo)
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def evaluate_offer(product_id: str = "airport") -> str:
    """Evalúa y refina la oferta del producto activo."""
    product = PRODUCTS.get(product_id, PRODUCTS["airport"])

    prompt = f"""
    Analiza la oferta actual de {product['name']}:
    - Precio: ${product['price_usd']}
    - Tagline: "{product['tagline']}"
    - Audiencia: {', '.join(product['target_audience'][:3])}

    Dame:
    1. ¿La oferta es clara y compelling? ¿Qué cambiarías?
    2. El ángulo de ventas más fuerte para los próximos 7 días
    3. El hook principal para el primer mensaje WhatsApp
    4. La objeción más común y cómo responderla
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def set_weekly_targets(week_number: int) -> dict:
    """Define las metas de la semana según el plan de 21 días."""
    if week_number == 1:  # Días 1-7
        return {
            "week": 1,
            "focus": "Setup y primeros contactos",
            "meta_reservas": 2,
            "meta_contactos": 30,
            "meta_cotizaciones": 8,
            "canal_principal": "clientes_anteriores",
            "presupuesto_ads": 0,
            "deliverable": "Sistema listo para vender"
        }
    elif week_number == 2:  # Días 8-14
        return {
            "week": 2,
            "focus": "Cerrar reservas y activar alianzas",
            "meta_reservas": 5,
            "meta_contactos": 50,
            "meta_cotizaciones": 15,
            "canal_principal": "alianzas + contenido_organico",
            "presupuesto_ads": 20,
            "deliverable": "5 reservas pagadas"
        }
    else:  # Días 15-21
        return {
            "week": 3,
            "focus": "Escalar lo que funciona",
            "meta_reservas": 10,
            "meta_contactos": 40,
            "meta_cotizaciones": 20,
            "canal_principal": "referidos + ads_si_cpa_ok",
            "presupuesto_ads": 40,
            "deliverable": "10 reservas pagadas, sistema validado"
        }
