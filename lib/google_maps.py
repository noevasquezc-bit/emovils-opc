"""
Emovils OPC — Google Maps API
Cálculo de rutas, distancias y estimación de precios.
"""
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


# Tipos de geocodificacion demasiado amplios (pais/provincia/municipio) — NO sirven
# para una recogida ni para medir una tarifa real. Si Google solo resuelve a este
# nivel, significa que no encontro el lugar exacto.
_TIPOS_VAGOS = {
    "country", "administrative_area_level_1", "administrative_area_level_2",
    "administrative_area_level_3", "political", "colloquial_area",
}


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
