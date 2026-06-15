"""
Monserrat MVP — Agente Claude con flujo cerrado y estricto.

Flujo unico:
  1. Saludar (una vez)
  2. Pedir origen, destino, pasajeros
  3. Cotizar (con tool cotizar_mvp)
  4. Cliente confirma -> preguntar forma de pago
  5. Pedir nombre completo
  6. Pedir telefono explicando uso
  7. Mostrar resumen final y pedir confirmación
  8. Crear reserva (tool crear_reserva_mvp) -> enviar QR cliente
     (el chofer se asigna y se notifica aparte cuando un conductor acepta)
  9. Si pasajeros > 7 o B2B o tours -> tool escalar_supervisor
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from opc.mvp import (
    Cotizacion, cotizar, cotizar_por_hora,
    crear_reserva, obtener_reserva,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_MVP = """Eres Monserrat, asistente virtual de movilidad de Emovils.

Tu función es atender solicitudes de transporte por WhatsApp de forma formal, amable, clara y profesional. El tono debe ser dominicano, pero ejecutivo: cercana, respetuosa y confiable, sin expresiones demasiado informales. Trata al cliente siempre de USTED.

═══════════════════════════════════════════════════════
ESTILO DE COMUNICACIÓN
═══════════════════════════════════════════════════════
- Saluda según la hora: "Buenos días." / "Buenas tardes." / "Buenas noches." (usa la franja que se inyecta abajo en el contexto).
- Preséntate SOLO al inicio de una conversación nueva. No repitas el saludo ni la presentación en cada mensaje.
- Mensajes breves: máximo 3 a 4 líneas, máximo 1 o 2 preguntas por turno.
- No hagas muchas preguntas juntas si el cliente no ha dado información suficiente.

TONO PERMITIDO: "Con gusto.", "Entendido.", "Correcto.", "Gracias por la información.",
"Permítame validar.", "Para continuar con la reserva...", "¿Desea confirmar la reserva?"

TONO PROHIBIDO (NO uses nunca): "Dale", "Mi amor", "Mi hermano", "Jefe", "Tranquilo",
"No te preocupes", "¿Lo reservamos?", "Estoy aquí para ayudarte", "A la orden siempre",
expresiones de confianza excesiva, y EMOJIS (salvo que el sistema lo autorice expresamente).

═══════════════════════════════════════════════════════
FRASE DE INICIO (solo en el primer mensaje de la conversación)
═══════════════════════════════════════════════════════
"[Buenos días/Buenas tardes/Buenas noches]. Mi nombre es Monserrat, asistente de movilidad de Emovils.

Para asistirle con su solicitud, por favor indíqueme el punto de recogida y el destino."

═══════════════════════════════════════════════════════
DATOS NECESARIOS PARA COTIZAR
═══════════════════════════════════════════════════════
Antes de cotizar debes obtener: 1) punto de recogida, 2) punto de destino,
3) cantidad de pasajeros, 4) hora del servicio (si dice "ahora" = hora actual).
Si falta información, pide solo lo necesario. Ejemplo:
"Gracias. Para calcular el precio, por favor indíqueme la cantidad de pasajeros y la hora del servicio."
NO inventes datos ni precios si falta información.

═══════════════════════════════════════════════════════
COTIZACIÓN
═══════════════════════════════════════════════════════
REGLA ABSOLUTA: NUNCA estimes ni inventes distancias, kilómetros ni precios. El sistema
mide la distancia REAL con Google Maps automáticamente al invocar cotizar_mvp. Tú solo
pasas origen y destino tal como los dio el cliente. No menciones kilómetros al cliente
salvo que la herramienta te los devuelva.

Cuando tengas los 4 datos → invoca la herramienta cotizar_mvp.
- Si la herramienta devuelve direccion_no_resuelta=true → NO des precio. Pide al cliente
  una dirección más específica:
  "Para calcular la tarifa exacta, ¿podría darme una dirección más precisa? Por ejemplo,
   el sector, un punto de referencia o el nombre exacto del lugar."
  Si tras pedirla la herramienta sigue sin resolver, escala a supervisor.
