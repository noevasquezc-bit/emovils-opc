"""
Emovils — WhatsApp API via GREEN API
Canal principal de ventas, cotizacion y cierre.
Green API: https://green-api.com — Instancia 7107644324 / numero 18298610090
"""
import requests
import logging
from typing import Optional
from config.settings import WHATSAPP_API_URL, WHATSAPP_INSTANCE_ID, WHATSAPP_API_KEY

logger = logging.getLogger(__name__)


def _url(method: str) -> str:
    return f"{WHATSAPP_API_URL}/waInstance{WHATSAPP_INSTANCE_ID}/{method}/{WHATSAPP_API_KEY}"


SCRIPTS = {
    "bienvenida_cotizacion": (
        "Hola, gracias por contactar a Emovils Traslados Ejecutivos.\n\n"
        "Con gusto le cotizamos su traslado. Para darle precio exacto necesitamos:\n"
        "1. Punto de origen\n"
        "2. Destino\n"
        "3. Fecha y hora\n"
        "4. Cantidad de pasajeros\n\n"
        "Si puede compartir su ubicacion de WhatsApp o la direccion exacta, "
        "le calculamos el precio de inmediato."
    ),
    "seguimiento_no_responde": (
        "Hola, le escribo para confirmar si aun desea reservar su traslado.\n\n"
        "Podemos dejarle el servicio coordinado con anticipacion para evitar contratiempos."
    ),
    "cierre_reserva": (
        "Perfecto. Para confirmar la reserva necesitamos:\n"
        "- Nombre del pasajero:\n"
        "- Fecha y hora:\n"
        "- Origen:\n"
        "- Destino:\n"
        "- Cantidad de pasajeros:\n"
        "- Forma de pago preferida:\n\n"
        "Una vez confirmado, le enviamos los datos del servicio y del conductor."
    ),
    "confirmacion_pago": (
        "Reserva confirmada.\n\n"
        "Su traslado esta listo. Detalles del servicio:\n"
        "- Chofer: {nombre_chofer}\n"
        "- Vehiculo: {vehiculo}\n"
        "- WhatsApp del chofer: {tel_chofer}\n\n"
        "Cualquier pregunta, estamos aqui."
    ),
    "recordatorio_24h": (
        "Hola {nombre}, le recordamos que manana tiene su traslado con Emovils.\n\n"
        "- Fecha: {fecha}\n"
        "- Hora: {hora}\n"
        "- Vuelo: {vuelo}\n"
        "- Chofer: {chofer}\n\n"
        "Si hay algun cambio en su vuelo, avisenos de inmediato."
    ),
    "solicitar_resena": (
        "Esperamos que su traslado haya sido excelente, {nombre}.\n\n"
        "Si le parecio bien el servicio, podria dejarnos una resena de 5 estrellas? "
        "Nos ayuda mucho:\n"
        "{google_review_link}\n\n"
        "Gracias y hasta la proxima!"
    )
}


def send_text(to: str, message: str) -> dict:
    """
    Envia un mensaje de texto por WhatsApp via Green API.
    'to' debe ser el numero completo: 18298610090 (sin + ni espacios)
    """
    chat_id = f"{to}@c.us" if "@" not in to else to
    payload = {
        "chatId": chat_id,
        "message": message
    }
    resp = requests.post(_url("sendMessage"), json=payload, timeout=15)
    resp.raise_for_status()
    logger.info("Mensaje enviado a %s***", to[:6])
    return resp.json()


def send_file_by_url(to: str, url: str, filename: str, caption: str = "") -> dict:
    """Envia un archivo (imagen, PDF) por URL."""
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
    precio_rd: int,
    paypal_link: str = ""
) -> dict:
    """Envia la cotizacion con precio en pesos dominicanos."""
    message = (
        "Cotizacion Emovils Traslados Ejecutivos\n\n"
        "Pasajero: " + nombre + "\n"
        "Fecha: " + fecha + " — " + hora + "\n"
        "Ruta: " + origen + " -> " + destino + "\n"
        "Precio: RD$" + "{:,}".format(precio_rd) + "\n\n"
        "Para confirmar la reserva, indicarnos su forma de pago preferida.\n"
        "Formas de pago: Zelle, PayPal, tarjeta o efectivo."
    )
    return send_text(to, message)


def get_instance_state() -> dict:
    """Verifica el estado de la instancia Green API."""
    resp = requests.get(_url("getStateInstance"), timeout=10)
    resp.raise_for_status()
    return resp.json()


def receive_notification() -> Optional[dict]:
    """
    Recibe una notificacion de la cola de Green API (polling).
    """
    resp = requests.get(_url("receiveNotification"), timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        if data:
            return data
    return None


def delete_notification(receipt_id: int) -> bool:
    """Borra una notificacion de la cola despues de procesarla."""
    url = (
        f"{WHATSAPP_API_URL}/waInstance{WHATSAPP_INSTANCE_ID}"
        f"/deleteNotification/{WHATSAPP_API_KEY}/{receipt_id}"
    )
    resp = requests.delete(url, timeout=10)
    return resp.status_code == 200


def parse_webhook_event(payload: dict) -> Optional[dict]:
    """
    Parsea un webhook/notificacion de Green API y extrae el mensaje.
    Soporta: mensajes de texto y mensajes de ubicacion (location).
    """
    try:
        body = payload.get("body", payload)
        type_webhook = body.get("typeWebhook", "")

        if type_webhook != "incomingMessageReceived":
            return None

        message_data = body.get("messageData", {})
        sender_data = body.get("senderData", {})

        from_number = sender_data.get("sender", "").replace("@c.us", "")
        contact_name = sender_data.get("senderName", "")
        message_id = body.get("idMessage", "")
        timestamp = body.get("timestamp", 0)
        type_message = message_data.get("typeMessage", "")

        if not from_number:
            return None

        # Mensaje de texto
        if type_message == "textMessage":
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

        # Mensaje de ubicacion (location share)
        if type_message == "locationMessage":
            location_data = message_data.get("locationMessageData", {})
            latitude = location_data.get("latitude", 0)
            longitude = location_data.get("longitude", 0)
            name_location = location_data.get("nameLocation", "")
            address = location_data.get("address", "")

            # Generar texto descriptivo de la ubicacion para el agente
            location_text = f"[UBICACION COMPARTIDA] Lat: {latitude}, Lon: {longitude}"
            if address:
                location_text += f" | Direccion: {address}"
            if name_location:
                location_text += f" | Lugar: {name_location}"

            return {
                "from": from_number,
                "type": "location",
                "text": location_text,
                "latitude": latitude,
                "longitude": longitude,
                "address": address,
                "name_location": name_location,
                "timestamp": timestamp,
                "message_id": message_id,
                "contact_name": contact_name
            }

        # Otros tipos de mensaje (imagen, audio, etc.) — acusar recibo
        return {
            "from": from_number,
            "type": type_message,
            "text": f"[Archivo recibido: {type_message}]",
            "timestamp": timestamp,
            "message_id": message_id,
            "contact_name": contact_name
        }

    except Exception as e:
        logger.error("Error parseando webhook Green API: %s", e)
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
        "forma_pago": None,
        "latitud": None,
        "longitud": None
    }
