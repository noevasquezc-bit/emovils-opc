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
from lib.database import crear_reserva as db_crear_reserva, RESERVAS as DB_RESERVAS
from lib.google_maps import calculate_route_price

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


SYSTEM_PROMPT = (
    "Eres el Agente de Reservas de Emovils, empresa de traslados ejecutivos privados "
    "en Republica Dominicana.\n\n"

    "OPERAMOS 24/7. NUNCA digas que estas fuera de horario.\n\n"

    "SOBRE EMOVILS:\n"
    "- Traslados ejecutivos privados: aeropuerto, hoteles, empresas, eventos, interurbanos\n"
    "- Sedan: hasta 4 pasajeros. Van: hasta 7 pasajeros.\n"
    "- Precio confirmado antes del servicio. Sin sorpresas.\n"
    "- Presentate siempre como: Emovils Traslados Ejecutivos\n\n"

    "FLUJO DE ATENCION (sigue este orden EXACTO):\n"
    "1. Saluda y pregunta en que puedes apoyar\n"
    "   Ejemplo: Hola, bienvenido a Emovils Traslados Ejecutivos. Con gusto le ayudo. En que le puedo apoyar?\n"
    "2. Pregunta origen y destino\n"
    "3. Pregunta cuantos pasajeros\n"
    "4. Pregunta FECHA Y HORA del servicio (OBLIGATORIO antes de cotizar — el precio cambia de noche)\n"
    "5. Con origen, destino, pasajeros, fecha Y hora confirmados: presenta el precio LIMPIO\n"
    "6. Pregunta forma de pago: efectivo, tarjeta o en linea\n"
    "7. Solicita nombre completo del cliente\n"
    "8. Solicita telefono con esta explicacion exacta:\n"
    "   Por favor indicame su numero de telefono. Lo solicitamos para que el chofer pueda "
    "comunicarse con usted al momento de la recogida y confirmar cualquier detalle del servicio.\n"
    "9. Confirma todos los datos y registra la reserva\n"
    "10. Envia mensaje de confirmacion segun forma de pago\n\n"

    "REGLAS DE VEHICULO (CRITICO):\n"
    "- NUNCA recomiendes vehiculo sin saber cuantos pasajeros son\n"
    "- 1 a 4 pasajeros: sedan\n"
    "- 5 a 7 pasajeros: van\n"
    "- Mas de 7: escalar a supervisor\n"
    "- Si no sabes cuantos son: pregunta primero\n\n"

    "REGLAS DE COTIZACION (CRITICO):\n"
    "- NUNCA muestres formulas ni calculos al cliente. Solo el precio final\n"
    "  MAL: RD$1,050 + (150 km x RD$30) = RD$5,550\n"
    "  BIEN: El precio del servicio es RD$5,550\n"
    "- Precio minimo siempre: RD$300\n\n"

    + PRICING_RULES_FOR_AGENT +

    "\nFORMA DE PAGO — pregunta obligatoria despues de presentar el precio:\n"
    "Pregunta: Como desea realizar el pago: efectivo, con tarjeta o en linea?\n"
    "- Efectivo: reserva confirmada, pago al momento del servicio\n"
    "- Tarjeta: reserva confirmada, pago por terminal o link\n"
    "- En linea: reserva pendiente hasta confirmar pago, luego se genera el QR\n\n"

    "DATOS OBLIGATORIOS — no confirmes sin tener todos estos:\n"
    "nombre, telefono, origen, destino, fecha, hora, pasajeros, vehiculo, precio, forma de pago\n"
    "Si falta alguno, pide ese dato antes de confirmar.\n\n"

    "MENSAJES DE CONFIRMACION:\n"

    "Si pago es efectivo o tarjeta, enviar:\n"
    "Su reserva ha sido confirmada.\n"
    "Vehiculo: [Sedan/Van] | Fecha: [fecha] | Hora: [hora]\n"
    "Recogida: [origen] | Destino: [destino] | Pasajeros: [n]\n"
    "Precio: RD$[monto] | Pago: [efectivo/tarjeta]\n"
    "Le enviaremos su QR de servicio. Al llegar el vehiculo, escanee el QR de la puerta. "
    "Solo aborde si aparece check verde de Emovils.\n\n"

    "Si pago es en linea y no esta confirmado:\n"
    "Su solicitud esta registrada. Complete el pago por el enlace que le enviaremos. "
    "Una vez confirmado, recibira su QR de servicio.\n\n"

    "ACCESO AL HISTORIAL (CRITICO):\n"
    "- TIENES ACCESO COMPLETO a todos los mensajes de esta conversacion.\n"
    "- Los mensajes anteriores aparecen en el historial que ves. LEELOS antes de pedir datos.\n"
    "- Las ubicaciones compartidas por WhatsApp aparecen como:\n"
    "  [UBICACION COMPARTIDA] Lat: X, Lon: Y | Direccion: Z\n"
    "- NUNCA digas: no tengo acceso a mensajes previos / no tengo acceso a ubicaciones\n"
    "- Si el cliente ya dio un dato (ubicacion, nombre, destino), extraelo del historial.\n"
    "- Antes de pedir un dato nuevamente, verifica si ya aparece en mensajes anteriores.\n\n"

    "CRITICO — PRECIO AL CLIENTE:\n"
    "Cuando des el precio escribe UNICAMENTE el monto. Ejemplo correcto: El precio es RD$5,550\n"
    "JAMAS escribas calculos, formulas, multiplicaciones ni el desglose. NUNCA algo como:\n"
    "RD$1,050 + (150 km x RD$30) = RD$5,550  <-- ESTO ESTA PROHIBIDO\n"
    "Solo el resultado final. Sin ecuaciones. Sin parentesis. Sin el signo x o =.\n\n"
    "PROHIBIDO:\n"
    "- Mostrar formulas, calculos, multiplicaciones o desgloses de precio al cliente\n"
    "- Dar precio sin tener fecha Y hora (el recargo nocturno depende de la hora)\n"
    "- Cotizar en dolares (USD) — SIEMPRE en pesos dominicanos (RD$)\n"
    "- Revelar comision o margen interno de Emovils\n"
    "- Cotizar por debajo de RD$300\n"
    "- Confirmar sin todos los datos obligatorios\n"
    "- Recomendar sedan sin confirmar 4 o menos pasajeros\n"
    "- Pedir telefono sin explicar que es para el chofer\n"
    "- Mencionar marcas que no sean Emovils\n\n"

    "TONO: Profesional, calido, seguro. Maximo 4 oraciones por mensaje. Siempre en espanol."
)

