"""
Emovils OPC — WhatsApp API via GREEN API ✅
Canal principal de ventas, cotización y cierre.
Green API: https://green-api.com — Conectado con número 18298610090
"""
import requests
import logging
from typing import Optional
from config.settings import WHATSAPP_API_URL, WHATSAPP_INSTANCE_ID, WHATSAPP_API_KEY

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# BASE URL para Green API
# Formato: {apiUrl}/waInstance{instanceId}/{method}/{apiTokenInstance}
# ─────────────────────────────────────────────
def _url(method: str) -> str:
    return f"{WHATSAPP_API_URL}/waInstance{WHATSAPP_INSTANCE_ID}/{method}/{WHATSAPP_API_KEY}"


# ─────────────────────────────────────────────
# GUIONES DE WHATSAPP — Emovils Airport
# ─────────────────────────────────────────────
SCRIPTS = {
    "bienvenida_cotizacion": """¡Hola! Bienvenido a Emovils 🚗

Hacemos traslados ejecutivos en todo el país — aeropuerto, ciudad, médicos, interprovinciales. Dígame, ¿para dónde necesita ir y cuándo?""",

    "seguimiento_no_responde": """Hola, le escribo para confirmar si aún desea reservar su traslado desde el aeropuerto.

Podemos dejarle el servicio coordinado antes de su llegada para que no tenga que negociar transporte ni esperar al salir.""",

    "cierre_reserva": """Perfecto. Podemos reservar su traslado.

Para confirmar necesitamos:
- Nombre del pasajero:
- Fecha y hora:
- Número de vuelo:
- Destino:
- Cantidad de pasajeros:
- Teléfono de contacto:

Una vez confirmado, le enviamos los datos del servicio y las instrucciones de encuentro.""",

    "confirmacion_pago": """✅ ¡Reserva confirmada!

Su traslado está listo. Detalles del servicio:
- Chofer: {nombre_chofer}
- Vehículo: {vehiculo}
- WhatsApp del chofer: {tel_chofer}
- Punto de encuentro: Sala de llegadas internacionales, señal con su nombre.

¿Alguna pregunta? Estamos aquí.""",

    "recordatorio_24h": """Hola {nombre}, le recordamos que mañana tiene su traslado con Emovils.

- Fecha: {fecha}
- Hora: {hora}
- Vuelo: {vuelo}
- Chofer: {chofer}

Si hay algún cambio en su vuelo, avísenos de inmediato. 🙏""",

    "solicitar_resena": """Esperamos que su traslado haya sido excelente, {nombre}.

Si le pareció bien el servicio, ¿podría dejarnos una reseña de 5 estrellas? Nos ayuda mucho:
👉 {google_review_link}

¡Gracias y hasta la próxima!"""
}


# ─────────────────────────────────────────────
# ENVÍO DE MENSAJES — Green API
# ─────────────────────────────────────────────
def send_text(to: str, message: str) -> dict:
    """
    Envía un mensaje de texto por WhatsApp via Green API.
    'to' debe ser el número completo: 18298610090 (sin + ni espacios)
    """
    chat_id = f"{to}@c.us" if "@" not in to else to
    payload = {
        "chatId": chat_id,
        "message": message
    }
    resp = requests.post(_url("sendMessage"), json=payload, timeout=15)
    resp.raise_for_status()
    logger.info(f"Mensaje enviado a {to[:6]}***")
    return resp.json()


def send_file_by_url(to: str, url: str, filename: str, caption: str = "") -> dict:
    """Envía un archivo (imagen, PDF) por URL."""
    chat_id = f"{to}@c.us" if "@" not in to else to
    payload = {
        "chatId": chat_id,
        "urlFile": url,
        "fileName": filename,
        "caption": caption
    }
    resp = requests.post(_url("sendFileByUrl"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def send_quote(
    to: str,
    nombre: str,
    fecha: str,
    hora: str,
    origen: str,
    destino: str,
    precio_usd: float,
    paypal_link: str
) -> dict:
    """Envía la cotización con precio y link de pago PayPal."""
    message = (
        f"✅ Cotización Emovils Airport\n\n"
        f"Pasajero: {nombre}\n"
        f"Fecha: {fecha} — {hora}\n"
        f"Ruta: {origen} → {destino}\n"
        f"Precio: USD ${precio_usd:.2f}\n\n"
        f"Para reservar y confirmar su traslado, realice el pago aquí:\n"
        f"👉 {paypal_link}\n\n"
        f"Una vez confirmado el pago, le enviamos los datos del chofer."
    )
    return send_text(to, message)


def get_instance_state() -> dict:
    """Verifica el estado de la instancia Green API."""
    resp = requests.get(_url("getStateInstance"), timeout=10)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────
# WEBHOOK — Recepción de mensajes
# ─────────────────────────────────────────────
def receive_notification() -> Optional[dict]:
    """
    Recibe una notificación de la cola de Green API (polling).
    Usar cuando no hay webhook configurado.
    """
    resp = requests.get(_url("receiveNotification"), timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        if data:
            return data
    return None


def delete_notification(receipt_id: int) -> bool:
    """Borra una notificación de la cola después de procesarla."""
    url = (
        f"{WHATSAPP_API_URL}/waInstance{WHATSAPP_INSTANCE_ID}"
        f"/deleteNotification/{WHATSAPP_API_KEY}/{receipt_id}"
    )
    resp = requests.delete(url, timeout=10)
    return resp.status_code == 200


def parse_webhook_event(payload: dict) -> Optional[dict]:
    """
    Parsea un webhook/notificación de Green API y extrae el mensaje.
    Green API usa formato diferente a Meta Cloud API.
    """
    try:
        body = payload.get("body", payload)
        type_webhook = body.get("typeWebhook", "")

        logger.info(f"Webhook recibido: typeWebhook={type_webhook}")

        if type_webhook != "incomingMessageReceived":
            return None

        message_data = body.get("messageData", {})
        sender_data = body.get("senderData", {})

        from_number = sender_data.get("sender", "").replace("@c.us", "")
        contact_name = sender_data.get("senderName", "")
        message_id = body.get("idMessage", "")
        timestamp = body.get("timestamp", 0)
        type_message = message_data.get("typeMessage", "")

        logger.info(f"typeMessage={type_message} de {from_number[:6] if from_number else 'unknown'}***")

        if not from_number:
            return None

        # Nota de voz: Green API usa typeMessage "audioMessage" o "pttMessage"
        if type_message in ("audioMessage", "pttMessage"):
            return {
                "from": from_number,
                "type": "audio",
                "text": "[nota de voz]",
                "timestamp": timestamp,
                "message_id": message_id,
                "contact_name": contact_name
            }

        # Texto normal
        text_data = message_data.get("textMessageData", {})
        text = text_data.get("textMessage", "")

        if not text:
            return None

        return {
            "from": from_number,
            "type": "text",
            "text": text,
            "timestamp": timestamp,
            "message_id": message_id,
            "contact_name": contact_name
        }
    except Exception as e:
        logger.error(f"Error parseando webhook Green API: {e}")
        return None


def extract_booking_data(message_text: str) -> dict:
    """
    Esquema de campos a extraer de un mensaje de reserva.
    El agente IA rellena estos campos via NLP.
    """
    return {
        "nombre": None,
        "fecha_viaje": None,
        "hora_viaje": None,
        "vuelo": None,
        "aerolinea": None,
        "origen": None,
        "destino": None,
        "pasajeros": None,
        "maletas": None,
        "tipo_servicio": "ida",
        "forma_pago": None
    }
