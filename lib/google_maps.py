"""
Emovils OPC — Google Maps API
Cálculo de rutas, distancias y estimación de precios.
"""
import re
import requests
import logging
from config.settings import GOOGLE_MAPS_API_KEY

logger = logging.getLogger(__name__)
MAPS_BASE = "https://maps.googleapis.com/maps/api"

# Puntos clave de Santo Domingo
KEY_LOCATIONS = {
    "aila_sdq": "Aeropuerto Internacional Las Américas, Santo Domingo, DO",
    "zona_colonial": "Zona Colonial, Santo Domingo, DO",
    "piantini": "Piantini, Santo Domingo, DO",
    "naco": "Naco, Santo Domingo, DO",
    "bella_vista": "Bella Vista, Santo Domingo, DO",
    "punta_cana": "Punta Cana, La Altagracia, DO",
    "bavaro": "Bávaro, La Altagracia, DO",
    "la_romana": "La Romana, DO",
    "samana": "Samaná, DO"
}

# Tarifas base (USD) desde/hacia AILA
BASE_FARES = {
    "zona_colonial": 20,
    "piantini": 25,
    "naco": 25,
    "bella_vista": 22,
    "punta_cana": 120,
    "bavaro": 130,
    "la_romana": 80,
    "samana": 150
}


