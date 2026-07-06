"""
Emovils OPC — MVP (Minimal Viable Product)

Modulo enfocado SOLO en el flujo critico:
  cliente WhatsApp -> cotizar -> confirmar -> pago -> nombre -> tel -> reserva
  -> QR cliente -> asignar conductor/vehiculo -> cliente escanea QR vehiculo
  -> check verde -> conductor escanea QR cliente -> in_progress

No depende de despachador complejo, NCF, reportes, social, etc.
Solo Airtable base prod (app9CGq0LCsEk09r7).
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CONFIG MVP — todo configurable, NO hardcoded mas alla del default
# ═══════════════════════════════════════════════════════════════

# ─── TARIFARIO EMOVILS — modelo ANCLA-DESCUENTO ─────────────────
# El cliente paga el "precio preferencial" (competitivo, ~Uber XL+).
# Monserrat lo presenta junto a una "tarifa de lista" (ancla) mas alta
# para que se perciba el descuento. La tarifa de lista se deriva del
# precio preferencial y del % de descuento del segmento, y queda cerca
# del tarifario premium real de Emovils.
#
# Etapa actual: SOLO vans (sin sedan).
#   - Van Ejecutiva: 1 a 6 pasajeros   (x1.0)
#   - Van Grande:    7 a 10 pasajeros  (x1.40)
#   - Mas de 10:     requiere supervisor
#
# >>> Todos estos numeros son AJUSTABLES en pruebas. <<<

# Ciudad (Gran Santo Domingo)
MINIMUM_FARE_DOP = 350          # piso de ciudad (RD$)
KM_INCLUIDOS_BASE = 3           # km incluidos en la base de ciudad
CIUDAD_POR_KM_ADICIONAL = 40    # RD$/km adicional en ciudad
CIUDAD_MAX_KM = 30              # > este km el viaje se cobra como INTERIOR

# Interior (fuera del area metropolitana)
INTERIOR_BASE_DOP = 1500        # componente fijo de salida (RD$)
INTERIOR_POR_KM = 40            # RD$/km

# Aeropuerto (AILA y similares) — precio FIJO por categoria de van
AEROPUERTO_PRECIO_0_6 = 2500    # Van Ejecutiva 1-6 pax (piso del negocio)
AEROPUERTO_PRECIO_7_10 = 3500   # Van Grande 7-10 pax
AEROPUERTO_LISTA_0_6 = 4000     # ancla = tarifa de mercado de competidores
AEROPUERTO_LISTA_7_10 = 6000
AEROPUERTO_MAX_KM = 45          # traslado de aeropuerto valido hasta este km;
                                # mas lejos se cobra como INTERIOR (evita perder
                                # dinero en un AILA->lejos a precio fijo)

# Servicio por hora (a disposicion)
TARIFA_POR_HORA_DOP = 800       # RD$/hora
HORAS_MINIMAS = 2               # minimo facturable

# Descuento de cada segmento -> construye la tarifa de lista (ancla):
#   tarifa_lista = precio_preferencial / (1 - descuento/100)
DESCUENTO_CIUDAD = 40           # %
DESCUENTO_INTERIOR = 20         # %
DESCUENTO_HORA = 30             # %
# (aeropuerto usa anclas fijas de mercado, ver constantes arriba)

# Recargo nocturno
RECARGO_NOCTURNO_PORCENTAJE = 20
HORARIO_NOCTURNO_INICIO = 23    # 23:00
HORARIO_NOCTURNO_FIN = 6        # 06:00

# Categorias de vehiculo (etapa actual: solo vans, sin sedan)
VAN_EJECUTIVA_MAX_PAX = 6       # 1-6 pax
VAN_GRANDE_MAX_PAX = 10         # 7-10 pax
VAN_GRANDE_MULTIPLIER = 1.40    # recargo Van Grande vs Van Ejecutiva
CAPACIDAD_MAXIMA = 10           # > esto -> supervisor

# Palabras que indican que el viaje toca un aeropuerto (precio fijo de traslado).
# OJO: NO incluir "las americas" sola — "Av. Las Américas" es una avenida larga de
# la ciudad, no el aeropuerto; "aeropuerto"/"aila" ya cubren el AILA sin ambiguedad.
_AEROPUERTO_KEYWORDS = (
    "aila", "aeropuerto", "airport", "sdq", "terminal aerea", "terminal aérea",
)

def _es_entorno_produccion() -> bool:
    """True si corremos en el servidor (Railway), no en una laptop local.
    Railway siempre inyecta estas variables de sistema; en local no existen."""
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or os.getenv("RAILWAY_PROJECT_ID") or os.getenv("RAILWAY_SERVICE_ID")
    )


# Token HMAC que firma los QR de viaje. OBLIGATORIO en producción: sin una clave
# secreta propia (puesta en Railway), cualquiera podría falsificar un token e
# iniciar viajes. En local se permite una clave de desarrollo insegura.
QR_SIGNING_KEY = os.getenv("QR_SIGNING_KEY", "").strip()
if not QR_SIGNING_KEY:
    if _es_entorno_produccion():
        raise RuntimeError(
            "QR_SIGNING_KEY no está configurada. Es obligatoria en producción "
            "para firmar los QR de viaje de forma segura (ponla en Railway)."
        )
    QR_SIGNING_KEY = "dev-only-insecure-qr-key-no-usar-en-produccion"

# Base URL del sistema (para verification_url en QRs)
PUBLIC_BASE_URL = os.getenv("BACKEND_URL", "https://emovils-opc-production.up.railway.app")

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "app9CGq0LCsEk09r7")
AT_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json",
}
AT_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"


# ═══════════════════════════════════════════════════════════════
# TARIFARIO MVP
# ═══════════════════════════════════════════════════════════════

@dataclass
class Cotizacion:
    origen: str
    destino: str
    pasajeros: int
    km_estimados: float            # km REAL medido por Google (nombre por compat)
    vehiculo_recomendado: str      # "Van Ejecutiva", "Van Grande", "supervisor", ""
    precio_rd: int                 # PRECIO PREFERENCIAL (lo que paga el cliente)
    es_nocturno: bool
    requiere_supervisor: bool
    razon_supervisor: str = ""
    moneda: str = "RD$"
    distancia_texto: str = ""       # ej "33,0 km"
    duracion_texto: str = ""        # ej "37 min"
    maps_url: str = ""              # link Google Maps para el chofer
    direccion_no_resuelta: bool = False
    fuente_distancia: str = "google"  # google | manual | ninguna
    tarifa_lista: int = 0          # ANCLA: tarifa de lista (mas alta) que se muestra
    descuento_pct: int = 0         # % de descuento aplicado vs la tarifa de lista
    segmento: str = ""             # "ciudad" | "aeropuerto" | "interior" | "hora"


def _es_hora_nocturna(hora: int) -> bool:
    """11pm-6am"""
    return hora >= HORARIO_NOCTURNO_INICIO or hora < HORARIO_NOCTURNO_FIN


def _es_aeropuerto(texto: str) -> bool:
    """True si el texto menciona un aeropuerto (traslado a precio fijo)."""
    low = (texto or "").lower()
    return any(k in low for k in _AEROPUERTO_KEYWORDS)


def _categoria_van(pasajeros: int) -> tuple[str, float]:
    """Devuelve (nombre_display, multiplicador) segun pasajeros. Solo vans."""
    if pasajeros <= VAN_EJECUTIVA_MAX_PAX:
        return "Van Ejecutiva", 1.0
    return "Van Grande", VAN_GRANDE_MULTIPLIER


def _capacidad_display(vehiculo: str) -> int:
    """Capacidad mostrada al cliente segun la categoria de van."""
    return VAN_GRANDE_MAX_PAX if vehiculo == "Van Grande" else VAN_EJECUTIVA_MAX_PAX


# El campo select 'vehicle_type_mvp' en Airtable SOLO admite estas opciones reales
# (la flota fisica). Las categorias comerciales (Van Ejecutiva / Van Grande) deben
# mapearse a una opcion existente o Airtable rechaza el create con 422.
_VEHICLE_TYPE_AIRTABLE = {
    "Van Ejecutiva": "Van Caravan",
    "Van Grande": "Van Caravan",
    "Van Caravan": "Van Caravan",
    "Hyundai H1": "Hyundai H1",
    "Sedan": "Sedan",
}


def _vehicle_type_airtable(vehiculo: str) -> str:
    """Mapea la categoria comercial a una opcion valida del select de Airtable."""
    return _VEHICLE_TYPE_AIRTABLE.get((vehiculo or "").strip(), "Van Caravan")


def _redondear_10(valor: float) -> int:
    """Redondea al multiplo de 10 mas cercano."""
    return int(round(valor / 10.0) * 10)


def _lista_desde_precio(precio: float, descuento_pct: int) -> float:
    """Tarifa de lista (ancla) a partir del precio preferencial y el % de descuento."""
    if descuento_pct <= 0 or descuento_pct >= 100:
        return precio
    return precio / (1 - descuento_pct / 100)


# Coordenadas "lat, lng" (lo que manda WhatsApp al compartir ubicacion).
_COORD_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def _es_coordenada(texto: str) -> bool:
    """True si el texto es un par lat,lng valido. Una coordenada es un punto EXACTO."""
    m = _COORD_RE.match(texto or "")
    if not m:
        return False
    try:
        lat, lng = float(m.group(1)), float(m.group(2))
        return -90 <= lat <= 90 and -180 <= lng <= 180
    except ValueError:
        return False


# Coordenadas EMBEBIDAS en texto libre. Casos reales:
#   - El cliente escribe "estoy en 18.4861, -69.9312".
#   - El webhook arma, al compartir ubicacion de WhatsApp:
#     "Mi ubicacion es: <lugar> (coordenadas 18.4861, -69.9312)".
# Antes solo se aceptaba la coordenada PURA, asi que estos textos caian a geocoding
# (que falla) y Monserrat terminaba escalando a supervisor. Exigimos decimales en
# AMBOS numeros para no confundir pares como "2, 14" (pasajeros, hora) con coordenadas.
_COORD_FIND_RE = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def _extraer_coordenada(texto: str) -> Optional[str]:
    """Devuelve 'lat,lng' si el texto CONTIENE un par de coordenadas valido
    (aunque venga rodeado de palabras). None si no hay coordenadas."""
    for m in _COORD_FIND_RE.finditer(texto or ""):
        try:
            lat, lng = float(m.group(1)), float(m.group(2))
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return f"{lat},{lng}"
    return None


def _sin_acentos(s: str) -> str:
    """Minusculas sin acentos, para comparar nombres de sectores."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", (s or "").lower())
        if not unicodedata.combining(c)
    ).strip()


# Sectores del Distrito Nacional (la ciudad de Santo Domingo propiamente dicha).
# Para estos conviene anclar a "Distrito Nacional": con solo el nombre — o peor,
# con "Santo Domingo" (la provincia, mucho mas grande) — Google a veces elige un
# punto lejano. Caso real medido: "Naco" -> 64.5 km; "Naco, Distrito Nacional" -> 2.4 km.
# Solo se incluyen sectores que SI pertenecen al Distrito Nacional (NO Los Mina,
# Villa Mella, Sabana Perdida, etc., que son la provincia Santo Domingo).
_SECTORES_DN = frozenset({
    "piantini", "naco", "ensanche naco", "serralles", "evaristo morales",
    "bella vista", "gazcue", "gascue", "la esperilla", "los cacicazgos",
    "el millon", "la julia", "paraiso", "ensanche paraiso", "mirador sur",
    "mirador norte", "el vergel", "quisqueya", "ensanche quisqueya",
    "miramar", "atala", "los prados", "renacimiento", "julieta morales",
})
# Tokens que, si acompanan al sector, no cambian que sea del Distrito Nacional.
_FILLER_UBICACION = frozenset({
    "", "santo domingo", "sto domingo", "sd", "do", "rd",
    "republica dominicana", "dn", "distrito nacional",
})


def _normalizar_direccion(direccion: str) -> str:
    """Agrega contexto de RD si la direccion no menciona el pais — mejora geocoding.

    IMPORTANTE: si es una coordenada (lat,lng) la devuelve TAL CUAL. Agregarle
    ", República Dominicana" rompe el geocoding (Google la interpretaria como el
    pais entero en vez del punto exacto)."""
    d = (direccion or "").strip()
    # Coordenada exacta: pura ("18.48,-69.93") o embebida en texto / ubicacion
    # compartida de WhatsApp. Si hay coordenadas, son un punto EXACTO: se usan tal cual.
    coord = _extraer_coordenada(d)
    if coord:
        return coord
    if _es_coordenada(d):
        return d

    # Desambiguar sectores del Distrito Nacional (evita que "Naco" caiga lejos).
    # Solo si la direccion es ESENCIALMENTE el nombre del sector (sin calle propia).
    partes = [p.strip() for p in d.split(",")]
    if partes:
        sector = _sin_acentos(partes[0])
        resto_filler = all(_sin_acentos(p) in _FILLER_UBICACION for p in partes[1:])
        if sector in _SECTORES_DN and resto_filler:
            return f"{partes[0]}, Distrito Nacional, República Dominicana"

    low = d.lower()
    marcadores = ["republica dominicana", "república dominicana", "rep. dom",
                  "dominican", ", do", ", rd", " do ", " rd "]
    if any(m in low for m in marcadores):
        return d
    return f"{d}, República Dominicana"


