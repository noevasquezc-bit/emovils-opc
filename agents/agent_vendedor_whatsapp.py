"""
Emovils — Agente Vendedor WhatsApp
Responsabilidad: Califica leads, cotiza, maneja objeciones,
da seguimiento y empuja al cierre. Canal principal de ventas.
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


def calcular_precio_rd(km: float) -> int:
    """
    Calcula precio en pesos dominicanos segun distancia en km.
    Minimo 3 km: RD$300
    3-10 km: +RD$50/km
    10-20 km: +RD$40/km
    >20 km: +RD$30/km
    """
    if km <= 3:
        return 300
    elif km <= 10:
        return int(300 + (km - 3) * 50)
    elif km <= 20:
        return int(650 + (km - 10) * 40)
    else:
        return int(1050 + (km - 20) * 30)


TABLA_PRECIOS = (
    "TABLA DE PRECIOS (Pesos Dominicanos):\n"
    "- Minimo hasta 3 km: RD$300\n"
    "- 5 km: RD$400\n"
    "- 7 km: RD$500\n"
    "- 10 km: RD$650\n"
    "- 12 km: RD$730\n"
    "- 15 km: RD$850\n"
    "- 18 km: RD$970\n"
    "- 20 km: RD$1,050\n"
    "- 25 km: RD$1,200\n"
    "- 30 km: RD$1,350\n"
    "- 40 km: RD$1,650\n"
    "- 50 km: RD$1,950\n"
    "\n"
    "Formula:\n"
    "  Hasta 3 km: RD$300 fijo\n"
    "  De 3 a 10 km: RD$300 + (km-3) x RD$50\n"
    "  De 10 a 20 km: RD$650 + (km-10) x RD$40\n"
    "  Mas de 20 km: RD$1,050 + (km-20) x RD$30\n"
)

SYSTEM_PROMPT = (
    "Eres el Agente de Reservas de Emovils, empresa de traslados ejecutivos privados "
    "en Republica Dominicana.\n\n"
    "OPERAMOS 24/7 — NUNCA digas que estas fuera de horario. Siempre disponible.\n\n"
    "SOBRE EMOVILS:\n"
    "- Somos una empresa de traslados ejecutivos privados\n"
    "- Cubrimos: aeropuerto, hoteles, empresas, eventos, viajes interurbanos y cualquier destino\n"
    "- NO somos solo aeropuerto — hacemos cualquier traslado en RD\n"
    "- Servicio privado, formal, con precio confirmado de antemano\n"
    "- Al presentarte di siempre: Emovils Traslados Ejecutivos\n\n"
    "TU FUNCION:\n"
    "- Atender a todo cliente que escriba, a cualquier hora\n"
    "- Recopilar los datos necesarios para la reserva\n"
    "- Calcular y confirmar el precio en pesos dominicanos\n"
    "- Informar al cliente que un supervisor revisara y asignara conductor\n\n"
    "FLUJO DE RESERVA:\n"
    "1. Saluda como Emovils Traslados Ejecutivos\n"
    "2. Pregunta punto de origen y destino\n"
    "3. IMPORTANTE: Pide que compartan su ubicacion de WhatsApp o la direccion exacta.\n"
    "   Di: Para calcular el precio exacto y facilitar que el conductor llegue, "
    "comparta su ubicacion de WhatsApp (clip > ubicacion) o indicanos la direccion exacta.\n"
    "4. Con origen y destino, calcula el precio segun la tabla de precios\n"
    "5. Confirma precio al cliente en pesos dominicanos\n"
    "6. Recoge datos completos de la reserva\n"
    "7. Informa: Su reserva fue registrada. Un supervisor la revisara y le asignara conductor en breve.\n\n"
    + TABLA_PRECIOS +
    "\n"
    "DATOS QUE DEBES RECOPILAR:\n"
    "1. Nombre completo del pasajero\n"
    "2. Fecha del servicio\n"
    "3. Hora estimada\n"
    "4. Punto de recogida (direccion o ubicacion WhatsApp)\n"
    "5. Destino final\n"
    "6. Cantidad de pasajeros\n"
    "7. Tipo de servicio (sencillo / ida y vuelta)\n"
    "8. Si es aeropuerto: numero de vuelo y aerolinea\n"
    "9. Forma de pago (Zelle, PayPal, tarjeta, efectivo)\n\n"
    "MANEJO DE OBJECIONES:\n"
    "- Es muy caro -> El precio es fijo y confirmado antes del servicio; sin sorpresas\n"
    "- Tengo a alguien conocido -> Respetar, pero preguntar si tienen plan B confirmado\n"
    "- Es seguro? -> Chofer identificado, empresa formal, seguimiento por WhatsApp\n\n"
    "TONO: Profesional, calido, seguro. Maximo 3 oraciones por mensaje.\n"
    "Responde siempre en espanol. Se conciso, directo y orientado al cierre."
)

CONVERSATION_HISTORY = {}


def process_incoming_message(webhook_payload: dict) -> dict:
    msg = parse_webhook_event(webhook_payload)
    if not msg:
        return {"status": "no_message"}

    wa_number = msg["from"]
    message_text = msg["text"]
    contact_name = msg.get("contact_name", "")

    logger.info("Mensaje recibido de %s***: %s", wa_number[:6], message_text[:50])

    try:
        existing_lead = get_lead_by_whatsapp(wa_number)
        if not existing_lead:
            create_lead(
                whatsapp=wa_number,
                nombre=contact_name,
                canal_origen="whatsapp_inbound",
                producto="ejecutivo"
            )
    except Exception as e:
        logger.warning("Airtable error (non-fatal): %s", e)

    if wa_number not in CONVERSATION_HISTORY:
        CONVERSATION_HISTORY[wa_number] = []

    CONVERSATION_HISTORY[wa_number].append({"role": "user", "content": message_text})

    es_urgente = detectar_urgencia(message_text)

    response_text = generate_sales_response(
        wa_number=wa_number,
        message=message_text,
        history=CONVERSATION_HISTORY[wa_number],
        urgente=es_urgente
    )

    CONVERSATION_HISTORY[wa_number].append({"role": "assistant", "content": response_text})
    send_text(wa_number, response_text)

    return {
        "status": "responded",
        "to": wa_number,
        "response_preview": response_text[:100]
    }


def generate_sales_response(wa_number: str, message: str, history: list, urgente: bool = False) -> str:
    messages = history[-10:]
    system = SYSTEM_PROMPT
    if urgente:
        system = SYSTEM_PROMPT + "\n\n" + PROMPT_MODO_URGENTE
        logger.info("Modo URGENTE activado para %s***", wa_number[:6])

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=system,
        messages=messages
    )
    return response.content[0].text


def qualify_lead(wa_number: str, conversation_summary: str) -> dict:
    prompt = (
        "Analiza esta conversacion de WhatsApp con un prospecto de Emovils:\n\n"
        + conversation_summary
        + "\n\nEvalua:\n"
        "1. Nivel de interes (1-10)\n"
        "2. Tiene fecha de viaje confirmada? (si/no/no menciono)\n"
        "3. Menciono precio como objecion? (si/no)\n"
        "4. Siguiente accion: COTIZAR / PREGUNTAR_MAS / SEGUIMIENTO / PERDIDO\n\n"
        "Responde en formato JSON."
    )
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
    pasajeros: int = 1,
    km: float = 0
) -> dict:
    if km > 0:
        precio_rd = calcular_precio_rd(km)
    else:
        try:
            price_info = estimate_price(origen, destino, pasajeros)
            km_estimado = price_info.get("distance_km", 10)
            precio_rd = calcular_precio_rd(km_estimado)
        except Exception:
            precio_rd = 650

    message = (
        "Cotizacion Emovils Traslados Ejecutivos\n\n"
        "Pasajero: " + nombre + "\n"
        "Fecha: " + fecha + " a las " + hora + "\n"
        "Ruta: " + origen + " -> " + destino + "\n"
        "Precio: RD$" + "{:,}".format(precio_rd) + "\n\n"
        "Para confirmar su reserva, indicarnos su forma de pago preferida.\n"
        "Formas de pago: Zelle, PayPal, tarjeta o efectivo."
    )
    send_text(wa_number, message)

    try:
        lead = get_lead_by_whatsapp(wa_number)
        if lead:
            update_lead_status(lead["id"], LeadStatus.COTIZADO, "Cotizacion RD$" + str(precio_rd) + " enviada")
    except Exception as e:
        logger.warning("Airtable update error (non-fatal): %s", e)

    logger.info("Cotizacion enviada a %s***: RD$%s — %s -> %s", wa_number[:6], precio_rd, origen, destino)
    return {"status": "quoted", "precio_rd": precio_rd}


def send_followup(wa_number: str, reason: str = "no_response") -> dict:
    followup_message = SCRIPTS["seguimiento_no_responde"]
    if reason == "not_paid":
        followup_message = (
            "Hola, le escribo para confirmar si pudo completar la reserva.\n\n"
            "Si tiene alguna pregunta sobre el servicio o la forma de pago, con gusto le ayudamos."
        )
    send_text(wa_number, followup_message)
    return {"status": "followup_sent", "reason": reason}


def handle_objection(wa_number: str, objection_type: str) -> str:
    objections = {
        "precio_caro": (
            "Entendemos la consulta sobre el precio.\n\n"
            "Con Emovils usted tiene precio confirmado antes del servicio, "
            "chofer identificado esperandole y sin sorpresas.\n"
            "El precio que cotizamos es fijo y final."
        ),
        "tiene_conocido": (
            "Perfecto, si ya tiene transporte coordinado, excelente.\n\n"
            "Como plan B: si por alguna razon ese arreglo falla, estamos disponibles 24/7."
        ),
        "inseguridad": (
            "Nuestros choferes estan identificados. Al confirmar su reserva enviamos "
            "nombre del chofer, placa del vehiculo y WhatsApp directo del chofer."
        ),
        "lo_pienso": (
            "Claro, tomese su tiempo.\n\n"
            "Cuando sea necesario, escribanos y le coordinamos de inmediato."
        )
    }
    response_text = objections.get(objection_type, SCRIPTS["bienvenida_cotizacion"])
    send_text(wa_number, response_text)
    return response_text
