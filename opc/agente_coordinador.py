"""
Emovils OPC — Agente Coordinador (Monserrat)

⚠️ NO ES UN BOT. ES UN AGENTE CLAUDE REAL.

Monserrat es un AGENTE construido con Claude (Anthropic API). Tiene:
- Identidad persistente (dominicana, profesional, calida)
- Memoria conversacional (historial por whatsapp)
- Tool-use real (cotizar, reservar, escalar, consultar Airtable)
- Decisiones autonomas (no arbol de if/else)

Las heuristicas (clasificar_intencion, extraer_datos_cotizacion) solo
existen como FALLBACK cuando ANTHROPIC_API_KEY no esta configurada.
En produccion, TODO pasa por Claude.

Funciones:
  1. Conversa en espanol dominicano natural via Claude API
  2. Cotiza llamando a la herramienta calcular_tarifa
  3. Crea reservas en Airtable usando la herramienta crear_reserva
  4. Escala quejas y casos criticos a nvasquez@emovils.com + WhatsApp dueno
  5. Notifica al chofer asignado por el Despachador
  6. Genera QRs y los envia al cliente

Para la voz dominicana (ElevenLabs) se integra en voz_dominicana.py.
"""
from __future__ import annotations
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def _cargar_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


_cargar_env()
sys.path.insert(0, str(ROOT))

from opc.airtable_api_opc import AirtableOPC
from opc.agente_ingesta import (
    Canal,
    es_solicitud_urgente,
    es_solicitud_vip,
    procesar_solicitud_b2c,
)
from opc.agente_despachador import (
    CandidatoChofer,
    SolicitudServicio,
    asignar,
    mensaje_cliente_chofer_asignado,
    mensaje_oferta_chofer,
)
from opc.precios import calcular_tarifa, explicar_tarifa
from opc.qr_generator_opc import generar_qr_cliente

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# IDENTIDAD DOMINICANA DE MONSERRAT
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT_MONSERRAT = """Eres Monserrat. Una mujer dominicana de 32 años que atiende WhatsApp de Emovils — transporte ejecutivo con 14 anos operando en RD.

═══════════════════════════════════════════════════════
TU IDENTIDAD (interiorizala, no la copies literal)
═══════════════════════════════════════════════════════
- Eres dominicana de pura cepa, de Santo Domingo. Hablas como habla la gente aqui.
- Eres calida pero PROFESIONAL — no eres una "amiguita" del cliente, eres la cara de Emovils
- Resolvers, vas al grano, no enrollas
- Llevas anos atendiendo clientes ejecutivos: ejecutivos de call centers, gente de hoteles, navieras, turistas VIP
- Sabes cuando ser breve (cliente apurado) y cuando explicar (cliente con dudas)

═══════════════════════════════════════════════════════
COMO HABLAS (voz dominicana real, NO de bot)
═══════════════════════════════════════════════════════
NATURAL — asi habla la gente en RD:
- "Mira", "Dime", "Cuentame", "Vamo a ve", "Listo"
- "Dale" (para confirmar), "Tranqui" / "Tranquilo" (para calmar)
- "Ya te resuelvo", "Eso es facil", "Sin problema"
- "Que mas?" en vez de "¿hay algo mas en lo que pueda ayudarle?"
- "Dime cuando" en vez de "¿podria indicarme la fecha?"
- "Te lo tengo en RD$X" en vez de "El costo asciende a..."

NUNCA digas (suena a bot):
- ❌ "Estoy aqui para ayudarte"
- ❌ "Es un placer atenderle"
- ❌ "No dude en consultarnos"
- ❌ "Quedo a su disposicion"
- ❌ "Cordialmente" / "Atentamente"
- ❌ "Permitame ayudarle"
- ❌ Reflejar el saludo del cliente cada vez ("Hola! Hola!" en cada turno)

REGLA DE ORO: SALUDA SOLO UNA VEZ POR CONVERSACION
- Si ya saludaste a un cliente en este chat, NUNCA vuelvas a decir "Hola" o "Buenas".
- Continua la conversacion donde quedo. Revisa el historial: si ya hubo saludo, ve directo al grano.
- Si el cliente vuelve a saludar a media conversacion, responde "Dime" o "Aqui estoy" — NO repitas el "Hola Maria" otra vez.

═══════════════════════════════════════════════════════
SERVICIOS DE EMOVILS
═══════════════════════════════════════════════════════
- Traslados AILA (aeropuerto Santo Domingo): llegadas y salidas
- VIP: Casa de Campo, Punta Cana, Bavaro, hoteles 5 estrellas
- Corporativo: call centers (Intelcia y otros), navieras, empresas
- Por hora: chofer reservado para reuniones, eventos
- Tarifa base RD$300 (cubre 3 km), despues RD$60/km ciudad o RD$110/km larga distancia
- +20% nocturno (11PM-6AM) · +10% si necesitas van H1 grande (7+ pax)

Vehiculos: Van Caravan (hasta 7 pax) y Hyundai H1 (hasta 10 pax). Chofer profesional verificado.

═══════════════════════════════════════════════════════
TUS HERRAMIENTAS (usa cuando aplique, sin avisar)
═══════════════════════════════════════════════════════
- cotizar_servicio: Cuando tengas origen + destino + pasajeros → calcula precio
- crear_reserva: SOLO cuando el cliente confirma con SI claro
- escalar_a_humano: Queja critica, accidente, reembolso, "quiero hablar con el dueno"
- consultar_estado_reserva: "donde esta mi chofer", "mi reserva"

═══════════════════════════════════════════════════════
ESTILO DE RESPUESTA
═══════════════════════════════════════════════════════
- CORTO: maximo 4 lineas, idealmente 2-3
- 1-2 preguntas por turno, no mas
- Emojis SOLO cuando suman (📍 ubicacion · 💰 precio · ⏰ hora · ✅ confirmado · 🚨 urgente)
- Sin parrafos largos. Usa saltos de linea.
- Pesos = RD$ siempre. Nunca dolares salvo que el cliente lo pida.
- Si el cliente dice "ahora" / "ya" / "urgente" → modo express, ve al grano sin explicar nada extra

EJEMPLOS DE BUENA VOZ:
- Cliente "Hola" (primer mensaje): "Hola! Soy Monserrat de Emovils. Dime, en que te ayudo?"
- Cliente "del aila a Casa de Campo, 3 pax 6am": [usa cotizar] "Te lo tengo: RD$X. Lo reservamos?"
- Cliente "mi conductor no llega": "Dejame chequear ahora mismo. Dame 1 min."
- Cliente queja: "Que pena lo que pasaste. Cuentame que paso para resolverte." [escalar_a_humano]
- Cliente "gracias": "A ti! Cualquier cosa estoy por aqui."

═══════════════════════════════════════════════════════
LIMITES
═══════════════════════════════════════════════════════
- NUNCA prometas reembolsos sin autorizacion
- NUNCA inventes precios — usa la herramienta cotizar
- Si el cliente pide algo fuera de tu alcance (legal, accidente, queja seria) → escalar_a_humano de una vez
- Si no entiendes el mensaje, di "Dime un poco mas" en vez de adivinar
"""