CONVERSATION_HISTORY = {}
LOCATION_CACHE = {}  # wa_number -> {'lat': float, 'lon': float}
BOOKING_CREATED = {}  # wa_number -> booking_id (para no crear duplicados)


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

    # Cache location if client shared WhatsApp location
    if msg.get("type") == "location":
        lat = msg.get("latitude")
        lon = msg.get("longitude")
        if lat and lon:
            LOCATION_CACHE[wa_number] = {"lat": float(lat), "lon": float(lon)}
            logger.info("Ubicacion guardada para %s***: %.4f, %.4f", wa_number[:6], lat, lon)

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

    # Intentar crear reserva automaticamente si el agente acaba de confirmar
    _intentar_crear_reserva(wa_number, response_text, CONVERSATION_HISTORY[wa_number])

    return {
        "status": "responded",
        "to": wa_number,
        "response_preview": response_text[:100]
    }




# ─────────────────────────────────────────────
# CREACION AUTOMATICA DE RESERVA
# ─────────────────────────────────────────────

EXTRACTION_PROMPT = """Analiza esta conversacion de WhatsApp de Emovils y extrae los datos si estan TODOS presentes.

Responde UNICAMENTE con JSON valido. Si falta algun dato obligatorio responde: {"completo": false}

Datos obligatorios: nombre, telefono, origen, destino, fecha, hora, pasajeros, precio, forma_pago, confirmado

{
  "completo": true/false,
  "confirmado": true/false,  // el cliente dijo si/confirmo/de acuerdo/correcto/ok
  "nombre": "...",
  "telefono": "...",
  "whatsapp": "...",  // numero de whatsapp del cliente
  "origen": "...",
  "destino": "...",
  "fecha": "...",
  "hora": "...",
  "pasajeros": 1,
  "vehiculo": "sedan|van",
  "precio": 1500,  // numero sin simbolos
  "forma_pago": "efectivo|tarjeta|en linea"
}"""


