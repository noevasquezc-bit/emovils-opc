"""
Emovils OPC — Agente 3: Vendedor WhatsApp (Monserrat)
Responsabilidad: Califica leads, cotiza, maneja objeciones,
da seguimiento y empuja al cierre. Canal principal de ventas.

LLM: OpenAI GPT-4o-mini (más económico, muy rápido)
Voz: OpenAI TTS "nova" (disponible bajo demanda)
QR: QR.io para verificación de recogida

Flujo: WhatsApp → preguntas básicas → cotización → confirmación → pago → reserva → QR
"""
import os
import logging
import secrets
from openai import OpenAI
from config.settings import OPENAI_API_KEY, OPENAI_MODEL, ANTHROPIC_API_KEY
from lib.airtable_api import (
    create_lead, update_lead_status, get_lead_by_whatsapp,
    create_reserva, LeadStatus
)
from lib.whatsapp_api import send_text, send_quote, SCRIPTS, parse_webhook_event
from lib.paypal_api import get_payment_link
from lib.google_maps import estimate_price
from lib.qr_generator import generate_pickup_qr, generate_qr_message
from lib.voice_agent import send_voice_message, should_send_voice
from workflows.wf_reserva_urgente import detectar_urgencia, PROMPT_MODO_URGENTE
from workflows.wf_followup_sequence import schedule_followup_sequence

logger = logging.getLogger(__name__)

# Cliente OpenAI — LLM principal
client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """Eres Monserrat, Agente de Reservas de Emovils OPC, empresa de movilidad privada premium en República Dominicana.

OPERAMOS 24/7 — NUNCA digas que estás fuera de horario. Siempre disponible.

TU FUNCIÓN:
- Atender a todo cliente que escriba, a cualquier hora
- Recopilar TODOS los datos necesarios para la reserva
- Confirmar precio y crear la reserva en el sistema
- Informar al cliente que un supervisor revisará y asignará conductor
- Manejar objeciones con confianza y cerrar

FLUJO DE RESERVA (sigue este orden):
1. Saluda calurosamente y pregunta qué necesitan
2. Recopila los datos de la reserva (ver lista abajo)
3. Confirma precio al cliente
4. Crea la reserva → sistema notifica al supervisor
5. Informa: "Su reserva fue registrada. Un supervisor la revisará y le asignará conductor en breve."
6. Envía QR de verificación de recogida

PRODUCTO PRINCIPAL: Emovils Airport
- Servicio: Traslado privado desde/hacia AILA/SDQ (Santo Domingo)
- Precio base: USD $25 (sencillo), $45 (ida y vuelta)
- Incluye: vehículo confirmado, chofer identificado, seguimiento WhatsApp
- Promesa: "Precio confirmado antes de su llegada. Sin sorpresas."

DATOS QUE DEBES RECOPILAR:
1. Nombre completo del pasajero
2. Fecha de llegada/salida
3. Hora estimada
4. Punto de recogida (aeropuerto u otro)
5. Destino final
6. Cantidad de pasajeros
7. Tipo de servicio (ida / regreso / ida y vuelta)
8. Número de vuelo y aerolínea (si es aeropuerto)
9. Cantidad de maletas
10. Forma de pago preferida (Zelle, PayPal, tarjeta, efectivo)

MANEJO DE OBJECIONES:
- "Es muy caro" → Comparar con el costo/estrés de improvisar; precio confirmado, sin sorpresas
- "Tengo a alguien conocido" → Respetar, pero preguntar si tienen plan B confirmado
- "¿Es seguro?" → Chofer identificado, empresa formal, seguimiento por WhatsApp
- "Lo pienso" → Los cupos se llenan; la reserva no requiere pago total ahora

TONO: Profesional, cálido, seguro. Máximo 3 oraciones por mensaje. Sin emojis excesivos.
Responde siempre en español. Sé conciso, directo y orientado al cierre."""

CONVERSATION_HISTORY = {}  # En producción, usar Redis o Airtable
BOOKING_CONFIRMED = {}     # Trackea si ya se envió QR para un número