def _medir_distancia_google(origen: str, destino: str) -> Optional[dict]:
    """Distancia y duracion REAL via Google Distance Matrix.

    Devuelve None si:
      - no hay API key,
      - Google NO encuentra con precision el origen o el destino (direccion vaga
        o con errores: Google 'adivina' un punto generico y daria un precio falso),
      - la API falla.
    Cuando devuelve None, cotizar() responde direccion_no_resuelta=True y Monserrat
    pide una direccion mas especifica en vez de inventar un precio.
    """
    if not os.getenv("GOOGLE_MAPS_API_KEY"):
        logger.warning("GOOGLE_MAPS_API_KEY no configurada — no se puede medir distancia")
        return None
    try:
        from lib.google_maps import get_distance_matrix, geocode_detallado
        o_norm = _normalizar_direccion(origen)
        d_norm = _normalizar_direccion(destino)

        # 1) Verificar que Google encuentra AMBOS lugares con precision (no adivina).
        #    Las coordenadas (lat,lng) son un punto EXACTO: se aceptan sin geocodificar.
        def _evaluar(texto_norm):
            if _es_coordenada(texto_norm):
                return True, {"preciso": True, "tipo": "coordenada", "formatted": texto_norm}
            g = geocode_detallado(texto_norm)
            return bool(g.get("preciso")), g

        o_ok, go = _evaluar(o_norm)
        d_ok, gd = _evaluar(d_norm)
        if not o_ok or not d_ok:
            logger.info(
                "Geocode impreciso — origen '%s' (%s) destino '%s' (%s)",
                origen, {k: go.get(k) for k in ("preciso", "partial_match", "types", "formatted")},
                destino, {k: gd.get(k) for k in ("preciso", "partial_match", "types", "formatted")},
            )
            return None

        # 2) Medir distancia real
        r = get_distance_matrix(o_norm, d_norm)
        if "error" not in r and r.get("distance_km"):
            return {
                "km": round(float(r["distance_km"]), 1),
                "distancia_texto": r.get("distance_text", ""),
                "duracion_texto": r.get("duration_text", ""),
            }
        logger.warning("Google no resolvio la ruta: %s", r.get("error", r))
    except Exception as e:
        logger.warning("Google Distance Matrix fallo: %s", e)
    return None


def _maps_url(origen: str, destino: str) -> str:
    """Link de Google Maps (direcciones) para que el chofer navegue."""
    try:
        from lib.google_maps import get_directions_url
        return get_directions_url(_normalizar_direccion(origen), _normalizar_direccion(destino))
    except Exception:
        return ""


def cotizar(origen: str, destino: str, pasajeros: int, hora: int,
            km_estimados: Optional[float] = None, usar_google: bool = True) -> Cotizacion:
    """Cotizacion MVP. Mide la distancia REAL con Google Maps.

    REGLA: nunca se inventa la distancia. Si Google no resuelve y no se pasa
    km_estimados manual, devuelve direccion_no_resuelta=True (sin precio)."""
    # Validar pasajeros
    if pasajeros > CAPACIDAD_MAXIMA:
        return Cotizacion(
            origen=origen, destino=destino, pasajeros=pasajeros,
            km_estimados=0.0, vehiculo_recomendado="supervisor",
            precio_rd=0, es_nocturno=False, requiere_supervisor=True,
            razon_supervisor=f"Mas de {CAPACIDAD_MAXIMA} pasajeros — requiere coordinacion especial",
        )

    if pasajeros <= 0:
        pasajeros = 1

    es_nocturno = _es_hora_nocturna(hora)

    # 1) Medir distancia REAL con Google
    km = None
    distancia_texto = duracion_texto = ""
    fuente = "google"
    if usar_google:
        med = _medir_distancia_google(origen, destino)
        if med:
            km = med["km"]
            distancia_texto = med["distancia_texto"]
            duracion_texto = med["duracion_texto"]

    # 2) Fallback SOLO si se paso km manual (endpoint/test). NUNCA inventar.
    if km is None:
        if km_estimados is not None:
            km = float(km_estimados)
            fuente = "manual"
        else:
            return Cotizacion(
                origen=origen, destino=destino, pasajeros=pasajeros,
                km_estimados=0.0, vehiculo_recomendado="", precio_rd=0,
                es_nocturno=es_nocturno, requiere_supervisor=False,
                razon_supervisor="No se pudo calcular la distancia con Google — direccion imprecisa.",
                direccion_no_resuelta=True, fuente_distancia="ninguna",
            )

    # 3) Calculo de precio segun SEGMENTO (ciudad / aeropuerto / interior)
    vehiculo, mult_van = _categoria_van(pasajeros)
    toca_aeropuerto = _es_aeropuerto(origen) or _es_aeropuerto(destino)

    if toca_aeropuerto and km <= AEROPUERTO_MAX_KM:
        # Traslado de aeropuerto: PRECIO FIJO por categoria de van.
        segmento = "aeropuerto"
        if pasajeros <= VAN_EJECUTIVA_MAX_PAX:
            precio = float(AEROPUERTO_PRECIO_0_6)
            lista = float(AEROPUERTO_LISTA_0_6)
        else:
            precio = float(AEROPUERTO_PRECIO_7_10)
            lista = float(AEROPUERTO_LISTA_7_10)
        descuento = int(round((1 - precio / lista) * 100)) if lista > precio else 0
    elif km > CIUDAD_MAX_KM:
        # Interior: tarifa de salida + km, x categoria de van.
        segmento = "interior"
        precio = (INTERIOR_BASE_DOP + INTERIOR_POR_KM * km) * mult_van
        descuento = DESCUENTO_INTERIOR
        lista = _lista_desde_precio(precio, descuento)
    else:
        # Ciudad (Gran Santo Domingo): base + km adicional, x categoria de van.
        segmento = "ciudad"
        km_extra = max(0, km - KM_INCLUIDOS_BASE)
        precio = (MINIMUM_FARE_DOP + km_extra * CIUDAD_POR_KM_ADICIONAL) * mult_van
        descuento = DESCUENTO_CIUDAD
        lista = _lista_desde_precio(precio, descuento)

    # Recargo nocturno: sobre el precio Y el ancla por igual.
    if es_nocturno:
        factor = 1 + RECARGO_NOCTURNO_PORCENTAJE / 100
        precio *= factor
        lista *= factor

    # Piso de ciudad
    precio = max(precio, MINIMUM_FARE_DOP)
    precio_rd = _redondear_10(precio)
    tarifa_lista = _redondear_10(lista)
    # La lista solo tiene sentido si queda por ENCIMA del precio preferencial.
    if tarifa_lista <= precio_rd:
        tarifa_lista = 0
        descuento = 0

    return Cotizacion(
        origen=origen, destino=destino, pasajeros=pasajeros,
        km_estimados=km, vehiculo_recomendado=vehiculo,
        precio_rd=precio_rd, es_nocturno=es_nocturno,
        requiere_supervisor=False, distancia_texto=distancia_texto,
        duracion_texto=duracion_texto, maps_url=_maps_url(origen, destino),
        fuente_distancia=fuente, tarifa_lista=tarifa_lista,
        descuento_pct=descuento, segmento=segmento,
    )


def cotizar_por_hora(horas: float, pasajeros: int, hora: int) -> Cotizacion:
    """Servicio por hora (a disposicion). RD$/hora, minimo HORAS_MINIMAS horas."""
    if pasajeros > CAPACIDAD_MAXIMA:
        return Cotizacion(
            origen="(por hora)", destino="(por hora)", pasajeros=pasajeros,
            km_estimados=0.0, vehiculo_recomendado="supervisor", precio_rd=0,
            es_nocturno=False, requiere_supervisor=True, segmento="hora",
            razon_supervisor=f"Mas de {CAPACIDAD_MAXIMA} pasajeros — requiere coordinacion especial",
        )
    if pasajeros <= 0:
        pasajeros = 1
    horas_fact = max(float(HORAS_MINIMAS), float(horas or 0))
    es_nocturno = _es_hora_nocturna(hora)
    vehiculo, mult_van = _categoria_van(pasajeros)

    precio = TARIFA_POR_HORA_DOP * horas_fact * mult_van
    lista = _lista_desde_precio(precio, DESCUENTO_HORA)
    if es_nocturno:
        factor = 1 + RECARGO_NOCTURNO_PORCENTAJE / 100
        precio *= factor
        lista *= factor

    precio_rd = _redondear_10(precio)
    tarifa_lista = _redondear_10(lista)
    if tarifa_lista <= precio_rd:
        tarifa_lista = 0

    return Cotizacion(
        origen="(servicio por hora)", destino=f"{horas_fact:g} horas",
        pasajeros=pasajeros, km_estimados=0.0, vehiculo_recomendado=vehiculo,
        precio_rd=precio_rd, es_nocturno=es_nocturno, requiere_supervisor=False,
        tarifa_lista=tarifa_lista,
        descuento_pct=DESCUENTO_HORA if tarifa_lista else 0,
        segmento="hora", fuente_distancia="na",
    )


# ═══════════════════════════════════════════════════════════════
# QR — generacion y validacion
# ═══════════════════════════════════════════════════════════════

def _firmar(payload: str) -> str:
    return hmac.new(
        QR_SIGNING_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:24]


def generar_qr_cliente(booking_id: str) -> tuple[str, str]:
    """Devuelve (token, url) para el QR del cliente.
    El QR contiene solo: booking_id + token + URL de verificacion."""
    raw = secrets.token_urlsafe(18)
    token = f"{raw}.{_firmar(booking_id + ':' + raw)}"
    url = f"{PUBLIC_BASE_URL}/qr/cliente/{booking_id}?t={token}"
    return token, url


def generar_qr_vehiculo(vehicle_id: str) -> tuple[str, str]:
    """Devuelve (token, url) para el QR fisico del vehiculo."""
    raw = secrets.token_urlsafe(18)
    token = f"{raw}.{_firmar(vehicle_id + ':' + raw)}"
    url = f"{PUBLIC_BASE_URL}/vehicle/verify/{vehicle_id}?t={token}"
    return token, url


# ── Imagen QR escaneable (PNG en memoria) — para pasajero y vehiculo ──