- Si requiere_supervisor=true (más de 10 pax, B2B, tours, fuera de RD) → invoca
  escalar_supervisor con la razón y responde al cliente:
  "Ese tipo de servicio requiere validación especial. Lo voy a pasar con un supervisor
   de Emovils para ofrecerle una cotización correcta."
- Si OK → entrega la cotización usando el MODELO DE TARIFA PREFERENCIAL. La herramienta
  devuelve precio_rd (lo que paga el cliente), tarifa_lista (tarifa de lista, más alta) y
  descuento_pct. Usa exactamente esos montos, no los inventes ni redondees:
  Muestra SIEMPRE el origen y el destino en la cotización (tal como los dio el cliente),
  para que pueda detectar cualquier error antes de continuar.
  · Si tarifa_lista > 0 y descuento_pct > 0:
    "Con gusto.

     Recogida: [origen]
     Destino: [destino]
     Pasajeros: [cantidad]

     Tarifa de lista: RD$[tarifa_lista].
     Su tarifa preferencial Emovils: RD$[precio_rd] ([descuento_pct]% de descuento).
     Vehículo: [vehiculo_recomendado], hasta [capacidad] pasajeros.

     ¿Desea confirmar la reserva?"
  · Si tarifa_lista es 0 (sin descuento aplicable):
    "Con gusto.

     Recogida: [origen]
     Destino: [destino]
     Pasajeros: [cantidad]

     Tarifa del servicio: RD$[precio_rd].
     Vehículo: [vehiculo_recomendado], hasta [capacidad] pasajeros.

     ¿Desea confirmar la reserva?"
  Capacidad a mostrar según el vehículo: "Van Ejecutiva" → 6 pasajeros; "Van Grande" → 10 pasajeros.
  NUNCA digas "¿Lo reservamos?". Usa siempre "¿Desea confirmar la reserva?".
  NUNCA menciones que el cálculo es por kilómetro, ni la fórmula, ni los costos internos.

═══════════════════════════════════════════════════════
SERVICIO POR HORA (a disposición)
═══════════════════════════════════════════════════════
Si el cliente pide el vehículo "por hora", "a disposición", "por el día" o "renta por
horas" (sin un destino fijo) → pide cuántas horas y cuántos pasajeros, y usa la herramienta
cotizar_por_hora_mvp (mínimo 2 horas). Presenta el precio con el mismo modelo de tarifa
preferencial de arriba.

═══════════════════════════════════════════════════════
FLUJO DE RESERVA (sigue el orden, no te saltes pasos)
═══════════════════════════════════════════════════════
1) Tras la cotización, pregunta si desea confirmar la reserva.
2) Cuando el cliente confirme → pregunta la forma de pago:
   "¿Cómo desea realizar el pago: efectivo, con tarjeta o en línea?"
   (Opciones válidas: efectivo, tarjeta, en línea. No confirmes la reserva sin forma de pago.)
3) Después de la forma de pago → solicita el nombre:
   "Para continuar, por favor indíqueme el nombre completo de la persona que abordará el servicio."
4) Después del nombre → solicita el teléfono explicando su uso:
   "Por favor indíqueme su número de teléfono. Lo solicitamos para que el chofer pueda
    comunicarse con usted al momento de la recogida y confirmar cualquier detalle del servicio."
   No pidas el teléfono sin explicar para qué se usará.
5) Con todos los datos (origen, destino, pasajeros, hora, nombre, teléfono, forma de pago
   y precio) → MUESTRA un resumen final y pide una última confirmación ANTES de reservar:
   "Antes de confirmar, verifique los datos de su reserva:

    Recogida: [origen]
    Destino: [destino]
    Pasajeros: [cantidad]
    Fecha y hora: [fecha/hora]
    Precio: RD$[precio_rd]
    Forma de pago: [forma]
    Nombre: [nombre]

    ¿Confirmo la reserva con estos datos?"
   Si el cliente corrige algún dato → ajústalo (si cambió origen, destino o pasajeros, vuelve
   a cotizar con cotizar_mvp) y muestra el resumen otra vez. NO reserves hasta que el cliente
   diga que sí al resumen.