def get_distance_matrix(origin: str, destination: str) -> dict:
    """Calcula la distancia y tiempo estimado entre dos puntos."""
    if not GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY no configurada — usar tarifario fijo")
        return {"error": "GOOGLE_MAPS_API_KEY no configurada",
                "origin": origin, "destination": destination}
    url = f"{MAPS_BASE}/distancematrix/json"
    params = {
        "origins": origin,
        "destinations": destination,
        "units": "metric",
        "language": "es",
        "key": GOOGLE_MAPS_API_KEY
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    try:
        element = data["rows"][0]["elements"][0]
        return {
            "origin": origin,
            "destination": destination,
            "distance_km": element["distance"]["value"] / 1000,
            "distance_text": element["distance"]["text"],
            "duration_minutes": element["duration"]["value"] / 60,
            "duration_text": element["duration"]["text"],
            "status": element["status"]
        }
    except (KeyError, IndexError) as e:
        logger.error(f"Error calculando distancia: {e}")
        return {"error": str(e), "origin": origin, "destination": destination}


def estimate_price(origin: str, destination: str, passengers: int = 1) -> dict:
    """
    Estima el precio del traslado basado en distancia.
    Precio base mínimo: $20 USD para Santo Domingo.
    """
    matrix = get_distance_matrix(origin, destination)
    if "error" in matrix:
        return {"error": matrix["error"], "price_usd": 25.0}  # Precio default

    distance_km = matrix["distance_km"]

    # Lógica de precios Emovils Airport
    if distance_km <= 15:
        base_price = 20.0
    elif distance_km <= 30:
        base_price = 25.0
    elif distance_km <= 60:
        base_price = 45.0
    elif distance_km <= 120:
        base_price = 80.0
    else:
        base_price = max(80.0, distance_km * 0.8)

    # Ajuste por pasajeros (más de 4 = vehículo grande)
    if passengers > 4:
        base_price *= 1.3

    return {
        "origin": origin,
        "destination": destination,
        "distance_km": round(distance_km, 1),
        "duration_text": matrix["duration_text"],
        "price_usd": round(base_price, 2),
        "passengers": passengers,
        "vehicle_type": "SUV/Van" if passengers > 4 else "Sedán/SUV"
    }


def geocode(address: str) -> dict:
    """Convierte una dirección en coordenadas."""
    if not GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY no configurada — geocode no disponible")
        return {}
    url = f"{MAPS_BASE}/geocode/json"
    params = {"address": address, "key": GOOGLE_MAPS_API_KEY, "language": "es"}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if results:
        loc = results[0]["geometry"]["location"]
        return {"lat": loc["lat"], "lng": loc["lng"], "formatted": results[0]["formatted_address"]}
    return {}


# Tipos de geocodificacion demasiado amplios (pais/provincia) — NO sirven para una
# recogida ni para medir una tarifa real. SI aceptamos nivel municipio/sector
# (administrative_area_level_2 y _3, locality, sublocality): en el Gran Santo Domingo
# la gente cotiza por sector ("Villa Mella", "Los Alcarrizos", "Santo Domingo Este") y
# el centro del sector da una distancia razonable para una cotizacion inicial — el
# cliente confirma origen y destino antes de reservar. Rechazar esos sectores hacia que
# Monserrat escalara al supervisor sin necesidad.
_TIPOS_VAGOS = {
    "country", "administrative_area_level_1", "political", "colloquial_area",
}


def buscar_lugar(texto: str) -> dict:
    """Busca un lugar por NOMBRE, como el buscador de Google Maps.

    Resuelve puntos de referencia que el geocoding clásico no encuentra:
    'Plaza de la Salud', 'Megacentro', 'Agora Mall', 'Caribe Tours', etc.
    Usa Places Text Search sesgado a República Dominicana. Devuelve {} si
    no encuentra nada o si el resultado cae fuera de RD."""
    if not GOOGLE_MAPS_API_KEY or not (texto or "").strip():
        return {}
    url = f"{MAPS_BASE}/place/textsearch/json"
    params = {"query": texto, "key": GOOGLE_MAPS_API_KEY,
              "language": "es", "region": "do"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"buscar_lugar error: {e}")
        return {}
    status = data.get("status", "")
    if status != "OK":
        if status != "ZERO_RESULTS":
            # REQUEST_DENIED aqui = falta habilitar 'Places API' en Google Cloud
            logger.warning(f"buscar_lugar status={status}: {data.get('error_message', '')}")
        return {}
    r = (data.get("results") or [{}])[0]
    loc = r.get("geometry", {}).get("location", {})
    if loc.get("lat") is None or loc.get("lng") is None:
        return {}
    nombre = r.get("name", "")
    direccion = r.get("formatted_address", "")
    # Guardia anti-resultado-lejano: el lugar debe estar en RD.
    en_rd = ("dominican" in direccion.lower() or "dominicana" in direccion.lower())
    if not en_rd:
        logger.info(f"buscar_lugar '{texto}' resolvio fuera de RD: {direccion}")
        return {}
    if nombre and nombre.lower() not in direccion.lower():
        formatted = f"{nombre}, {direccion}"
    else:
        formatted = direccion or nombre
    return {"lat": loc["lat"], "lng": loc["lng"], "formatted": formatted,
            "name": nombre, "types": r.get("types", [])}


def geocode_detallado(address: str) -> dict:
    """Geocodifica una direccion devolviendo informacion de PRECISION.

    preciso=True solo si Google encontro el lugar exacto (no 'partial_match')
    y no es una zona amplia (pais/provincia). Si preciso=False, la direccion es
    demasiado vaga o tiene errores: NO se debe cotizar, hay que pedir mas detalle.
    """
    if not GOOGLE_MAPS_API_KEY:
        return {"ok": False, "preciso": False, "motivo": "sin_api_key"}
    url = f"{MAPS_BASE}/geocode/json"
    params = {"address": address, "key": GOOGLE_MAPS_API_KEY,
              "language": "es", "region": "do"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"geocode_detallado error: {e}")
        return {"ok": False, "preciso": False, "motivo": f"error:{e}"}

    results = data.get("results", [])
    if not results:
        # Geocoding no encontro nada — puede ser un NOMBRE de lugar
        # ("Plaza de la Salud"). Buscar como en el buscador de Google Maps.
        lugar = buscar_lugar(address)
        if lugar:
            return {"ok": True, "preciso": True, "via_places": True,
                    "partial_match": False, "solo_vago": False,
                    "types": sorted(lugar.get("types", [])), "location_type": "PLACES",
                    "formatted": lugar["formatted"],
                    "lat": lugar["lat"], "lng": lugar["lng"]}
        return {"ok": False, "preciso": False, "motivo": "sin_resultados",
                "status": data.get("status", "")}

    r = results[0]
    partial = bool(r.get("partial_match", False))
    types = set(r.get("types", []))
    loc_type = r.get("geometry", {}).get("location_type", "")
    # 'solo_vago' = Google solo pudo ubicar el texto a nivel pais/provincia/municipio
    # (p.ej. "Caribe tour" -> {country, political} = "República Dominicana"). Eso da una
    # distancia falsa. NO usamos partial_match como criterio: Google lo activa tambien en
    # direcciones validas que resuelven a una calle concreta (route), y rechazarlas seria
    # un falso positivo. La senal confiable es que el resultado NO sea solo una zona amplia.
    solo_vago = types.issubset(_TIPOS_VAGOS) if types else True
    if solo_vago:
        # Google solo ubico pais/provincia — tipico cuando el texto es el NOMBRE
        # de un lugar ("Caribe Tours", "Megacentro"). Intentar como busqueda de
        # Google Maps antes de rendirse.
        lugar = buscar_lugar(address)
        if lugar:
            return {"ok": True, "preciso": True, "via_places": True,
                    "partial_match": partial, "solo_vago": False,
                    "types": sorted(lugar.get("types", [])), "location_type": "PLACES",
                    "formatted": lugar["formatted"],
                    "lat": lugar["lat"], "lng": lugar["lng"]}
    preciso = not solo_vago
    return {
        "ok": True,
        "preciso": preciso,
        "partial_match": partial,
        "solo_vago": solo_vago,
        "types": sorted(types),
        "location_type": loc_type,
        "formatted": r.get("formatted_address", ""),
        "lat": r["geometry"]["location"]["lat"],
        "lng": r["geometry"]["location"]["lng"],
    }


def get_directions_url(origin: str, destination: str) -> str:
    """Genera un URL de Google Maps para compartir por WhatsApp."""
    o = origin.replace(" ", "+")
    d = destination.replace(" ", "+")
    return f"https://www.google.com/maps/dir/{o}/{d}"


def _decode_polyline(s: str):
    """Decodifica una polyline codificada de Google a lista de (lat, lng).
    Algoritmo estandar de Google Encoded Polyline Algorithm Format."""
    if not s:
        return []
    puntos, index, lat, lng = [], 0, 0, 0
    longitud = len(s)
    while index < longitud:
        for es_lng in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if (result & 1) else (result >> 1)
            if es_lng:
                lng += d
            else:
                lat += d
        puntos.append((lat / 1e5, lng / 1e5))
    return puntos


def ruta_calles(o_lat, o_lng, d_lat, d_lng):
    """Geometria de la ruta por CALLE entre dos puntos (lat,lng) via Google
    Directions: lista [[lat,lng], ...] o [] si no hay clave / falla / la API no
    esta habilitada. Es la MISMA fuente que geocodifica los puntos, asi la linea
    de ruta coincide con las calles reales (OSRM publico daba rutas torcidas en RD)."""
    if not GOOGLE_MAPS_API_KEY:
        return []
    url = f"{MAPS_BASE}/directions/json"
    params = {
        "origin": f"{o_lat},{o_lng}",
        "destination": f"{d_lat},{d_lng}",
        "mode": "driving",
        "language": "es",
        "region": "do",
        "key": GOOGLE_MAPS_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"ruta_calles error: {e}")
        return []
    if data.get("status") != "OK":
        logger.warning("ruta_calles status=%s %s",
                       data.get("status"), data.get("error_message", ""))
        return []
    rutas = data.get("routes", [])
    if not rutas:
        return []
    encoded = rutas[0].get("overview_polyline", {}).get("points", "")
    return [[round(la, 6), round(ln, 6)] for la, ln in _decode_polyline(encoded)]


def reverse_geocode(lat, lng) -> str:
    """Convierte coordenadas (lat, lng) en una dirección legible y CORTA.

    Cuando el cliente comparte su ubicación por WhatsApp, llega como números
    largos (18.44570541381836, -69.93488311767578). Eso es incómodo y Monserrat
    no debería leerlo. Aquí lo volvemos algo como "Calle El Sol, Piantini".
    Devuelve "" si no hay clave o Google no encuentra nada.
    """
    if not GOOGLE_MAPS_API_KEY:
        return ""
    url = f"{MAPS_BASE}/geocode/json"
    params = {"latlng": f"{lat},{lng}", "key": GOOGLE_MAPS_API_KEY,
              "language": "es", "region": "do"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        logger.warning(f"reverse_geocode error: {e}")
        return ""
    if not results:
        return ""
    return _acortar_direccion(results[0].get("formatted_address", ""))


def _acortar_direccion(formatted: str) -> str:
    """Quita el país y el código postal, y deja a lo sumo 3 partes (calle, sector,
    ciudad) para que la dirección sea corta y fácil de leer."""
    if not formatted:
        return ""
    partes = [p.strip() for p in formatted.split(",") if p.strip()]
    paises = {"república dominicana", "republica dominicana",
              "dominican republic", "rep. dominicana", "rd"}
    limpio = []
    for p in partes:
        if p.lower() in paises:
            continue
        # Quita códigos postales (4-6 dígitos sueltos al final de la parte).
        p2 = re.sub(r"\s*\b\d{4,6}\b\s*$", "", p).strip()
        if p2:
            limpio.append(p2)
    return ", ".join(limpio[:3])
