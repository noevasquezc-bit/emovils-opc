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

# Token HMAC firma QR (deberia estar en env var)
QR_SIGNING_KEY = os.getenv("QR_SIGNING_KEY", "emovils-mvp-2026-default-CHANGE-IN-PROD")

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


# ═══════════════════════════════════════════════════════════════
# AIRTABLE CRUD (directo, sin dependencia de modulos viejos)
# ═══════════════════════════════════════════════════════════════

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
        "service_time": service_time or now,
        "Travel_Date": service_date or datetime.now().date().isoformat(),
        "Created_At": now,
    }
    rec = _at_create("Bookings", fields)
    logger.info("✓ Booking creado: %s (record %s)", booking_id, rec["id"])

    # Despacho automatico: ofrecer al chofer mas cercano. Nunca rompe la reserva.
    despacho = None
    if booking_status == "confirmed":
        try:
            despacho = iniciar_despacho(booking_id)
            logger.info("Despacho %s: %s", booking_id,
                        despacho.get("offer_status") or despacho.get("razon"))
        except Exception as e:
            logger.warning("Despacho fallo para %s: %s", booking_id, e)

    return {
        "booking_id": booking_id,
        "record_id": rec["id"],
        "qr_url": qr_url,
        "qr_token": qr_token,
        "booking_status": booking_status,
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
                formula=f"AND({{driver_id}}='{driver_id}', {{driver_status}}='available')",
                max_records=1,
            )
            if not drivers:
                continue

            d = drivers[0]
            df = d["fields"]
            booking = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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
        formula=f"AND({{vehicle_type}}='{vehicle_type}', {{vehicle_status}}='active')",
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

OFERTA_TTL_SEG = 60          # segundos que tiene el chofer para responder
# Por decision del negocio el chofer queda EN LINEA hasta desconectarse (panel
# web) o entrar en carrera (busy); su ubicacion NO caduca por defecto. Este
# valor solo aplica si se pasa max_edad_horas explicitamente a choferes_cercanos.
UBICACION_MAX_HORAS = 12


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
    drivers = _at_get("Drivers", formula=f"{{driver_id}}='{driver_id}'", max_records=1)
    if not drivers:
        return {"ok": False, "razon": "chofer no encontrado"}
    d = drivers[0]
    df = d["fields"]
    estado = df.get("driver_status")
    # Mientras manda GPS queda EN LINEA (available), salvo suspendido o en carrera.
    nuevo = estado if estado in ("suspended", "busy") else "available"
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
    drivers = _at_get("Drivers", formula=f"{{driver_id}}='{driver_id}'", max_records=1)
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
    ds = _at_get("Drivers", formula=f"{{driver_id}}='{driver_id}'", max_records=1)
    if not ds:
        return {"ok": False, "razon": "Chofer no encontrado."}
    df = ds[0]["fields"]
    veh_id = df.get("assigned_vehicle_id", "") or ""
    vf = {}
    if veh_id:
        vs = _at_get("Vehicles", formula=f"{{vehicle_id}}='{veh_id}'", max_records=1)
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
    ds = _at_get("Drivers", formula=f"{{driver_id}}='{driver_id}'", max_records=1)
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
        vs = _at_get("Vehicles", formula=f"{{vehicle_id}}='{veh_id}'", max_records=1)
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


def _msg_oferta(elegido: dict, bf: dict) -> str:
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


