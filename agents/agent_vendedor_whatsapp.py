"""
Emovils OPC - Agente 3: Vendedor WhatsApp
Responsabilidad: Califica leads, cotiza, maneja objeciones,
da seguimiento y empuja al cierre. Canal principal de ventas.
Flujo: WhatsApp -> preguntas basicas -> cotizacion -> confirmacion -> pago -> reserva
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
from workflows.wf_reserva_urgente import detectar_urgencia, PROMPT_MODO_URGENTE
from workflows.wf_followup_sequence import schedule_followup_sequence

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Eres el Agente de Reservas de Emovils OPC, empresa de movilidad privada premium en Republica Dominicana.

OPERAMOS 24/7 - NUNCA digas que estas fuera de horario. Siempre disponible.

TU FUNCION:
- Atender a todo cliente que escriba, a cualquier hora
- Recopilar TODOS los datos necesarios para la reserva
- Confirmar precio y crear la reserva en el sistema
- Informar al cliente que un supervisor revisara y asignara conductor
- Manejar objeciones con confianza y cerrar

FLUJO DE RESERVA (sigue este orden):
1. Saluda calurosamente y pregunta que necesitan
2. Recopila los datos de la reserva
3. Confirma precio al cliente
4. Crea la reserva -> sistema notifica al supervisor
5. Informa: "Su reserva fue registrada. Un supervisor la revisara y le asignara conductor en breve."

PRODUCTO PRINCIPAL: Emovils Airport
- Servicio: Traslado privado desde/hacia AILA/SDQ (Santo Domingo)
- Precio base: USD $25 (sencillo), $45 (ida y vuelta)
- Incluye: vehiculo confirmado, chofer identificado, seguimiento WhatsApp
- Promesa: "Precio confirmado antes de su llegada. Sin sorpresas."

DATOS QUE DEBES RECOPILAR:
1. Nombre completo del pasajero
2. Fecha de llegada/salida
3. Hora estimada
4. Punto de recogida
5. Destino final
6. Cantidad de pasajeros
7. Tipo de servicio (ida / regreso / ida y vuelta)
8. Numero de vuelo y aerolinea
9. Cantidad de maletas
10. Forma de pago preferida (Zelle, PayPal, tarjeta, efectivo)

MANEJO DE OBJECIONES:
- "Es muy caro" -> Comparar con el costo/estres de improvisar; precio confirmado, sin sorpresas
- "Tengo a alguien conocido" -> Respetar, pero preguntar si tienen plan B confirmado
- "Es seguro?" -> Chofer identificado, empresa formal, seguimiento por WhatsApp
- "Lo pienso" -> Los cupos se llenan; la reserva no requiere pago total ahora

TONO: Profesional, calido, seguro. Maximo 3 oraciones por mensaje. Sin emojis excesivos.

Responde siempre en espanol. Se conciso, directo y orientado al cierre."""

CONVERSATION_HISTORY = {}


def process_incoming_message(webhook_payload: dict) -> dict:
    """
    Procesa un mensaje entrante de WhatsApp y genera respuesta.
    """
    msg = parse_webhook_event(webhook_payload)
    if not msg:
        return {"status": "no_message"}

    wa_number = msg["from"]
    message_text = msg["text"]
    contact_name = msg.get("contact_name", "")

    logger.info("Mensaje recibido de " + wa_number[:6] + "***: " + message_text[:50])

    # Verificar/crear lead en Airtable (no-fatal si falla)
    try:
        existing_lead = get_lead_by_whatsapp(wa_number)
        if not existing_lead:
            create_lead(
                whatsapp=wa_number,
                nombre=contact_name,
                canal_origen="whatsapp_inbound",
                producto="airport"
            )
    except Exception as e:
        logger.warning("Airtable error (non-fatal): " + str(e))

    # Historial de conversacion
    if wa_number not in CONVERSATION_HISTORY:
        CONVERSATION_HISTORY[wa_number] = []

    CONVERSATION_HISTORY[wa_number].append({"role": "user", "content": message_text})

    # Detectar urgencia
    es_urgente = detectar_urgencia(message_text)

    # Generar respuesta con Claude
    response_text = generate_sales_response(
        wa_number=wa_number,
        message=message_text,
        history=CONVERSATION_HISTORY[wa_number],
        urgente=es_urgente
    )

    CONVERSATION_HISTORY[wa_number].append({"role": "assistant", "content": response_text})

    # Enviar respuesta
    send_text(wa_number, response_text)

    return {
        "status": "responded",
        "to": wa_number,
        "response_preview": response_text[:100]
    }


