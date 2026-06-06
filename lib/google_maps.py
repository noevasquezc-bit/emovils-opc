"""
Emovils — Google Maps Distance Matrix
Calcula distancias reales y precios exactos usando la formula oficial de Emovils.
"""
import requests
import logging
from datetime import datetime
from config.settings import GOOGLE_MAPS_API_KEY

logger = logging.getLogger(__name__)
MAPS_BASE = "https://maps.googleapis.com/maps/api"


def get_distance_matrix(origin: str, destination: str) -> dict:
    """
    Llama a Google Maps Distance Matrix API.
    origin/destination pueden ser texto (direccion) o "lat,lon".
    Retorna: distance_km, duration_minutes, o error.
    """
    if not GOOGLE_MAPS_API_KEY:
        return {"error": "GOOGLE_MAPS_API_KEY no configurada"}

    url = f"{MAPS_BASE}/distancematrix/json"
    params = {
        "origins": origin,
        "destinations": destination,
        "units": "metric",
        "language": "es",
        "region": "do",
        "key": GOOGLE_MAPS_API_KEY
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "OK":
            return {"error": f"Maps API status: {data.get('status')}"}

        element = data["rows"][0]["elements"][0]
        if element.get("status") != "OK":
            return {"error": f"Element status: {element.get('status')}"}

        return {
            "distance_km": round(element["distance"]["value"] / 1000, 1),
            "distance_text": element["distance"]["text"],
            "duration_minutes": round(element["duration"]["value"] / 60, 0),
            "duration_text": element["duration"]["text"],
        }
    except Exception as e:
        logger.error("Error Google Maps: %s", e)
        return {"error": str(e)}


def _emovils_price(distance_km: float, is_van: bool = False,
                   is_night: bool = False, round_trip: bool = False) -> int:
    """
    Aplica la formula oficial de precios Emovils.
    Retorna precio final en RD$ (entero, redondeado al multiplo de 25 mas cercano).

    Formula sedan:
      0-3 km:   RD$300 (minimo)
      3-10 km:  RD$300 + (km-3) x 50
      10-20 km: RD$650 + (km-10) x 40
      20+ km:   RD$1,050 + (km-20) x 30
    """
    km = max(0.0, distance_km)

    if km <= 3:
        price = 300.0
    elif km <= 10:
        price = 300.0 + (km - 3) * 50
    elif km <= 20:
        price = 650.0 + (km - 10) * 40
    else:
        price = 1050.0 + (km - 20) * 30

    if is_van:
        price *= 1.40

    if is_night:
        price *= 1.20

    if round_trip:
        price *= 1.90  # ida y vuelta con 5% descuento

    # Redondear al multiplo de 25 mas cercano
    price = round(price / 25) * 25
    return max(300, int(price))


def calculate_route_price(origin: str, destination: str,
                          passengers: int = 1,
                          service_hour: int = None,
                          round_trip: bool = False) -> dict:
    """
    Calcula precio real usando Google Maps + formula Emovils.
    
    Args:
        origin: direccion o "lat,lon" de recogida
        destination: direccion o sector de destino
        passengers: cantidad de pasajeros
        service_hour: hora del servicio (0-23), None = hora actual
        round_trip: si es ida y vuelta
    
    Returns dict con: price_dop, vehicle, distance_km, duration_text,
                      is_night, note_for_agent
    """
    # Determinar vehiculo
    is_van = passengers > 4
    vehicle = "van" if is_van else "sedan"

    # Determinar si es nocturno
    if service_hour is None:
        service_hour = datetime.now().hour
    is_night = service_hour >= 21 or service_hour < 6

    # Llamar a Google Maps
    route = get_distance_matrix(origin, destination)

    if "error" in route:
        logger.warning("Google Maps error: %s", route["error"])
        return {
            "error": route["error"],
            "vehicle": vehicle,
            "is_night": is_night,
        }

    km = route["distance_km"]
    price = _emovils_price(km, is_van=is_van, is_night=is_night, round_trip=round_trip)

    night_note = " (incluye recargo nocturno 20%)" if is_night else ""
    van_note = " (van, hasta 7 pax)" if is_van else " (sedan, hasta 4 pax)"

    return {
        "price_dop": price,
        "vehicle": vehicle,
        "distance_km": km,
        "distance_text": route["distance_text"],
        "duration_minutes": route["duration_minutes"],
        "duration_text": route["duration_text"],
        "is_night": is_night,
        "is_van": is_van,
        "round_trip": round_trip,
        # Texto listo para inyectar en el prompt de Claude
        "note_for_agent": (
            f"[PRECIO_CALCULADO: RD${price:,}{night_note}{van_note} | "
            f"{route['distance_text']} | {route['duration_text']}]"
        )
    }


def get_directions_url(origin: str, destination: str) -> str:
    o = origin.replace(" ", "+")
    d = destination.replace(" ", "+")
    return f"https://www.google.com/maps/dir/{o}/{d}"