def generar_qr_png(data: str, box_size: int = 10, border: int = 2) -> bytes:
    """Genera un QR escaneable (PNG en memoria) que codifica `data`."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M,
                       box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def validar_token_cliente(booking_id: str, token: str) -> bool:
    """Verifica firma HMAC del token del cliente."""
    try:
        raw, firma = token.rsplit(".", 1)
        return hmac.compare_digest(firma, _firmar(booking_id + ':' + raw))
    except Exception:
        return False


def validar_token_vehiculo(vehicle_id: str, token: str) -> bool:
    try:
        raw, firma = token.rsplit(".", 1)
        return hmac.compare_digest(firma, _firmar(vehicle_id + ':' + raw))
    except Exception:
        return False


def firmar_driver(driver_id: str) -> str:
    """Token-capacidad del chofer: prueba que recibió su enlace oficial.
    Es estable por chofer (el mismo enlace sirve toda su jornada) y nadie puede
    falsificarlo sin la llave secreta. Lo usan la página de GPS y la de
    disponibilidad para que solo el chofer real reporte su ubicación/estado."""
    return _firmar("driver:" + str(driver_id or ""))


def validar_token_driver(driver_id: str, token: str) -> bool:
    """Verifica el token-capacidad del chofer (comparación a prueba de timing)."""
    try:
        return hmac.compare_digest(str(token or ""), firmar_driver(driver_id))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# AIRTABLE CRUD (directo, sin dependencia de modulos viejos)
# ═══════════════════════════════════════════════════════════════

# Sanea un valor antes de interpolarlo en una fórmula de Airtable
# (filterByFormula). Todos los valores que buscamos son identificadores
# (booking_id, driver_id, vehicle_id, teléfono, tipo, estado) que NUNCA llevan
# comillas, llaves ni paréntesis; quitar esos caracteres neutraliza cualquier
# intento de "colar" texto para alterar la búsqueda y leer datos de otros
# clientes, sin afectar los datos legítimos.
_AF_NO_SEGUROS = re.compile(r"[^A-Za-z0-9 _.\-+:@]")


def _af(valor) -> str:
    return _AF_NO_SEGUROS.sub("", str(valor if valor is not None else ""))


def _at_get(tabla: str, formula: str = "", max_records: int = 100) -> list[dict]:
    params = {"maxRecords": max_records}
    if formula:
        params["filterByFormula"] = formula
    r = requests.get(f"{AT_URL}/{tabla}", headers=AT_HEADERS, params=params, timeout=15)
    if not r.ok:
        logger.warning("Airtable GET %s: %s", tabla, r.text[:200])
        return []
    return r.json().get("records", [])


def _at_create(tabla: str, fields: dict) -> dict:
    def _post(f: dict):
        return requests.post(
            f"{AT_URL}/{tabla}",
            headers=AT_HEADERS,
            json={"records": [{"fields": f}]},
            timeout=15,
        )

    r = _post(fields)
    # Red de seguridad: si un select rechaza un valor no existente, quitamos los
    # campos con ese valor y reintentamos UNA vez para que la reserva no se pierda.
    if r.status_code == 422 and "INVALID_MULTIPLE_CHOICE_OPTIONS" in r.text:
        m = re.search(r'option\s+\\*"+([^"\\]+)\\*"+', r.text)
        bad = m.group(1) if m else None
        if bad is not None:
            depurado = {k: v for k, v in fields.items() if v != bad}
            logger.warning(
                "Airtable %s: opcion invalida '%s' — reintento sin los campos afectados",
                tabla, bad,
            )
            r = _post(depurado)
    if not r.ok:
        logger.error("Airtable CREATE %s: %s", tabla, r.text[:300])
        raise RuntimeError(f"Airtable create fallo: {r.status_code}")
    return r.json()["records"][0]


def _at_update(tabla: str, record_id: str, fields: dict) -> dict:
    r = requests.patch(
        f"{AT_URL}/{tabla}/{record_id}",
        headers=AT_HEADERS,
        json={"fields": fields},
        timeout=15,
    )
    if not r.ok:
        logger.error("Airtable UPDATE %s: %s", tabla, r.text[:300])
        raise RuntimeError(f"Airtable update fallo: {r.status_code}")
    return r.json()


# ═══════════════════════════════════════════════════════════════
# RESERVAR + ASIGNAR
# ═══════════════════════════════════════════════════════════════

def crear_reserva(
    customer_name: str,
    customer_phone: str,
    origin: str,
    destination: str,
    passengers: int,
    final_price: int,
    payment_method: str,
    service_date: Optional[str] = None,
    service_time: Optional[str] = None,
    vehicle_type: str = "Van Ejecutiva",
    distance_km: Optional[float] = None,
) -> dict:
    """Crea booking en Airtable. Devuelve {booking_id, record_id, qr_url, qr_token}."""
    booking_id = "EMV-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2).upper()

    # Distancia real (para registro y navegacion del chofer)
    if distance_km is None:
        med = _medir_distancia_google(origin, destination)
        distance_km = med["km"] if med else None

    pm = payment_method.lower()
    if pm == "cash":
        payment_status = "cash_pending"
        booking_status = "confirmed"
    elif pm == "card":
        payment_status = "card_pending"
        booking_status = "confirmed"
    elif pm == "online":
        payment_status = "pending"
        booking_status = "pending_payment"
    else:
        payment_status = "pending"
        booking_status = "supervisor_review"

    qr_token, qr_url = generar_qr_cliente(booking_id)
    now = datetime.now().isoformat()

    # --- INMEDIATO vs PROGRAMADO ------------------------------------------
    # Interpretamos la hora de recogida que pidio el cliente.
    #   pickup_dt = None  -> es para AHORA (taxi mas cercano ya)
    #   pickup_dt > 2h     -> es PROGRAMADO (se agenda y se recuerda 2h antes)
    texto_hora = (service_time or "").strip()
    # Si llega la fecha por separado y la hora no trae fecha propia, las unimos.
    if service_date and not re.search(r"\d{4}-\d{2}-\d{2}", texto_hora):
        _hm = _parse_hora_texto(texto_hora)
        if _hm:
            texto_hora = f"{service_date} {_hm[0]:02d}:{_hm[1]:02d}"
    pickup_dt = _parse_pickup_dt(texto_hora)
    es_prog = _es_programado(pickup_dt)
    # service_time: guardamos el texto humano (lindo en los mensajes).
    service_time_txt = (service_time or "").strip() or "ahora"
    # Travel_Date (dateTime): hora exacta de recogida si es programado; si es
    # inmediato, el instante de creacion (marca temporal, queda en el pasado).
    travel_date_val = pickup_dt.isoformat() if pickup_dt is not None else _now_utc().isoformat()

    fields = {
        "Booking_ID": booking_id,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "Pickup_Location": origin,
        "Dropoff_Location": destination,
        "Passengers": passengers,
        "Distance_KM": distance_km if distance_km is not None else 0,
        "final_price": final_price,
        "currency": "RD$",
        "payment_method": pm,
        "payment_status": payment_status,
        "booking_status": booking_status,
        "vehicle_type_mvp": _vehicle_type_airtable(vehicle_type),
        "customer_qr_token": qr_token,
        "customer_qr_url": qr_url,
        "customer_qr_status": "active",
        "vehicle_verification_status": "not_started",
        "pickup_confirmed": False,
        "service_time": service_time_txt,
        "Travel_Date": travel_date_val,
        "Created_At": now,
    }
    rec = _at_create("Bookings", fields)
    logger.info("✓ Booking creado: %s (record %s)", booking_id, rec["id"])

    # Despacho automatico. INMEDIATO = taxi mas cercano YA; PROGRAMADO = se le
    # ofrece a un chofer para que lo agende (mismo flujo, con TTL/mensaje propios
    # que decide _despachar_siguiente segun la hora de recogida). Nunca rompe la reserva.
    despacho = None
    if booking_status == "confirmed":
        try:
            despacho = iniciar_despacho(booking_id)
            logger.info("Despacho %s [%s]: %s", booking_id,
                        "PROGRAMADO " + _fmt_dt_rd(pickup_dt) if es_prog else "INMEDIATO",
                        despacho.get("offer_status") or despacho.get("razon"))
        except Exception as e:
            logger.warning("Despacho fallo para %s: %s", booking_id, e)

    return {
        "booking_id": booking_id,
        "record_id": rec["id"],
        "qr_url": qr_url,
        "qr_token": qr_token,
        "booking_status": booking_status,
        "programado": es_prog,
        "pickup_dt": pickup_dt.isoformat() if pickup_dt else None,
        "despacho": despacho,
    }


def asignar_conductor_y_vehiculo(booking_id: str, vehicle_type: str) -> dict:
    """Asigna el primer vehiculo activo con chofer disponible.

    Intenta primero el tipo solicitado (vehicle_type); si no hay coincidencia
    (p. ej. la flota real usa otros nombres de tipo), cae a CUALQUIER vehiculo
    activo. Asi, renombrar las categorias comerciales (Van Ejecutiva / Van
    Grande) NO rompe la asignacion con la flota real existente.
    """
    def _intentar(vehiculos):
        """Asigna el primer (vehiculo, chofer disponible) de la lista.
        Devuelve el dict de exito, un dict de error duro, o None si no encaja."""
        for v in vehiculos:
            vf = v["fields"]
            driver_id = vf.get("assigned_driver_id", "")
            if not driver_id:
                continue
            drivers = _at_get(
                "Drivers",
                formula=f"AND({{driver_id}}='{_af(driver_id)}', {{driver_status}}='available')",
                max_records=1,
            )
            if not drivers:
                continue

            d = drivers[0]
            df = d["fields"]
            booking = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
            if not booking:
                return {"asignado": False, "razon": f"Booking {booking_id} no existe"}

            _at_update("Bookings", booking[0]["id"], {
                "driver_id": driver_id,
                "vehicle_id": vf.get("vehicle_id", ""),
                "Driver_Name": df.get("driver_name", ""),
                "Driver_Phone": df.get("driver_phone", ""),
                "Driver_Vehicle": f"{vf.get('vehicle_brand','')} {vf.get('vehicle_model','')} {vf.get('vehicle_plate','')}",
            })
            _at_update("Drivers", d["id"], {"driver_status": "busy"})
            return {
                "asignado": True,
                "driver_id": driver_id,
                "driver_name": df.get("driver_name", ""),
                "driver_phone": df.get("driver_phone", ""),
                "vehicle_id": vf.get("vehicle_id", ""),
                "vehicle_plate": vf.get("vehicle_plate", ""),
                "vehicle_color": vf.get("vehicle_color", ""),
                "vehicle_brand": vf.get("vehicle_brand", ""),
                "vehicle_model": vf.get("vehicle_model", ""),
            }
        return None

    # 1) Intentar por el tipo solicitado
    typed = _at_get(
        "Vehicles",
        formula=f"AND({{vehicle_type}}='{_af(vehicle_type)}', {{vehicle_status}}='active')",
        max_records=10,
    )
    res = _intentar(typed)
    if res is not None:
        return res

    # 2) Fallback tolerante: cualquier vehiculo activo con chofer disponible
    logger.info("Sin match exacto para tipo '%s' — fallback a cualquier vehiculo activo", vehicle_type)
    activos = _at_get("Vehicles", formula="{vehicle_status}='active'", max_records=25)
    res = _intentar(activos)
    if res is not None:
        return res

    return {"asignado": False, "razon": "Sin choferes disponibles en este momento"}


# ═══════════════════════════════════════════════════════════════
# DESPACHO POR CERCANIA — chofer mas cercano + oferta con aceptacion
# ═══════════════════════════════════════════════════════════════
#
# Flujo:
#   1) El chofer comparte su ubicacion por WhatsApp una vez -> queda 'available'
#      con current_lat/current_lng/location_updated_at.
#   2) Al confirmarse una reserva se ofrece la carrera al chofer disponible
#      MAS CERCANO al punto de recogida (Haversine).
#   3) El chofer responde ACEPTO / RECHAZO. Si rechaza o no responde en
#      OFERTA_TTL_SEG, la oferta se pasa automaticamente al siguiente mas
#      cercano (excluyendo a quienes ya la recibieron).
#
# Estado persistido en Bookings: offer_status, offered_driver_id,
# offer_expires_at, offer_attempts, offer_log.

OFERTA_TTL_SEG = 60          # segundos que tiene el chofer para responder (viaje YA)
# Por decision del negocio el chofer queda EN LINEA hasta desconectarse (panel
# web) o entrar en carrera (busy); su ubicacion NO caduca por defecto. Este
# valor solo aplica si se pasa max_edad_horas explicitamente a choferes_cercanos.
UBICACION_MAX_HORAS = 12

# --- Viajes PROGRAMADOS (reserva para mas tarde) -------------------------
# Zona horaria de Republica Dominicana (AST = UTC-4, sin horario de verano).
TZ_RD = timezone(timedelta(hours=-4))
# Si la recogida es a mas de este margen en el futuro => es PROGRAMADO.
# Si es para "ahora" o dentro de las proximas 2h => es INMEDIATO (taxi mas cercano ya).
UMBRAL_PROGRAMADA_MIN = 120
# Un viaje programado se le ofrece al chofer con mas tiempo para aceptar (30 min).
OFERTA_TTL_PROGRAMADA_SEG = 1800
# Cuanto antes de la recogida se le recuerda al chofer (con todos los detalles).
RECORDATORIO_ANTES_MIN = 120
# Marca que se escribe en offer_log cuando ya se envio el recordatorio (no repetir).
TAG_RECORDATORIO = "RECORDATORIO_2H_ENVIADO"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _log_append(bf: dict, msg: str) -> str:
    prev = (bf.get("offer_log") or "").strip()
    line = f"{_now_utc().isoformat()} {msg}"
    return (prev + "\n" + line).strip() if prev else line


def _strip_acentos(s) -> str:
    """quita acentos y pasa a minuscula (para comparar palabras sin tildes)."""
    t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return t.strip().lower()


def _parse_hora_texto(txt: str):
    """De un texto tipo '6pm', '6:30 pm', '23:34', '8am' saca (hora, minuto) 24h.
    Devuelve None si no encuentra una hora valida."""
    t = _strip_acentos(txt).replace(".", "")
    # busca patron hora[:min] opcional am/pm
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m|p\.m)?", t)
    if not m:
        return None
    h = int(m.group(1))
    mnt = int(m.group(2) or 0)
    ampm = (m.group(3) or "").replace(".", "")
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        return None
    return h, mnt


def _parse_pickup_dt(service_time, ahora=None) -> Optional[datetime]:
    """Interpreta el texto de hora de recogida y devuelve un datetime AWARE (UTC),
    o None si es para AHORA MISMO (inmediato) o no se entiende.

    Acepta:
      - "" / None / "ahora" / "ya" / "lo antes posible"  -> None (inmediato)
      - ISO "2026-06-15 14:00" o "2026-06-15T14:00"        -> esa fecha/hora (RD)
      - "hoy 6pm", "manana 8:30am", "pasado manana 14:00"  -> dia relativo + hora
      - "6pm", "23:34", "8am"                              -> hoy a esa hora
                                                              (manana si ya paso)
    """
    if ahora is None:
        ahora = _now_utc()
    ahora_rd = ahora.astimezone(TZ_RD)
    t = _strip_acentos(service_time)
    if not t or t in ("ahora", "ya", "ahora mismo", "lo antes posible", "cuanto antes", "now"):
        return None

    # 1) ISO explicito (con fecha) ej "2026-06-15 14:00" o "2026-06-15T14:00:00"
    m_iso = re.search(r"(\d{4})-(\d{2})-(\d{2})[ t]+(\d{1,2}):(\d{2})", t)
    if m_iso:
        y, mo, d, h, mi = (int(g) for g in m_iso.groups())
        try:
            dt = datetime(y, mo, d, h, mi, tzinfo=TZ_RD)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    # 2) dia relativo
    base = ahora_rd
    dia_offset = None
    if re.search(r"\bpasado\s+manana\b", t):
        dia_offset = 2
    elif re.search(r"\bmanana\b", t):
        dia_offset = 1
    elif re.search(r"\bhoy\b", t):
        dia_offset = 0

    hm = _parse_hora_texto(t)
    if hm is None:
        # sin hora reconocible -> no podemos programar, tratar como inmediato
        return None
    h, mi = hm

    if dia_offset is None:
        # solo hora (ej "6pm"): hoy; si ya paso, manana
        cand = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand <= ahora_rd:
            cand = cand + timedelta(days=1)
    else:
        dia = base + timedelta(days=dia_offset)
        cand = dia.replace(hour=h, minute=mi, second=0, microsecond=0)
    return cand.astimezone(timezone.utc)


_DIAS_RD = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
_MESES_RD = ["ene", "feb", "mar", "abr", "may", "jun",
             "jul", "ago", "sep", "oct", "nov", "dic"]


def _fmt_dt_rd(dt: Optional[datetime], ahora=None) -> str:
    """datetime UTC -> texto humano en hora RD: 'hoy 11:34 PM', 'manana 6:00 AM',
    'lun 16 jun 6:00 PM'. Sirve para mensajes al chofer/cliente."""
    if dt is None:
        return "ahora"
    if ahora is None:
        ahora = _now_utc()
    loc = dt.astimezone(TZ_RD)
    hoy = ahora.astimezone(TZ_RD).date()
    delta_dias = (loc.date() - hoy).days
    h = loc.hour
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    hora_txt = f"{h12}:{loc.minute:02d} {ampm}"
    if delta_dias == 0:
        return f"hoy {hora_txt}"
    if delta_dias == 1:
        return f"manana {hora_txt}"
    dia = _DIAS_RD[loc.weekday()]
    mes = _MESES_RD[loc.month - 1]
    return f"{dia} {loc.day} {mes} {hora_txt}"


def _es_programado(pickup_dt: Optional[datetime], ahora=None) -> bool:
    """True si la recogida cae a mas de UMBRAL_PROGRAMADA_MIN en el futuro."""
    if pickup_dt is None:
        return False
    if ahora is None:
        ahora = _now_utc()
    return pickup_dt - ahora > timedelta(minutes=UMBRAL_PROGRAMADA_MIN)


def _norm_tel(tel) -> str:
    return re.sub(r"\D", "", str(tel or ""))


_DRIVERS_CACHE: dict = {"ts": 0.0, "data": []}
_DRIVERS_CACHE_TTL = 30.0  # seg — el listado de choferes cambia poco


def _drivers_cached() -> list[dict]:
    """Listado de choferes con cache corto (se usa en CADA mensaje entrante
    para distinguir chofer de cliente; evita pegarle a Airtable por mensaje)."""
    import time
    ahora = time.time()
    if ahora - _DRIVERS_CACHE["ts"] > _DRIVERS_CACHE_TTL or not _DRIVERS_CACHE["data"]:
        _DRIVERS_CACHE["data"] = _at_get("Drivers", max_records=200)
        _DRIVERS_CACHE["ts"] = ahora
    return _DRIVERS_CACHE["data"]


def _buscar_driver_por_tel(tel: str) -> list[dict]:
    """Encuentra al chofer comparando los ultimos 10 digitos del telefono."""
    last10 = _norm_tel(tel)[-10:]
    if not last10:
        return []
    return [d for d in _drivers_cached()
            if _norm_tel(d["fields"].get("driver_phone", ""))[-10:] == last10]


def _norm_palabra(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower().strip(" .,!?¿¡")


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _geocode_pickup(direccion: str):
    """(lat, lng) del punto de recogida via Google, o None."""
    try:
        from lib.google_maps import geocode
        g = geocode(_normalizar_direccion(direccion))
        if g and g.get("lat") is not None and g.get("lng") is not None:
            return float(g["lat"]), float(g["lng"])
    except Exception as e:
        logger.warning("Geocode pickup fallo: %s", e)
    return None


def actualizar_ubicacion_chofer(driver_phone: str, lat: float, lng: float) -> dict:
    """El chofer comparte su ubicacion (WhatsApp). Se guarda y queda 'available'."""
    drivers = _buscar_driver_por_tel(driver_phone)
    if not drivers:
        return {"ok": False, "es_chofer": False, "razon": "telefono no registrado como chofer"}
    d = drivers[0]
    df = d["fields"]
    estado = df.get("driver_status")
    # Compartir ubicacion = quedar EN LINEA (disponible), salvo suspendido, y
    # mantenerse asi hasta desconectarse en la web o entrar en carrera (busy).
    # Cada ping (incl. 'ubicacion en tiempo real' de WhatsApp) refresca la
    # posicion, asi Monserrat siempre sabe donde esta el chofer.
    nuevo = "suspended" if estado == "suspended" else "available"
    _at_update("Drivers", d["id"], {
        "current_lat": float(lat),
        "current_lng": float(lng),
        "location_updated_at": _now_utc().isoformat(),
        "driver_status": nuevo,
    })
    return {"ok": True, "es_chofer": True, "driver_id": df.get("driver_id"),
            "driver_name": df.get("driver_name"), "driver_status": nuevo}


def actualizar_ubicacion_chofer_por_id(driver_id: str, lat: float, lng: float) -> dict:
    """Igual que actualizar_ubicacion_chofer pero identifica al chofer por su
    driver_id. Lo usa la pagina GPS del navegador (manda posicion cada 3-5 s)."""
    drivers = _at_get("Drivers", formula=f"{{driver_id}}='{_af(driver_id)}'", max_records=1)
    if not drivers:
        return {"ok": False, "razon": "chofer no encontrado"}
    d = drivers[0]
    df = d["fields"]
    estado = df.get("driver_status")
    # El GPS de la pagina corre en SEGUNDO PLANO: solo refresca la posicion, NO
    # debe cambiar el estado del chofer. En particular NO debe REVIVIR a un chofer
    # que se desconecto (offline) ni sacar de 'suspended'/'busy'. Para volver a EN
    # LINEA el chofer debe marcarse disponible en su panel o compartir su ubicacion
    # por WhatsApp a proposito (eso si es una accion deliberada). Antes esta funcion
    # ponia 'available' a cualquiera que no fuera suspended/busy, asi que un chofer
    # offline con la pagina abierta "volvia solo" y le entraban carreras sin estar
    # en linea. Por eso 'offline' ahora se respeta.
    nuevo = estado if estado in ("suspended", "busy", "offline") else "available"
    _at_update("Drivers", d["id"], {
        "current_lat": float(lat),
        "current_lng": float(lng),
        "location_updated_at": _now_utc().isoformat(),
        "driver_status": nuevo,
    })
    return {"ok": True, "driver_id": driver_id,
            "driver_name": df.get("driver_name"), "driver_status": nuevo}


def cambiar_disponibilidad_chofer(driver_id: str, disponible: bool) -> dict:
    """Panel web del chofer: marcarse 'No disponible' (offline) o volver."""
    drivers = _at_get("Drivers", formula=f"{{driver_id}}='{_af(driver_id)}'", max_records=1)
    if not drivers:
        return {"ok": False, "razon": "chofer no encontrado"}
    nuevo = "available" if disponible else "offline"
    _at_update("Drivers", drivers[0]["id"], {"driver_status": nuevo})
    return {"ok": True, "driver_id": driver_id, "driver_status": nuevo}


# ── Alta de choferes (dashboard de registro) ─────────────────────
_MAX_PAX_POR_TIPO = {"Sedan": 4, "Van Caravan": 6, "Hyundai H1": 8}


def _siguiente_codigo(table: str, prefix: str, field: str) -> str:
    """Devuelve el proximo codigo correlativo tipo DRV-003 / VEH-007."""
    mx = 0
    for r in _at_get(table, max_records=1000):
        m = re.match(rf"{prefix}-(\d+)$", str(r["fields"].get(field, "") or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"{prefix}-{mx + 1:03d}"


def _fmt_tel_rd(telefono: str) -> str:
    """Normaliza a formato +1XXXXXXXXXX (Rep. Dominicana) cuando aplica."""
    d = _norm_tel(telefono)
    if len(d) == 10:
        d = "1" + d
    return "+" + d if d else ""


def registrar_chofer(nombre: str, telefono: str, vehiculo_tipo: str,
                     placa: str = "", marca: str = "", modelo: str = "",
                     color: str = "", anio=None, max_pax=None,
                     driver_type: str = "propio") -> dict:
    """Crea un chofer + su vehiculo y los enlaza. El chofer queda 'offline' y
    pasa a EN LINEA automaticamente cuando comparte su ubicacion por WhatsApp."""
    nombre = (nombre or "").strip()
    tel = _fmt_tel_rd(telefono)
    if not nombre or len(_norm_tel(tel)) < 10:
        return {"ok": False, "razon": "Nombre y teléfono válido son obligatorios."}
    if vehiculo_tipo not in _MAX_PAX_POR_TIPO:
        return {"ok": False, "razon": "Tipo de vehículo inválido."}
    if _buscar_driver_por_tel(tel):
        return {"ok": False, "razon": "Ya existe un chofer con ese teléfono."}

    driver_id = _siguiente_codigo("Drivers", "DRV", "driver_id")
    vehicle_id = _siguiente_codigo("Vehicles", "VEH", "vehicle_id")
    try:
        max_pax = int(max_pax) if max_pax else _MAX_PAX_POR_TIPO[vehiculo_tipo]
    except (TypeError, ValueError):
        max_pax = _MAX_PAX_POR_TIPO[vehiculo_tipo]
    now = _now_utc().isoformat()

    vfields = {
        "vehicle_id": vehicle_id, "vehicle_type": vehiculo_tipo,
        "vehicle_status": "active", "assigned_driver_id": driver_id,
        "max_passengers": max_pax, "created_at": now, "updated_at": now,
    }
    if placa:  vfields["vehicle_plate"] = placa.strip().upper()
    if marca:  vfields["vehicle_brand"] = marca.strip()
    if modelo: vfields["vehicle_model"] = modelo.strip()
    if color:  vfields["vehicle_color"] = color.strip()
    if anio:
        try: vfields["vehicle_year"] = int(anio)
        except (TypeError, ValueError): pass
    _at_create("Vehicles", vfields)

    _at_create("Drivers", {
        "driver_id": driver_id, "driver_name": nombre, "driver_phone": tel,
        "driver_status": "offline", "assigned_vehicle_id": vehicle_id,
        "driver_type": driver_type if driver_type in ("propio", "afiliado") else "propio",
        "created_at": now, "updated_at": now,
    })
    _DRIVERS_CACHE["ts"] = 0.0  # invalida cache para que aparezca al instante
    return {"ok": True, "driver_id": driver_id, "vehicle_id": vehicle_id,
            "driver_name": nombre, "driver_phone": tel,
            "vehicle_type": vehiculo_tipo, "driver_status": "offline"}


def listar_choferes() -> list[dict]:
    """Lista de choferes para el dashboard de administración."""
    out = []
    for d in _at_get("Drivers", max_records=1000):
        f = d["fields"]
        out.append({
            "driver_id": f.get("driver_id", ""),
            "driver_name": f.get("driver_name", ""),
            "driver_phone": f.get("driver_phone", ""),
            "driver_status": f.get("driver_status", ""),
            "assigned_vehicle_id": f.get("assigned_vehicle_id", ""),
            "tiene_ubicacion": f.get("current_lat") is not None and f.get("current_lng") is not None,
        })
    out.sort(key=lambda x: str(x.get("driver_id") or ""))
    return out


def obtener_chofer(driver_id: str) -> dict:
    """Datos completos del chofer + su vehiculo (para el formulario de edicion)."""
    ds = _at_get("Drivers", formula=f"{{driver_id}}='{_af(driver_id)}'", max_records=1)
    if not ds:
        return {"ok": False, "razon": "Chofer no encontrado."}
    df = ds[0]["fields"]
    veh_id = df.get("assigned_vehicle_id", "") or ""
    vf = {}
    if veh_id:
        vs = _at_get("Vehicles", formula=f"{{vehicle_id}}='{_af(veh_id)}'", max_records=1)
        if vs:
            vf = vs[0]["fields"]
    return {
        "ok": True,
        "driver_id": df.get("driver_id", ""),
        "driver_name": df.get("driver_name", ""),
        "driver_phone": df.get("driver_phone", ""),
        "driver_type": df.get("driver_type", "propio"),
        "driver_status": df.get("driver_status", "offline"),
        "vehicle_id": veh_id,
        "vehiculo_tipo": vf.get("vehicle_type", ""),
        "placa": vf.get("vehicle_plate", ""),
        "marca": vf.get("vehicle_brand", ""),
        "modelo": vf.get("vehicle_model", ""),
        "color": vf.get("vehicle_color", ""),
        "anio": vf.get("vehicle_year", ""),
        "max_pax": vf.get("max_passengers", ""),
    }


def actualizar_chofer(driver_id: str, nombre=None, telefono=None, driver_type=None,
                      driver_status=None, vehiculo_tipo=None, placa=None, marca=None,
                      modelo=None, color=None, anio=None, max_pax=None) -> dict:
    """Edita los campos de un chofer y/o de su vehiculo desde el dashboard."""
    ds = _at_get("Drivers", formula=f"{{driver_id}}='{_af(driver_id)}'", max_records=1)
    if not ds:
        return {"ok": False, "razon": "Chofer no encontrado."}
    drec = ds[0]
    df = drec["fields"]

    dfields = {}
    if nombre is not None and str(nombre).strip():
        dfields["driver_name"] = str(nombre).strip()
    if telefono is not None and str(telefono).strip():
        tel = _fmt_tel_rd(telefono)
        if len(_norm_tel(tel)) < 10:
            return {"ok": False, "razon": "Teléfono inválido."}
        otros = [d for d in _buscar_driver_por_tel(tel)
                 if d["fields"].get("driver_id") != driver_id]
        if otros:
            return {"ok": False, "razon": "Otro chofer ya tiene ese teléfono."}
        dfields["driver_phone"] = tel
    if driver_type in ("propio", "afiliado"):
        dfields["driver_type"] = driver_type
    if driver_status in ("available", "busy", "offline", "suspended"):
        dfields["driver_status"] = driver_status

    vfields = {}
    if vehiculo_tipo:
        if vehiculo_tipo not in _MAX_PAX_POR_TIPO:
            return {"ok": False, "razon": "Tipo de vehículo inválido."}
        vfields["vehicle_type"] = vehiculo_tipo
    if placa is not None:
        vfields["vehicle_plate"] = str(placa).strip().upper()
    if marca is not None:
        vfields["vehicle_brand"] = str(marca).strip()
    if modelo is not None:
        vfields["vehicle_model"] = str(modelo).strip()
    if color is not None:
        vfields["vehicle_color"] = str(color).strip()
    if anio is not None and str(anio).strip():
        try: vfields["vehicle_year"] = int(anio)
        except (TypeError, ValueError): pass
    if max_pax is not None and str(max_pax).strip():
        try: vfields["max_passengers"] = int(max_pax)
        except (TypeError, ValueError): pass

    if not dfields and not vfields:
        return {"ok": False, "razon": "No hay cambios para guardar."}

    now = _now_utc().isoformat()
    if dfields:
        dfields["updated_at"] = now
        _at_update("Drivers", drec["id"], dfields)
    veh_id = df.get("assigned_vehicle_id", "") or ""
    if vfields and veh_id:
        vs = _at_get("Vehicles", formula=f"{{vehicle_id}}='{_af(veh_id)}'", max_records=1)
        if vs:
            vfields["updated_at"] = now
            _at_update("Vehicles", vs[0]["id"], vfields)
    _DRIVERS_CACHE["ts"] = 0.0  # invalida cache para reflejar el cambio al instante
    return {"ok": True, "driver_id": driver_id,
            "campos_chofer": [k for k in dfields if k != "updated_at"],
            "campos_vehiculo": [k for k in vfields if k != "updated_at"]}


def choferes_cercanos(pickup_lat: float, pickup_lng: float,
                      excluir=None, max_edad_horas: Optional[float] = None) -> list[dict]:
    """Choferes 'available' con ubicacion conocida, ordenados por cercania al pickup.

    El chofer queda EN LINEA hasta desconectarse (panel web) o entrar en carrera
    (busy); la ubicacion NO caduca por defecto (max_edad_horas=None). Se espera
    que comparta 'ubicacion en tiempo real' de WhatsApp para que su posicion se
    mantenga fresca sola. Si se pasa un numero en max_edad_horas, se excluye a
    quien tenga la ubicacion mas vieja que eso (util para monitoreo/depuracion).
    """
    excluir = set(excluir or [])
    drivers = _at_get("Drivers", formula="{driver_status}='available'", max_records=200)
    rank = []
    for d in drivers:
        f = d["fields"]
        did = f.get("driver_id")
        if did in excluir:
            continue
        lat, lng = f.get("current_lat"), f.get("current_lng")
        if lat is None or lng is None:
            continue
        te = _parse_dt(f.get("location_updated_at"))
        edad_min = None
        if te:
            edad_min = round((_now_utc() - te).total_seconds() / 60.0, 1)
            if max_edad_horas is not None and edad_min > max_edad_horas * 60:
                continue
        dist = _haversine_km(pickup_lat, pickup_lng, float(lat), float(lng))
        rank.append({
            "driver_id": did,
            "driver_name": f.get("driver_name"),
            "driver_phone": f.get("driver_phone"),
            "assigned_vehicle_id": f.get("assigned_vehicle_id"),
            "record_id": d["id"],
            "dist_km": round(dist, 2),
            "loc_age_min": edad_min,
        })
    rank.sort(key=lambda x: x["dist_km"])
    return rank


def _drivers_ya_ofertados(bf: dict) -> list[str]:
    log = bf.get("offer_log", "") or ""
    ids = set(re.findall(r"->\s*(DRV-\w+)", log))
    if bf.get("offered_driver_id"):
        ids.add(bf["offered_driver_id"])
    return list(ids)


def _booking_pickup_dt(bf: dict) -> Optional[datetime]:
    """Hora de recogida programada de la reserva (Travel_Date como datetime UTC)."""
    return _parse_dt(bf.get("Travel_Date"))


def _booking_es_programado(bf: dict, ahora=None) -> bool:
    """True si la reserva es para mas de 2h en el futuro (viaje PROGRAMADO)."""
    return _es_programado(_booking_pickup_dt(bf), ahora)


def _booking_es_futuro(bf: dict, ahora=None) -> bool:
    """True si la recogida aun no ha llegado (sirve para permitir declinar/reasignar
    un servicio agendado, incluso cuando ya falta <2h y dejo de ser 'programado')."""
    dt = _booking_pickup_dt(bf)
    if dt is None:
        return False
    if ahora is None:
        ahora = _now_utc()
    return dt > ahora


def _msg_oferta(elegido: dict, bf: dict, pickup_dt: Optional[datetime] = None) -> str:
    if _es_programado(pickup_dt):
        mins = OFERTA_TTL_PROGRAMADA_SEG // 60
        return (
            "🗓️ *Servicio PROGRAMADO Emovils*\n\n"
            f"📅 Para: *{_fmt_dt_rd(pickup_dt)}*\n"
            f"📍 Recogida: {bf.get('Pickup_Location','')}\n"
            f"🎯 Destino: {bf.get('Dropoff_Location','')}\n"
            f"👥 Pasajeros: {bf.get('Passengers','')}\n"
            f"📏 A ~{elegido['dist_km']} km de tu ubicacion\n"
            f"💵 RD${bf.get('final_price',0)} ({bf.get('payment_method','')})\n\n"
            "Si lo aceptas, *queda agendado para ti* y te lo recordamos 2 horas antes.\n"
            "Responde *ACEPTO* para agendarlo o *RECHAZO* para pasarlo.\n"
            f"Tienes {mins} minutos para responder."
        )
    return (
        "🚖 *Nueva carrera Emovils*\n\n"
        f"📍 Recogida: {bf.get('Pickup_Location','')}\n"
        f"🎯 Destino: {bf.get('Dropoff_Location','')}\n"
        f"👥 Pasajeros: {bf.get('Passengers','')}\n"
        f"📏 A ~{elegido['dist_km']} km de tu ubicacion\n"
        f"💵 RD${bf.get('final_price',0)} ({bf.get('payment_method','')})\n\n"
        f"Responde *ACEPTO* para tomarla o *RECHAZO* para pasarla.\n"
        f"Tienes {OFERTA_TTL_SEG} segundos."
    )


def _enviar_oferta_whatsapp(elegido: dict, bf: dict, pickup_dt: Optional[datetime] = None) -> None:
    try:
        from opc.whatsapp_green_api import notificar_chofer
        notificar_chofer(elegido["driver_phone"], _msg_oferta(elegido, bf, pickup_dt))
    except Exception as e:
        logger.warning("No se pudo enviar oferta al chofer %s: %s",
                       elegido.get("driver_id"), e)


def _avisar_sin_choferes(booking_rec: dict) -> None:
    """No hay choferes disponibles para la reserva. En vez de INVENTAR o dejar
    un chofer 'fantasma' pegado, se le dice la VERDAD al cliente y se escala a un
    supervisor humano para que lo contacte y resuelva."""
    bf = booking_rec["fields"]
    booking_id = bf.get("Booking_ID", "")
    cliente = bf.get("customer_phone", "") or ""
    try:
        from opc.whatsapp_green_api import enviar_a_cliente
        # 1) Mensaje TRANQUILO al cliente: no se le dice "no hay choferes", se le
        # avisa que un supervisor está coordinando su servicio y lo contactará.
        if cliente:
            enviar_a_cliente(
                cliente,
                "🙏 Gracias por tu paciencia. Estamos *coordinando tu servicio* "
                "con un supervisor, que te va a contactar enseguida para "
                "confirmarte los detalles de tu viaje. 💙"
            )
        # 2) Escalar a un supervisor/dueño para que llame al cliente.
        owner = os.getenv("OWNER_WHATSAPP", "+18298610090")
        if owner:
            enviar_a_cliente(
                owner,
                "🚨 *RESERVA SIN CHOFERES — contactar al cliente*\n"
                f"Reserva: {booking_id}\n"
                f"Cliente: {bf.get('customer_name','')} ({cliente})\n"
                f"📍 {bf.get('Pickup_Location','')} → {bf.get('Dropoff_Location','')}\n"
                f"👥 {bf.get('Passengers','')}  ·  💵 RD${bf.get('final_price',0)} "
                f"({bf.get('payment_method','')})\n"
                f"⏰ {bf.get('service_time','')}\n\n"
                "Nadie aceptó la oferta. Llamar al cliente para coordinar."
            )
    except Exception as e:
        logger.warning("Aviso 'sin choferes' falló para %s: %s", booking_id, e)


def _despachar_siguiente(booking_rec: dict) -> dict:
    """Ofrece la carrera al chofer mas cercano que aun no la haya recibido."""
    bf = booking_rec["fields"]
    booking_id = bf.get("Booking_ID", "")
    coords = _geocode_pickup(bf.get("Pickup_Location", ""))
    if not coords:
        _at_update("Bookings", booking_rec["id"], {
            "offer_status": "searching",
            "offer_log": _log_append(bf, "recogida no geocodificable — no se pudo ordenar por cercania"),
        })
        return {"ok": False, "razon": "recogida no geocodificable", "offer_status": "searching"}
    lat, lng = coords
    excluir = _drivers_ya_ofertados(bf)
    candidatos = choferes_cercanos(lat, lng, excluir=excluir)
    if not candidatos:
        _at_update("Bookings", booking_rec["id"], {
            "offer_status": "no_drivers",
            "offer_log": _log_append(bf, "sin choferes disponibles cerca"),
            # Honestidad: no dejar pegado un chofer "fantasma" de un intento previo.
            "offered_driver_id": "",
            "driver_id": "",
            "Driver_Name": "",
            "Driver_Phone": "",
            "Driver_Vehicle": "",
            "vehicle_id": "",
        })
        _avisar_sin_choferes(booking_rec)
        return {"ok": False, "razon": "sin choferes disponibles cerca", "offer_status": "no_drivers"}
    elegido = candidatos[0]
    intentos = int(bf.get("offer_attempts", 0) or 0) + 1
    # PROGRAMADO: el chofer tiene mas tiempo para aceptar (lo agenda con calma).
    pickup_dt = _booking_pickup_dt(bf)
    es_prog = _es_programado(pickup_dt)
    ttl = OFERTA_TTL_PROGRAMADA_SEG if es_prog else OFERTA_TTL_SEG
    expira = _now_utc() + timedelta(seconds=ttl)
    etiqueta = f"PROGRAMADA {_fmt_dt_rd(pickup_dt)}" if es_prog else "inmediata"
    _at_update("Bookings", booking_rec["id"], {
        "offer_status": "offered",
        "offered_driver_id": elegido["driver_id"],
        "offer_expires_at": expira.isoformat(),
        "offer_attempts": intentos,
        "offer_log": _log_append(bf, f"oferta #{intentos} ({etiqueta}) -> {elegido['driver_id']} ({elegido['dist_km']} km)"),
    })
    _enviar_oferta_whatsapp(elegido, bf, pickup_dt)
    return {
        "ok": True, "offer_status": "offered", "intento": intentos,
        "programado": es_prog,
        "driver_id": elegido["driver_id"], "driver_name": elegido["driver_name"],
        "dist_km": elegido["dist_km"], "expira_en_seg": ttl,
        "candidatos": [{"driver_id": c["driver_id"], "dist_km": c["dist_km"]} for c in candidatos],
    }


def iniciar_despacho(booking_id: str) -> dict:
    """Arranca la busqueda del chofer mas cercano para una reserva."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bk:
        return {"ok": False, "razon": "booking no existe"}
    return _despachar_siguiente(bk[0])


