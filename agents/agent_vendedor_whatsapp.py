"""
Emovils OPC — Agente 3: Vendedor WhatsApp
Responsabilidad: Califica leads, cotiza, maneja objeciones,
da seguimiento y empuja al cierre. Canal principal de ventas.

Flujo: WhatsApp → preguntas básicas → cotización → confirmación → pago → reserva
"""
import anthropic
import logging
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from lib.airtable_api import (
    create_lead, update_lead_status, get_lead_by_whatsapp,
    create_reserva, LeadStatus
)
from lib.whatsapp_api import send_text, send_quote, SCRIPTS, parse_webhook_event
from lib.paypal_api import get_payment_link
from lib.google_maps import estimate_price

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Eres el Vendedor WhatsApp de Emovils OPC, empresa de movilidad privada en República Dominicana.

TU FUNCIÓN:
- Atender leads que llegan por WhatsApp
- Calificar si son candidatos reales para el servicio
- Obtener la información necesaria para cotizar
- Enviar cotización con precio exacto y link de pago
- Manejar objeciones y cerrar la reserva
- Dar seguimiento si no responden

PRODUCTO PRINCIPAL: Emovils Airport
- Servicio: Traslado privado desde/hacia AILA/SDQ (Santo Domingo)
- Precio base: USD $25 (sencillo), $45 (ida y vuelta)
- Incluye: vehículo confirmado, chofer identificado, seguimiento WhatsApp
- Promesa: "Precio confirmado antes de su llegada. Sin sorpresas."

PREGUNTAS QUE NECESITAS (para cotizar):
BÁSICAS (todos los servicios):
1. Nombre completo del pasajero
2. Fecha de llegada/salida
3. Hora estimada
4. Punto de recogida (aeropuerto u otro)
5. Destino final
6. Cantidad de pasajeros
7. Tipo de servicio (ida / regreso / espera)
8. WhatsApp de contacto
9. Forma de pago (Zelle, tarjeta, PayPal, efectivo)

ADICIONALES para Airport:
- Número de vuelo
- Aerolínea
- Cantidad de maletas

MANEJO DE OBJECIONES:
- "Es muy caro" → Comparar con el costo/estrés de improvisar; recordar que precio es confirmado
- "Tengo a alguien conocido" → Respetar, pero preguntar si tienen plan B confirmado
- "¿Es seguro?" → Chofer identificado, empresa formal, seguimiento por WhatsApp
- "Lo pienso" → Recordar que los cupos se llenan; ofrecer reservar sin pago total ahora

TONO:
- Profesional pero cálido, como una empresa seria
- Nunca presiones agresivamente
- Sé específico, no genérico
- Responde rápido (dentro de 15 minutos)

Responde siempre en español. Sé conciso, directo y orientado al cierre."""

CONVERSATION_HISTORY = {}  # En producción, usar Redis o Airtable


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

    CONVERSATION_HISTORY[wa_number].append({
        "role": "user",
        "content": message_text
    })

    # Generar respuesta con el agente
    response_text = generate_sales_response(
        wa_number=wa_number,
        message=message_text,
        history=CONVERSATION_HISTORY[wa_number]
    )

    CONVERSATION_HISTORY[wa_number].append({
        "role": "assistant",
        "content": response_text
    })

    # Enviar respuesta
    send_text(wa_number, response_text)

    return {
        "status": "responded",
        "to": wa_number,
        "response_preview": response_text[:100]
    }


def generate_sales_response(wa_number: str, message: str, history: list) -> str:
    """
    Genera la respuesta del vendedor usando Claude.
    Mantiene el historial de la conversación.
    """
    messages = history[-10:]  # Últimos 10 mensajes para contexto

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    return response.content[0].text


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
    Genera y envía cotización con precio y link de pago de Stripe.
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