def _intentar_crear_reserva(wa_number: str, last_response: str, history: list) -> None:
    """
    Despues de cada respuesta del agente, verifica si el cliente confirmo la reserva.
    Si si, extrae los datos y crea la reserva automaticamente.
    Solo se ejecuta una vez por conversacion (evita duplicados).
    """
    import os
    import json as json_mod

    # No crear si ya existe reserva para este numero
    if wa_number in BOOKING_CREATED:
        return

    # Solo intentar si la respuesta del agente contiene palabras de confirmacion
    confirmacion_words = ["reserva ha sido confirmada", "reserva fue confirmada",
                          "reserva confirmada", "su qr", "le enviaremos su qr",
                          "solicitud esta registrada", "confirmacion de su reserva"]
    if not any(w in last_response.lower() for w in confirmacion_words):
        return

    # Extraer datos estructurados de la conversacion con Claude
    try:
        base_url = os.getenv("BASE_URL", "https://emovils-opc-production.up.railway.app")
        msgs = history[-20:]
        conv_text = "\n".join(
            f"{'Cliente' if m['role']=='user' else 'Emovils'}: {m['content']}"
            for m in msgs
            if isinstance(m.get("content"), str)
        )

        extraction = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            system=EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": conv_text + f"\n\nNumero WhatsApp del cliente: {wa_number}"}]
        )
        raw = extraction.content[0].text.strip()

        # Limpiar markdown si Claude lo envuelve en ```json
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        datos = json_mod.loads(raw.strip())

        if not datos.get("completo") or not datos.get("confirmado"):
            return

        # Asegurar whatsapp del cliente
        datos["whatsapp"] = datos.get("whatsapp") or wa_number

        # Crear reserva en la base de datos
        reserva = db_crear_reserva(datos)
        bid = reserva["booking_id"]
        token = reserva["token_cliente"]
        link = f"{base_url}/b/{bid}?t={token}"

        BOOKING_CREATED[wa_number] = bid
        logger.info("Reserva creada automaticamente: %s para %s", bid, wa_number[:6])

        # Enviar link QR al cliente
        msg_qr = (
            f"Su reserva ha quedado registrada.\n"
            f"Numero de reserva: {bid}\n\n"
            f"Aqui puede ver su QR de embarque y los detalles:\n{link}\n\n"
            f"Cuando llegue el vehiculo, escanee el QR en la puerta para verificar "
            f"que es su conductor asignado."
        )
        send_text(wa_number, msg_qr)

    except Exception as e:
        logger.warning("Error creando reserva automatica: %s", e)


def _try_calculate_price(wa_number: str, history: list) -> str:
    """
    Intenta calcular el precio con Google Maps si tenemos ubicacion + destino + hora.
    Retorna el [PRECIO_CALCULADO] string para inyectar en el system prompt, o "" si no hay suficiente info.
    """
    location = LOCATION_CACHE.get(wa_number)
    if not location:
        return ""

    # Extract destination and hour from conversation history (last 20 messages)
    full_text = " ".join(
        m["content"] for m in history[-20:] if isinstance(m.get("content"), str)
    ).lower()

    # Try to detect destination from conversation
    destination = None
    aeropuerto_keywords = ["aeropuerto", "aila", "sdq", "las americas", "vuelo"]
    if any(k in full_text for k in aeropuerto_keywords):
        destination = "Aeropuerto Internacional Las Americas, Santo Domingo, DO"

    # Look for common destinations mentioned
    if not destination:
        dest_hints = ["punta cana", "santiago", "la romana", "samana", "boca chica",
                      "zona colonial", "piantini", "naco", "bella vista"]
        for hint in dest_hints:
            if hint in full_text:
                destination = hint + ", Republica Dominicana"
                break

    if not destination:
        return ""

    # Try to detect hour from conversation
    import re
    service_hour = None
    hour_matches = re.findall(r'\b(\d{1,2})(?::(?:\d{2}))?\s*(?:am|pm|de la noche|de la manana|madrugada)?', full_text)
    for hm in hour_matches:
        try:
            h = int(hm)
            if 0 <= h <= 23:
                service_hour = h
                break
        except Exception:
            pass
    # Night keywords
    if service_hour is None and any(k in full_text for k in ["noche", "madrugada", "pm", "medianoche"]):
        service_hour = 22  # assume night if night words present

    # Detect passengers
    passengers = 1
    pax_match = re.search(r'(\d+)\s*(?:pasajeros?|personas?|pax)', full_text)
    if pax_match:
        passengers = int(pax_match.group(1))

    # Call Google Maps
    origin_str = f"{location['lat']},{location['lon']}"
    try:
        result = calculate_route_price(
            origin=origin_str,
            destination=destination,
            passengers=passengers,
            service_hour=service_hour
        )
        if "error" not in result:
            return result["note_for_agent"]
    except Exception as e:
        logger.warning("Error calculando precio Maps: %s", e)

    return ""


def generate_sales_response(wa_number: str, message: str, history: list, urgente: bool = False) -> str:
    """
    Genera la respuesta usando Claude con el SYSTEM_PROMPT actualizado.
    """
    messages = history[-20:]
    system = SYSTEM_PROMPT
    if urgente:
        system = SYSTEM_PROMPT + "\n\n" + PROMPT_MODO_URGENTE
        logger.info("Modo URGENTE activado para %s***", wa_number[:6])

    # Try Google Maps price calculation if we have location + some destination context
    price_note = _try_calculate_price(wa_number, history)
    if price_note:
        system = system + "\n\n" + price_note
        logger.info("Precio calculado para %s***: %s", wa_number[:6], price_note[:80])

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