def _reofrecer(booking_id: str) -> dict:
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bk:
        return {"ok": False, "razon": "booking no existe"}
    return _despachar_siguiente(bk[0])


def _aceptar_oferta(booking: dict, driver: dict) -> dict:
    bf = booking["fields"]
    df = driver["fields"]
    did = df.get("driver_id")
    veh_id = df.get("assigned_vehicle_id", "")
    vf = {}
    if veh_id:
        veh = _at_get("Vehicles", formula=f"{{vehicle_id}}='{_af(veh_id)}'", max_records=1)
        vf = veh[0]["fields"] if veh else {}

    pickup_dt = _booking_pickup_dt(bf)
    es_prog = _es_programado(pickup_dt)
    veh_txt = " ".join(x for x in [vf.get("vehicle_brand", ""),
                                   vf.get("vehicle_model", ""),
                                   vf.get("vehicle_color", "")] if x).strip()
    placa = vf.get("vehicle_plate", "")

    nota = "AGENDADA (programada) por" if es_prog else "ACEPTADA por"
    _at_update("Bookings", booking["id"], {
        "driver_id": did,
        "vehicle_id": veh_id,
        "Driver_Name": df.get("driver_name", ""),
        "Driver_Phone": df.get("driver_phone", ""),
        "Driver_Vehicle": f"{vf.get('vehicle_brand','')} {vf.get('vehicle_model','')} {vf.get('vehicle_plate','')}".strip(),
        "offer_status": "accepted",
        "offer_log": _log_append(bf, f"{nota} {did}"),
    })
    # PROGRAMADO: el chofer NO queda 'busy' (sigue EN LINEA y puede tomar otras
    # carreras hasta que llegue la hora); el dia del servicio se le recuerda 2h antes.
    # INMEDIATO: queda 'busy' (va en camino ya).
    if not es_prog:
        _at_update("Drivers", driver["id"], {"driver_status": "busy"})

    try:
        from opc.whatsapp_green_api import enviar_a_cliente as _wa_cli, notificar_chofer as _wa_drv
        cust = bf.get("customer_phone", "")
        if es_prog:
            # Pasajero: reserva CONFIRMADA para tal hora (sin link en vivo todavia).
            if cust:
                _wa_cli(
                    cust,
                    "✅ *¡Tu reserva quedó confirmada!* 🗓️\n\n"
                    f"📅 {_fmt_dt_rd(pickup_dt)}\n"
                    f"📍 {bf.get('Pickup_Location','')} → {bf.get('Dropoff_Location','')}\n"
                    f"Chofer: {df.get('driver_name','')}\n"
                    + (f"Vehículo: {veh_txt}\n" if veh_txt else "")
                    + (f"Placa: {placa}\n" if placa else "")
                    + "\nTe enviaremos el enlace de seguimiento en vivo cuando se "
                    "acerque la hora. ¡Gracias por reservar con Emovils! 💙")
            # Chofer: confirmacion de que quedó agendado.
            _wa_drv(
                df.get("driver_phone", ""),
                "🗓️ *Servicio agendado.* Quedó guardado en tu agenda.\n\n"
                f"📅 {_fmt_dt_rd(pickup_dt)}\n"
                f"📍 {bf.get('Pickup_Location','')} → {bf.get('Dropoff_Location','')}\n"
                f"👥 {bf.get('Passengers','')}  ·  💵 RD${bf.get('final_price',0)} "
                f"({bf.get('payment_method','')})\n\n"
                "Te lo recordaremos *2 horas antes* con todos los detalles. "
                "Si llegado el momento no puedes, podrás responder *RECHAZO* y se "
                "lo asignamos a otro chofer.")
        else:
            # Inmediato: el chofer va en camino. Avisamos a AMBOS con el tiempo
            # estimado de llegada (ETA) y los codigos QR del viaje.
            bid = bf.get("Booking_ID", "")
            # ETA: releer la ficha del chofer para usar su ubicacion MAS reciente
            # (el GPS pudo refrescarse despues de enviar la oferta). Asi el cliente
            # casi siempre recibe el tiempo estimado de llegada.
            df_eta = df
            try:
                _dd = _at_get("Drivers", formula=f"{{driver_id}}='{_af(did)}'", max_records=1)
                if _dd:
                    df_eta = _dd[0]["fields"]
            except Exception:
                pass
            eta_min, _dist_m = _eta_chofer_a_pickup(bf, df_eta)
            eta_cli = f"⏱️ Llega en ~{eta_min} min\n" if eta_min else ""
            qr_tok = bf.get("customer_qr_token", "")
            # ── CLIENTE: chofer en camino + ETA + seguimiento en vivo + su QR ──
            if cust:
                link = f"{PUBLIC_BASE_URL}/seguir/{bid}?t={qr_tok}"
                _wa_cli(
                    cust,
                    "✅ *¡Tu Emovils va en camino!* 🚖\n\n"
                    f"👤 {df.get('driver_name','')}\n"
                    + (f"🚐 {veh_txt}\n" if veh_txt else "")
                    + (f"🔖 Placa {placa}\n" if placa else "")
                    + (f"📱 {df.get('driver_phone','')}\n" if df.get("driver_phone") else "")
                    + eta_cli
                    + f"\n📍 *Sigue tu taxi EN VIVO aquí* (verás cómo se acerca):\n{link}\n"
                    + "\n🛡️ Cuando llegue el auto, escanea el QR pegado en su "
                      "lateral para confirmar que es tu Emovils.\n\n"
                      "Te avisaremos con un sonido cuando esté a 100 metros.")
                # ── CLIENTE: además del texto, enviarle la IMAGEN del QR ──
                # Es lo que el chofer ESCANEA para abrir la hoja e iniciar el
                # viaje. Antes solo iba un enlace (no un QR), el chofer no tenía
                # nada que escanear y por eso el viaje nunca arrancaba (ni se
                # podía finalizar).
                if qr_tok:
                    try:
                        import tempfile, os as _os
                        from opc.whatsapp_green_api import get_client as _wa_client
                        _hoja_qr = f"{PUBLIC_BASE_URL}/qr/cliente/{bid}?t={qr_tok}&g=1"
                        _png = generar_qr_png(_hoja_qr, box_size=12, border=3)
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _tf:
                            _tf.write(_png)
                            _qr_path = _tf.name
                        _wa_client().enviar_archivo(
                            cust, _qr_path,
                            caption=("📲 *Tu código del viaje.* Cuando llegue tu "
                                     "Emovils, muéstrale ESTA imagen al chofer para "
                                     "que la escanee e inicie tu viaje."))
                        try:
                            _os.unlink(_qr_path)
                        except OSError:
                            pass
                    except Exception as _qe:
                        logger.warning("No se pudo enviar imagen QR al cliente: %s", _qe)
            # ── CHOFER: hoja de servicio + datos del cliente + ETA + navegacion ──
            drv_phone = df.get("driver_phone", "")
            if drv_phone:
                eta_drv = f"   (a ~{eta_min} min de ti)\n" if eta_min else ""
                pu = _pickup_coords(bid, bf.get("Pickup_Location", ""))
                maps_nav = (f"https://www.google.com/maps/dir/?api=1&"
                            f"destination={pu[0]},{pu[1]}") if pu else ""
                hoja = f"{PUBLIC_BASE_URL}/qr/cliente/{bid}?t={qr_tok}" if qr_tok else ""
                qr_veh = f"{PUBLIC_BASE_URL}/vehicle/{veh_id}/qr" if veh_id else ""
                _wa_drv(
                    drv_phone,
                    "🚖 *Servicio confirmado — vas en camino.*\n\n"
                    f"👤 {bf.get('customer_name','')}   📱 {cust}\n"
                    f"📍 Recoger en: {bf.get('Pickup_Location','')}\n"
                    + eta_drv
                    + f"🏁 Destino: {bf.get('Dropoff_Location','')}\n"
                    f"👥 {bf.get('Passengers','')}  ·  💵 RD${bf.get('final_price',0)} "
                    f"({bf.get('payment_method','')})\n"
                    + (f"\n🧭 Navegar a la recogida:\n{maps_nav}\n" if maps_nav else "")
                    + (f"\n📋 Hoja de servicio (datos del viaje y navegación):\n{hoja}\n"
                       if hoja else "")
                    + (f"\n🔳 QR de tu vehículo (muéstralo / pégalo para el cliente):\n{qr_veh}\n"
                       if qr_veh else "")
                    + "\n✅ Al recoger al cliente, escanea SU código QR para iniciar "
                      "el viaje. (Si el QR no abre, el cliente puede iniciarlo desde "
                      "su pantalla.)")
    except Exception as _e:
        logger.warning("Aviso (aceptacion) fallo: %s", _e)

    return {
        "ok": True, "es_chofer": True,
        "accion": "agendada" if es_prog else "aceptada",
        "programado": es_prog,
        "booking_id": bf.get("Booking_ID"), "driver_id": did,
        "driver_name": df.get("driver_name", ""),
        "pickup": bf.get("Pickup_Location", ""),
        "destino": bf.get("Dropoff_Location", ""),
        "precio": bf.get("final_price", 0),
        "payment_method": bf.get("payment_method", ""),
    }