def process_incoming_message(webhook_payload: dict) -> dict:
    """
    Procesa un mensaje entrante de WhatsApp y genera respuesta.
    Este es el endpoint principal del bot.
    """
    msg = parse_webhook_event(webhook_payload)
    if not msg:
        return {"status": "no_message"}

    wa_number = msg["from"]
    message_text = msg["text"]
    contact_name = msg.get("contact_name", "")

    logger.info(f"Mensaje recibido de {wa_number[:6]}***: {message_text[:50]}")

    # Verificar si el lead existe en Airtable
    existing_lead = get_lead_by_whatsapp(wa_number)
    if not existing_lead:
        create_lead(
            whatsapp=wa_number,
            nombre=contact_name,
            canal_origen="whatsapp_inbound",
            producto="airport"
        )

    # Obtener o crear historial de conversación
    if wa_number not in CONVERSATION_HISTORY:
        CONVERSATION_HISTORY[wa_number] = []

    turn_number = len(CONVERSATION_HISTORY[wa_number]) // 2

    CONVERSATION_HISTORY[wa_number].append({
        "role": "user",
        "content": message_text
    })

    # Detectar si es urgente (viaje hoy / ahora)
    es_urgente = detectar_urgencia(message_text)

    # Generar respuesta con OpenAI
    response_text = generate_sales_response(
        wa_number=wa_number,
        message=message_text,
        history=CONVERSATION_HISTORY[wa_number],
        urgente=es_urgente
    )

    CONVERSATION_HISTORY[wa_number].append({
        "role": "assistant",
        "content": response_text
    })

    # Enviar respuesta: voz en primer saludo, texto el resto
    if should_send_voice(message_text, turn_number):
        send_voice_message(wa_number, response_text)
    else:
        send_text(wa_number, response_text)

    # Detectar reserva confirmada y enviar QR (solo una vez por número)
    if _is_booking_confirmed(response_text) and not BOOKING_CONFIRMED.get(wa_number):
        _send_booking_qr(wa_number, contact_name)
        BOOKING_CONFIRMED[wa_number] = True

    return {
        "status": "responded",
        "to": wa_number,
        "response_preview": response_text[:100]
    }


def generate_sales_response(wa_number: str, message: str, history: list, urgente: bool = False) -> str:
    """
    Genera la respuesta de Monserrat usando OpenAI GPT-4o-mini.
    Si es urgente, usa el prompt de modo rápido para capturar datos mínimos.
    """
    messages = history[-10:]  # Últimos 10 mensajes para contexto

    system = SYSTEM_PROMPT
    if urgente:
        system = SYSTEM_PROMPT + "\n\n" + PROMPT_MODO_URGENTE
        logger.info(f"Modo URGENTE activado para {wa_number[:6]}***")

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=500,
        messages=[
            {"role": "system", "content": system},
            *messages
        ]
    )
    return response.choices[0].message.content


def _is_booking_confirmed(response_text: str) -> bool:
    """
    Detecta si el mensaje de Monserrat indica que se creó una reserva.
    """
    keywords = [
        "reserva fue registrada",
        "reserva ha sido registrada",
        "reserva confirmada",
        "su reserva",
        "supervisor la revisará",
        "le asignará conductor",
        "EMV-"
    ]
    text_lower = response_text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _send_booking_qr(wa_number: str, nombre: str) -> None:
    """
    Genera QR de verificación de recogida y lo envía al pasajero.
    """
    try:
        booking_id = f"EMV-{wa_number[-4:]}-{secrets.token_hex(3).upper()}"
        token = secrets.token_urlsafe(16)

        qr_data = generate_pickup_qr(
            booking_id=booking_id,
            token=token,
            nombre=nombre or "Pasajero",
            fecha="Próximo viaje"
        )

        qr_message = generate_qr_message({
            "pasajero": nombre or "Pasajero",
            "booking_id": booking_id,
            "fecha": "Próximo viaje",
            "qr_url": qr_data.get("qr_url", "")
        })

        send_text(wa_number, qr_message)
        logger.info(f"QR enviado a {wa_number[:6]}*** — booking {booking_id}")

    except Exception as e:
        logger.error(f"Error enviando QR a {wa_number[:6]}***: {e}")


