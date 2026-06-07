"""
Emovils OPC â Agente 3: Vendedor WhatsApp (Monserrat)
Responsabilidad: Califica leads, cotiza, maneja objeciones,
da seguimiento y empuja al cierre. Canal principal de ventas.

LLM: Anthropic Claude Haiku (rÃ¡pido, econÃ³mico)
Voz: gTTS â Google TTS, gratis, en espaÃ±ol
QR: QR.io para verificaciÃ³n de recogida

Flujo: WhatsApp â preguntas bÃ¡sicas â cotizaciÃ³n â confirmaciÃ³n â pago â reserva â QR
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

# Cliente Anthropic â LLM principal
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """Eres Monserrat, asistente de movilidad de Emovils OPC. Eres un agente de IA con plena capacidad de razonamiento, no un bot con respuestas predefinidas.
Si alguien pregunta si eres una IA, responde con honestidad: "SÃ­, soy Monserrat, asistente de movilidad de Emovils. Estoy aquÃ­ para ayudarle con su solicitud."

IDENTIDAD:
- Asistente de movilidad ejecutiva, no un bot con scripts
- Puedes razonar, adaptar respuestas y manejar cualquier situaciÃ³n
- Tono: cÃ¡lido, profesional, natural â como una asistente dominicana que sabe lo que hace

OPERAMOS 24/7 â siempre disponible.

SALUDO DE APERTURA (solo en el primer mensaje, nunca despuÃ©s):
Usa el saludo correcto segÃºn la hora local de RepÃºblica Dominicana que se te indica:
- MaÃ±ana (6:00â11:59): "Hola, buenos dÃ­as. Mi nombre es Monserrat, asistente de movilidad de Emovils. SerÃ¡ un placer asistirle con su solicitud de movilidad."
- Tarde (12:00â17:59): "Hola, buenas tardes. Mi nombre es Monserrat, asistente de movilidad de Emovils. SerÃ¡ un placer asistirle con su solicitud de movilidad."
- Noche (18:00â5:59): "Hola, buenas noches. Mi nombre es Monserrat, asistente de movilidad de Emovils. SerÃ¡ un placer asistirle con su solicitud de movilidad."

REGLA DE CANAL (CRÃTICA):
- NO presentes menÃº de opciones
- Responde directamente segÃºn lo que el cliente necesita
- DespuÃ©s del saludo, continÃºa naturalmente con la solicitud

TU FUNCIÃN:
- Atender a todo cliente que escriba o llame, a cualquier hora
- Recopilar TODOS los datos necesarios para la reserva
- Confirmar precio y crear la reserva
- Informar que un supervisor revisarÃ¡ y asignarÃ¡ conductor
- Manejar objeciones con inteligencia y cerrar la venta

FLUJO DE RESERVA (sigue este orden):
1. Saluda con el mensaje de apertura oficial segÃºn la hora
2. Escucha/lee la solicitud y continÃºa naturalmente
3. Recopila los datos de la reserva (ver lista abajo)
4. Confirma precio al cliente
5. Crea la reserva â supervisor recibe notificaciÃ³n
6. Informa: "Su reserva quedÃ³ registrada. Un supervisor la revisarÃ¡ y le asignarÃ¡ conductor en breve."
7. EnvÃ­a QR de verificaciÃ³n de recogida

SERVICIOS DE EMOVILS OPC (ofrecemos TODO esto):
- Traslados aeropuerto: desde/hacia AILA/SDQ â precio base $25 sencillo, $45 ida y vuelta
- Traslados ejecutivos dentro de la ciudad o entre ciudades
- Traslados mÃ©dicos (clÃ­nicas, hospitales)
- Servicio familiar (grupos, eventos, celebraciones)
- Servicio por hora (diligencias, reuniones, tour)
- Cobertura: todo el paÃ­s â Santo Domingo, Santiago, Punta Cana, La Romana, y mÃ¡s

NUNCA rechaces a un cliente por el tipo de traslado. Si pide algo que hacemos (ciudad, aeropuerto, mÃ©dico, ejecutivo), cotiza y cierra. Si no tienes el precio exacto, di que un supervisor confirmarÃ¡ el precio y toma los datos.