def _rechazar_oferta(booking: dict, did: str) -> dict:
    bf = booking["fields"]
    _at_update("Bookings", booking["id"], {
        "offer_status": "rejected",
        "offer_log": _log_append(bf, f"RECHAZADA por {did}"),
    })
    siguiente = _reofrecer(bf.get("Booking_ID"))
    return {"ok": True, "es_chofer": True, "accion": "rechazada", "siguiente": siguiente}


def _declinar_agendada(booking: dict, did: str) -> dict:
    """El chofer que YA habia aceptado un servicio agendado lo declina (ej. al
    recibir el recordatorio). Se libera la reserva y se reasigna al siguiente mas
    cercano, con tiempo de sobra antes de la hora de recogida."""
    bf = booking["fields"]
    _at_update("Bookings", booking["id"], {
        "offer_status": "rejected",
        # quitar al chofer que declinó para no dejarlo "fantasma"
        "driver_id": "", "Driver_Name": "", "Driver_Phone": "",
        "Driver_Vehicle": "", "vehicle_id": "",
        "offer_log": _log_append(bf, f"DECLINADA tras aceptar (agendada) por {did} — reasignando"),
    })
    # El chofer de un agendado no quedaba 'busy', asi que no hay que liberarlo.
    # Avisar al cliente que seguimos buscando (sin alarmar).
    try:
        from opc.whatsapp_green_api import enviar_a_cliente as _wa_cli
        cust = bf.get("customer_phone", "")
        if cust:
            _wa_cli(cust, "🔄 Estamos reconfirmando tu chofer para tu reserva. "
                          "Te avisamos en breve con los datos actualizados. 🙏")
    except Exception as _e:
        logger.warning("Aviso (declinar agendada) fallo: %s", _e)
    siguiente = _reofrecer(bf.get("Booking_ID"))
    return {"ok": True, "es_chofer": True, "accion": "declinada_agendada", "siguiente": siguiente}


