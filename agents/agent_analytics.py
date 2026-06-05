"""
Emovils OPC — Agente 4: Analytics / Auditor de Resultados
Responsabilidad: Mide reservas, ingresos, margen, canal efectivo.
Genera el reporte diario de 7:15 AM para el dueño.
La métrica que importa: RESERVAS PAGADAS + CPA.
"""
import anthropic
import logging
from datetime import datetime, date
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, OWNER_WHATSAPP, OWNER_EMAIL
from config.safeguards import CPAEvaluator, BudgetGuardian, CPA_LIMITS, CPAStatus
from lib.airtable_api import get_pilot_totals, log_daily_metrics, create_cpa_alert
from lib.meta_api import get_total_spend_today, evaluate_and_enforce_cpa
from lib.whatsapp_api import send_text

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
cpa_evaluator = CPAEvaluator()
budget_guardian = BudgetGuardian()

SYSTEM_PROMPT = """Eres el Analista de Resultados de Emovils OPC.

TU FUNCIÓN:
- Leer las métricas diarias del piloto
- Calcular el CPA actual (Total gastado / Total clientes)
- Determinar si el negocio está funcionando
- Generar el reporte de 7:15 AM para el dueño
- Detectar patrones y recomendar ajustes

LA ÚNICA MÉTRICA QUE IMPORTA:
CPA (Costo Por Cliente) = Total gastado en publicidad / Total clientes confirmados
- CPA < $6: SALUDABLE — continúa igual
- CPA $6-$8: ALERTA AMARILLA — ajusta sin pausar
- CPA > $8: ALERTA ROJA — PAUSA automática

MÉTRICAS DE SEGUIMIENTO:
1. Contactos realizados (esfuerzo comercial)
2. Respuestas recibidas (interés real)
3. Cotizaciones enviadas (avance comercial)
4. Reservas pagadas (MÉTRICA PRINCIPAL)
5. Ingresos generados (caja)
6. Margen estimado (rentabilidad)
7. Canal de origen (saber de dónde viene el dinero)
8. Producto más vendido (saber qué escalar)

REPORTE PARA EL DUEÑO:
- Máximo 5 líneas
- 1 número principal (CPA)
- 1 semáforo (OK / ALERTA / PAUSA)
- 1 acción recomendada
- Sin jerga técnica

Responde siempre en español, claro y directo."""