# ─────────────────────────────────────────────────────────────
# CLASIFICACIÓN DE INTENCIÓN
# ─────────────────────────────────────────────────────────────

class Intencion:
    SALUDO = "SALUDO"
    COTIZAR = "COTIZAR"
    RESERVAR = "RESERVAR"
    URGENTE = "URGENTE"
    QUEJA = "QUEJA"
    INFORMACION = "INFORMACION"
    SEGUIMIENTO = "SEGUIMIENTO_RESERVA"
    DESCONOCIDO = "DESCONOCIDO"


def clasificar_intencion(mensaje: str) -> str:
    """Clasificación rápida sin LLM (heurística). El LLM se usa después para detalle."""
    m = mensaje.lower().strip()

    if not m:
        return Intencion.DESCONOCIDO

    # Queja PRIMERO (tiene precedencia sobre seguimiento)
    quejas = ["queja", "molesto", "mal servicio", "no aparec", "nunca lleg",
              "no lleg", "perdí mi", "perdi mi", "incumpl", "se perdió",
              "reclamar", "muy mal", "horrible", "pésimo", "pesimo"]
    if any(p in m for p in quejas):
        return Intencion.QUEJA

    if es_solicitud_urgente(m):
        return Intencion.URGENTE

    if any(p in m for p in ["cotiza", "cotizar", "cuánto", "cuanto", "precio", "tarifa",
                             "vale", "cuesta"]):
        return Intencion.COTIZAR

    if any(p in m for p in ["reservo", "reservar", "agendar", "agenda", "para mañana",
                             "para el", "necesito traslado", "necesito un servicio",
                             "necesito ir", "voy a necesitar"]):
        return Intencion.RESERVAR

    if any(p in m for p in ["¿dónde está mi", "donde esta mi", "ya viene", "cómo va",
                             "como va", "mi reserva", "mi conductor", "estado de mi"]):
        return Intencion.SEGUIMIENTO

    if m in ["hola", "buenos", "buenas", "saludos", "hey"] or m.startswith("hola"):
        return Intencion.SALUDO

    return Intencion.INFORMACION


# ─────────────────────────────────────────────────────────────
# RESPUESTAS BASE (sin necesitar LLM para mensajes comunes)
# ─────────────────────────────────────────────────────────────

RESPUESTAS_RAPIDAS = {
    Intencion.SALUDO: (
        "¡Hola! 👋 Soy Monserrat de Emovils. ¿En qué te puedo ayudar?\n"
        "Dime: ¿necesitas servicio ahora o quieres reservar para más tarde?"
    ),
    Intencion.COTIZAR: (
        "¡Claro! Para darte el precio exacto necesito:\n"
        "📍 Desde dónde sales\n"
        "📍 A dónde vas\n"
        "👥 Cuántos pasajeros\n"
        "📅 Cuándo (fecha y hora)\n"
        "Dime esos datos y te cotizo al instante."
    ),
    Intencion.URGENTE: (
        "🚨 Modo rápido activado. Dime:\n"
        "1. ¿Dónde estás exactamente?\n"
        "2. ¿A dónde vas?\n"
        "3. ¿Cuántos son?\n"
        "Te busco el conductor más cercano disponible."
    ),
    Intencion.QUEJA: (
        "Lamento mucho lo sucedido 🙏. Cuéntame qué pasó con detalle "
        "(fecha, conductor si recuerdas, qué ocurrió). El dueño revisará "
        "personalmente tu caso."
    ),
    Intencion.SEGUIMIENTO: (
        "Déjame revisar tu reserva. Dime tu nombre completo o el código "
        "de servicio (empieza con SVC-)."
    ),
}