def _enviar_oferta_whatsapp(elegido: dict, bf: dict) -> None:
    try:
        from opc.whatsapp_green_api import notificar_chofer
        notificar_chofer(elegido["driver_phone"], _msg_oferta(elegido, bf))
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
        # 1) Mensaje HONESTO al cliente (no se promete un chofer que no existe).
        if cliente:
            enviar_a_cliente(
                cliente,
                "🙏 Disculpa la demora. En este momento *no tenemos choferes "
                "disponibles en tu zona*.\n\n"
                "Ya estamos pasando tu solicitud a un supervisor, que te va a "
                "contactar personalmente para ayudarte. Gracias por tu paciencia. 💙"
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
    expira = _now_utc() + timedelta(seconds=OFERTA_TTL_SEG)
    _at_update("Bookings", booking_rec["id"], {
        "offer_status": "offered",
        "offered_driver_id": elegido["driver_id"],
        "offer_expires_at": expira.isoformat(),
        "offer_attempts": intentos,
        "offer_log": _log_append(bf, f"oferta #{intentos} -> {elegido['driver_id']} ({elegido['dist_km']} km)"),
    })
    _enviar_oferta_whatsapp(elegido, bf)
    return {
        "ok": True, "offer_status": "offered", "intento": intentos,
        "driver_id": elegido["driver_id"], "driver_name": elegido["driver_name"],
        "dist_km": elegido["dist_km"], "expira_en_seg": OFERTA_TTL_SEG,
        "candidatos": [{"driver_id": c["driver_id"], "dist_km": c["dist_km"]} for c in candidatos],
    }


def iniciar_despacho(booking_id: str) -> dict:
    """Arranca la busqueda del chofer mas cercano para una reserva."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
    if not bk:
        return {"ok": False, "razon": "booking no existe"}
    return _despachar_siguiente(bk[0])


def _reofrecer(booking_id: str) -> dict:
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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
        veh = _at_get("Vehicles", formula=f"{{vehicle_id}}='{veh_id}'", max_records=1)
        vf = veh[0]["fields"] if veh else {}
    _at_update("Bookings", booking["id"], {
        "driver_id": did,
        "vehicle_id": veh_id,
        "Driver_Name": df.get("driver_name", ""),
        "Driver_Phone": df.get("driver_phone", ""),
        "Driver_Vehicle": f"{vf.get('vehicle_brand','')} {vf.get('vehicle_model','')} {vf.get('vehicle_plate','')}".strip(),
        "offer_status": "accepted",
        "offer_log": _log_append(bf, f"ACEPTADA por {did}"),
    })
    _at_update("Drivers", driver["id"], {"driver_status": "busy"})

    # Avisar al pasajero que su chofer va en camino + link de seguimiento en vivo
    try:
        from opc.whatsapp_green_api import enviar_a_cliente as _wa_cli
        cust = bf.get("customer_phone", "")
        if cust:
            link = f"{PUBLIC_BASE_URL}/seguir/{bf.get('Booking_ID','')}"
            veh = " ".join(x for x in [vf.get("vehicle_brand", ""),
                                       vf.get("vehicle_model", ""),
                                       vf.get("vehicle_color", "")] if x).strip()
            placa = vf.get("vehicle_plate", "")
            _wa_cli(
                cust,
                "✅ ¡Tu Emovils va en camino! 🚖\n\n"
                f"Chofer: {df.get('driver_name','')}\n"
                + (f"Vehículo: {veh}\n" if veh else "")
                + (f"Placa: {placa}\n" if placa else "")
                + f"\n📍 Síguelo en vivo aquí:\n{link}\n\n"
                "Te avisaremos con un sonido cuando esté a 100 metros.")
    except Exception as _e:
        logger.warning("Aviso al pasajero (aceptacion) fallo: %s", _e)

    return {
        "ok": True, "es_chofer": True, "accion": "aceptada",
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


def responder_oferta(driver_phone: str, texto: str) -> dict:
    """Procesa la respuesta del chofer (ACEPTO / RECHAZO) a una oferta vigente."""
    drivers = _buscar_driver_por_tel(driver_phone)
    if not drivers:
        return {"ok": False, "es_chofer": False}
    d = drivers[0]
    did = d["fields"].get("driver_id")
    bks = _at_get("Bookings",
                  formula=f"AND({{offered_driver_id}}='{did}', {{offer_status}}='offered')",
                  max_records=1)
    if not bks:
        return {"ok": False, "es_chofer": True, "razon": "no_oferta_vigente"}
    booking = bks[0]
    bf = booking["fields"]
    te = _parse_dt(bf.get("offer_expires_at"))
    if te and _now_utc() > te:
        return {"ok": False, "es_chofer": True, "razon": "oferta_vencida"}
    t = _norm_palabra(texto)
    acepta = t.startswith("acepto") or t in {"si", "ok", "dale", "voy", "aceptar", "claro", "listo", "la tomo"}
    rechaza = t.startswith("rechazo") or t in {"no", "paso", "rechazar", "no puedo", "nel"}
    if acepta and not rechaza:
        return _aceptar_oferta(booking, d)
    if rechaza:
        return _rechazar_oferta(booking, did)
    return {"ok": False, "es_chofer": True, "razon": "respuesta_no_entendida"}


def completar_viaje(booking_id: str) -> dict:
    """Cierra una carrera: reserva -> completed y el chofer vuelve a 'available'
    (EN LINEA), listo para la siguiente. Cumple el modelo 'siempre en linea':
    el chofer solo deja de estar disponible si se desconecta en la web."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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
        drv = _at_get("Drivers", formula=f"{{driver_id}}='{did}'", max_records=1)
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
                  formula=f"AND({{driver_id}}='{did}', {{booking_status}}='in_progress')",
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


def estado_seguimiento(booking_id: str) -> dict:
    """Datos para la pagina de seguimiento del pasajero: posicion del chofer,
    punto de recogida, distancia en metros y minutos estimados de llegada."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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
        ds = _at_get("Drivers", formula=f"{{driver_id}}='{did}'", max_records=1)
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


def estado_despacho(booking_id: str) -> dict:
    """Inspecciona el estado de despacho de una reserva (para pruebas)."""
    bk = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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

    vehiculos = _at_get("Vehicles", formula=f"{{vehicle_id}}='{vehicle_id}'", max_records=1)
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
        formula=f"AND({{vehicle_id}}='{vehicle_id}', OR({{booking_status}}='confirmed', {{booking_status}}='in_progress'))",
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

    drivers = _at_get("Drivers", formula=f"{{driver_id}}='{driver_id}'", max_records=1)
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

    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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
    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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
        formula=f"AND({{driver_id}}='{driver_id}', OR({{booking_status}}='confirmed', {{booking_status}}='in_progress'))",
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
    bookings = _at_get("Bookings", formula=f"{{Booking_ID}}='{booking_id}'", max_records=1)
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
