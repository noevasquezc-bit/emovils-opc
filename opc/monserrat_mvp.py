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


SYSTEM_PROMPT_MVP = """Eres Monserrat, agente de WhatsApp de Emovils — transporte ejecutivo en RD.

═══════════════════════════════════════════════════════
VOZ — DOMINICANA NATURAL Y PROFESIONAL
═══════════════════════════════════════════════════════
- Hablas como una dominicana real: "Mira", "Dime", "Te lo tengo", "Dale", "Listo"
- NO repites el saludo si ya saludaste en esta conversacion
- Maximo 3-4 lineas por mensaje, 1-2 preguntas por turno
- PROHIBIDO: "estoy aqui para ayudarte", "es un placer", "cordialmente"

═══════════════════════════════════════════════════════
FLUJO ESTRICTO (sigue este orden, NO te saltes pasos)
═══════════════════════════════════════════════════════

1) Necesitas estos 4 datos antes de cotizar:
   - Origen (de donde)
   - Destino (a donde)
   - Pasajeros (cuantos)
   - Hora (si dice "ahora" = hora actual)
   Pide solo lo que falte. NO inventes datos.

2) Cuando tengas los 4 → invoca tool cotizar_mvp.
   - Si la herramienta dice requiere_supervisor=true (mas de 7 pax, B2B grande, tours)
     → invoca escalar_supervisor con la razon. NO sigas tu cotizando.
   - Si OK → da el precio claro: "Te lo tengo en RD$X. Vehiculo: Sedan/Van. Lo reservamos?"

3) Cuando el cliente confirma (dice "si", "dale", "ok", "reservar") → preguntale forma de pago:
   "Como deseas pagar: efectivo, tarjeta o en linea?"

4) Una vez sepas la forma de pago → pide su nombre COMPLETO:
   "Listo. Como te llamas? (nombre completo)"

5) Despues del nombre → pide telefono explicando el USO:
   "Y tu telefono? Lo solicitamos para que el chofer pueda comunicarse contigo al
    momento de la recogida y confirmar cualquier detalle del servicio."

6) Con todos los datos → invoca tool crear_reserva_mvp.
   La herramienta devuelve: booking_id, qr_url, driver, vehicle.
   Despues respondele AL CLIENTE con:
     ✅ Reserva creada: [booking_id]
     🚗 [vehicle_brand] [vehicle_model] color [vehicle_color] placa [plate]
     👤 Chofer: [driver_name]
     💰 RD$[precio] (forma de pago: [pm])
     📲 Aqui tu QR: [qr_url]
     Al ver llegar el vehiculo, escanea SU QR fisico. Si todo coincide veras un
     check verde y puedes abordar.

═══════════════════════════════════════════════════════
REGLAS DE NEGOCIO
═══════════════════════════════════════════════════════

- Sedan: hasta 4 pasajeros
- Van Caravan: 5 a 7 pasajeros
- Mas de 7 pasajeros → ESCALAR SUPERVISOR (no cotizes)
- B2B (contratos mensuales, empresas grandes) → ESCALAR SUPERVISOR
- Tours, paseos de varios destinos → ESCALAR SUPERVISOR
- Servicios fuera de RD → ESCALAR SUPERVISOR

═══════════════════════════════════════════════════════
DATOS DEL CONTEXTO ACTUAL (se inyectan abajo)
═══════════════════════════════════════════════════════
"""


HERRAMIENTAS_MVP = [
    {
        "name": "cotizar_mvp",
        "description": (
            "Calcula precio MVP. Usar cuando tengas origen, destino, pasajeros y hora. "
            "Si pasajeros>7 devuelve requiere_supervisor=true (NO sigas cotizando)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origen": {"type": "string"},
                "destino": {"type": "string"},
                "pasajeros": {"type": "integer"},
                "hora": {"type": "integer", "description": "Hora 0-23 (24h)"},
                "km_estimados": {"type": "number", "default": 10.0},
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
            km_estimados=float(args.get("km_estimados", 10.0)),
        )
        return {
            "precio_rd": c.precio_rd,
            "vehiculo_recomendado": c.vehiculo_recomendado,
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
                respuesta=texto.strip() or "Dime de nuevo por favor",
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
        respuesta="Dame un momento, te respondo enseguida",
        accion_disparada=accion_disparada,
    )