# ─────────────────────────────────────────────────────────────
# EXTRACCIÓN DE DATOS DE COTIZACIÓN
# ─────────────────────────────────────────────────────────────

@dataclass
class DatosCotizacion:
    origen: str = ""
    destino: str = ""
    pasajeros: int = 0
    fecha: str = ""
    hora: str = ""
    es_urgente: bool = False
    es_vip: bool = False
    completo: bool = False
    faltantes: list[str] = field(default_factory=list)


def extraer_datos_cotizacion(texto: str) -> DatosCotizacion:
    """Extrae lo que pueda del mensaje. Lo que falta se pregunta."""
    d = DatosCotizacion()
    t = texto.lower()

    # Pasajeros — patrones múltiples
    for patron in [
        r"(\d+)\s+(?:pax|pasajeros?|personas|adultos?|persona)",
        r"somos\s+(\d+)",
        r"para\s+(\d+)\s+personas",
    ]:
        m = re.search(patron, t)
        if m:
            d.pasajeros = int(m.group(1))
            break

    # Detectar urgencia / VIP
    d.es_urgente = es_solicitud_urgente(texto)
    d.es_vip = es_solicitud_vip(texto)

    # Detectar AILA (origen vs destino)
    if "aila" in t or "aeropuerto" in t or "sdq" in t:
        if any(p in t for p in ["del aeropuerto", "del aila", "desde aila",
                                  "desde el aeropuerto", "salgo del aila",
                                  "salgo del aeropuerto"]):
            d.origen = "AILA"
        elif any(p in t for p in ["al aeropuerto", "al aila", "hacia aila",
                                    "voy al aila", "voy al aeropuerto",
                                    "para el aeropuerto", "para el aila"]):
            d.destino = "AILA"
        else:
            # Por defecto si solo dice "aeropuerto" asumimos destino
            if not d.origen and not d.destino:
                d.destino = "AILA"

    # Detectar destinos comunes (sobreescribe destino solo si no estaba)
    destinos_comunes = ["punta cana", "bávaro", "bavaro", "casa de campo", "la romana",
                         "santiago", "puerto plata", "samaná", "samana", "las terrenas",
                         "boca chica", "juan dolio"]
    for dest in destinos_comunes:
        if dest in t:
            if not d.destino:
                d.destino = dest.title()
            break

    # Detectar origen por hotel/lugar mencionado con "del"/"desde"
    m_origen = re.search(r"(?:del|desde el?)\s+([\w\s]+?)(?:\s+(?:al|hacia|hasta)|,|$)", t)
    if m_origen and not d.origen:
        candidato = m_origen.group(1).strip()
        if candidato and "aeropuerto" not in candidato and "aila" not in candidato:
            d.origen = candidato.title()

    # Detectar hora — solo si hay marcador AM/PM o ":" (más estricto)
    patrones_hora = [
        # 6am, 6:30am, 6 am, 6:30 pm
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        # "a las 6" / "para las 18"
        r"a\s+las\s+(\d{1,2})(?::(\d{2}))?",
        # "6:30" formato 24h
        r"\b(\d{1,2}):(\d{2})\b",
    ]
    for patron in patrones_hora:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            h = int(m.group(1))
            mm = int(m.group(2) or 0)
            sufijo = m.group(3).lower() if len(m.groups()) >= 3 and m.group(3) else ""
            if "pm" in sufijo and h < 12:
                h += 12
            if "am" in sufijo and h == 12:
                h = 0
            d.hora = f"{h:02d}:{mm:02d}"
            break

    # Faltantes
    if not d.origen:
        d.faltantes.append("origen")
    if not d.destino:
        d.faltantes.append("destino")
    if not d.pasajeros:
        d.faltantes.append("pasajeros")

    d.completo = len(d.faltantes) == 0
    return d


# ─────────────────────────────────────────────────────────────
# ESTIMACIÓN DE KM (sin Google Maps por ahora)
# ─────────────────────────────────────────────────────────────

KM_APROXIMADO = {
    ("aila", "boca chica"): 10,
    ("aila", "juan dolio"): 45,
    ("aila", "la romana"): 110,
    ("aila", "casa de campo"): 115,
    ("aila", "punta cana"): 200,
    ("aila", "bavaro"): 195,
    ("aila", "santiago"): 155,
    ("aila", "puerto plata"): 235,
    ("aila", "samaná"): 245,
    ("aila", "las terrenas"): 240,
    ("centro", "aila"): 25,
    ("centro", "boca chica"): 30,
    ("centro", "punta cana"): 220,
}