6) Cuando el cliente confirme el resumen → invoca la herramienta crear_reserva_mvp.
   Devuelve: booking_id y qr_url. (El chofer NO se asigna en este paso; el sistema lo asigna
   y se lo notifica al cliente por separado cuando un conductor acepta.)

═══════════════════════════════════════════════════════
CONFIRMACIÓN DE RESERVA (responde AL CLIENTE con los datos reales de la herramienta)
═══════════════════════════════════════════════════════
"Su reserva ha sido registrada.

Recogida: [origen]
Destino: [destino]
Fecha: [fecha]
Hora: [hora]
Pasajeros: [cantidad]
Precio: RD$[monto]
Forma de pago: [efectivo/tarjeta/en línea]
Código de reserva: [booking_id]

Le compartimos su código QR de servicio: [qr_url]

Estamos asignando su conductor. En breve le enviaremos los datos del chofer y un enlace
para seguir su vehículo en vivo. El chofer escaneará su QR al momento de la recogida."

IMPORTANTE: incluye SIEMPRE el enlace real [qr_url] devuelto por la herramienta. Sin ese
enlace el cliente no recibe su QR.

NO inventes ni anuncies un conductor, vehículo o placa al crear la reserva. La asignación
del chofer la notifica el SISTEMA automáticamente cuando un conductor acepta el servicio
(el cliente recibe un mensaje aparte con el conductor y el enlace de seguimiento). Si el
cliente pregunta por su chofer y aún no hay asignación, responde que se le notificará en
cuanto un conductor confirme.

═══════════════════════════════════════════════════════
REGLAS DE VEHÍCULO Y ESCALADO
═══════════════════════════════════════════════════════
- Van Ejecutiva: de 1 a 6 pasajeros.
- Van Grande: de 7 a 10 pasajeros.
- En esta etapa NO hay sedán: todos los servicios se realizan en van. El sistema elige la
  categoría correcta automáticamente según los pasajeros; tú solo muestras el nombre que
  devuelve la herramienta (vehiculo_recomendado).
- Más de 10 pasajeros, B2B, tours, o servicios fuera de República Dominicana → ESCALAR
  SUPERVISOR (no cotices automáticamente).

═══════════════════════════════════════════════════════
REGLAS QUE NO DEBES VIOLAR
═══════════════════════════════════════════════════════
- No confirmes reserva sin: origen, destino, hora, cantidad de pasajeros, nombre del cliente,
  teléfono del cliente, forma de pago y precio final.
- No reveles: comisión de Emovils, pago del conductor, información interna, datos sensibles,
  ni lógica financiera.
- Regla de seguridad principal: cliente correcto + vehículo correcto + chofer correcto +
  reserva correcta = check verde. Sin check verde, el cliente NO debe abordar.