def generate_sales_response(wa_number: str, message: str, history: list, urgente: bool = False) -> str:
    """
    Genera la respuesta del vendedor usando Claude.
    """
    messages = history[-10:]
    system = SYSTEM_PROMPT
    if urgente:
        system = SYSTEM_PROMPT + "\n\n" + PROMPT_MODO_URGENTE
        logger.info("Modo URGENTE activado para " + wa_number[:6] + "***")

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=system,
        messages=messages
    )
    return response.content[0].text


def qualify_lead(wa_number: str, conversation_summary: str) -> dict:
    """Califica un lead despues de la conversacion inicial."""
    prompt = "Analiza esta conversacion de WhatsApp con un prospecto de Emovils Airport:\n\n" + conversation_summary + "\n\nEvalua nivel de interes (1-10), fecha confirmada, precio como objecion, alternativa de transporte, y proxima accion. Responde en JSON."
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return {"qualification": response.content[0].text}


def send_quotation(wa_number: str, nombre: str, fecha: str, hora: str, origen: str, destino: str, pasajeros: int = 1) -> dict:
    """Genera y envia cotizacion con precio y link de pago."""
    price_info = estimate_price(origen, destino, pasajeros)
    precio = price_info.get("price_usd", 25.0)
    booking_id = "EMV-" + wa_number[-4:] + "-" + fecha.replace("-", "")
    paypal_link = get_payment_link(product_key="airport_sencillo", customer_name=nombre, booking_id=booking_id, custom_price=precio)
    send_quote(to=wa_number, nombre=nombre, fecha=fecha, hora=hora, origen=origen, destino=destino, precio_usd=precio, paypal_link=paypal_link)
    try:
        lead = get_lead_by_whatsapp(wa_number)
        if lead:
            update_lead_status(lead["id"], LeadStatus.COTIZADO, "Cotizacion $" + str(precio) + " enviada")
    except Exception as e:
        logger.warning("Airtable update error: " + str(e))
    logger.info("Cotizacion enviada a " + wa_number[:6] + "***: $" + str(precio))
    return {"status": "quoted", "precio_usd": precio, "payment_url": paypal_link}


def send_followup(wa_number: str, reason: str = "no_response") -> dict:
    """Envia mensaje de seguimiento."""
    followup_message = SCRIPTS["seguimiento_no_responde"]
    if reason == "not_paid":
        followup_message = "Hola, le escribo para confirmar si pudo completar la reserva. El link de pago sigue activo."
    send_text(wa_number, followup_message)
    return {"status": "followup_sent", "reason": reason}


def handle_objection(wa_number: str, objection_type: str) -> str:
    """Genera respuesta para una objecion especifica."""
    objections = {
        "precio_caro": "Entendemos que $25 puede parecer un poco mas que otras opciones. Con Emovils tiene precio confirmado ANTES de aterrizar, chofer identificado esperandole, y seguimiento por WhatsApp. Sin negociacion al salir cansado del vuelo.",
        "tiene_conocido": "Perfecto, si ya tiene transporte coordinado, excelente. Solo como plan B: si ese arreglo falla, estamos disponibles. Muchos clientes nos contactan en ultimo momento. Le guardamos el numero?",
        "inseguridad": "Nuestros choferes estan identificados y registrados. Al confirmar su reserva, le enviamos foto y nombre del chofer, placa del vehiculo y WhatsApp directo del chofer.",
        "lo_pienso": "Claro, tomese su tiempo. Solo le digo que los cupos para fechas de fin de semana se llenan con anticipacion. La reserva no requiere pago total ahora. Cuando es su fecha de llegada exactamente?"
    }
    response_text = objections.get(objection_type, SCRIPTS["bienvenida_cotizacion"])
    send_text(wa_number, response_text)
    return response_text
