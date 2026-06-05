"""
Emovils OPC — PayPal API (Pagos digitales) ✅
Usado en República Dominicana en lugar de Stripe.
PayPal Live conectado con credenciales del .env
"""
import requests
import logging
import base64
from typing import Optional
from config.settings import PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_MODE

logger = logging.getLogger(__name__)

# URLs según el modo
PAYPAL_BASE_URL = (
    "https://api-m.paypal.com" if PAYPAL_MODE == "live"
    else "https://api-m.sandbox.paypal.com"
)

EMOVILS_PRODUCTS = {
    "airport_sencillo": {
        "name": "Emovils Airport — Traslado Sencillo",
        "price_usd": "25.00",
        "description": "Traslado privado desde/hacia AILA/SDQ. Vehículo confirmado, chofer identificado."
    },
    "airport_ida_vuelta": {
        "name": "Emovils Airport — Ida y Vuelta",
        "price_usd": "45.00",
        "description": "Traslado ida y vuelta AILA/SDQ. Precio confirmado antes de su llegada."
    },
    "family": {
        "name": "Emovils Family — Traslado Familiar",
        "price_usd": "35.00",
        "description": "Traslado familiar privado con cuidado especial."
    },
    "medical": {
        "name": "Emovils Medical — Cita Médica",
        "price_usd": "20.00",
        "description": "Transporte confiable para citas médicas."
    }
}


def _get_access_token() -> str:
    """Obtiene el token de acceso OAuth2 de PayPal."""
    credentials = base64.b64encode(
        f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()
    ).decode()

    resp = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data="grant_type=client_credentials",
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_payment_order(
    product_key: str,
    customer_name: str,
    booking_id: str,
    custom_price: Optional[float] = None,
    return_url: str = "https://emovils.com/pago-exitoso",
    cancel_url: str = "https://emovils.com/pago-cancelado"
) -> dict:
    """
    Crea una orden de pago en PayPal.
    Retorna el link de aprobación para enviar al cliente.
    """
    product = EMOVILS_PRODUCTS.get(product_key, EMOVILS_PRODUCTS["airport_sencillo"])
    price = str(custom_price) if custom_price else product["price_usd"]

    token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": booking_id,
            "description": f"{product['name']} — {customer_name}",
            "custom_id": booking_id,
            "amount": {
                "currency_code": "USD",
                "value": price
            }
        }],
        "application_context": {
            "brand_name": "Emovils OPC",
            "user_action": "PAY_NOW",
            "return_url": return_url,
            "cancel_url": cancel_url
        }
    }

    resp = requests.post(
        f"{PAYPAL_BASE_URL}/v2/checkout/orders",
        json=order_data,
        headers=headers,
        timeout=15
    )
    resp.raise_for_status()
    data = resp.json()

    # Extraer link de aprobación
    approve_url = next(
        (link["href"] for link in data.get("links", []) if link["rel"] == "approve"),
        None
    )

    logger.info(f"Orden PayPal creada: {data['id']} — {customer_name} — ${price}")
    return {
        "order_id": data["id"],
        "status": data["status"],
        "approve_url": approve_url,
        "amount_usd": price
    }


def capture_payment(order_id: str) -> dict:
    """Captura (confirma) un pago aprobado por el cliente."""
    token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    resp = requests.post(
        f"{PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture",
        headers=headers,
        timeout=15
    )
    resp.raise_for_status()
    data = resp.json()

    logger.info(f"Pago capturado: {order_id} — Status: {data['status']}")
    return {
        "order_id": order_id,
        "status": data["status"],
        "capture_id": data.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [{}])[0].get("id"),
        "amount_usd": data.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [{}])[0].get("amount", {}).get("value"),
        "payer_email": data.get("payer", {}).get("email_address"),
        "payer_name": f"{data.get('payer', {}).get('name', {}).get('given_name', '')} {data.get('payer', {}).get('name', {}).get('surname', '')}".strip()
    }


def get_payment_link(product_key: str, customer_name: str, booking_id: str,
                     custom_price: Optional[float] = None) -> str:
    """Helper: crea orden y retorna solo el link de aprobación."""
    order = create_payment_order(product_key, customer_name, booking_id, custom_price)
    return order["approve_url"] or f"https://www.paypal.com/checkoutnow?token={order['order_id']}"


def handle_webhook(payload: dict) -> dict:
    """
    Procesa un webhook de PayPal (pago completado, etc.)
    Retorna datos del evento normalizado.
    """
    event_type = payload.get("event_type", "")
    resource = payload.get("resource", {})

    if event_type == "CHECKOUT.ORDER.APPROVED":
        order_id = resource.get("id")
        return {
            "type": "payment_approved",
            "order_id": order_id,
            "status": "approved",
            "action": "capture_payment"
        }

    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        return {
            "type": "payment_completed",
            "capture_id": resource.get("id"),
            "order_id": resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id"),
            "amount_usd": resource.get("amount", {}).get("value"),
            "payer_email": resource.get("payer", {}).get("email_address"),
            "status": "completed",
            "action": "confirm_booking"
        }

    elif event_type in ("PAYMENT.CAPTURE.DENIED", "CHECKOUT.ORDER.VOIDED"):
        return {
            "type": "payment_failed",
            "status": "failed",
            "action": "notify_failure"
        }

    return {"type": "unknown_event", "event_type": event_type}
