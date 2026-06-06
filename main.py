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
from lib.booking_manager import (
    create_booking, validate_required_fields, get_confirmation_message,
    get_driver_assignment_message, validate_customer_qr, confirm_pickup,
    BookingStatus, PaymentStatus, VehicleVerificationStatus,
    CustomerQRStatus
)
from lib.qr_generator import build_vehicle_qr_payload

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
# RESERVAS
# ─────────────────────────────────────────────
@app.route("/booking/create", methods=["POST"])
def booking_create():
    """Crea una nueva reserva con validacion de datos obligatorios."""
    data = request.get_json()
    try:
        missing = validate_required_fields(data)
        if missing:
            return jsonify({
                "error": "Datos faltantes",
                "missing_fields": missing,
                "message": "Faltan los siguientes datos para confirmar la reserva: " + ", ".join(missing)
            }), 400

        booking = create_booking(
            customer_name=data["customer_name"],
            customer_phone=data["customer_phone"],
            origin=data["origin"],
            destination=data["destination"],
            service_date=data["service_date"],
            service_time=data["service_time"],
            passengers=int(data["passengers"]),
            vehicle_type=data["vehicle_type"],
            final_price=float(data["final_price"]),
            payment_method=data["payment_method"],
            customer_whatsapp=data.get("customer_whatsapp", ""),
            notes=data.get("notes", "")
        )

        confirmation_msg = get_confirmation_message(booking)
        return jsonify({
            "booking_id": booking.booking_id,
            "booking_status": booking.booking_status,
            "payment_status": booking.payment_status,
            "customer_qr_status": booking.customer_qr_status,
            "customer_qr_url": booking.customer_qr_url,
            "confirmation_message": confirmation_msg
        })
    except Exception as e:
        logger.error("Error creando reserva: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/booking/validate-fields", methods=["POST"])
def validate_booking_fields():
    """Valida si estan todos los datos para confirmar una reserva."""
    data = request.get_json()
    missing = validate_required_fields(data or {})
    return jsonify({
        "ready": len(missing) == 0,
        "missing_fields": missing
    })


# ─────────────────────────────────────────────
# VERIFICACION QR — CLIENTE ESCANEA VEHICULO
# ─────────────────────────────────────────────
@app.route("/vehicle/verify/<vehicle_id>", methods=["GET"])
def vehicle_verify(vehicle_id):
    """
    El cliente escanea el QR del vehiculo.
    Retorna GREEN / RED / YELLOW segun validaciones.
    """
    token = request.args.get("token", "")
    booking_id = request.args.get("booking_id", "")

    # En produccion: consultar base de datos con vehicle_id y token
    # Por ahora retorna estructura de respuesta correcta
    try:
        # TODO: consultar Airtable/DB para validar vehicle_id + token + booking
        # Estructura de respuesta para la pantalla del cliente
        result = {
            "vehicle_id": vehicle_id,
            "color_status": "yellow",
            "boarding_allowed": False,
            "vehicle_verification_status": VehicleVerificationStatus.YELLOW_PENDING,
            "status_message": "Validacion pendiente. Contacte a la central antes de abordar.",
            "call_central": True,
            "emovils_central": "+18091234567"
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "color_status": "red",
            "boarding_allowed": False,
            "vehicle_verification_status": VehicleVerificationStatus.RED_FAILED,
            "status_message": "No aborde. Contacte a la central de Emovils.",
            "error": str(e)
        }), 500


# ─────────────────────────────────────────────
# VERIFICACION QR — CONDUCTOR ESCANEA QR DEL CLIENTE
# ─────────────────────────────────────────────
@app.route("/verify/<booking_id>", methods=["GET"])
def customer_qr_verify(booking_id):
    """
    El conductor escanea el QR del cliente.
    Valida reserva, token, conductor asignado y estado del QR.
    """
    token = request.args.get("token", "")
    driver_id = request.args.get("driver_id", "")

    # En produccion: consultar DB por booking_id, validar token y driver_id
    # Retornar resultado de validacion
    return jsonify({
        "booking_id": booking_id,
        "token_received": bool(token),
        "status": "pending_validation",
        "message": "Validacion en proceso. Configure la base de datos para activar esta funcion."
    })


@app.route("/driver/confirm-pickup", methods=["POST"])
def driver_confirm_pickup():
    """El conductor confirma la recogida despues de escanear el QR del cliente."""
    data = request.get_json()
    booking_id = data.get("booking_id")
    driver_id = data.get("driver_id")
    location = data.get("location", "")

    if not booking_id or not driver_id:
        return jsonify({"error": "booking_id y driver_id son requeridos"}), 400

    # En produccion: actualizar estado en DB
    return jsonify({
        "booking_id": booking_id,
        "pickup_confirmed": True,
        "booking_status": BookingStatus.IN_PROGRESS,
        "customer_qr_status": CustomerQRStatus.USED,
        "message": "Recogida confirmada. Servicio en progreso."
    })


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    logger.info(f"Emovils OPC servidor iniciado en puerto {port}")
    logger.info("CPA Máximo: $6 USD — INVIOLABLE")
    app.run(host="0.0.0.0", port=port, debug=debug)