═══════════════════════════════════════════════════════
DATOS DEL CONTEXTO ACTUAL (se inyectan abajo)
═══════════════════════════════════════════════════════
"""


HERRAMIENTAS_MVP = [
    {
        "name": "cotizar_mvp",
        "description": (
            "Calcula el precio de un traslado punto a punto. El sistema mide la distancia "
            "REAL con Google Maps automaticamente a partir de origen y destino. NO le pases "
            "distancia ni km: tu NO debes estimar ni inventar distancias. Usar cuando tengas "
            "origen, destino, pasajeros y hora. Devuelve precio_rd (precio preferencial que "
            "paga el cliente), tarifa_lista (ancla, mas alta), descuento_pct y vehiculo_recomendado. "
            "Si pasajeros>10 devuelve requiere_supervisor=true. Si devuelve "
            "direccion_no_resuelta=true, NO des precio: pide una direccion mas especifica."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origen": {"type": "string", "description": "Direccion/lugar de recogida tal como lo dio el cliente"},
                "destino": {"type": "string", "description": "Direccion/lugar de destino tal como lo dio el cliente"},
                "pasajeros": {"type": "integer"},
                "hora": {"type": "integer", "description": "Hora 0-23 (24h)"},
            },
            "required": ["origen", "destino", "pasajeros", "hora"],
        },
    },
    {
        "name": "cotizar_por_hora_mvp",
        "description": (
            "Calcula el precio de un servicio POR HORA (a disposicion), cuando el cliente "
            "renta el vehiculo por tiempo y no por un destino fijo. Minimo 2 horas. Devuelve "
            "precio_rd, tarifa_lista, descuento_pct y vehiculo_recomendado, igual que cotizar_mvp. "
            "Si pasajeros>10 devuelve requiere_supervisor=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "horas": {"type": "number", "description": "Cantidad de horas solicitadas (minimo 2)"},
                "pasajeros": {"type": "integer"},
                "hora": {"type": "integer", "description": "Hora de inicio 0-23 (24h), para recargo nocturno"},
            },
            "required": ["horas", "pasajeros", "hora"],
        },
    },
    {
        "name": "crear_reserva_mvp",
        "description": (
            "Crea la reserva en Airtable, genera QR del cliente y asigna chofer+vehiculo. "
            "Solo usar cuando tengas: nombre completo, telefono, origen, destino, pasajeros, "
            "precio, forma de pago. Devuelve booking_id + qr_url + driver + vehicle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "customer_phone": {"type": "string"},
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "passengers": {"type": "integer"},
                "final_price": {"type": "integer"},
                "payment_method": {"type": "string", "enum": ["cash", "card", "online"]},
                "vehicle_type": {"type": "string", "enum": ["Van Ejecutiva", "Van Grande"]},
                "service_time": {"type": "string", "description": "ej: 'hoy 6pm' o '2026-06-13 14:00'"},
            },
            "required": ["customer_name", "customer_phone", "origin", "destination",
                          "passengers", "final_price", "payment_method", "vehicle_type"],
        },
    },
    {
        "name": "escalar_supervisor",
        "description": (
            "Pasar el caso a un supervisor humano. Usar para: >10 pasajeros, B2B, "
            "tours, servicios fuera de RD, quejas, accidentes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "enum": ["MAS_DE_10_PAX", "B2B", "TOUR", "FUERA_RD", "QUEJA", "ACCIDENTE", "OTRO"]},
                "resumen": {"type": "string"},
            },
            "required": ["motivo", "resumen"],
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# BLOQUEO ANTI-ERROR: "lo que se cotiza es lo que se reserva".
# Guardamos la última cotización confirmable por número de WhatsApp. Al crear la
# reserva NO confiamos en lo que el modelo reescriba (puede alucinar origen,
# destino, pasajeros o precio): usamos SIEMPRE estos datos ya validados por el
# cliente cuando dijo "confirmar". Memoria en RAM (single worker en Railway).
# ═══════════════════════════════════════════════════════════════
_ULTIMA_COTIZACION: dict[str, dict] = {}


def _avisar_divergencia_cotizacion(args: dict, cot: dict, whatsapp: str) -> None:
    """Registra en el log si el modelo intentó crear la reserva con datos
    distintos a la cotización confirmada. La reserva igual usa la cotización;
    esto sirve para monitorear cuántas veces el modelo se habría equivocado."""
    try:
        difs = []
        if cot.get("tipo") == "punto":
            a_o = str(args.get("origin") or "").strip().lower()
            a_d = str(args.get("destination") or "").strip().lower()
            c_o = str(cot.get("origen") or "").strip().lower()
            c_d = str(cot.get("destino") or "").strip().lower()
            if a_o and a_o != c_o:
                difs.append(f"origen: modelo='{args.get('origin')}' vs cotizado='{cot.get('origen')}'")
            if a_d and a_d != c_d:
                difs.append(f"destino: modelo='{args.get('destination')}' vs cotizado='{cot.get('destino')}'")
        try:
            if args.get("passengers") is not None and int(args["passengers"]) != int(cot.get("pasajeros") or 0):
                difs.append(f"pasajeros: modelo={args.get('passengers')} vs cotizado={cot.get('pasajeros')}")
        except (TypeError, ValueError):
            pass
        try:
            if args.get("final_price") is not None and int(args["final_price"]) != int(cot.get("precio_rd") or 0):
                difs.append(f"precio: modelo={args.get('final_price')} vs cotizado={cot.get('precio_rd')}")
        except (TypeError, ValueError):
            pass
        if difs:
            logger.warning(
                "⚠️ crear_reserva_mvp: el modelo divergió de la cotización para %s; se IGNORA y se usa la cotización. [%s]",
                whatsapp, " | ".join(difs),
            )
    except Exception as e:  # nunca debe romper la creación de la reserva
        logger.debug("chequeo de divergencia falló: %s", e)


def _ejecutar_tool(nombre: str, args: dict, whatsapp_cliente: str) -> dict:
    if nombre == "cotizar_mvp":
        c = cotizar(
            origen=args["origen"],
            destino=args["destino"],
            pasajeros=int(args["pasajeros"]),
            hora=int(args["hora"]),
            # SIN km: el sistema mide la distancia real con Google. Monserrat no inventa.
        )
        if c.direccion_no_resuelta:
            return {
                "direccion_no_resuelta": True,
                "mensaje": ("No se pudo calcular la distancia con Google Maps. "
                            "Pide al cliente una direccion mas especifica (sector, "
                            "punto de referencia o nombre exacto del lugar). NO des precio."),
            }
        # BLOQUEO: recordamos esta cotización; la reserva se construirá con ESTOS datos.
        _ULTIMA_COTIZACION[whatsapp_cliente] = {
            "tipo": "punto",
            "origen": c.origen,
            "destino": c.destino,
            "pasajeros": c.pasajeros,
            "hora": int(args["hora"]),
            "precio_rd": c.precio_rd,
            "tarifa_lista": c.tarifa_lista,
            "vehiculo_recomendado": c.vehiculo_recomendado,
            "segmento": c.segmento,
            "distancia_km": c.km_estimados,
            "ts": datetime.now().isoformat(),
        }
        return {
            "precio_rd": c.precio_rd,
            "tarifa_lista": c.tarifa_lista,
            "descuento_pct": c.descuento_pct,
            "segmento": c.segmento,
            "vehiculo_recomendado": c.vehiculo_recomendado,
            "distancia_km": c.km_estimados,
            "distancia_texto": c.distancia_texto,
            "duracion_texto": c.duracion_texto,
            "es_nocturno": c.es_nocturno,
            "requiere_supervisor": c.requiere_supervisor,
            "razon_supervisor": c.razon_supervisor,
        }

    if nombre == "cotizar_por_hora_mvp":
        c = cotizar_por_hora(
            horas=float(args.get("horas", 0)),
            pasajeros=int(args["pasajeros"]),
            hora=int(args["hora"]),
        )
        # BLOQUEO: servicio por hora no tiene origen/destino fijo; bloqueamos
        # precio, pasajeros y vehículo (origen/destino vendrán del modelo).
        _ULTIMA_COTIZACION[whatsapp_cliente] = {
            "tipo": "hora",
            "origen": None,
            "destino": None,
            "pasajeros": c.pasajeros,
            "hora": int(args["hora"]),
            "precio_rd": c.precio_rd,
            "tarifa_lista": c.tarifa_lista,
            "vehiculo_recomendado": c.vehiculo_recomendado,
            "segmento": c.segmento,
            "distancia_km": None,
            "ts": datetime.now().isoformat(),
        }
        return {
            "precio_rd": c.precio_rd,
            "tarifa_lista": c.tarifa_lista,
            "descuento_pct": c.descuento_pct,
            "segmento": c.segmento,
            "vehiculo_recomendado": c.vehiculo_recomendado,
            "es_nocturno": c.es_nocturno,
            "requiere_supervisor": c.requiere_supervisor,
            "razon_supervisor": c.razon_supervisor,
        }

    if nombre == "crear_reserva_mvp":
        # Datos que SOLO da el cliente (no aparecen en la cotización):
        customer_name = args["customer_name"]
        customer_phone = args["customer_phone"]
        payment_method = args["payment_method"]
        service_time = args.get("service_time")

        # BLOQUEO ANTI-ERROR: "lo que se cotiza es lo que se reserva".
        # Usamos los datos de la última cotización confirmada por el cliente, NO
        # lo que el modelo reescriba (evita que un origen/destino/precio
        # alucinado llegue a la reserva).
        cot = _ULTIMA_COTIZACION.get(whatsapp_cliente)
        if cot:
            _avisar_divergencia_cotizacion(args, cot, whatsapp_cliente)
            origin = cot.get("origen") or args.get("origin")
            destination = cot.get("destino") or args.get("destination")
            passengers = int(cot.get("pasajeros") or args.get("passengers") or 1)
            final_price = int(cot.get("precio_rd") or args.get("final_price") or 0)
            vehicle_type = cot.get("vehiculo_recomendado") or args.get("vehicle_type", "Van Ejecutiva")
            distance_km = cot.get("distancia_km")
        else:
            # Sin cotización en cache (no debería ocurrir): se usa lo del modelo.
            logger.warning(
                "crear_reserva_mvp SIN cotización en cache para %s — se usan los "
                "datos del modelo (riesgo de error).", whatsapp_cliente,
            )
            origin = args["origin"]
            destination = args["destination"]
            passengers = int(args["passengers"])
            final_price = int(args["final_price"])
            vehicle_type = args.get("vehicle_type", "Van Ejecutiva")
            distance_km = None

        resultado = crear_reserva(
            customer_name=customer_name,
            customer_phone=customer_phone,
            origin=origin,
            destination=destination,
            passengers=passengers,
            final_price=final_price,
            payment_method=payment_method,
            vehicle_type=vehicle_type,
            service_time=service_time,
            distance_km=distance_km,
        )
        # NO se asigna chofer aquí. El despacho (oferta al chofer más cercano) ya
        # lo dispara crear_reserva vía iniciar_despacho; el cliente recibe los
        # datos del chofer + el link de seguimiento SOLO cuando alguien ACEPTA la
        # oferta. Así no se promete un chofer antes de que exista.
        _ULTIMA_COTIZACION.pop(whatsapp_cliente, None)  # cotización ya usada
        return resultado

    if nombre == "escalar_supervisor":
        try:
            from opc.whatsapp_green_api import enviar_a_cliente
            owner = os.getenv("OWNER_WHATSAPP", "+18298610090")
            mensaje = (
                f"🚨 ESCALACION SUPERVISOR\n"
                f"Cliente: {whatsapp_cliente}\n"
                f"Motivo: {args['motivo']}\n"
                f"{args['resumen']}"
            )
            enviar_a_cliente(owner, mensaje)
        except Exception as exc:
            logger.warning("No se pudo notificar supervisor: %s", exc)
        return {"escalado": True, "supervisor_avisado": True}

    return {"error": f"Tool desconocido: {nombre}"}


# Memoria en RAM por whatsapp (single worker en Railway)
_MEMORIA: dict[str, list[dict]] = {}
_MAX_TURNOS = 16


def reset_memoria(whatsapp: str) -> None:
    _MEMORIA.pop(whatsapp, None)


def _sanear_memoria(mem: list[dict]) -> None:
    """Repara la memoria si quedo un tool_use de assistant sin su tool_result.

    Si una corrida anterior fallo entre el tool_use y el tool_result, la
    conversacion quedaria invalida y la API de Claude rechazaria el siguiente
    mensaje (error 400). Esto elimina el turno colgante para poder continuar.
    """
    while mem:
        ultimo = mem[-1]
        if ultimo.get("role") != "assistant":
            break
        contenido = ultimo.get("content")
        bloques = contenido if isinstance(contenido, list) else []
        tiene_tool_use = any(
            getattr(b, "type", None) == "tool_use"
            or (isinstance(b, dict) and b.get("type") == "tool_use")
            for b in bloques
        )
        if tiene_tool_use:
            mem.pop()
        else:
            break


@dataclass
class RespuestaMonserrat:
    respuesta: str
    intencion: str = "MVP"
    accion_disparada: Optional[str] = None
    envio_voz: bool = False


def procesar(mensaje: str, whatsapp_cliente: str, nombre_cliente: str = "",
             cliente_uso_audio: bool = False) -> RespuestaMonserrat:
    """Punto de entrada Monserrat MVP."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return RespuestaMonserrat(respuesta="Sistema no configurado (falta ANTHROPIC_API_KEY)")

    try:
        import anthropic
    except ImportError:
        return RespuestaMonserrat(respuesta="Falta dependencia 'anthropic'")

    client = anthropic.Anthropic(api_key=api_key)
    modelo = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

    # Hora de Republica Dominicana (UTC-4, sin horario de verano).
    # Railway corre en UTC: usar datetime.now() daria una franja incorrecta.
    try:
        from zoneinfo import ZoneInfo
        ahora = datetime.now(ZoneInfo("America/Santo_Domingo"))
    except Exception:
        from datetime import timezone, timedelta
        ahora = datetime.now(timezone(timedelta(hours=-4)))
    dia = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"][ahora.weekday()]
    franja = "manana (Buenos dias)" if 5 <= ahora.hour < 12 else \
             "tarde (Buenas tardes)" if 12 <= ahora.hour < 19 else \
             "noche (Buenas noches)"

    system = SYSTEM_PROMPT_MVP + (
        f"- Cliente WhatsApp: {whatsapp_cliente}\n"
        f"- Nombre detectado: {nombre_cliente or 'no registrado'}\n"
        f"- Fecha hoy: {dia} {ahora.strftime('%d de %B de %Y')}\n"
        f"- Hora actual: {ahora.hour:02d}:{ahora.minute:02d} ({franja})\n"
    )

    memoria = _MEMORIA.setdefault(whatsapp_cliente, [])
    _sanear_memoria(memoria)
    memoria.append({"role": "user", "content": mensaje})
    if len(memoria) > _MAX_TURNOS:
        memoria[:] = memoria[-_MAX_TURNOS:]

    accion_disparada = None
    for _ in range(6):  # max iteraciones tool-use
        response = client.messages.create(
            model=modelo, max_tokens=1024, system=system,
            tools=HERRAMIENTAS_MVP, messages=list(memoria),
        )

        if response.stop_reason == "end_turn":
            texto = ""
            for block in response.content:
                if block.type == "text":
                    texto += block.text
            memoria.append({"role": "assistant", "content": response.content})
            return RespuestaMonserrat(
                respuesta=texto.strip() or "Disculpe, ¿podría repetirme su solicitud, por favor?",
                accion_disparada=accion_disparada,
                envio_voz=cliente_uso_audio,
            )

        if response.stop_reason == "tool_use":
            memoria.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info("🛠️ Tool: %s args=%s", block.name, dict(block.input))
                    if block.name == "crear_reserva_mvp":
                        accion_disparada = "RESERVA_CREADA"
                    elif block.name in ("cotizar_mvp", "cotizar_por_hora_mvp"):
                        accion_disparada = "COTIZACION_LISTA"
                    elif block.name == "escalar_supervisor":
                        accion_disparada = "ESCALAR_SUPERVISOR"
                    try:
                        resultado = _ejecutar_tool(block.name, dict(block.input), whatsapp_cliente)
                    except Exception as exc:
                        logger.exception("Tool %s fallo: %s", block.name, exc)
                        resultado = {
                            "error": True,
                            "mensaje": ("Hubo un inconveniente tecnico al procesar este paso. "
                                        "Discúlpate brevemente con el cliente y pídele que lo confirme "
                                        "de nuevo en un momento; NO inventes datos ni confirmes la reserva."),
                        }
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(resultado, ensure_ascii=False, default=str),
                    })
            memoria.append({"role": "user", "content": tool_results})
            continue
        break

    return RespuestaMonserrat(
        respuesta="Permítame un momento, le respondo enseguida.",
        accion_disparada=accion_disparada,
    )