def responder_oferta(driver_phone: str, texto: str) -> dict:
    """Procesa la respuesta del chofer (ACEPTO / RECHAZO) a una oferta vigente,
    o un RECHAZO a un servicio AGENDADO que ya habia aceptado (para reasignarlo)."""
    drivers = _buscar_driver_por_tel(driver_phone)
    if not drivers:
        return {"ok": False, "es_chofer": False}
    d = drivers[0]
    did = d["fields"].get("driver_id")
    t = _norm_palabra(texto)
    acepta = t.startswith("acepto") or t in {"si", "ok", "dale", "voy", "aceptar", "claro", "listo", "la tomo"}
    rechaza = (t.startswith("rechazo") or t.startswith("cancel") or t.startswith("declin")
               or t in {"no", "paso", "rechazar", "no puedo", "nel"})

    # DEFENSA: un chofer DESCONECTADO (offline) o suspendido NO puede tomar una
    # carrera, aunque le hubiera llegado una oferta justo antes de desconectarse.
    # Asi un viaje nunca "entra" a un chofer que no esta EN LINEA. Para tomar
    # carreras debe primero ponerse disponible en su panel (o compartir ubicacion).
    estado_drv = d["fields"].get("driver_status")
    if acepta and not rechaza and estado_drv in ("offline", "suspended"):
        return {"ok": False, "es_chofer": True, "razon": "chofer_desconectado"}

    # 1) Oferta EN VUELO (offer_status='offered'): la via normal.
    bks = _at_get("Bookings",
                  formula=f"AND({{offered_driver_id}}='{_af(did)}', {{offer_status}}='offered')",
                  max_records=1)
    if bks:
        booking = bks[0]
        bf = booking["fields"]
        te = _parse_dt(bf.get("offer_expires_at"))
        if te and _now_utc() > te:
            return {"ok": False, "es_chofer": True, "razon": "oferta_vencida"}
        if acepta and not rechaza:
            return _aceptar_oferta(booking, d)
        if rechaza:
            return _rechazar_oferta(booking, did)
        return {"ok": False, "es_chofer": True, "razon": "respuesta_no_entendida"}

    # 2) Sin oferta en vuelo: ¿declina un servicio AGENDADO que ya aceptó?
    if rechaza:
        ag = _at_get("Bookings",
                     formula=f"AND({{driver_id}}='{_af(did)}', {{offer_status}}='accepted')",
                     max_records=1)
        if ag and _booking_es_futuro(ag[0]["fields"]):
            return _declinar_agendada(ag[0], did)

    # 3) ACEPTACIÓN TARDÍA: el chofer dijo "Acepto" después de que su oferta
    # venció, PERO la carrera sigue libre (nadie más la tomó). En vez de
    # bloquearlo, volvemos a revisar: si el viaje todavía no tiene chofer, se lo
    # asignamos a él. Así no se pierde un servicio por unos segundos de retraso.
    if acepta and not rechaza:
        token = f"-> {did} "
        libres = _at_get(
            "Bookings",
            formula=(f"AND(FIND('{token}', {{offer_log}}), {{driver_id}}='', "
                     f"NOT({{booking_status}}='cancelled'), "
                     f"NOT({{booking_status}}='completed'), "
                     f"NOT({{booking_status}}='in_progress'), "
                     f"NOT({{offer_status}}='offered'))"),
            max_records=1)
        if libres and not (libres[0]["fields"].get("driver_id") or "").strip():
            return _aceptar_oferta(libres[0], d)

    return {"ok": False, "es_chofer": True, "razon": "no_oferta_vigente"}


