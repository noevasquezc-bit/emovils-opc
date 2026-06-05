"""
Emovils OPC — Servidor Principal (Flask)
Expone los endpoints para n8n y WhatsApp webhook.
"""
import logging
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

from agents.agent_vendedor_whatsapp import process_incoming_message, send_quotation
from agents.agent_analytics import generate_daily_report, pilot_health_check
from agents.agent_director_comercial import get_weekly_strategy
from agents.agent_contenido import create_instagram_post, create_7day_content_calendar
from lib.paypal_api import handle_webhook, capture_payment
from config.safeguards import CPAEvaluator
from lib.airtable_api import get_pilot_totals

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "system": "Emovils OPC",
        "tagline": "No vendemos traslados. Vendemos certeza al llegar.",
        "status": "online",
        "cpa_limit": "$6 USD — INVIOLABLE"
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "emovils-opc"})


# ─────────────────────────────────────────────
# WHATSAPP WEBHOOKS
# ─────────────────────────────────────────────
@app.route("/webhook/whatsapp", methods=["GET", "POST"])
def whatsapp_webhook():
    """
    Recibe mensajes entrantes de WhatsApp via Green API.
    Green API envía POST con typeWebhook: incomingMessageReceived
    """
    if request.method == "GET":
        return jsonify({"status": "Emovils WhatsApp webhook activo"}), 200

    payload = request.get_json(force=True)
    try:
        result = process_incoming_message(payload)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error procesando webhook WhatsApp: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# PAYPAL WEBHOOKS
# ─────────────────────────────────────────────
@app.route("/webhook/paypal", methods=["POST"])
def paypal_webhook():
    """Procesa eventos de PayPal (pago aprobado, completado, etc.)"""
    payload = request.get_json(force=True)
    try:
        event = handle_webhook(payload)
        # Si el pago fue aprobado, capturarlo automáticamente
        if event.get("action") == "capture_payment" and event.get("order_id"):
            capture_result = capture_payment(event["order_id"])
            logger.info(f"Pago capturado automáticamente: {capture_result}")
            return jsonify({**event, "capture": capture_result})
        return jsonify(event)
    except Exception as e:
        logger.error(f"Error procesando webhook PayPal: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# AGENTES IA (para n8n)
# ─────────────────────────────────────────────
@app.route("/agent/vendedor/respond", methods=["POST"])
def agent_vendedor():
    """El agente vendedor responde un mensaje de WhatsApp."""
    data = request.get_json()
    try:
        result = process_incoming_message({
            "entry": [{"changes": [{"value": {
                "messages": [{"from": data["wa_number"], "text": {"body": data["message"]}, "type": "text", "id": "msg_id", "timestamp": "0"}],
                "contacts": [{"profile": {"name": data.get("contact_name", "")}}]
            }}]}]
        })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/agent/contenido/post", methods=["POST"])
def agent_contenido():
    """El agente de contenido genera un post."""
    data = request.get_json()
    try:
        content = create_instagram_post(
            hook=data.get("angulo", "seguridad"),
            product=data.get("producto", "airport"),
            format_type=data.get("formato", "carrusel")
        )
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/agent/director/strategy", methods=["GET"])
def agent_director():
    """El director comercial genera la estrategia semanal."""
    try:
        strategy = get_weekly_strategy()
        return jsonify({"strategy": strategy})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/agent/contenido/calendar", methods=["GET"])
def content_calendar():
    """Genera el calendario de contenido de 7 días."""
    try:
        calendar = create_7day_content_calendar()
        return jsonify({"calendar": calendar})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# ANALYTICS (para n8n y dashboard)
# ─────────────────────────────────────────────
@app.route("/analytics/pilot-totals", methods=["GET"])
def get_totals():
    """Retorna los totales acumulados del piloto."""
    try:
        totals = get_pilot_totals()
        return jsonify(totals)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/cpa-check", methods=["GET"])
def cpa_check():
    """Verifica el CPA actual del piloto."""
    try:
        totals = get_pilot_totals()
        evaluator = CPAEvaluator()
        result = evaluator.evaluate(totals["total_spent"], totals["total_clients"])
        return jsonify({
            "cpa": result["cpa"],
            "status": result["status"].value,
            "emoji": result["emoji"],
            "message": result["message"],
            "pause_ads": result["pause_ads"],
            "totals": totals
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/daily-report", methods=["POST"])
def daily_report():
    """Genera y envía el reporte diario al dueño."""
    try:
        report = generate_daily_report()
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/health-check", methods=["GET"])
def health_check_pilot():
    """Estado de salud del piloto: ¿vamos bien para 10 reservas en 21 días?"""
    try:
        health = pilot_health_check()
        return jsonify(health)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    logger.info(f"Emovils OPC servidor iniciado en puerto {port}")
    logger.info("CPA Máximo: $6 USD — INVIOLABLE")
    app.run(host="0.0.0.0", port=port, debug=debug)