def generate_daily_report() -> dict:
    """
    Genera el reporte diario de 7:15 AM.
    Este es el email/WhatsApp que el dueño recibe cada mañana.
    """
    totals = get_pilot_totals()
    today_spend = get_total_spend_today()

    cpa_result = cpa_evaluator.evaluate(
        total_spent=totals["total_spent"],
        total_clients=totals["total_clients"]
    )

    budget_status = budget_guardian.check_total_remaining(totals["total_spent"])

    # Ejecutar safeguard si corresponde
    if cpa_result["pause_ads"]:
        enforce_result = evaluate_and_enforce_cpa(totals["total_spent"], totals["total_clients"])
        create_cpa_alert(
            cpa=cpa_result["cpa"],
            action=cpa_result["action"],
            message=cpa_result["message"]
        )
        logger.warning(f"SAFEGUARD ACTIVADO: {enforce_result}")

    # Generar análisis con IA
    analysis_prompt = f"""
    Estado del piloto Emovils Airport — {datetime.now().strftime('%d/%m/%Y')}:

    MÉTRICAS ACUMULADAS:
    - Gastado en ads: ${totals['total_spent']}
    - Clientes confirmados: {totals['total_clients']}
    - Ingresos: ${totals['total_revenue']}
    - Leads totales: {totals['total_leads']}
    - CPA actual: {f"${cpa_result['cpa']}" if cpa_result['cpa'] else 'Sin datos'}
    - Status: {cpa_result['emoji']} {cpa_result['status'].value}
    - Presupuesto restante: ${budget_status['remaining_usd']}
    - Días estimados con presupuesto actual: {budget_status['estimated_days_remaining']}
    - Gasto de hoy: ${today_spend}

    Genera el reporte diario para el dueño.
    Formato exacto:

    ═══════════════════════════════════
    EMOVILS — TU CPA — {datetime.now().strftime('%d/%m/%Y')}
    ═══════════════════════════════════

    CPA HOY: $X.XX
    LÍMITE: $6.00
    STATUS: [emoji] [SALUDABLE / ALERTA / PAUSA]

    DESGLOSE:
    ├─ Gastado acumulado: $XXX
    ├─ Clientes acumulados: X
    ├─ CPA: $X.XX
    └─ Tendencia: [↓ Bajando / → Estable / ↑ Subiendo]

    TU DECISIÓN HOY:
    □ [Una acción concreta y específica]

    ═══════════════════════════════════
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": analysis_prompt}]
    )

    report_text = response.content[0].text

    # Log en Airtable
    try:
        log_daily_metrics(
            fecha=datetime.now().strftime('%Y-%m-%d'),
            gastado_ads=totals["total_spent"],
            leads_nuevos=0,  # Se actualiza con datos de Meta
            cotizaciones=0,
            reservas_pagadas=totals["total_clients"],
            ingresos_usd=totals["total_revenue"],
            cpa=cpa_result["cpa"],
            cpa_status=cpa_result["status"].value
        )
    except Exception as e:
        logger.error(f"Error logueando métricas: {e}")

    # Enviar al dueño por WhatsApp
    if OWNER_WHATSAPP:
        try:
            send_text(OWNER_WHATSAPP, report_text)
        except Exception as e:
            logger.error(f"Error enviando reporte al dueño: {e}")

    return {
        "date": datetime.now().isoformat(),
        "cpa": cpa_result["cpa"],
        "status": cpa_result["status"].value,
        "emoji": cpa_result["emoji"],
        "report_text": report_text,
        "totals": totals,
        "pause_activated": cpa_result["pause_ads"],
        "budget_remaining": budget_status["remaining_usd"]
    }


def analyze_channel_performance() -> str:
    """Analiza qué canal está trayendo más clientes."""
    prompt = """
    Analiza el rendimiento por canal de captación para Emovils Airport:

    Canales a evaluar:
    1. Clientes anteriores (reactivación)
    2. Referidos
    3. Alianzas (hoteles, Airbnb, clínicas)
    4. Google Business Profile
    5. Contenido orgánico (Instagram/Facebook)
    6. Publicidad pagada Meta Ads

    Para cada canal dame:
    - Esfuerzo requerido (1-5)
    - CPA estimado
    - Velocidad de conversión
    - Recomendación: escalar / mantener / pausar

    Basa tu análisis en los principios del piloto Emovils OPC.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def detect_objections_pattern(conversations: list) -> str:
    """
    Analiza conversaciones y detecta las objeciones más comunes.
    Útil para mejorar el script del vendedor.
    """
    conversations_text = "\n---\n".join(conversations[:10])

    prompt = f"""
    Analiza estas conversaciones de WhatsApp de Emovils Airport y detecta:

    {conversations_text}

    1. Las 3 objeciones más frecuentes
    2. El punto donde se pierde más leads (precio / tiempo / desconfianza / etc.)
    3. Qué información falta en el script actual
    4. Una mejora concreta al guion de ventas

    Sé específico con ejemplos de las conversaciones.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def pilot_health_check() -> dict:
    """
    Verificación rápida del estado del piloto.
    ¿Vamos bien, regular o mal para alcanzar 10 reservas en 21 días?
    """
    totals = get_pilot_totals()
    days = totals.get("days_recorded", 1)
    clients = totals["total_clients"]
    target = CPA_LIMITS.TARGET_CLIENTS  # 17 clientes

    clients_per_day = clients / max(days, 1)
    projected_clients = clients_per_day * 21
    on_track = projected_clients >= target * 0.7  # 70% del target = aceptable

    return {
        "days_elapsed": days,
        "clients_actual": clients,
        "clients_per_day": round(clients_per_day, 2),
        "projected_clients_21d": round(projected_clients, 1),
        "target": target,
        "on_track": on_track,
        "status": "verde" if on_track else "rojo",
        "message": f"{'En buen ritmo' if on_track else 'Por debajo del ritmo necesario'} — {clients} clientes en {days} días"
    }
