"""
Emovils OPC — Stripe API (Pagos digitales)
Reduce informalidad, confirma reservas con pago real.
"""
import logging
from config.settings import STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET

logger = logging.getLogger(__name__)

# El SDK de Stripe es opcional (en RD usamos PayPal — ver lib/paypal_api.py).
# Si no está instalado, el módulo importa igual y las funciones degradan con error claro.
try:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
except ImportError:
    stripe = None
    logger.warning("SDK 'stripe' no instalado — lib.stripe_api en modo degradado (usa PayPal)")


def _stripe_listo() -> bool:
    """True si el SDK está instalado y hay clave secreta configurada."""
    if stripe is None:
        logger.warning("Stripe no disponible: falta el paquete 'stripe' (pip install stripe)")
        return False
    if not STRIPE_SECRET_KEY:
        logger.warning("Stripe no disponible: falta STRIPE_SECRET_KEY en el .env")
        return False
    return True

EMOVILS_PRODUCTS = {
    "airport_sencillo": {
        "name": "Emovils Airport — Traslado Sencillo",
        "price_usd": 2500,  # En centavos
        "description": "Traslado privado desde/hacia AILA/SDQ. Vehículo confirmado, chofer identificado."
    },
    "airport_ida_vuelta": {
        "name": "Emovils Airport — Ida y Vuelta",
        "price_usd": 4500,
        "description": "Traslado ida y vuelta AILA/SDQ. Precio confirmado antes de su llegada."
    },
    "family": {
        "name": "Emovils Family — Traslado Familiar",
        "price_usd": 3000,
        "description": "Traslado familiar privado con cuidado especial."
    },
    "medical": {
        "name": "Emovils Medical — Cita Médica",
        "price_usd": 2000,
        "description": "Transporte confiable para citas médicas."
    }
}


def create_payment_link(
    product_key: str,
    customer_name: str,
    customer_email: str = None,
    metadata: dict = None,
    custom_price_usd: int = None
) -> dict:
    """
    Crea un link de pago de Stripe para enviar por WhatsApp.
    Retorna el URL del link de pago.
    """
    if not _stripe_listo():
        return {"error": "stripe_no_configurado", "payment_url": None}
    product_info = EMOVILS_PRODUCTS.get(product_key, EMOVILS_PRODUCTS["airport_sencillo"])
    price_cents = custom_price_usd if custom_price_usd else product_info["price_usd"]

    line_items = [{
        "price_data": {
            "currency": "usd",
            "unit_amount": price_cents,
            "product_data": {
                "name": product_info["name"],
                "description": product_info["description"]
            }
        },
        "quantity": 1
    }]

    session_meta = {
        "customer_name": customer_name,
        "product": product_key,
        **(metadata or {})
    }

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=line_items,
        mode="payment",
        success_url="https://emovils.com/gracias?session_id={CHECKOUT_SESSION_ID}",
        cancel_url="https://emovils.com/cotizacion",
        customer_email=customer_email,
        metadata=session_meta
    )

    logger.info(f"Payment link creado: {customer_name} — ${price_cents/100}")
    return {
        "session_id": session.id,
        "payment_url": session.url,
        "amount_usd": price_cents / 100,
        "customer": customer_name
    }


def verify_payment(session_id: str) -> dict:
    """Verifica si un pago fue completado."""
    if not _stripe_listo():
        return {"error": "stripe_no_configurado", "session_id": session_id, "paid": False}
    session = stripe.checkout.Session.retrieve(session_id)
    return {
        "session_id": session_id,
        "status": session.payment_status,
        "paid": session.payment_status == "paid",
        "amount_usd": session.amount_total / 100 if session.amount_total else 0,
        "customer_email": session.customer_email,
        "metadata": dict(session.metadata)
    }


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """
    Procesa webhooks de Stripe.
    Retorna el evento y datos relevantes si el pago fue completado.
    """
    if not _stripe_listo():
        return {"event": "stripe_no_configurado", "processed": False}
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Firma de webhook inválida: {e}")
        raise

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        logger.info(f"Pago completado: {session['id']} — ${session['amount_total']/100}")
        return {
            "event": "payment_completed",
            "session_id": session["id"],
            "amount_usd": session["amount_total"] / 100,
            "customer_email": session.get("customer_email"),
            "metadata": dict(session.get("metadata", {}))
        }

    return {"event": event["type"], "processed": False}


def create_refund(payment_intent_id: str, reason: str = "requested_by_customer") -> dict:
    """Procesa un reembolso."""
    if not _stripe_listo():
        return {"error": "stripe_no_configurado", "refund_id": None, "status": "no_disponible"}
    refund = stripe.Refund.create(
        payment_intent=payment_intent_id,
        reason=reason
    )
    logger.info(f"Reembolso creado: {refund.id}")
    return {"refund_id": refund.id, "status": refund.status}
