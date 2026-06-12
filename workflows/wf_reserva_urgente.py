"""
Emovils OPC - Flujo de Reserva Urgente
Para clientes que necesitan traslado HOY o en las proximas horas.
"""

import logging
from datetime import datetime
from lib.airtable_api import (
    create_lead, get_lead_by_whatsapp,
    update_lead_status, create_reserva,
    LeadStatus
)
from lib.whatsapp_api import send_text

logger = logging.getLogger(__name__)

SUPERVISOR_WHATSAPP = "18091234567"  # TODO: actualizar con numero real

PROMPT_MODO_URGENTE = """
MODO URGENTE ACTIVADO - El cliente necesita traslado HOY o en pocas horas.
Tu objetivo: obtener la informacion minima en el menor numero de mensajes.

DATOS MINIMOS PARA RESERVA URGENTE:
1. Donde esta ahora o donde lo recogemos?
2. A donde va?
3. Para que hora lo necesita?
4. Cuantas personas?
5. Nombre del pasajero

Precio: $25 desde aeropuerto, $30 dentro de la ciudad.
Cuando tengas los 5 datos confirma: Perfecto [nombre]. Un supervisor confirmara conductor en 10 minutos.
NO pidas mas datos si ya tienes los 5. La rapidez es clave.
"""

PALABRAS_URGENCIA = [
    "ahora", "ya", "urgente", "urgencia", "hoy", "inmediato",
    "aterrizando", "aterrice", "llegue", "llegando",
    "en el aeropuerto", "estoy en", "pocas horas",
    "2 horas", "1 hora", "esta tarde", "esta noche",
    "ahorita", "lo antes posible", "rapido", "de urgencia", "necesito ya"
]


def detectar_urgencia(mensaje):
    mensaje_lower = mensaje.lower()
    return any(p in mensaje_lower for p in PALABRAS_URGENCIA)


def crear_reserva_urgente(
    wa_number, nombre, origen, destino,
    hora_recogida, pasajeros=1, notas=""
):
    hoy = datetime.now().strftime("%Y-%m-%d")
    hora_actual = datetime.now().strftime("%H:%M")
    precio = 25.0
    if not any(x in origen.lower() for x in ["aeropuerto", "aila", "sdq"]):
        if not any(x in destino.lower() for x in ["aeropuerto", "aila", "sdq"]):
            precio = 30.0
    booking_id = "URG-" + wa_number[-4:] + "-" + datetime.now().strftime("%H%M")
    try:
        create_reserva(
            lead_whatsapp=wa_number,
            nombre_pasajero=nombre,
            fecha=hoy,
            hora=hora_recogida,
            origen=origen,
            destino=destino,
            pasajeros=pasajeros,
            precio_usd=precio,
            tipo_servicio="urgente",
            notas="[URGENTE] Solicitado a las " + hora_actual + ". " + notas,
            booking_id=booking_id
        )
    except Exception as e:
        logger.warning("Error creando reserva en Airtable: " + str(e))
    lead = get_lead_by_whatsapp(wa_number)
    if lead:
        update_lead_status(lead["id"], LeadStatus.RESERVADO, "Urgente: " + booking_id)
    _alertar_supervisor(booking_id, nombre, wa_number, origen, destino, hora_recogida, pasajeros, precio)
    msg = ("Reserva urgente registrada, " + nombre + ". " +
           origen + " -> " + destino + " a las " + hora_recogida +
           ". Precio: $" + str(int(precio)) +
           ". Supervisor confirma conductor en 10 min.")
    send_text(wa_number, msg)
    logger.info("Reserva URGENTE creada: " + booking_id)
    return {"status": "urgente_creada", "booking_id": booking_id, "precio_usd": precio}


def _alertar_supervisor(booking_id, nombre, wa_cliente, origen, destino, hora_recogida, pasajeros, precio):
    hora_actual = datetime.now().strftime("%H:%M")
    msg = ("RESERVA URGENTE - " + booking_id +
           " | Cliente: " + nombre +
           " | WA: " + wa_cliente +
           " | " + origen + " -> " + destino +
           " a las " + hora_recogida +
           " | " + str(pasajeros) + " pax | $" + str(int(precio)) +
           " | Solicitado " + hora_actual +
           " | Asigne conductor en 10 minutos.")
    try:
        send_text(SUPERVISOR_WHATSAPP, msg)
        logger.info("Supervisor alertado: " + booking_id)
    except Exception as e:
        logger.error("Error alertando supervisor: " + str(e))


def confirmar_conductor_al_cliente(
    wa_cliente, nombre_cliente, nombre_conductor,
    placa, wa_conductor, minutos_llegada=15
):
    msg = ("Conductor confirmado, " + nombre_cliente + ". " +
           "Conductor: " + nombre_conductor + " | Placa: " + placa +
           " | WA: " + wa_conductor +
           " | Llega en aprox. " + str(minutos_llegada) + " minutos.")
    send_text(wa_cliente, msg)
    lead = get_lead_by_whatsapp(wa_cliente)
    if lead:
        update_lead_status(
            lead["id"], LeadStatus.CONFIRMADO,
            "Conductor: " + nombre_conductor + " - " + placa
        )
    logger.info("Conductor confirmado al cliente " + wa_cliente[:6] + "***")
    return {"status": "conductor_confirmado", "conductor": nombre_conductor}