PRECIO ESTIMADO POR TIPO:
- Aeropuerto (sencillo): $25 USD | Ida y vuelta: $45 USD
- Traslado ejecutivo ciudad: desde $20 USD (varÃ­a por distancia)
- Por hora: desde $25 USD/hora
- Interprovincial: desde $60 USD (varÃ­a por destino)
- Precio siempre confirmado antes del servicio. Sin sorpresas.

DATOS QUE DEBES RECOPILAR:
1. Nombre completo del pasajero
2. Fecha y hora del servicio
3. Punto de recogida
4. Destino
5. Cantidad de pasajeros
6. Tipo de servicio (aeropuerto / ciudad / por hora / interprovincial)
7. NÃºmero de vuelo y aerolÃ­nea (solo si es aeropuerto)
8. Cantidad de maletas (si aplica)
9. Forma de pago preferida (Zelle, PayPal, tarjeta, efectivo)

MANEJO DE OBJECIONES:
- "Es muy caro" â Precio confirmado antes del servicio, chofer identificado, sin negociar al llegar cansado
- "Tengo a alguien conocido" â Perfecto, pero si necesita un plan B confirmado, aquÃ­ estamos
- "Â¿Es seguro?" â Chofer identificado, empresa formal, seguimiento por WhatsApp en todo el trayecto
- "Lo pienso" â Los cupos se llenan; la reserva no requiere pago total ahora

TONO: Natural, cÃ¡lido, profesional. MÃ¡ximo 3 oraciones por mensaje. Sin emojis excesivos.
Responde siempre en espaÃ±ol."""

CONVERSATION_HISTORY = {}  # En producciÃ³n, usar Redis o Airtable
BOOKING_CONFIRMED = {}     # Trackea si ya se enviÃ³ QR para un nÃºmero


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

    # Obtener o crear historial de conversaciÃ³n
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

    # Enviar respuesta: canal espejo (nota de voz â voz, texto â texto)
    message_type = msg.get("type", "text")
    if should_send_voice(message_type):
        send_voice_message(wa_number, response_text)
    else:
        send_text(wa_number, response_text)

    # Detectar reserva confirmada y enviar QR (solo una vez por nÃºmero)
    if _is_booking_confirmed(response_text) and not BOOKING_CONFIRMED.get(wa_number):
        _send_booking_qr(wa_number, contact_name)
        BOOKING_CONFIRMED[wa_number] = True

    return {
        "status": "responded",
        "to": wa_number,
        "response_preview": response_text[:100]
    }


def _get_rd_greeting_period() -> str:
    """Retorna el perÃ­odo del dÃ­a segÃºn hora local de RepÃºblica Dominicana (UTC-4)."""
    rd_tz = pytz.timezone("America/Santo_Domingo")
    hora = datetime.now(rd_tz).hour
    if 6 <= hora < 12:
        return "MAÃANA (usa 'buenos dÃ­as')"
    elif 12 <= hora < 18:
        return "TARDE (usa 'buenas tardes')"
    else:
        return "NOCHE (usa 'buenas noches')"


def generate_sales_response(wa_number: str, message: str, history: list, urgente: bool = False) -> str:
    """
    Genera la respuesta de Monserrat usando Claude Haiku.
    Si es urgente, usa el prompt de modo rÃ¡pido para capturar datos mÃ­nimos.
    """
    messages = history[-10:]  # Ãltimos 10 mensajes para contexto

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
    Detecta si el mensaje de Monserrat indica que se creÃ³ una reserva.
    """
    keywords = [
        "reserva fue registrada",
        "reserva ha sido registrada",
        "reserva confirmada",
        "su reserva",
        "supervisor la revisarÃ¡",
        "le asignarÃ¡ conductor",
        "EMV-"
    ]
    text_lower = response_text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _send_booking_qr(wa_number: str, nombre: str) -> None:
    """
    Genera QR de verificaciÃ³n de recogida y lo envÃ­a al pasajero.
    """
    try:
        booking_id = f"EMV-{wa_number[-4:]}-{secrets.token_hex(3).upper()}"
        token = secrets.token_urlsafe(16)

        qr_data = generate_pickup_qr(
            booking_id=booking_id,
            token=token,
            nombre=nombre or "Pasajero",
            fecha="PrÃ³ximo viaje"
        )

        qr_message = generate_qr_message({
            "pasajero": nombre or "Pasajero",
            "booking_id": booking_id,
            "fecha": "PrÃ³ximo viaje",
            "qr_url": qr_data.get("qr_url", "")
        })

        send_text(wa_number, qr_message)
        logger.info(f"QR enviado a {wa_number[:6]}*** â booking {booking_id}")

    except Exception as e:
        logger.error(f"Error enviando QR a {wa_number[:6]}***: {e}")