def estimar_km(origen: str, destino: str) -> float:
    """Estimación grosera. En producción se reemplaza con Google Maps API."""
    o = origen.lower()
    d = destino.lower()

    for (k_o, k_d), km in KM_APROXIMADO.items():
        if k_o in o and k_d in d:
            return float(km)
        if k_d in o and k_o in d:  # bidireccional
            return float(km)

    # Defaults
    if "aila" in o or "aila" in d:
        return 50.0
    return 10.0


# ─────────────────────────────────────────────────────────────
# COTIZACIÓN AL CLIENTE
# ─────────────────────────────────────────────────────────────

def generar_cotizacion(d: DatosCotizacion) -> str:
    """Genera el mensaje de cotización al cliente."""
    if not d.completo:
        falta_texto = ", ".join(d.faltantes)
        return f"Para cotizarte exacto, dime {falta_texto}."

    km = estimar_km(d.origen, d.destino)
    hora_int = 12
    if d.hora:
        try:
            hora_int = int(d.hora.split(":")[0])
        except (ValueError, IndexError):
            pass

    calculo = calcular_tarifa(
        km=km,
        origen=d.origen,
        destino=d.destino,
        hora=hora_int,
        pasajeros=d.pasajeros,
    )

    nocturno = " (incluye recargo nocturno +20%)" if calculo.es_nocturno else ""
    h1 = " (van H1 grande)" if calculo.es_h1 else ""

    mensaje = (
        f"📍 *{d.origen} → {d.destino}*\n"
        f"📏 Aprox {km:g} km · 👥 {d.pasajeros} pax{h1}\n"
        f"💰 *RD${calculo.precio_final:,}*{nocturno}\n"
    )

    if d.es_urgente:
        mensaje += (
            f"\n🚨 *Modo express* — En cuanto confirmes te asigno el "
            f"conductor más cercano. ¿Confirmas?"
        )
    else:
        mensaje += "\nResponde SÍ para reservar o dime si tienes preguntas."

    return mensaje


# ─────────────────────────────────────────────────────────────
# PROCESAMIENTO DE MENSAJE ENTRANTE
# ─────────────────────────────────────────────────────────────

@dataclass
class ResultadoConversacion:
    respuesta: str
    intencion: str
    accion_disparada: Optional[str] = None
    necesita_cliente_continuar: bool = True
    datos_cotizacion: Optional[DatosCotizacion] = None
    enviar_como_voz: bool = False  # True si el cliente uso audio → responder con audio


def procesar_mensaje_entrante(
    mensaje: str,
    whatsapp_cliente: str,
    nombre_cliente: str = "",
    historial: Optional[list[dict]] = None,
) -> ResultadoConversacion:
    """
    Procesa un mensaje y devuelve la respuesta + metadata.

    Si ANTHROPIC_API_KEY esta configurada → usa Monserrat AGENTE Claude real.
    Si no → fallback a heuristicas (modo dev / sin internet).
    """
    # PRIMARIO: Agente Claude real con tool-use
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            return _procesar_con_claude(mensaje, whatsapp_cliente, nombre_cliente, historial)
        except Exception as exc:
            logger.exception("Fallo Claude API, usando fallback heuristico: %s", exc)
            # cae a fallback

    # FALLBACK: heuristicas
    intencion = clasificar_intencion(mensaje)

    # Casos simples sin LLM
    if intencion in (Intencion.SALUDO, Intencion.SEGUIMIENTO):
        return ResultadoConversacion(
            respuesta=RESPUESTAS_RAPIDAS[intencion],
            intencion=intencion,
        )

    if intencion == Intencion.QUEJA:
        return ResultadoConversacion(
            respuesta=RESPUESTAS_RAPIDAS[intencion],
            intencion=intencion,
            accion_disparada="ESCALAR_DUEÑO",
        )

    # Cotizar / Reservar / Urgente — intentar extraer datos
    if intencion in (Intencion.COTIZAR, Intencion.RESERVAR, Intencion.URGENTE):
        datos = extraer_datos_cotizacion(mensaje)

        if not datos.completo:
            # Pedir lo que falta
            faltan = ", ".join(datos.faltantes)
            return ResultadoConversacion(
                respuesta=(
                    f"Bien, ya casi. Solo me falta: {faltan}.\n"
                    "Dame esos datos y te cotizo."
                ),
                intencion=intencion,
                datos_cotizacion=datos,
            )

        # Tenemos datos completos → cotizar
        respuesta = generar_cotizacion(datos)
        return ResultadoConversacion(
            respuesta=respuesta,
            intencion=intencion,
            datos_cotizacion=datos,
            accion_disparada="COTIZACION_LISTA",
        )

    # Información general / desconocido
    return ResultadoConversacion(
        respuesta=(
            "Para servirte mejor, dime ¿qué necesitas?\n"
            "1️⃣ Cotizar un servicio\n"
            "2️⃣ Reservar ahora (modo urgente)\n"
            "3️⃣ Otra cosa"
        ),
        intencion=intencion,
    )


