"""
Monserrat MVP — Agente Claude con flujo cerrado y estricto.

Flujo unico:
  1. Saludar (una vez)
  2. Pedir origen, destino, pasajeros
  3. Cotizar (con tool cotizar_mvp)
  4. Cliente confirma -> preguntar forma de pago
  5. Pedir nombre completo
  6. Pedir telefono explicando uso
  7. Crear reserva (tool crear_reserva_mvp)
  8. Enviar QR cliente + datos del chofer
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
    Cotizacion, asignar_conductor_y_vehiculo, cotizar, crear_reserva,
    obtener_reserva,
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
- Si requiere_supervisor=true (más de 7 pax, B2B, tours, fuera de RD) → invoca
  escalar_supervisor con la razón y responde al cliente:
  "Ese tipo de servicio requiere validación especial. Lo voy a pasar con un supervisor
   de Emovils para ofrecerle una cotización correcta."
- Si OK → entrega la cotización así:
  "El precio del servicio es RD$[monto].

   Vehículo recomendado: [Sedán/Van].
   Capacidad: [cantidad] pasajeros.

   ¿Desea confirmar la reserva?"
  NUNCA digas "¿Lo reservamos?". Usa siempre "¿Desea confirmar la reserva?".

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
5) Con todos los datos → invoca la herramienta crear_reserva_mvp.
   Devuelve: booking_id, qr_url, y la asignación (driver_name, vehicle_brand, vehicle_model,
   vehicle_color, vehicle_plate).

═══════════════════════════════════════════════════════
CONFIRMACIÓN DE RESERVA (responde AL CLIENTE con los datos reales de la herramienta)
═══════════════════════════════════════════════════════
"Su reserva ha sido confirmada.

Vehículo: [Sedán/Van]
Fecha: [fecha]
Hora: [hora]
Recogida: [origen]
Destino: [destino]
Pasajeros: [cantidad]
Precio: RD$[monto]
Forma de pago: [efectivo/tarjeta/en línea]

Le compartimos su código QR de servicio: [qr_url]
El chofer lo escaneará al momento de la recogida para confirmar el inicio del servicio."

IMPORTANTE: incluye SIEMPRE el enlace real [qr_url] devuelto por la herramienta. Sin ese
enlace el cliente no recibe su QR.

MENSAJE DE VEHÍCULO ASIGNADO (cuando exista conductor y vehículo, en el mismo mensaje):
"Su servicio Emovils ha sido asignado.

Conductor: [driver_name]
Vehículo: [Sedán/Van]
Color: [vehicle_color]
Placa: [vehicle_plate]
Código de reserva: [booking_id]

Antes de abordar, escanee el QR colocado en la puerta del vehículo. Solo aborde si la
pantalla muestra el check verde de Emovils. Si no aparece verde, no aborde y comuníquese
con nuestra central."

═══════════════════════════════════════════════════════
REGLAS DE VEHÍCULO Y ESCALADO
═══════════════════════════════════════════════════════
- Sedán: hasta 4 pasajeros.
- Van: de 5 a 7 pasajeros.
- Más de 7 pasajeros, B2B, tours, o servicios fuera de República Dominicana → ESCALAR
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
            "Calcula el precio MVP. El sistema mide la distancia REAL con Google Maps "
            "automaticamente a partir de origen y destino. NO le pases distancia ni km: "
            "tu NO debes estimar ni inventar distancias. Usar cuando tengas origen, destino, "
            "pasajeros y hora. Si pasajeros>7 devuelve requiere_supervisor=true. Si devuelve "
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
                "vehicle_type": {"type": "string", "enum": ["Sedan", "Van Caravan", "Hyundai H1"]},
                "service_time": {"type": "string", "description": "ej: 'hoy 6pm' o '2026-06-13 14:00'"},
            },
            "required": ["customer_name", "customer_phone", "origin", "destination",
                          "passengers", "final_price", "payment_method", "vehicle_type"],
        },
    },
    {
        "name": "escalar_supervisor",
        "description": (
            "Pasar el caso a un supervisor humano. Usar para: >7 pasajeros, B2B, "
            "tours, servicios fuera de RD, quejas, accidentes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "enum": ["MAS_DE_7_PAX", "B2B", "TOUR", "FUERA_RD", "QUEJA", "ACCIDENTE", "OTRO"]},
                "resumen": {"type": "string"},
            },
            "required": ["motivo", "resumen"],
        },
    },
]


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
        return {
            "precio_rd": c.precio_rd,
            "vehiculo_recomendado": c.vehiculo_recomendado,
            "distancia_km": c.km_estimados,
            "distancia_texto": c.distancia_texto,
            "duracion_texto": c.duracion_texto,
            "es_nocturno": c.es_nocturno,
            "requiere_supervisor": c.requiere_supervisor,
            "razon_supervisor": c.razon_supervisor,
        }

    if nombre == "crear_reserva_mvp":
        # 1) crear reserva
        resultado = crear_reserva(
            customer_name=args["customer_name"],
            customer_phone=args["customer_phone"],
            origin=args["origin"],
            destination=args["destination"],
            passengers=int(args["passengers"]),
            final_price=int(args["final_price"]),
            payment_method=args["payment_method"],
            vehicle_type=args.get("vehicle_type", "Sedan"),
            service_time=args.get("service_time"),
        )
        # 2) asignar chofer + vehiculo
        asig = asignar_conductor_y_vehiculo(
            booking_id=resultado["booking_id"],
            vehicle_type=args.get("vehicle_type", "Sedan"),
        )
        resultado["asignacion"] = asig
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

    ahora = datetime.now()
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
                    elif block.name == "cotizar_mvp":
                        accion_disparada = "COTIZACION_LISTA"
                    elif block.name == "escalar_supervisor":
                        accion_disparada = "ESCALAR_SUPERVISOR"
                    resultado = _ejecutar_tool(block.name, dict(block.input), whatsapp_cliente)
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