def completar_viaje(booking_id: str) -> dict:
    """Cierra una carrera: reserva -> completed y el chofer vuelve a 'available'
    (EN LINEA), listo para la siguiente. Cumple el modelo 'siempre en linea':
    el chofer solo deja de estar disponible si se desconecta en la web."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bk:
        return {"ok": False, "razon": "booking no existe"}
    booking = bk[0]
    bf = booking["fields"]
    if bf.get("booking_status") == "completed":
        return {"ok": True, "ya_completado": True, "booking_id": booking_id,
                "booking_status": "completed", "driver_id": bf.get("driver_id", "")}
    did = bf.get("driver_id", "")
    _at_update("Bookings", booking["id"], {
        "booking_status": "completed",
        "offer_log": _log_append(bf, f"VIAJE COMPLETADO ({did})"),
    })
    driver_status = None
    if did:
        drv = _at_get("Drivers", formula=f"{{driver_id}}='{_af(did)}'", max_records=1)
        if drv:
            est = drv[0]["fields"].get("driver_status")
            # Vuelve a EN LINEA salvo que se haya desconectado (offline) o este suspendido.
            driver_status = est if est in ("offline", "suspended") else "available"
            _at_update("Drivers", drv[0]["id"], {"driver_status": driver_status})
    return {"ok": True, "booking_id": booking_id, "driver_id": did,
            "booking_status": "completed", "driver_status": driver_status}


# Palabras con las que el chofer avisa por WhatsApp que termino la carrera.
_PALABRAS_FIN = {
    "termine", "termino", "terminado", "terminada", "finalizado", "finalice",
    "finalizada", "completado", "completada", "complete", "completo", "llegamos",
    "llegue", "entregado", "entregada", "fin", "ya", "deje", "dejado",
}


def chofer_finaliza_viaje(driver_phone: str, texto: str) -> dict:
    """Si el chofer escribe que ya terminó y tiene una carrera en curso, la cierra
    y vuelve a quedar EN LINEA. Devuelve es_completar=False si no aplica."""
    drivers = _buscar_driver_por_tel(driver_phone)
    if not drivers:
        return {"ok": False, "es_chofer": False, "es_completar": False}
    did = drivers[0]["fields"].get("driver_id")
    t = _norm_palabra(texto)
    es_fin = (t in _PALABRAS_FIN or t.startswith("termin") or
              t.startswith("finaliz") or t.startswith("complet") or
              t.startswith("ya termin") or t.startswith("ya lleg"))
    if not es_fin:
        return {"ok": False, "es_chofer": True, "es_completar": False}
    bks = _at_get("Bookings",
                  formula=f"AND({{driver_id}}='{_af(did)}', {{booking_status}}='in_progress')",
                  max_records=1)
    if not bks:
        return {"ok": False, "es_chofer": True, "es_completar": False,
                "razon": "sin_viaje_en_curso"}
    res = completar_viaje(bks[0]["fields"].get("Booking_ID"))
    res.update({"es_chofer": True, "es_completar": True})
    return res


def revisar_ofertas_vencidas() -> dict:
    """Revisa ofertas 'offered' vencidas y las reasigna al siguiente mas cercano.

    Pensado para llamarse periodicamente (hilo de fondo o cron)."""
    bks = _at_get("Bookings", formula="{offer_status}='offered'", max_records=50)
    detalles = []
    for b in bks:
        bf = b["fields"]
        te = _parse_dt(bf.get("offer_expires_at"))
        if te and _now_utc() > te:
            did = bf.get("offered_driver_id", "")
            _at_update("Bookings", b["id"], {
                "offer_status": "expired",
                "offer_log": _log_append(bf, f"VENCIDA sin respuesta — {did}"),
            })
            sig = _reofrecer(bf.get("Booking_ID"))
            detalles.append({"booking_id": bf.get("Booking_ID"), "siguiente": sig})
    return {"revisadas": len(bks), "vencidas": len(detalles), "detalles": detalles}


def _msg_recordatorio_chofer(bf: dict, pickup_dt: Optional[datetime]) -> str:
    return (
        "⏰ *Recordatorio de servicio Emovils*\n\n"
        f"Tienes un servicio *{_fmt_dt_rd(pickup_dt)}* (en ~2 horas).\n\n"
        f"📍 Recogida: {bf.get('Pickup_Location','')}\n"
        f"🎯 Destino: {bf.get('Dropoff_Location','')}\n"
        f"👥 Pasajeros: {bf.get('Passengers','')}\n"
        f"👤 Cliente: {bf.get('customer_name','')}\n"
        f"💵 RD${bf.get('final_price',0)} ({bf.get('payment_method','')})\n\n"
        "Por favor prepárate para llegar a tiempo. Si *no puedes*, responde "
        "*RECHAZO* ahora para asignárselo a otro chofer a tiempo."
    )


def _enviar_recordatorio_chofer(booking: dict) -> bool:
    """Envia al chofer el recordatorio (con todos los detalles) de un servicio
    agendado. Devuelve True si se logro enviar."""
    bf = booking["fields"]
    phone = bf.get("Driver_Phone", "") or ""
    if not phone:
        did = bf.get("driver_id", "")
        drv = _at_get("Drivers", formula=f"{{driver_id}}='{_af(did)}'", max_records=1) if did else []
        phone = drv[0]["fields"].get("driver_phone", "") if drv else ""
    if not phone:
        return False
    try:
        from opc.whatsapp_green_api import notificar_chofer
        notificar_chofer(phone, _msg_recordatorio_chofer(bf, _booking_pickup_dt(bf)))
        return True
    except Exception as e:
        logger.warning("Recordatorio a chofer fallo (%s): %s", bf.get("Booking_ID"), e)
        return False


def revisar_recordatorios_agendados() -> dict:
    """Recuerda a los choferes, ~2h antes, los servicios AGENDADOS que aceptaron.
    Envia todos los detalles y la opcion de declinar (para reasignar a tiempo).

    Pensado para llamarse periodicamente (cada pocos minutos)."""
    ahora = _now_utc()
    limite = ahora + timedelta(minutes=RECORDATORIO_ANTES_MIN)
    bks = _at_get("Bookings", formula="{offer_status}='accepted'", max_records=100)
    enviados = []
    for b in bks:
        bf = b["fields"]
        dt = _booking_pickup_dt(bf)
        if dt is None or not (ahora < dt <= limite):
            continue  # no es agendado, o aun falta mas de 2h, o ya pasó
        if TAG_RECORDATORIO in (bf.get("offer_log") or ""):
            continue  # ya se le recordó
        if _enviar_recordatorio_chofer(b):
            _at_update("Bookings", b["id"], {"offer_log": _log_append(bf, TAG_RECORDATORIO)})
            enviados.append(bf.get("Booking_ID"))
    return {"revisadas": len(bks), "recordados": len(enviados), "detalles": enviados}


# ── Seguimiento en vivo del chofer para el pasajero ──────────────
_PICKUP_GEO_CACHE: dict = {}  # booking_id -> (lat, lng); el pickup no cambia


def _pickup_coords(booking_id: str, direccion: str):
    """Coordenadas del punto de recogida (geocodificadas una vez y cacheadas)."""
    if booking_id in _PICKUP_GEO_CACHE:
        return _PICKUP_GEO_CACHE[booking_id]
    coords = _geocode_pickup(direccion) if direccion else None
    if coords:
        _PICKUP_GEO_CACHE[booking_id] = coords
    return coords


_ETA_VELOCIDAD_KMH = 30.0   # velocidad urbana promedio para estimar minutos
_ETA_FACTOR_RUTA = 1.3      # haversine (linea recta) -> distancia real por calle (aprox)


def _eta_minutos(distancia_m) -> int:
    """Minutos estimados de llegada a partir de la distancia en linea recta.
    Estimacion simple (sin llamar a Google en cada ping): aplica un factor de
    ruta y una velocidad urbana promedio."""
    try:
        d = int(distancia_m)
    except (TypeError, ValueError):
        return 0
    if d <= 0:
        return 0
    km = (d / 1000.0) * _ETA_FACTOR_RUTA
    minutos = km / _ETA_VELOCIDAD_KMH * 60.0
    return max(1, int(round(minutos)))


def _eta_chofer_a_pickup(bf: dict, df: dict):
    """ETA (minutos) y distancia (metros) del chofer al punto de recogida en el
    momento de aceptar. Usa el GPS actual del chofer + las coordenadas del pickup.
    Devuelve (eta_min, dist_m) o (None, None) si falta algun dato (p.ej. sin GPS
    o sin clave de geocodificacion)."""
    try:
        pu = _pickup_coords(bf.get("Booking_ID", ""), bf.get("Pickup_Location", ""))
        dlat, dlng = df.get("current_lat"), df.get("current_lng")
        if pu and dlat not in (None, "") and dlng not in (None, ""):
            dist_m = int(round(
                _haversine_km(pu[0], pu[1], float(dlat), float(dlng)) * 1000))
            return _eta_minutos(dist_m), dist_m
    except Exception as _e:
        logger.warning("ETA chofer->pickup fallo: %s", _e)
    return None, None


def estado_seguimiento(booking_id: str) -> dict:
    """Datos para la pagina de seguimiento del pasajero: posicion del chofer,
    punto de recogida, distancia en metros y minutos estimados de llegada."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bk:
        return {"ok": False, "razon": "Reserva no encontrada."}
    bf = bk[0]["fields"]
    did = bf.get("driver_id", "") or ""
    pu = _pickup_coords(booking_id, bf.get("Pickup_Location", ""))
    out = {
        "ok": True,
        "booking_id": booking_id,
        "booking_status": bf.get("booking_status", ""),
        "offer_status": bf.get("offer_status", ""),
        "pickup": bf.get("Pickup_Location", ""),
        "destino": bf.get("Dropoff_Location", ""),
        "pickup_lat": pu[0] if pu else None,
        "pickup_lng": pu[1] if pu else None,
        "driver_id": did,
        "driver_name": bf.get("Driver_Name", ""),
        "vehiculo": bf.get("Driver_Vehicle", ""),
        "asignado": bool(did),
        "driver_lat": None,
        "driver_lng": None,
        "distancia_m": None,
        "eta_min": None,
        "ubic_actualizada": None,
    }
    if did:
        ds = _at_get("Drivers", formula=f"{{driver_id}}='{_af(did)}'", max_records=1)
        if ds:
            dff = ds[0]["fields"]
            dlat, dlng = dff.get("current_lat"), dff.get("current_lng")
            out["ubic_actualizada"] = dff.get("location_updated_at")
            if dlat is not None and dlng is not None:
                out["driver_lat"] = float(dlat)
                out["driver_lng"] = float(dlng)
                if pu:
                    out["distancia_m"] = int(round(
                        _haversine_km(pu[0], pu[1], float(dlat), float(dlng)) * 1000))
                    out["eta_min"] = _eta_minutos(out["distancia_m"])
    return out


_FLOTA_LIVE_MAX_SEG = 300  # GPS recibido en los ultimos 5 min = "en vivo"


def estado_flota() -> dict:
    """Lista de TODOS los choferes con su ultima posicion y estado, para que la
    institucion los vea en el mapa de flota. Distingue quien esta enviando GPS
    EN VIVO (ultimos 5 min) de quien solo tiene una ultima posicion guardada
    (sin señal ahora). Incluye, si esta en viaje, hacia donde va."""
    recs = _at_get("Drivers", max_records=200)
    choferes = []
    for r in recs:
        f = r["fields"]
        lat, lng = f.get("current_lat"), f.get("current_lng")
        ts = f.get("location_updated_at", "") or ""
        secs = None
        try:
            if ts:
                secs = int((_now_utc() - _parse_dt(ts)).total_seconds())
        except Exception:
            secs = None
        tiene_gps = lat not in (None, "") and lng not in (None, "")
        live = bool(tiene_gps and secs is not None and secs <= _FLOTA_LIVE_MAX_SEG)
        choferes.append({
            "driver_id": f.get("driver_id", ""),
            "driver_name": f.get("driver_name", ""),
            "status": f.get("driver_status", "offline") or "offline",
            "phone": f.get("driver_phone", ""),
            "lat": float(lat) if tiene_gps else None,
            "lng": float(lng) if tiene_gps else None,
            "updated_at": ts,
            "secs_ago": secs,
            "live": live,
            "vehiculo": f.get("assigned_vehicle_id", ""),
        })
    # Viajes en curso (para anotar a quien lleva cada chofer ocupado).
    viajes = {}
    try:
        activos = _at_get(
            "Bookings",
            formula="OR({offer_status}='accepted',{booking_status}='in_progress')",
            max_records=100)
        for b in activos:
            bf = b["fields"]
            did = bf.get("driver_id", "")
            if did:
                viajes[did] = {
                    "cliente": bf.get("customer_name", ""),
                    "pickup": bf.get("Pickup_Location", ""),
                    "destino": bf.get("Dropoff_Location", ""),
                    "booking_id": bf.get("Booking_ID", ""),
                }
    except Exception as _e:
        logger.warning("estado_flota viajes fallo: %s", _e)
    for c in choferes:
        c["viaje"] = viajes.get(c["driver_id"])
    resumen = {
        "en_vivo": sum(1 for c in choferes if c["live"]),
        "sin_senal": sum(1 for c in choferes if c["lat"] is not None and not c["live"]),
        "sin_gps": sum(1 for c in choferes if c["lat"] is None),
        "available": sum(1 for c in choferes if c["status"] == "available"),
        "busy": sum(1 for c in choferes if c["status"] == "busy"),
    }
    return {"ok": True, "total": len(choferes), "resumen": resumen, "choferes": choferes}


