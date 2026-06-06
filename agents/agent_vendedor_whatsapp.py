"""
Emovils — Agente Vendedor WhatsApp v2
Usa el motor de cotizacion (pricing_engine) para calcular precios
de forma automatica, alineada al mercado y con escalamiento correcto.
"""
import anthropic
import logging
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from lib.airtable_api import (
    create_lead, update_lead_status, get_lead_by_whatsapp,
    create_reserva, LeadStatus
)
from lib.whatsapp_api import send_text, SCRIPTS, parse_webhook_event
from lib.pricing_engine import (
    calculate_fare, FareInput, PRICING_RULES_FOR_AGENT,
    get_waiting_fee_table_text, PRICING_CONFIG
)
from workflows.wf_reserva_urgente import detectar_urgencia, PROMPT_MODO_URGENTE
from workflows.wf_followup_sequence import schedule_followup_sequence

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


SYSTEM_PROMPT = (
    "Eres el Agente de Reservas de Emovils, empresa de traslados ejecutivos privados "
    "en Republica Dominicana. Operamos bajo la marca Emovils / eTransfers.\n\n"
    "OPERAMOS 24/7. NUNCA digas que estas fuera de horario.\n\n"

    "SOBRE EMOVILS:\n"
    "- Traslados ejecutivos privados — aeropuerto, hoteles, empresas, eventos, interurbanos\n"
    "- Dos tipos de vehiculo: sedan (hasta 4 pasajeros) y van (hasta 7 pasajeros)\n"
    "- Precio confirmado antes del servicio. Sin sorpresas.\n"
    "- Al presentarte di siempre: Emovils Traslados Ejecutivos\n\n"

    "FLUJO DE ATENCION:\n"
    "1. Cuando alguien escriba POR PRIMERA VEZ, saludalo calurosamente y pregunta en que puedes ayudarle.\n"
       "   Ejemplo: Hola, bienvenido a Emovils Traslados Ejecutivos. Con gusto le ayudo. En que le puedo apoyar?\n"
    "2. Recopila: punto de recogida, destino y cantidad de pasajeros\n"
    "3. Pide ubicacion de WhatsApp o direccion exacta para calcular distancia\n"
    "4. Si el servicio es normal (urbano o aeropuerto), cotiza segun las reglas de precios\n"
    "5. Si el servicio requiere supervisor, escala sin dar precio\n"
    "6. Confirma la reserva solo cuando tengas: nombre, telefono, origen, destino, fecha y hora\n"
    "7. Informa: Su reserva fue registrada. Un supervisor la revisara y le asignara conductor en breve.\n\n"

    + PRICING_RULES_FOR_AGENT +

    "\n\nREGLAS CRITICAS PARA COTIZAR:\n"
    "1. NUNCA muestres la formula ni el calculo al cliente. Solo el precio final.\n"
    "   MAL: RD$300 + (150km x RD$30) = RD$5,550\n"
    "   BIEN: El precio del servicio es RD$5,550\n"
    "2. SIEMPRE pregunta cuantos pasajeros ANTES de recomendar vehiculo.\n"
    "   Si no sabes cuantos son, no puedes recomendar sedan ni van.\n"
    "3. Sedan: MAXIMO 4 pasajeros. Van: MAXIMO 7 pasajeros.\n"
    "   Nunca ofrezcas sedan sin confirmar que son 4 o menos pasajeros.\n"
    "4. El precio se presenta limpio, sin formulas ni desglose.\n\n"
    "EJEMPLOS DE RESPUESTA CORRECTA:\n"
    "Sin saber pasajeros: Perfecto. Para calcularle el precio, cuantos pasajeros viajaran?\n"
    "Sedan 1-4 pax: Le recomiendo sedan (hasta 4 pasajeros). Precio del servicio: RD$700. "
    "Incluye recogida, traslado directo y hasta 15 minutos de espera sin cargo adicional. "
    "Desea que le reserve ese servicio?\n"
    "Van 5-7 pax: Le recomiendo van (hasta 7 pasajeros). Precio del servicio: RD$1,000. "
    "Incluye traslado directo con espacio para el grupo y equipaje.\n"
    "Supervisor: Para ese tipo de servicio necesitamos validar disponibilidad. "
    "Lo paso con un supervisor de Emovils.\n\n"

    "TONO: Profesional, calido, seguro. Maximo 3-4 oraciones por mensaje.\n"
    "Responde siempre en espanol. Se conciso, directo y orientado al cierre.\n\n"

    "PROHIBIDO:\n"
    "- Revelar comision interna de Emovils o pago al conductor\n"
    "- Cotizar por debajo de RD$300\n"
    "- Inventar precios si faltan datos\n"
    "- Prometer disponibilidad sin validar\n"
    "- Dar descuentos mayores al 10% sin autorizacion\n"
    "- Confirmar reserva sin nombre, telefono, origen, destino, fecha y hora"
)

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

    logger.info("Mensaje recibido de %s***: %s", wa_number[:6], message_text[:50])

    # Airtable (no-fatal)
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

    # Historial de conversacion
    if wa_number not in CONVERSATION_HISTORY:
        CONVERSATION_HISTORY[wa_number] = []

    CONVERSATION_HISTORY[wa_number].append({"role": "user", "content": message_text})

    # Urgencia
    es_urgente = detectar_urgencia(message_text)

    # Generar respuesta
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
    """
    Genera la respuesta usando Claude con el SYSTEM_PROMPT actualizado.
    """
    messages = history[-10:]
    system = SYSTEM_PROMPT
    if urgente:
        system = SYSTEM_PROMPT + "\n\n" + PROMPT_MODO_URGENTE
        logger.info("Modo URGENTE activado para %s***", wa_number[:6])

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=system,
        messages=messages
    )
    return response.content[0].text


