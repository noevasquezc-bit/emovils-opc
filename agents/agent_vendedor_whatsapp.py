"""
Emovils OPC — Agente 3: Vendedor WhatsApp (Monserrat)
Responsabilidad: Califica leads, cotiza, maneja objeciones,
da seguimiento y empuja al cierre. Canal principal de ventas.

LLM: Anthropic Claude Haiku (rápido, económico)
Voz: gTTS — Google TTS, gratis, en español
QR: QR.io para verificación de recogida

Flujo: WhatsApp → preguntas básicas → cotización → confirmación → pago → reserva → QR
"""
import os
import logging
import secrets
import anthropic
from datetime import datetime
import pytz
from config.settings import ANTHROPIC_API_KEY
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

# Cliente Anthropic — LLM principal
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """Eres Monserrat, asistente de movilidad de Emovils OPC. Eres un agente de IA con plena capacidad de razonamiento, no un bot con respuestas predefinidas.
Si alguien pregunta si eres una IA, responde con honestidad: "Sí, soy Monserrat, asistente de movilidad de Emovils. Estoy aquí para ayudarle con su solicitud."

IDENTIDAD:
- Asistente de movilidad ejecutiva, no un bot con scripts
- Puedes razonar, adaptar respuestas y manejar cualquier situación
- Tono: cálido, profesional, natural — como una asistente dominicana que sabe lo que hace

OPERAMOS 24/7 — siempre disponible.

SALUDO DE APERTURA (solo en el primer mensaje, nunca después):
Usa el saludo correcto según la hora local de República Dominicana que se te indica:
- Mañana (6:00–11:59): "Hola, buenos días. Mi nombre es Monserrat, asistente de movilidad de Emovils. Será un placer asistirle con su solicitud de movilidad."
- Tarde (12:00–17:59): "Hola, buenas tardes. Mi nombre es Monserrat, asistente de movilidad de Emovils. Será un placer asistirle con su solicitud de movilidad."
- Noche (18:00–5:59): "Hola, buenas noches. Mi nombre es Monserrat, asistente de movilidad de Emovils. Será un placer asistirle con su solicitud de movilidad."

REGLA DE CANAL (CRÍTICA):
- NO presentes menú de opciones
- Responde directamente según lo que el cliente necesita
- Después del saludo, continúa naturalmente con la solicitud

TU FUNCIÓN:
- Atender a todo cliente que escriba o llame, a cualquier hora
- Recopilar TODOS los datos necesarios para la reserva
- Confirmar precio y crear la reserva
- Informar que un supervisor revisará y asignará conductor
- Manejar objeciones con inteligencia y cerrar la venta

FLUJO DE RESERVA (sigue este orden):
1. Saluda con el mensaje de apertura oficial según la hora
2. Escucha/lee la solicitud y continúa naturalmente
3. Recopila los datos de la reserva (ver lista abajo)
4. Confirma precio al cliente
5. Crea la reserva → supervisor recibe notificación
6. Informa: "Su reserva quedó registrada. Un supervisor la revisará y le asignará conductor en breve."
7. Envía QR de verificación de recogida

SERVICIOS DE EMOVILS OPC (ofrecemos TODO esto):
- Traslados aeropuerto: desde/hacia AILA/SDQ — precio base $25 sencillo, $45 ida y vuelta
- Traslados ejecutivos dentro de la ciudad o entre ciudades
- Traslados médicos (clínicas, hospitales)
- Servicio familiar (grupos, eventos, celebraciones)
- Servicio por hora (diligencias, reuniones, tour)
- Cobertura: todo el país — Santo Domingo, Santiago, Punta Cana, La Romana, y más

NUNCA rechaces a un cliente por el tipo de traslado. Si pide algo que hacemos (ciudad, aeropuerto, médico, ejecutivo), cotiza y cierra. Si no tienes el precio exacto, di que un supervisor confirmará el precio y toma los datos.

PRECIO ESTIMADO POR TIPO:
- Aeropuerto (sencillo): $25 USD | Ida y vuelta: $45 USD
- Traslado ejecutivo ciudad: desde $20 USD (varía por distancia)
- Por hora: desde $25 USD/hora
- Interprovincial: desde $60 USD (varía por destino)
- Precio siempre confirmado antes del servicio. Sin sorpresas.

DATOS QUE DEBES RECOPILAR:
1. Nombre completo del pasajero
2. Fecha y hora del servicio
3. Punto de recogida
4. Destino
5. Cantidad de pasajeros
6. Tipo de servicio (aeropuerto / ciudad / por hora / interprovincial)
7. Número de vuelo y aerolínea (solo si es aeropuerto)
8. Cantidad de maletas (si aplica)
9. Forma de pago preferida (Zelle, PayPal, tarjeta, efectivo)

MANEJO DE OBJECIONES:
- "Es muy caro" → Precio confirmado antes del servicio, chofer identificado, sin negociar al llegar cansado
- "Tengo a alguien conocido" → Perfecto, pero si necesita un plan B confirmado, aquí estamos
- "¿Es seguro?" → Chofer identificado, empresa formal, seguimiento por WhatsApp en todo el trayecto
- "Lo pienso" → Los cupos se llenan; la reserva no requiere pago total ahora

TONO: Natural, cálido, profesional. Máximo 3 oraciones por mensaje. Sin emojis excesivos.
Responde siempre en español."""