def estado_despacho(booking_id: str) -> dict:
    """Inspecciona el estado de despacho de una reserva (para pruebas)."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bk:
        return {"ok": False, "razon": "no existe"}
    bf = bk[0]["fields"]
    return {
        "ok": True, "booking_id": booking_id,
        "offer_status": bf.get("offer_status"),
        "offered_driver_id": bf.get("offered_driver_id"),
        "offer_attempts": bf.get("offer_attempts"),
        "offer_expires_at": bf.get("offer_expires_at"),
        "driver_id": bf.get("driver_id"),
        "driver_name": bf.get("Driver_Name"),
        "vehicle_id": bf.get("vehicle_id"),
        "offer_log": bf.get("offer_log"),
    }


# ═══════════════════════════════════════════════════════════════
# VERIFICACION QR (cliente y vehiculo)
# ═══════════════════════════════════════════════════════════════

def verificar_qr_vehiculo(vehicle_id: str, token: str) -> dict:
    """Cliente escanea QR fisico del vehiculo. Devuelve {color: green/red/yellow, ...}"""
    if not validar_token_vehiculo(vehicle_id, token):
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "red",
                          notes="Token invalido")
        return {"color": "red", "razon": "QR invalido"}

    vehiculos = _at_get("Vehicles", formula=f"{{vehicle_id}}='{_af(vehicle_id)}'", max_records=1)
    if not vehiculos:
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "red",
                          notes="Vehiculo no existe")
        return {"color": "red", "razon": "Vehiculo no registrado en Emovils"}

    v = vehiculos[0]["fields"]
    if v.get("vehicle_status") != "active":
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "yellow",
                          notes=f"Vehiculo {v.get('vehicle_status')}")
        return {"color": "yellow", "razon": "Vehiculo no esta activo"}

    # Buscar la reserva confirmada actual de este vehiculo
    bookings = _at_get(
        "Bookings",
        formula=f"AND({{vehicle_id}}='{_af(vehicle_id)}', OR({{booking_status}}='confirmed', {{booking_status}}='in_progress'))",
        max_records=5,
    )
    if not bookings:
        _log_verification(None, vehicle_id, None, "client_scans_vehicle", "yellow",
                          notes="Sin reserva activa")
        return {
            "color": "yellow",
            "razon": "Este vehiculo no tiene reservas activas",
            "vehicle": {
                "plate": v.get("vehicle_plate", ""),
                "brand": v.get("vehicle_brand", ""),
                "model": v.get("vehicle_model", ""),
                "color": v.get("vehicle_color", ""),
            },
        }

    booking = bookings[0]
    bf = booking["fields"]
    driver_id = bf.get("driver_id", "")
    if not driver_id:
        _log_verification(bf.get("Booking_ID"), vehicle_id, None, "client_scans_vehicle", "yellow",
                          notes="Sin chofer asignado")
        return {"color": "yellow", "razon": "Sin chofer asignado a la reserva"}

    drivers = _at_get("Drivers", formula=f"{{driver_id}}='{_af(driver_id)}'", max_records=1)
    if not drivers:
        return {"color": "red", "razon": "Chofer no registrado"}
    df = drivers[0]["fields"]

    # Marcar verificacion en booking
    _at_update("Bookings", booking["id"], {
        "vehicle_verification_status": "green",
    })

    _log_verification(bf.get("Booking_ID"), vehicle_id, driver_id, "client_scans_vehicle", "green",
                      notes="OK")

    return {
        "color": "green",
        "vehicle": {
            "plate": v.get("vehicle_plate", ""),
            "brand": v.get("vehicle_brand", ""),
            "model": v.get("vehicle_model", ""),
            "color": v.get("vehicle_color", ""),
            "type": v.get("vehicle_type", ""),
        },
        "driver": {
            "name": df.get("driver_name", ""),
            "phone": df.get("driver_phone", ""),
            "rating": df.get("rating_avg", 0),
        },
        "booking": {
            "code": bf.get("Booking_ID", ""),
            "origen": bf.get("Pickup_Location", ""),
            "destino": bf.get("Dropoff_Location", ""),
            "customer": bf.get("customer_name", ""),
        },
    }


def verificar_qr_cliente(booking_id: str, token: str, driver_id: str) -> dict:
    """Conductor escanea QR del cliente. Confirma recogida → in_progress."""
    if not validar_token_cliente(booking_id, token):
        _log_verification(booking_id, None, driver_id, "driver_scans_client", "red",
                          notes="Token invalido")
        return {"ok": False, "razon": "QR invalido o falsificado"}

    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bookings:
        return {"ok": False, "razon": "Reserva no encontrada"}
    booking = bookings[0]
    bf = booking["fields"]

    # Verificar QR no usado
    if bf.get("customer_qr_status") == "used":
        return {"ok": False, "razon": "QR ya utilizado anteriormente"}

    # Verificar chofer asignado coincide
    if bf.get("driver_id") != driver_id:
        _log_verification(booking_id, bf.get("vehicle_id"), driver_id, "driver_scans_client", "red",
                          notes="Chofer no asignado a esta reserva")
        return {"ok": False, "razon": "Esta reserva no esta asignada a este chofer"}

    # Verificar status confirmado
    if bf.get("booking_status") not in ("confirmed",):
        return {"ok": False, "razon": f"Reserva en estado {bf.get('booking_status')}"}

    now = datetime.now().isoformat()
    _at_update("Bookings", booking["id"], {
        "customer_qr_status": "used",
        "pickup_confirmed": True,
        "pickup_confirmed_at": now,
        "pickup_confirmed_by_driver_id": driver_id,
        "booking_status": "in_progress",
    })

    _log_verification(booking_id, bf.get("vehicle_id"), driver_id, "driver_scans_client", "green",
                      notes="Recogida confirmada")

    return {
        "ok": True,
        "booking_id": booking_id,
        "customer_name": bf.get("customer_name", ""),
        "origen": bf.get("Pickup_Location", ""),
        "destino": bf.get("Dropoff_Location", ""),
        "passengers": bf.get("Passengers", 0),
        "payment_method": bf.get("payment_method", ""),
        "payment_status": bf.get("payment_status", ""),
        "final_price": bf.get("final_price", 0),
    }


def iniciar_viaje(booking_id: str, token: str) -> dict:
    """El conductor escanea el QR del pasajero (con la camara) e inicia el viaje.

    El QR del pasajero abre su hoja de servicio; el chofer pulsa "Iniciar viaje".
    Valida la firma del token del pasajero y pasa la reserva a in_progress
    (recogida confirmada). Idempotente: si ya esta en curso, lo reporta.
    """
    if not validar_token_cliente(booking_id, token):
        return {"ok": False, "razon": "QR invalido o falsificado"}
    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bookings:
        return {"ok": False, "razon": "Reserva no encontrada"}
    booking = bookings[0]
    bf = booking["fields"]
    estado = bf.get("booking_status")
    if estado == "in_progress":
        return {"ok": True, "ya_iniciado": True, "booking_status": "in_progress"}
    if estado in ("completed", "cancelled"):
        return {"ok": False, "razon": f"La reserva esta {estado}"}
    if estado not in ("confirmed",):
        return {"ok": False, "razon": f"Reserva en estado {estado}; aun no se puede iniciar"}
    now = datetime.now().isoformat()
    _at_update("Bookings", booking["id"], {
        "customer_qr_status": "used",
        "pickup_confirmed": True,
        "pickup_confirmed_at": now,
        "booking_status": "in_progress",
    })
    _log_verification(booking_id, bf.get("vehicle_id"), bf.get("driver_id", ""),
                      "driver_scans_client", "green", notes="Viaje iniciado via QR pasajero")
    return {
        "ok": True,
        "booking_status": "in_progress",
        "customer_name": bf.get("customer_name", ""),
        "origen": bf.get("Pickup_Location", ""),
        "destino": bf.get("Dropoff_Location", ""),
    }


def _log_verification(booking_id, vehicle_id, driver_id, verification_type, result, notes=""):
    try:
        _at_create("Verification_Logs", {
            "verification_id": secrets.token_hex(8),
            "booking_id": booking_id or "",
            "vehicle_id": vehicle_id or "",
            "driver_id": driver_id or "",
            "verification_type": verification_type,
            "verification_result": result,
            "verified_at": datetime.now().isoformat(),
            "notes": notes,
        })
    except Exception as exc:
        logger.warning("No se pudo loggear verificacion: %s", exc)


# ═══════════════════════════════════════════════════════════════
# PANEL CONDUCTOR — consulta reservas
# ═══════════════════════════════════════════════════════════════

def reservas_conductor(driver_id: str) -> list[dict]:
    """Reservas asignadas a un chofer en estado activo."""
    bookings = _at_get(
        "Bookings",
        formula=f"AND({{driver_id}}='{_af(driver_id)}', OR({{booking_status}}='confirmed', {{booking_status}}='in_progress'))",
        max_records=20,
    )
    out = []
    for b in bookings:
        bf = b["fields"]
        origen = bf.get("Pickup_Location", "")
        destino = bf.get("Dropoff_Location", "")
        out.append({
            "booking_id": bf.get("Booking_ID", ""),
            "customer_name": bf.get("customer_name", ""),
            "customer_phone": bf.get("customer_phone", ""),
            "origen": origen,
            "destino": destino,
            "service_time": bf.get("service_time", ""),
            "passengers": bf.get("Passengers", 0),
            "vehicle_type": bf.get("vehicle_type_mvp", ""),
            "payment_method": bf.get("payment_method", ""),
            "payment_status": bf.get("payment_status", ""),
            "final_price": bf.get("final_price", 0),
            "distancia_km": bf.get("Distance_KM", ""),
            "maps_url": _maps_url(origen, destino),
            "booking_status": bf.get("booking_status", ""),
            "vehicle_verification_status": bf.get("vehicle_verification_status", "not_started"),
            "pickup_confirmed": bf.get("pickup_confirmed", False),
        })
    return out


def obtener_reserva(booking_id: str) -> Optional[dict]:
    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{_af(booking_id)}'", max_records=1)
    if not bookings:
        return None
    bf = bookings[0]["fields"]
    origen = bf.get("Pickup_Location", "")
    destino = bf.get("Dropoff_Location", "")
    return {
        "booking_id": bf.get("Booking_ID", ""),
        "customer_name": bf.get("customer_name", ""),
        "customer_phone": bf.get("customer_phone", ""),
        "origen": origen,
        "destino": destino,
        "passengers": bf.get("Passengers", 0),
        "vehicle_type": bf.get("vehicle_type_mvp", ""),
        "final_price": bf.get("final_price", 0),
        "currency": bf.get("currency", "RD$"),
        "payment_method": bf.get("payment_method", ""),
        "payment_status": bf.get("payment_status", ""),
        "booking_status": bf.get("booking_status", ""),
        "driver_id": bf.get("driver_id", ""),
        "driver_name": bf.get("Driver_Name", ""),
        "driver_phone": bf.get("Driver_Phone", ""),
        "driver_vehicle": bf.get("Driver_Vehicle", ""),
        "vehicle_id": bf.get("vehicle_id", ""),
        "distancia_km": bf.get("Distance_KM", ""),
        "maps_url": _maps_url(origen, destino),
        "customer_qr_token": bf.get("customer_qr_token", ""),
        "customer_qr_url": bf.get("customer_qr_url", ""),
        "customer_qr_status": bf.get("customer_qr_status", ""),
        "vehicle_verification_status": bf.get("vehicle_verification_status", ""),
        "pickup_confirmed": bf.get("pickup_confirmed", False),
        "service_time": bf.get("service_time", ""),
        "travel_date": bf.get("Travel_Date", ""),
    }


# ═══════════════════════════════════════════════════════════════
# CLI test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Test MVP — tarifario ancla-descuento\n")
    casos = [
        ("Ciudad 8km 2pax dia",      cotizar("Naco", "Piantini", 2, 14, km_estimados=8)),
        ("Ciudad 8km 2pax NOCHE",    cotizar("Naco", "Piantini", 2, 23, km_estimados=8)),
        ("Aeropuerto AILA 4pax",     cotizar("AILA", "Hotel Embajador", 4, 18, km_estimados=28)),
        ("Aeropuerto AILA 8pax",     cotizar("AILA", "Punta Cana", 8, 10, km_estimados=30)),
        ("Interior 110km 4pax",      cotizar("Santo Domingo", "La Romana", 4, 14, km_estimados=110)),
        ("Interior 110km 10pax",     cotizar("Santo Domingo", "La Romana", 10, 14, km_estimados=110)),
        ("Mas de 10 pax (escala)",   cotizar("Naco", "Piantini", 12, 14, km_estimados=8)),
    ]
    for desc, c in casos:
        print(f"{desc:28} seg={c.segmento:10} precio=RD${c.precio_rd:<6} "
              f"lista=RD${c.tarifa_lista:<6} (-{c.descuento_pct}%) "
              f"veh={c.vehiculo_recomendado:13} sup={c.requiere_supervisor}")
    h = cotizar_por_hora(3, 8, 14)
    print(f"\n{'Por hora 3h 8pax':28} seg={h.segmento:10} precio=RD${h.precio_rd:<6} "
          f"lista=RD${h.tarifa_lista:<6} (-{h.descuento_pct}%) veh={h.vehiculo_recomendado}")