# ─────────────────────────────────────────────────────────────
# CONFIRMACIÓN Y CREACIÓN DE RESERVA
# ─────────────────────────────────────────────────────────────

def crear_reserva_y_asignar(
    datos: DatosCotizacion,
    nombre_cliente: str,
    whatsapp_cliente: str,
    fecha: Optional[str] = None,
) -> dict:
    """
    Cuando el cliente confirma con SÍ, este es el flujo:
    1. Crea servicio en Airtable
    2. Genera QR
    3. Busca conductor disponible (Despachador)
    4. Devuelve mensajes listos para enviar a cliente y conductor
    """
    fecha = fecha or datetime.now().date().isoformat()
    api = AirtableOPC()

    # Servicio normalizado
    servicio_norm = procesar_solicitud_b2c(
        origen=datos.origen,
        destino=datos.destino,
        fecha=fecha,
        hora=datos.hora or "12:00",
        pasajeros=datos.pasajeros,
        nombre_cliente=nombre_cliente,
        whatsapp_cliente=whatsapp_cliente,
        canal=Canal.WHATSAPP,
        es_inmediato=datos.es_urgente,
        es_vip=datos.es_vip,
    )

    # Crear en Airtable
    record = api.crear_registro("Servicios", servicio_norm.como_record_airtable())
    servicio_id = record["id"]

    # Generar QR
    qr_path, qr_payload = generar_qr_cliente(
        servicio_id=servicio_id,
        nombre_cliente=nombre_cliente,
        whatsapp_cliente=whatsapp_cliente,
        origen=datos.origen,
        destino=datos.destino,
        fecha_hora=f"{fecha} {datos.hora}",
        pasajeros=datos.pasajeros,
        monto_rd=servicio_norm.tarifa_rd,
        estado_pago="PENDIENTE",
    )

    # Cargar conductores DISPONIBLES desde Airtable
    try:
        choferes = api.conductores_disponibles()
    except Exception:
        choferes = []

    candidatos = []
    for c in choferes:
        f = c["fields"]
        candidatos.append(CandidatoChofer(
            chofer_id=c["id"],
            nombre=f.get("Nombre_completo", ""),
            whatsapp=f.get("WhatsApp", ""),
            tipo=f.get("Tipo", "Propio"),
            zona_base=f.get("Zona_base", []) or [],
            calificacion=float(f.get("Calificacion_promedio", 0) or 0),
            capacidad_max_pax=10,  # mejor: derivar de Vehiculo asignado
            tipo_vehiculo="Caravan",
            placa_vehiculo=f.get("Codigo", ""),
        ))

    # Solicitud al Despachador
    solicitud = SolicitudServicio(
        servicio_id=servicio_id,
        origen=datos.origen,
        destino=datos.destino,
        pasajeros=datos.pasajeros,
        fecha_hora=datetime.now(),
        canal="B2C_WHATSAPP",
        tarifa_rd=servicio_norm.tarifa_rd,
        prioridad="URGENTE" if datos.es_urgente else "NORMAL",
        es_vip=datos.es_vip,
    )

    if not candidatos:
        return {
            "servicio_id": servicio_id,
            "qr_path": str(qr_path),
            "mensaje_cliente": (
                f"✅ Reserva creada (#{servicio_id}).\n"
                f"⏳ Buscando conductor disponible — te aviso en cuanto haya uno asignado."
            ),
            "mensaje_chofer": "",
            "chofer_asignado": None,
        }

    resultado = asignar(solicitud, candidatos)
    if not resultado.asignado:
        return {
            "servicio_id": servicio_id,
            "qr_path": str(qr_path),
            "mensaje_cliente": "⏳ Buscando conductor disponible...",
            "mensaje_chofer": "",
            "chofer_asignado": None,
        }

    chofer = resultado.chofer_seleccionado
    return {
        "servicio_id": servicio_id,
        "qr_path": str(qr_path),
        "mensaje_cliente": mensaje_cliente_chofer_asignado(solicitud, chofer, eta_minutos=10),
        "mensaje_chofer": mensaje_oferta_chofer(solicitud, chofer),
        "chofer_asignado": chofer.chofer_id,
    }


# ═════════════════════════════════════════════════════════════
# MOTOR LLM REAL — MONSERRAT COMO AGENTE CLAUDE
# ═════════════════════════════════════════════════════════════
#
# Esto NO es un bot. Es un agente Claude que:
#   1. Mantiene contexto (memoria por whatsapp_cliente)
#   2. Usa herramientas (tool-use) para cotizar, reservar, escalar
#   3. Decide autonomamente que hacer (no arbol if/else)
#
# Memoria conversacional en RAM (en produccion: Airtable Conversations)
# ═════════════════════════════════════════════════════════════

def _franja_horaria(hora: int) -> str:
    if 5 <= hora < 12: return "mañana — saluda 'Buenos días'"
    if 12 <= hora < 19: return "tarde — saluda 'Buenas tardes'"
    return "noche — saluda 'Buenas noches'"


_MEMORIA_CONVERSACIONES: dict[str, list[dict]] = {}  # cache local (1 worker)
_MAX_TURNOS_MEMORIA = 16  # ultimos 16 turnos por cliente
_TABLA_CONVERSACIONES = os.getenv("AIRTABLE_CONVERSATIONS_TABLE", "Conversations")