def qualify_lead(wa_number: str, conversation_summary: str) -> dict:
    """
    Califica un lead después de la conversación inicial.
    Retorna scoring y próxima acción recomendada.
    """
    prompt = f"""
    Analiza esta conversación de WhatsApp con un prospecto de Emovils Airport:

    {conversation_summary}

    Evalúa:
    1. Nivel de interés (1-10)
    2. ¿Tiene fecha de viaje confirmada? (sí/no/no mencionó)
    3. ¿Mencionó precio como objeción? (sí/no)
    4. ¿Ya tiene alternativa de transporte? (sí/no/no mencionó)
    5. Siguiente acción recomendada:
       - COTIZAR: tiene toda la info necesaria
       - PREGUNTAR_MAS: necesita más datos
       - SEGUIMIENTO: respondió pero no dio info
       - PERDIDO: claramente no interesado

    Responde en formato JSON.
    """

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=400,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    )
    return {"qualification": response.choices[0].message.content}


def send_quotation(
    wa_number: str,
    nombre: str,
    fecha: str,
    hora: str,
    origen: str,
    destino: str,
    pasajeros: int = 1
) -> dict:
    """
    Genera y envía cotización con precio y link de pago.
    """
    # Calcular precio con Google Maps
    price_info = estimate_price(origen, destino, pasajeros)
    precio = price_info.get("price_usd", 25.0)

    # Crear link de pago en PayPal
    booking_id = f"EMV-{wa_number[-4:]}-{fecha.replace('-', '')}"
    paypal_link = get_payment_link(
        product_key="airport_sencillo",
        customer_name=nombre,
        booking_id=booking_id,
        custom_price=precio
    )

    # Enviar cotización por WhatsApp
    send_quote(
        to=wa_number,
        nombre=nombre,
        fecha=fecha,
        hora=hora,
        origen=origen,
        destino=destino,
        precio_usd=precio,
        paypal_link=paypal_link
    )

    # Actualizar lead en Airtable
    lead = get_lead_by_whatsapp(wa_number)
    if lead:
        update_lead_status(lead["id"], LeadStatus.COTIZADO, f"Cotización ${precio} enviada")

    logger.info(f"Cotización enviada a {wa_number[:6]}***: ${precio} — {origen} → {destino}")
    return {"status": "quoted", "precio_usd": precio, "payment_url": paypal_link}


def send_followup(wa_number: str, reason: str = "no_response") -> dict:
    """Envía mensaje de seguimiento si el cliente no ha respondido."""
    followup_message = SCRIPTS["seguimiento_no_responde"]

    if reason == "not_paid":
        followup_message = """Hola, le escribo para confirmar si pudo completar la reserva.

El link de pago sigue activo. Si tiene alguna pregunta sobre el servicio o el proceso de pago, con gusto le ayudamos."""

    send_text(wa_number, followup_message)
    return {"status": "followup_sent", "reason": reason}


def handle_objection(wa_number: str, objection_type: str) -> str:
    """Genera respuesta personalizada para una objeción específica."""
    objections = {
        "precio_caro": """Entendemos que $25 puede parecer un poco más que otras opciones.

La diferencia es que con Emovils usted tiene:
✓ Precio confirmado ANTES de aterrizar
✓ Chofer identificado esperándole
✓ No hay negociación al salir cansado del vuelo
✓ Seguimiento por WhatsApp

¿Le parece si comparamos opciones y ve cuál le conviene más?""",

        "tiene_conocido": """Perfecto, si ya tiene transporte coordinado, ¡excelente!

Solo como plan B: si por alguna razón ese arreglo falla, estamos disponibles. Muchos clientes nos contactan en último momento y a veces no tenemos disponibilidad.

¿Le guarda el número por si acaso?""",

        "inseguridad": """Nuestros choferes están identificados y registrados. Al confirmar su reserva, le enviamos:
- Foto y nombre del chofer
- Placa del vehículo
- WhatsApp directo del chofer

Empresa formal con historial de clientes. ¿Le cuento más sobre cómo funciona el servicio?""",

        "lo_pienso": """Claro, tómese su tiempo.

Solo le digo que los cupos para fechas de fin de semana y temporada alta se llenan con anticipación. Si su viaje es pronto, le recomiendo reservar aunque sea con un depósito.

¿Cuándo es su fecha de llegada exactamente?"""
    }

    response_text = objections.get(objection_type, SCRIPTS["bienvenida_cotizacion"])
    send_text(wa_number, response_text)
    return response_text
