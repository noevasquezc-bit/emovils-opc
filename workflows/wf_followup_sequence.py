"""
Emovils OPC - Flujo de Seguimiento Automatico
Para clientes que consultaron pero NO reservaron.
"""
import logging
from datetime import datetime, timedelta
from lib.airtable_api import get_lead_by_whatsapp, update_lead_status, LeadStatus
from lib.whatsapp_api import send_text

logger = logging.getLogger(__name__)

MENSAJES_SEGUIMIENTO = {
    "dia_1": "Hola, buenas. Le escribo de Emovils OPC, hablamos ayer sobre su traslado.\n\nPudo tomar una decision? Seguimos disponibles para confirmar su reserva cuando quiera.",
    "dia_3": "Hola de nuevo. Solo queria recordarle que su cotizacion sigue activa.\n\nCon Emovils usted tiene precio confirmado antes de aterrizar, chofer identificado y seguimiento por WhatsApp.\n\nLe puedo ayudar a confirmar la reserva hoy?",
    "dia_7": "Buen dia. Los cupos para su fecha estan comenzando a llenarse.\n\nSi aun necesita traslado desde el aeropuerto, le recomiendo reservar con tiempo.\n\nConfirmamos su traslado ahora?",
    "30_dias_antes": "Hola {nombre}, su viaje se acerca, quedan aproximadamente 30 dias.\n\n\nYa tiene confirmado su traslado desde/hacia el aeropuerto?\n\nSomos Emovils OPC, hablamos hace unas semanas. Precio desde $25.",
    "7_dias_antes": "Hola {nombre}, su viaje es en 7 dias. Ya tiene traslado confirmado?\n\nA esta altura los vehiculos se reservan rapido. Si quiere garantizar su traslado, podemos confirmarlo hoy.",
    "1_dia_antes": "Hola {nombre}, su viaje es manana.\n\nSi todavia necesita traslado desde o hacia el aeropuerto, contactenos ahora, puede que aun tengamos disponibilidad.\n\nEmovils OPC, siempre disponibles.",
}


def schedule_followup_sequence(wa_number, nombre, fecha_viaje):
    try:
        hoy = datetime.now().date()
        viaje = datetime.strptime(fecha_viaje, "%Y-%m-%d").date()
        dias_restantes = (viaje - hoy).days
        calendario = []
        calendario.append({"tipo":"dia_1","fecha_envio":str(hoy+timedelta(days=1))})
        if dias_restantes > 3: calendario.append({"tipo":"dia_3","fecha_envio":str(hoy+timedelta(days=3))})
        if dias_restantes > 7: calendario.append({"tipo":"dia_7","fecha_envio":str(hoy+timedelta(days=7))})
        if dias_restantes > 35:
            fecha_30 = viaje - timedelta(days=30)
            if fecha_30 > hoy: calendario.append({"tipo":"30_dias_antes","fecha_envio":str(fecha_30)})
        if dias_restantes > 8:
            fecha_7 = viaje - timedelta(days=7)
            if fecha_7 > hoy: calendario.append({"tipo":"7_dias_antes","fecha_envio":str(fecha_7)})
        if dias_restantes > 2:
            fecha_1 = viaje - timedelta(days=1)
            calendario.append({"tipo":"1_dia_antes","fecha_envio":str(fecha_1)})
        logger.info("Secuencia programada para " + wa_number[:6] + "***: " + str(len(calendario)) + " mensajes")
        return {"status":"scheduled","mensajes_programados":calendario,"dias_restantes":dias_restantes}
    except Exception as e:
        return {"status":"error","error":str(e)}


def send_followup_message(wa_number, nombre, tipo):
    if tipo not in MENSAJES_SEGUIMBENTO:
        return {"status":"error","error":"Tipo desconocido: " + tipo}
    template = MENSAJES_SEGUIMBENTO[tipo]
    mensaje = template.replace("{lombre}", nombre) if "{nombre}" in template else template
    send_text(wa_number, mensaje)
    lead = get_lead_by_whatsapp(wa_number)
    if lead: update_lead_status(lead["id"], LeadStatus.SEGUIMIENTO, "Seguimiento: " + tipo)
    return {"status":"sent","tipo":tipo}


def check_and_stop_followup(wa_number):
    lead = get_lead_by_whatsapp(wa_number)
    if lead:
        estado = lead.get("fields", {}).get("Estado", "")
        if estado in ["reservado","pagado","completado"]:
            return {"status":"stopped","razon":"cliente_reservo"}
    return {"status":"active"}