def _cargar_memoria_persistente(whatsapp_cliente: str) -> list[dict]:
    """Carga el historial del cliente desde Airtable (persiste entre workers/restarts)."""
    if whatsapp_cliente in _MEMORIA_CONVERSACIONES:
        return _MEMORIA_CONVERSACIONES[whatsapp_cliente]
    try:
        api = AirtableOPC()
        existente = api.buscar_por_campo(_TABLA_CONVERSACIONES, "WhatsApp", whatsapp_cliente)
        if existente:
            raw = existente.get("fields", {}).get("Historial", "[]")
            historial = json.loads(raw) if isinstance(raw, str) else []
            _MEMORIA_CONVERSACIONES[whatsapp_cliente] = historial[-_MAX_TURNOS_MEMORIA:]
            return _MEMORIA_CONVERSACIONES[whatsapp_cliente]
    except Exception as exc:
        logger.warning("No se pudo cargar memoria de Airtable: %s", exc)
    _MEMORIA_CONVERSACIONES[whatsapp_cliente] = []
    return []


def _guardar_memoria_persistente(whatsapp_cliente: str, nombre: str = "") -> None:
    """Guarda el historial del cliente en Airtable (sobrevive restarts y workers)."""
    historial = _MEMORIA_CONVERSACIONES.get(whatsapp_cliente, [])
    if not historial:
        return
    # Serializar content (anthropic ContentBlocks → dicts)
    serializable = []
    for turno in historial[-_MAX_TURNOS_MEMORIA:]:
        c = turno.get("content")
        if isinstance(c, str):
            serializable.append({"role": turno["role"], "content": c})
        elif isinstance(c, list):
            blocks = []
            for b in c:
                if hasattr(b, "type"):
                    if b.type == "text":
                        blocks.append({"type": "text", "text": b.text})
                    elif b.type == "tool_use":
                        blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)})
                elif isinstance(b, dict):
                    blocks.append(b)
            serializable.append({"role": turno["role"], "content": blocks})
    try:
        api = AirtableOPC()
        payload = {
            "WhatsApp": whatsapp_cliente,
            "Nombre": nombre or "",
            "Historial": json.dumps(serializable, ensure_ascii=False, default=str)[:95000],
            "Ultima_actividad": datetime.now().isoformat(),
        }
        api.upsert(_TABLA_CONVERSACIONES, "WhatsApp", whatsapp_cliente, payload)
    except Exception as exc:
        logger.warning("No se pudo persistir memoria en Airtable: %s", exc)

HERRAMIENTAS_MONSERRAT = [
    {
        "name": "cotizar_servicio",
        "description": (
            "Calcula la tarifa para un servicio de transporte. Usalo cuando "
            "tengas origen, destino y cantidad de pasajeros. Devuelve precio "
            "en RD$ con recargo nocturno y H1 si aplica."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origen": {"type": "string", "description": "Lugar de salida"},
                "destino": {"type": "string", "description": "Lugar de llegada"},
                "pasajeros": {"type": "integer", "description": "Cantidad de pasajeros"},
                "hora": {"type": "string", "description": "Hora del servicio HH:MM (24h)", "default": "12:00"},
                "es_urgente": {"type": "boolean", "description": "Cliente pide servicio inmediato", "default": False},
            },
            "required": ["origen", "destino", "pasajeros"],
        },
    },
    {
        "name": "crear_reserva",
        "description": (
            "Crea la reserva en Airtable y dispara al Despachador para asignar "
            "chofer. Usar SOLO cuando el cliente confirma con SI."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origen": {"type": "string"},
                "destino": {"type": "string"},
                "pasajeros": {"type": "integer"},
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "hora": {"type": "string", "description": "HH:MM"},
                "nombre_cliente": {"type": "string"},
                "whatsapp_cliente": {"type": "string"},
                "es_urgente": {"type": "boolean", "default": False},
                "es_vip": {"type": "boolean", "default": False},
            },
            "required": ["origen", "destino", "pasajeros", "nombre_cliente", "whatsapp_cliente"],
        },
    },
    {
        "name": "escalar_a_humano",
        "description": (
            "Escala el caso a un humano. Usar para: quejas criticas, casos VIP "
            "con dudas legales, reembolsos, queja sobre chofer especifico, "
            "accidente, o cuando el cliente pide hablar con el dueno. Envia "
            "alerta a nvasquez@emovils.com + WhatsApp +18298610090."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "enum": ["QUEJA", "ACCIDENTE", "REEMBOLSO", "PIDE_DUENO", "VIP_CRITICO", "OTRO"]},
                "resumen": {"type": "string", "description": "1-2 lineas con lo esencial"},
                "urgencia": {"type": "string", "enum": ["CRITICA", "ALTA", "MEDIA"], "default": "ALTA"},
            },
            "required": ["motivo", "resumen"],
        },
    },
    {
        "name": "consultar_estado_reserva",
        "description": (
            "Consulta el estado de una reserva del cliente en Airtable. Usar "
            "cuando el cliente pregunta 'donde esta mi chofer', 'mi reserva', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "whatsapp_cliente": {"type": "string"},
                "codigo_servicio": {"type": "string", "description": "Opcional: SVC-xxx"},
            },
            "required": ["whatsapp_cliente"],
        },
    },
]