def quote_fare(
    wa_number: str,
    origin: str,
    destination: str,
    passengers: int,
    distance_km: float = 0,
    time_minutes: float = 0,
    service_type: str = "urban",
    vehicle_type: str = "auto",
    trip_time: str = "",
    extra_stops: int = 0,
    waiting_minutes: float = 0,
    is_round_trip: bool = False,
    is_airport: bool = False,
    airport_zone: str = ""
) -> dict:
    """
    Calcula y envia cotizacion usando el motor de precios.
    """
    inp = FareInput(
        origin=origin,
        destination=destination,
        distance_km=distance_km,
        estimated_time_minutes=time_minutes,
        passengers=passengers,
        service_type=service_type,
        vehicle_type=vehicle_type,
        time=trip_time,
        extra_stops=extra_stops,
        waiting_minutes=waiting_minutes,
        is_round_trip=is_round_trip,
        is_airport=is_airport,
        airport_zone=airport_zone
    )

    result = calculate_fare(inp)

    send_text(wa_number, result.client_message)

    if result.requires_supervisor:
        logger.info("Escalamiento a supervisor para %s***: %s", wa_number[:6], result.supervisor_reason)
    else:
        logger.info(
            "Cotizacion enviada a %s***: RD$%s (%s) %s -> %s",
            wa_number[:6], result.final_price_dop, result.recommended_vehicle, origin, destination
        )

    return {
        "status": "supervisor_required" if result.requires_supervisor else "quoted",
        "requires_supervisor": result.requires_supervisor,
        "supervisor_reason": result.supervisor_reason,
        "vehicle": result.recommended_vehicle,
        "price_dop": result.final_price_dop,
        "price_usd": result.final_price_usd
    }


def qualify_lead(wa_number: str, conversation_summary: str) -> dict:
    """Califica un lead despues de la conversacion inicial."""
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


def send_followup(wa_number: str, reason: str = "no_response") -> dict:
    """Envia mensaje de seguimiento si el cliente no ha respondido."""
    msg = SCRIPTS["seguimiento_no_responde"]
    if reason == "not_paid":
        msg = (
            "Hola, le escribo para confirmar si pudo completar la reserva.\n\n"
            "Si tiene alguna pregunta sobre el servicio o la forma de pago, con gusto le ayudamos."
        )
    send_text(wa_number, msg)
    return {"status": "followup_sent", "reason": reason}


# Alias de compatibilidad con main.py
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
    """Alias para compatibilidad con main.py — usa quote_fare internamente."""
    return quote_fare(
        wa_number=wa_number,
        origin=origen,
        destination=destino,
        passengers=pasajeros,
        distance_km=km,
        time_minutes=km * 3 if km > 0 else 0
    )


def handle_objection(wa_number: str, objection_type: str) -> str:
    """Maneja objeciones del cliente."""
    objections = {
        "precio_caro": (
            "Entendemos la consulta sobre el precio.\n\n"
            "Con Emovils usted tiene precio confirmado antes del servicio, "
            "chofer identificado y sin sorpresas. El precio es fijo y final."
        ),
        "tiene_conocido": (
            "Perfecto, si ya tiene transporte coordinado, excelente.\n\n"
            "Como plan B: si ese arreglo falla, estamos disponibles 24/7."
        ),
        "inseguridad": (
            "Nuestros choferes estan identificados. Al confirmar, enviamos "
            "nombre del chofer, placa del vehiculo y WhatsApp directo."
        ),
        "lo_pienso": (
            "Claro, tomese su tiempo.\n\n"
            "Cuando sea necesario, escribanos y le coordinamos de inmediato."
        )
    }
    response_text = objections.get(objection_type, SCRIPTS["bienvenida_cotizacion"])
    send_text(wa_number, response_text)
    return response_text