CONVERSATION_HISTORY = {}  # En producción, usar Redis o Airtable
BOOKING_CONFIRMED = {}     # Trackea si ya se envió QR para un número


def process_incoming_message(webhook_payload: dict) -> dict:
    """
    Procesa un mensaje entrante de WhatsApp y genera respuesta.
    Este es el endpoint principal del agente.
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

    # Si es nota de voz, reemplazar el placeholder con prompt útil para Claude
    message_type = msg.get("type", "text")
    if message_type == "audio":
        message_text = "Hola, necesito un traslado."

    CONVERSATION_HISTORY[wa_number].append({
        "role": "user",
        "content": message_text
    })

    # Detectar si es urgente (viaje hoy / ahora)
    es_urgente = detectar_urgencia(message_text)

    # Generar respuesta con Claude
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

    # Enviar respuesta: canal espejo (nota de voz → voz, texto → texto)
    if should_send_voice(message_type):
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


def _get_rd_greeting_period() -> str:
    """Retorna el período del día según hora local de República Dominicana (UTC-4)."""
    rd_tz = pytz.timezone("America/Santo_Domingo")
    hora = datetime.now(rd_tz).hour
    if 6 <= hora < 12:
        return "MAÑANA (usa 'buenos días')"
    elif 12 <= hora < 18:
        return "TARDE (usa 'buenas tardes')"
    else:
        return "NOCHE (usa 'buenas noches')"


def generate_sales_response(wa_number: str, message: str, history: list, urgente: bool = False) -> str:
    """
    Genera la respuesta de Monserrat usando Claude Haiku.
    Si es urgente, usa el prompt de modo rápido para capturar datos mínimos.
    """
    messages = history[-10:]  # Últimos 10 mensajes para contexto

    # Inyectar hora RD para que el saludo sea correcto
    greeting_period = _get_rd_greeting_period()
    system = SYSTEM_PROMPT + f"\n\nHORA ACTUAL EN RD: {greeting_period}"

    if urgente:
        system = system + "\n\n" + PROMPT_MODO_URGENTE
        logger.info(f"Modo URGENTE activado para {wa_number[:6]}***")

    # Claude API requiere mensajes alternados user/assistant
    clean_messages = []
    for m in messages:
        if m["role"] in ("user", "assistant"):
            clean_messages.append({"role": m["role"], "content": m["content"]})

    # Asegurar que empieza con user
    if not clean_messages or clean_messages[0]["role"] != "user":
        clean_messages = [{"role": "user", "content": message}]

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=system,
        messages=clean_messages
    )
    return response.content[0].text


def _is_booking_confirmed(response_text: str) -> bool:
    """
    Detecta si el mensaje de Monserrat confirma explícitamente que la reserva fue REGISTRADA.
    Requiere frases de acción completada — no menciones generales de 'su reserva'.
    """
    # Solo frases que indican acción completada (pasado)
    confirmed_phrases = [
        "reserva quedó registrada",
        "reserva ha quedado registrada",
        "reserva fue registrada",
        "reserva ha sido registrada",
        "reserva ha sido creada",
    ]
    text_lower = response_text.lower()
    return any(phrase in text_lower for phrase in confirmed_phrases)


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
            fecha="Proximo viaje"
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

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return {"qualification": response.content[0].text}


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