def _ejecutar_herramienta(nombre: str, args: dict, whatsapp_cliente: str) -> dict:
    """Despacha tool_use de Claude a la funcion Python real."""
    if nombre == "cotizar_servicio":
        km = estimar_km(args["origen"], args["destino"])
        hora_int = 12
        try:
            hora_int = int(str(args.get("hora", "12:00")).split(":")[0])
        except (ValueError, IndexError):
            pass
        calculo = calcular_tarifa(
            km=km, origen=args["origen"], destino=args["destino"],
            hora=hora_int, pasajeros=int(args["pasajeros"]),
        )
        return {
            "precio_rd": calculo.precio_final,
            "km": km,
            "es_nocturno": calculo.es_nocturno,
            "es_h1": calculo.es_h1,
            "desglose": explicar_tarifa(calculo),
        }

    if nombre == "crear_reserva":
        datos = DatosCotizacion(
            origen=args["origen"], destino=args["destino"],
            pasajeros=int(args["pasajeros"]),
            fecha=args.get("fecha", datetime.now().date().isoformat()),
            hora=args.get("hora", "12:00"),
            es_urgente=args.get("es_urgente", False),
            es_vip=args.get("es_vip", False), completo=True,
        )
        return crear_reserva_y_asignar(
            datos=datos,
            nombre_cliente=args["nombre_cliente"],
            whatsapp_cliente=args["whatsapp_cliente"],
            fecha=args.get("fecha"),
        )

    if nombre == "escalar_a_humano":
        # Aqui dispararia: email a nvasquez@emovils.com + WhatsApp al dueno
        try:
            from opc.whatsapp_green_api import enviar_a_cliente as enviar_whatsapp
            owner_wa = os.getenv("OWNER_WHATSAPP", "+18298610090")
            enviar_whatsapp(
                owner_wa,
                f"🚨 ESCALACION {args.get('urgencia','ALTA')}\n"
                f"Cliente: {whatsapp_cliente}\n"
                f"Motivo: {args['motivo']}\n"
                f"{args['resumen']}",
            )
        except Exception as exc:
            logger.warning("No se pudo enviar WhatsApp al dueno: %s", exc)
        return {"escalado": True, "destino_email": "nvasquez@emovils.com",
                "destino_whatsapp": os.getenv("OWNER_WHATSAPP", "+18298610090")}

    if nombre == "consultar_estado_reserva":
        try:
            api = AirtableOPC()
            registros = api.buscar_por_campo(
                "Servicios", "WhatsApp_cliente", args["whatsapp_cliente"], max_records=3
            )
            if not registros:
                return {"encontrado": False, "mensaje": "No tienes reservas activas."}
            ultimo = registros[0]["fields"]
            return {
                "encontrado": True,
                "estado": ultimo.get("Estado", "Desconocido"),
                "origen": ultimo.get("Origen", ""),
                "destino": ultimo.get("Destino", ""),
                "fecha": ultimo.get("Fecha", ""),
                "chofer_asignado": ultimo.get("Conductor_asignado", ""),
            }
        except Exception as exc:
            return {"encontrado": False, "error": str(exc)}

    return {"error": f"Herramienta desconocida: {nombre}"}