def qualify_lead(wa_number: str, conversation_summary: str) -> dict:
    """
    Califica un lead despuÃ©s de la conversaciÃ³n inicial.
    Retorna scoring y prÃ³xima acciÃ³n recomendada.
    """
    prompt = f"""
    Analiza esta conversaciÃ³n de WhatsApp con un prospecto de Emovils Airport:

    {conversation_summary}

    EvalÃºa:
    1. Nivel de interÃ©s (1-10)
    2. Â¿Tiene fecha de viaje confirmada? (sÃ­/no/no mencionÃ³)
    3. Â¿MencionÃ³ precio como objeciÃ³n? (sÃ­/no)
    4. Â¿Ya tiene alternativa de transporte? (sÃ­/no/no mencionÃ³)
    5. Siguiente acciÃ³n recomendada:
       - COTIZAR: tiene toda la info necesaria
       - PREGUNTAR_MAS: necesita mÃ¡s datos
       - SEGUIMIENTO: respondiÃ³ pero no dio info
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
    Genera y envÃ­a cotizaciÃ³n con precio y link de pago.
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

    # Enviar cotizaciÃ³n por WhatsApp
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
        update_lead_status(lead["id"], LeadStatus.COTIZADO, f"CotizaciÃ³n ${precio} enviada")

    logger.info(f"CotizaciÃ³n enviada a {wa_number[:6]}***: ${precio} â {origen} â {destino}")
    return {"status": "quoted", "precio_usd": precio, "payment_url": paypal_link}


def send_followup(wa_number: str, reason: str = "no_response") -> dict:
    """EnvÃ­a mensaje de seguimiento si el cliente no ha respondido."""
    followup_message = SCRIPTS["seguimiento_no_responde"]

    if reason == "not_paid":
        followup_message = """Hola, le escribo para confirmar si pudo completar la reserva.

El link de pago sigue activo. Si tiene alguna pregunta sobre el servicio o el proceso de pago, con gusto le ayudamos."""

    send_text(wa_number, followup_message)
    return {"status": "followup_sent", "reason": reason}


def handle_objection(wa_number: str, objection_type: str) -> str:
    """Genera respuesta personalizada para una objeciÃ³n especÃ­fica."""
    objections = {
        "precio_caro": """Entendemos que $25 puede parecer un poco mÃ¡s que otras opciones.

La diferencia es que con Emovils usted tiene:
â Precio confirmado ANTES de aterrizar
â Chofer identificado esperÃ¡ndole
â No hay negociaciÃ³n al salir cansado del vuelo
â Seguimiento por WhatsApp

Â¿Le parece si comparamos opciones y ve cuÃ¡l le conviene mÃ¡s?""",

        "tiene_conocido": """Perfecto, si ya tiene transporte coordinado, Â¡excelente!

Solo como plan B: si por alguna razÃ³n ese arreglo falla, estamos disponibles. Muchos clientes nos contactan en Ãºltimo momento y a veces no tenemos disponibilidad.

Â¿Le guarda el nÃºmero por si acaso?""",

        "inseguridad": """Nuestros choferes estÃ¡n identificados y registrados. Al confirmar su reserva, le enviamos:
- Foto y nombre del chofer
- Placa del vehÃ­culo
- WhatsApp directo del chofer

Empresa formal con historial de clientes. Â¿Le cuento mÃ¡s sobre cÃ³mo funciona el servicio?""",

        "lo_pienso": """Claro, tÃ³mese su tiempo.

Solo le digo que los cupos para fechas de fin de semana y temporada alta se llenan con anticipaciÃ³n. Si su viaje es pronto, le recomiendo reservar aunque sea con un depÃ³sito.

Â¿CuÃ¡ndo es su fecha de llegada exactamente?"""
    }

    response_text = objections.get(objection_type, SCRIPTS["bienvenida_cotizacion"])
    send_text(wa_number, response_text)
    return response_text