def _procesar_con_claude(
    mensaje: str,
    whatsapp_cliente: str,
    nombre_cliente: str,
    historial: Optional[list[dict]],
) -> ResultadoConversacion:
    """Llama a Claude con tool-use real."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Falta 'anthropic' SDK. pip install anthropic") from exc

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    modelo = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # Memoria PERSISTENTE — carga de Airtable, sobrevive workers/restarts
    memoria = _cargar_memoria_persistente(whatsapp_cliente)
    memoria.append({"role": "user", "content": mensaje})
    if len(memoria) > _MAX_TURNOS_MEMORIA:
        memoria[:] = memoria[-_MAX_TURNOS_MEMORIA:]
    _MEMORIA_CONVERSACIONES[whatsapp_cliente] = memoria

    # Inyectar nombre cliente y fecha hoy en system
    ahora = datetime.now()
    dia_semana = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"][ahora.weekday()]
    system_completo = SYSTEM_PROMPT_MONSERRAT + (
        f"\n\n═══════════════════════════════════════════\n"
        f"DATOS DEL CLIENTE Y CONTEXTO ACTUAL:\n"
        f"═══════════════════════════════════════════\n"
        f"- WhatsApp: {whatsapp_cliente}\n"
        f"- Nombre: {nombre_cliente or 'No registrado aún — preguntáselo si va a reservar'}\n"
        f"- Fecha hoy: {dia_semana} {ahora.strftime('%d de %B de %Y')}\n"
        f"- Hora actual: {ahora.strftime('%H:%M')} ({_franja_horaria(ahora.hour)})\n"
        f"- Turnos previos en este chat: {len(memoria) - 1}\n"
        f"\n"
        f"⚠️ DATOS CRÍTICOS QUE DEBES TENER ANTES DE COTIZAR:\n"
        f"  1. ORIGEN (de dónde sale)\n"
        f"  2. DESTINO (a dónde va)\n"
        f"  3. PASAJEROS (cuántos viajan)\n"
        f"  4. FECHA (qué día) — si dice 'mañana', calcula tú la fecha real\n"
        f"  5. HORA (a qué hora) — si dice 'ahora', es servicio inmediato\n"
        f"\n"
        f"Solo cotiza cuando tengas LOS 5 datos. Si falta alguno, pídelo amablemente.\n"
        f"Si no falta nada → llama cotizar_servicio. Si confirma → crear_reserva.\n"
    )

    accion_disparada = None
    intencion_detectada = Intencion.DESCONOCIDO
    max_iter = 5  # max idas y vueltas con tool-use

    for _ in range(max_iter):
        response = client.messages.create(
            model=modelo, max_tokens=1024, system=system_completo,
            tools=HERRAMIENTAS_MONSERRAT, messages=list(memoria),
        )

        # Si Claude termino sin tool_use → respuesta final
        if response.stop_reason == "end_turn":
            texto_respuesta = ""
            for block in response.content:
                if block.type == "text":
                    texto_respuesta += block.text
            memoria.append({"role": "assistant", "content": response.content})
            # PERSISTIR memoria en Airtable (sobrevive workers/restarts)
            _guardar_memoria_persistente(whatsapp_cliente, nombre_cliente)
            return ResultadoConversacion(
                respuesta=texto_respuesta.strip() or "Disculpa, dime otra vez por favor.",
                intencion=intencion_detectada,
                accion_disparada=accion_disparada,
            )

        # Procesar tool_use
        if response.stop_reason == "tool_use":
            memoria.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    nombre_tool = block.name
                    args_tool = block.input
                    logger.info("Monserrat usa herramienta: %s args=%s", nombre_tool, args_tool)

                    # Mapear a intencion + accion
                    if nombre_tool == "cotizar_servicio":
                        intencion_detectada = Intencion.COTIZAR
                        accion_disparada = "COTIZACION_LISTA"
                    elif nombre_tool == "crear_reserva":
                        intencion_detectada = Intencion.RESERVAR
                        accion_disparada = "RESERVA_CREADA"
                    elif nombre_tool == "escalar_a_humano":
                        intencion_detectada = Intencion.QUEJA
                        accion_disparada = "ESCALAR_DUEÑO"
                    elif nombre_tool == "consultar_estado_reserva":
                        intencion_detectada = Intencion.SEGUIMIENTO

                    resultado = _ejecutar_herramienta(nombre_tool, args_tool, whatsapp_cliente)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(resultado, ensure_ascii=False, default=str),
                    })
            memoria.append({"role": "user", "content": tool_results})
            continue

        break

    # Si llego aqui: agotamos iteraciones
    _guardar_memoria_persistente(whatsapp_cliente, nombre_cliente)
    return ResultadoConversacion(
        respuesta="Dame un momentito, dejame revisar y te respondo.",
        intencion=intencion_detectada,
        accion_disparada=accion_disparada,
    )


def reset_memoria_cliente(whatsapp_cliente: str) -> None:
    """Borra el historial del cliente (util para tests o reset manual)."""
    _MEMORIA_CONVERSACIONES.pop(whatsapp_cliente, None)


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 70)
    print("EMOVILS OPC — Test de Agente Coordinador (Monserrat)")
    print("=" * 70)

    # Casos de prueba — simular conversación real
    casos = [
        ("Hola", "Saludo inicial"),
        ("Hola, cuánto cuesta un viaje del Hotel El Embajador al AILA",
         "Cotización sin pasajeros"),
        ("Cuánto cuesta 2 pasajeros del Embajador al AILA",
         "Cotización con datos completos"),
        ("Necesito un servicio AHORA del aeropuerto a Casa de Campo, somos 4",
         "Solicitud urgente VIP"),
        ("Necesito ir al aeropuerto mañana a las 6am, 3 personas",
         "Reserva programada"),
        ("Mi conductor nunca llegó ayer y perdí mi vuelo",
         "Queja crítica"),
        ("¿Dónde está mi conductor?", "Seguimiento"),
        ("blablabla cosas raras", "Desconocido"),
    ]

    for mensaje, descripcion in casos:
        print(f"\n💬 {descripcion}")
        print(f"   Cliente: \"{mensaje}\"")
        r = procesar_mensaje_entrante(
            mensaje,
            whatsapp_cliente="+18295551234",
            nombre_cliente="Cliente Test",
        )
        print(f"   Intención detectada: {r.intencion}")
        if r.accion_disparada:
            print(f"   Acción: {r.accion_disparada}")
        print(f"   📲 Monserrat responde:")
        for linea in r.respuesta.split("\n"):
            print(f"      {linea}")

    print()
    print("=" * 70)
    print("✓ Agente Coordinador (Monserrat) operativo")
    print("=" * 70)
