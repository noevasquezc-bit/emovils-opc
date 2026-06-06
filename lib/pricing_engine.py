"""
Emovils — Motor de Cotizacion de Precios
Calcula tarifas de forma automatica, clara, rentable y alineada al mercado dominicano.
Todas las variables son configurables sin reprogramar la logica.
"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ─────────────────────────────────────────────
# CONFIGURACION GLOBAL (editable sin tocar la logica)
# ─────────────────────────────────────────────
PRICING_CONFIG = {
    # Tarifa minima absoluta
    "minimum_fare_dop": 300,

    # Capacidades de vehiculos
    "sedan_capacity": 4,
    "van_capacity": 7,

    # Formula base sedan (servicio urbano)
    "sedan_base_fare": 300,
    "sedan_price_per_km": 40,
    "sedan_price_per_minute": 4,

    # Multiplicador van sobre precio sedan
    "van_multiplier": 1.40,

    # Recargo nocturno
    "night_surcharge_pct": 20,       # porcentaje
    "night_start_hour": 21,          # 9:00 PM
    "night_end_hour": 6,             # 6:00 AM

    # Distribucion interna (NUNCA mostrar al cliente)
    "driver_payout_pct": 70,
    "emovils_margin_pct": 30,

    # Tiempo de espera gratuito
    "free_waiting_minutes": 15,

    # Tabla de espera (minutos -> cargo en RD$)
    "waiting_fee_table": [
        {"max_minutes": 15,  "fee": 0},
        {"max_minutes": 30,  "fee": 75},
        {"max_minutes": 60,  "fee": 150},
        {"max_minutes": 75,  "fee": 200},
        {"max_minutes": 90,  "fee": 300},
    ],
    "waiting_over_90_supervisor": True,

    # Paradas adicionales
    "extra_stop_fee_min": 150,
    "extra_stop_fee_max": 300,

    # Ida y vuelta descuento maximo
    "round_trip_discount_max_pct": 10,

    # Tasa de cambio USD → DOP (configurable)
    "usd_to_dop_rate": 60.0,

    # Escalamiento automatico a supervisor
    "b2b_requires_supervisor": True,
    "tours_require_supervisor": True,
    "over_7_passengers_supervisor": True,
}

# ─────────────────────────────────────────────
# TABLA DE TARIFAS AEROPUERTO SDQ (editable)
# ─────────────────────────────────────────────
AIRPORT_FARES_USD = {
    "boca_chica":       {"sedan": (25, 30), "van": (35, 45)},
    "sto_domingo_este": {"sedan": (30, 35), "van": (40, 50)},
    "zona_colonial":    {"sedan": (35, 40), "van": (45, 55)},
    "distrito_nacional":{"sedan": (35, 40), "van": (45, 55)},
    "piantini":         {"sedan": (40, 45), "van": (55, 65)},
    "naco":             {"sedan": (40, 45), "van": (55, 65)},
    "serrallес":        {"sedan": (40, 45), "van": (55, 65)},
    "punta_cana":       {"sedan": (145, 160), "van": (220, 280)},
}

# Palabras clave para mapear zona desde texto libre
AIRPORT_ZONE_KEYWORDS = {
    "boca_chica":       ["boca chica"],
    "sto_domingo_este": ["este", "sd este", "santo domingo este", "los alcarrizos", "km 14"],
    "zona_colonial":    ["colonial", "zona colonial", "ciudad colonial"],
    "distrito_nacional":["dn", "distrito", "downtown", "centro"],
    "piantini":         ["piantini"],
    "naco":             ["naco"],
    "serralles":        ["serrallес", "serralles"],
    "punta_cana":       ["punta cana", "puntacana", "bavaro", "bavaro", "cap cana"],
}


# ─────────────────────────────────────────────
# TIPOS Y ENUMS
# ─────────────────────────────────────────────
class ServiceType(str, Enum):
    URBAN = "urban"
    AIRPORT = "airport"
    EXECUTIVE = "executive"
    B2B = "b2b"
    TOUR = "tour"
    ROUND_TRIP = "round_trip"

class VehicleType(str, Enum):
    SEDAN = "sedan"
    VAN = "van"
    AUTO = "auto"   # el sistema decide


@dataclass
class FareInput:
    origin: str = ""
    destination: str = ""
    distance_km: float = 0.0
    estimated_time_minutes: float = 0.0
    passengers: int = 1
    luggage_count: int = 0
    service_type: str = ServiceType.URBAN
    vehicle_type: str = VehicleType.AUTO
    date: str = ""
    time: str = ""          # formato "HH:MM" 24h
    extra_stops: int = 0
    waiting_minutes: float = 0.0
    is_round_trip: bool = False
    is_airport: bool = False
    airport_zone: str = ""
    currency: str = "DOP"


@dataclass
class FareOutput:
    recommended_vehicle: str = ""
    passenger_capacity: int = 0
    base_fare: float = 0
    distance_component: float = 0
    time_component: float = 0
    night_surcharge: float = 0
    waiting_fee: float = 0
    extra_stop_fee: float = 0
    round_trip_discount: float = 0
    final_price_dop: float = 0
    final_price_usd: Optional[float] = None
    driver_payout_internal: float = 0   # NUNCA mostrar al cliente
    emovils_margin_internal: float = 0  # NUNCA mostrar al cliente
    client_message: str = ""
    requires_supervisor: bool = False
    supervisor_reason: str = ""
    missing_data: list = field(default_factory=list)


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────
def _is_night_time(time_str: str, cfg: dict) -> bool:
    """Determina si la hora cae en horario nocturno."""
    if not time_str:
        return False
    try:
        parts = time_str.strip().replace(":", "").replace("h", "")
        if len(parts) >= 4:
            hour = int(parts[:2])
        elif len(parts) == 3:
            hour = int(parts[:1])
        else:
            return False
        night_start = cfg["night_start_hour"]  # 21
        night_end = cfg["night_end_hour"]       # 6
        return hour >= night_start or hour < night_end
    except Exception:
        return False


def _commercial_round(price: float) -> int:
    """Redondeo comercial limpio al multiplo de 25 mas cercano hacia arriba."""
    import math
    return int(math.ceil(price / 25) * 25)


def _get_waiting_fee(minutes: float, cfg: dict) -> tuple:
    """Retorna (fee_dop, requires_supervisor)."""
    if minutes <= cfg["free_waiting_minutes"]:
        return 0, False
    for tier in cfg["waiting_fee_table"]:
        if minutes <= tier["max_minutes"]:
            return tier["fee"], False
    # Mas de 90 minutos
    if cfg["waiting_over_90_supervisor"]:
        return 0, True
    return 300, False


def _detect_airport_zone(text: str) -> str:
    """Intenta detectar la zona del aeropuerto desde texto libre."""
    text_lower = text.lower()
    for zone, keywords in AIRPORT_ZONE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return zone
    return ""


def _get_airport_fare(zone: str, vehicle: str, cfg: dict) -> Optional[float]:
    """Retorna el precio promedio en USD para la zona y vehiculo dados."""
    fares = AIRPORT_FARES_USD.get(zone, {})
    if not fares:
        return None
    low, high = fares.get(vehicle, (0, 0))
    if low == 0:
        return None
    avg_usd = (low + high) / 2
    return round(avg_usd, 2)


# ─────────────────────────────────────────────
# FUNCION PRINCIPAL
# ─────────────────────────────────────────────
def calculate_fare(inp: FareInput, cfg: dict = None) -> FareOutput:
    """
    Motor principal de cotizacion de Emovils.
    Recibe FareInput, retorna FareOutput con precio final y mensaje al cliente.
    """
    if cfg is None:
        cfg = PRICING_CONFIG

    out = FareOutput()

    # ── 1. Validar datos minimos ──────────────────────────────────
    if not inp.origin:
        out.missing_data.append("punto de recogida")
    if not inp.destination:
        out.missing_data.append("destino")
    if inp.passengers <= 0:
        out.missing_data.append("cantidad de pasajeros")

    if out.missing_data:
        fields = ", ".join(out.missing_data)
        out.client_message = (
            "Para calcularle el precio necesito los siguientes datos: "
            + fields + ". Por favor indicame esa informacion."
        )
        return out

    # ── 2. Verificar escalamiento obligatorio ─────────────────────
    svc = inp.service_type.lower() if isinstance(inp.service_type, str) else inp.service_type.value

    if inp.passengers > cfg["van_capacity"]:
        out.requires_supervisor = True
        out.supervisor_reason = "Mas de 7 pasajeros"
        out.client_message = (
            "Para mas de 7 pasajeros necesitamos validar disponibilidad y definir "
            "si se requiere mas de una unidad. Lo voy a pasar con un supervisor de Emovils."
        )
        return out

    if svc == ServiceType.B2B or "b2b" in svc or "empresa" in svc or "corporat" in svc:
        out.requires_supervisor = True
        out.supervisor_reason = "Servicio B2B o empresarial"
        out.client_message = (
            "Ese servicio corresponde a una cotizacion empresarial. "
            "Para ofrecerle una tarifa correcta, lo voy a pasar con un supervisor de Emovils."
        )
        return out

    if svc == ServiceType.TOUR or "tour" in svc or "excursion" in svc or "turismo" in svc:
        out.requires_supervisor = True
        out.supervisor_reason = "Tour o servicio turistico"
        out.client_message = (
            "Ese servicio corresponde a una cotizacion turistica personalizada. "
            "Lo voy a pasar con un supervisor para validar ruta, duracion, paradas y tipo de vehiculo."
        )
        return out

    # ── 3. Determinar vehiculo recomendado ────────────────────────
    if inp.passengers <= cfg["sedan_capacity"]:
        vehicle = "sedan"
        out.recommended_vehicle = "sedan"
        out.passenger_capacity = cfg["sedan_capacity"]
    else:
        vehicle = "van"
        out.recommended_vehicle = "van"
        out.passenger_capacity = cfg["van_capacity"]

    # Respetar preferencia del cliente si especifico un vehiculo
    if inp.vehicle_type and inp.vehicle_type != VehicleType.AUTO:
        vehicle = inp.vehicle_type.lower() if isinstance(inp.vehicle_type, str) else inp.vehicle_type.value

    # ── 4. Calcular precio base ───────────────────────────────────
    is_airport = inp.is_airport or svc == ServiceType.AIRPORT or "aeropuerto" in inp.destination.lower()

    if is_airport:
        # Tarifa fija por zona
        zone = inp.airport_zone or _detect_airport_zone(inp.destination) or _detect_airport_zone(inp.origin)

        if not zone:
            out.requires_supervisor = True
            out.supervisor_reason = "Zona de aeropuerto no identificada"
            out.client_message = (
                "Para el traslado al aeropuerto, necesito confirmar su zona exacta de destino o recogida. "
                "Por favor indicame el sector, barrio o municipio."
            )
            return out

        price_usd = _get_airport_fare(zone, vehicle, cfg)
        if price_usd is None:
            out.requires_supervisor = True
            out.supervisor_reason = "Zona no tiene tarifa configurada"
            out.client_message = (
                "Esa zona no esta en nuestra tabla de tarifas estandar para aeropuerto. "
                "Lo voy a pasar con un supervisor para ofrecerle una cotizacion correcta."
            )
            return out

        price_dop = price_usd * cfg["usd_to_dop_rate"]
        out.final_price_usd = price_usd
        out.base_fare = price_dop
        out.distance_component = 0
        out.time_component = 0

    else:
        # Formula por distancia y tiempo (servicio urbano)
        if inp.distance_km <= 0 or inp.estimated_time_minutes <= 0:
            out.requires_supervisor = True
            out.supervisor_reason = "No se pudo calcular distancia o tiempo"
            out.client_message = (
                "Para calcular el precio necesito la distancia y tiempo estimados del traslado. "
                "Por favor comparta su ubicacion de WhatsApp o la direccion exacta de recogida y destino."
            )
            return out

        base = cfg["sedan_base_fare"]
        dist_comp = inp.distance_km * cfg["sedan_price_per_km"]
        time_comp = inp.estimated_time_minutes * cfg["sedan_price_per_minute"]
        sedan_price = base + dist_comp + time_comp

        out.base_fare = base
        out.distance_component = dist_comp
        out.time_component = time_comp

        if vehicle == "van":
            sedan_price = sedan_price * cfg["van_multiplier"]

        price_dop = sedan_price

    # ── 5. Recargo nocturno ───────────────────────────────────────
    if _is_night_time(inp.time, cfg):
        night_extra = price_dop * (cfg["night_surcharge_pct"] / 100)
        out.night_surcharge = night_extra
        price_dop += night_extra

    # ── 6. Cargo de espera ────────────────────────────────────────
    if inp.waiting_minutes > 0:
        waiting_fee, waiting_supervisor = _get_waiting_fee(inp.waiting_minutes, cfg)
        if waiting_supervisor:
            out.requires_supervisor = True
            out.supervisor_reason = "Espera mayor a 90 minutos"
            out.client_message = (
                "El tiempo de espera solicitado supera los 90 minutos. "
                "Lo voy a pasar con un supervisor para validar disponibilidad y condiciones."
            )
            return out
        out.waiting_fee = waiting_fee
        price_dop += waiting_fee

    # ── 7. Paradas adicionales ────────────────────────────────────
    if inp.extra_stops > 0:
        stop_fee = cfg["extra_stop_fee_min"] * inp.extra_stops
        out.extra_stop_fee = stop_fee
        price_dop += stop_fee

    # ── 8. Descuento ida y vuelta ─────────────────────────────────
    if inp.is_round_trip:
        discount_pct = cfg["round_trip_discount_max_pct"]
        discount = price_dop * 2 * (discount_pct / 100)
        price_dop = price_dop * 2 - discount
        out.round_trip_discount = discount

    # ── 9. Tarifa minima ─────────────────────────────────────────
    if price_dop < cfg["minimum_fare_dop"]:
        price_dop = cfg["minimum_fare_dop"]

    # ── 10. Redondeo comercial ────────────────────────────────────
    price_dop = _commercial_round(price_dop)

    # ── 11. Distribucion interna (NUNCA mostrar al cliente) ───────
    out.driver_payout_internal = round(price_dop * cfg["driver_payout_pct"] / 100, 2)
    out.emovils_margin_internal = round(price_dop * cfg["emovils_margin_pct"] / 100, 2)

    out.final_price_dop = price_dop

    # ── 12. Generar mensaje al cliente ────────────────────────────
    vehicle_label = "sedan (hasta 4 pasajeros)" if vehicle == "sedan" else "van (hasta 7 pasajeros)"
    price_fmt = "{:,}".format(int(price_dop))

    extras_note = ""
    if out.waiting_fee > 0:
        extras_note += f" Se aplico cargo adicional de RD${int(out.waiting_fee):,} por tiempo de espera."
    if out.extra_stop_fee > 0:
        extras_note += f" Se aplico cargo de RD${int(out.extra_stop_fee):,} por parada(s) adicional(es)."
    if out.night_surcharge > 0:
        extras_note += " Se aplico recargo nocturno del 20%."

    rt_note = " (ida y vuelta con descuento incluido)" if inp.is_round_trip else ""

    out.client_message = (
        f"Para ese traslado le recomiendo {vehicle_label}.\n\n"
        f"Precio del servicio: RD${price_fmt}{rt_note}.\n"
        f"Incluye recogida en el punto indicado, traslado directo al destino "
        f"y hasta 15 minutos de espera sin cargo adicional."
        + extras_note +
        "\n\nParadas adicionales o espera extendida se calculan aparte."
        "\n\nDesea que le reserve ese servicio?"
    )

    return out


# ─────────────────────────────────────────────
# UTILIDAD: RESUMEN DE REGLAS PARA EL AGENTE
# ─────────────────────────────────────────────
PRICING_RULES_FOR_AGENT = """
REGLAS DE COTIZACION EMOVILS — APLICA SIEMPRE EXACTAMENTE ASI:

== MONEDA ==
SIEMPRE en pesos dominicanos (RD$). NUNCA en dolares. NUNCA en USD.

== VEHICULOS ==
- 1 a 4 pasajeros: sedan
- 5 a 7 pasajeros: van
- Mas de 7: escalar al supervisor

== FORMULA DE PRECIO (servicios urbanos) ==
Tienes que saber los KILOMETROS reales del recorrido antes de calcular.
NUNCA inventes kilometros. Si no los sabes, pregunta al cliente.

Precio sedan segun distancia:
  Hasta 3 km       → RD$300 (minimo absoluto)
  4 km             → RD$350
  5 km             → RD$400
  6 km             → RD$450
  7 km             → RD$500
  8 km             → RD$550
  9 km             → RD$600
  10 km            → RD$650
  12 km            → RD$730
  15 km            → RD$850
  18 km            → RD$970
  20 km            → RD$1,050
  25 km            → RD$1,200
  30 km            → RD$1,350
  40 km            → RD$1,650
  50 km            → RD$1,950
  60 km            → RD$2,250
  70 km            → RD$2,550
  Si la distancia cae entre dos valores, interpola o usa el valor superior.

Precio van = precio sedan x 1.40 (redondea al multiplo de 25 mas cercano)

== RECARGO NOCTURNO ==
Entre 9:00 PM y 6:00 AM: multiplica el precio por 1.20 (+20%)
Por eso es OBLIGATORIO preguntar la hora antes de dar precio.

== TRASLADOS AL AEROPUERTO SDQ (tarifas fijas en RD$) ==
Estas son tarifas fijas — no uses la formula urbana para aeropuerto:
  Boca Chica:         sedan RD$1,500 / van RD$2,100
  Santo Domingo Este: sedan RD$1,800 / van RD$2,520
  Zona Colonial / DN: sedan RD$2,100 / van RD$2,940
  Piantini / Naco:    sedan RD$2,400 / van RD$3,360
  Punta Cana:         sedan RD$8,700 / van RD$12,180
  Si el sector no esta en esta lista, escala al supervisor.

== DISTANCIA ==
El sistema calcula la distancia con Google Maps cuando el cliente comparte su ubicacion.
Cuando el contexto incluya [PRECIO_CALCULADO: RD$X, Ykm, Zmin], usa ese precio exactamente.
Si no hay precio calculado, pide ubicacion WhatsApp o sector/direccion de recogida y destino.

== PROHIBIDO ==
- Dar precio en USD o dolares
- Cotizar sin saber la hora (por el recargo nocturno)
- Dar precio sin al menos saber origen y destino
- Mostrar formulas o calculos al cliente — solo el precio final
- Cotizar por debajo de RD$300
"""


def get_waiting_fee_table_text() -> str:
    """Retorna la tabla de espera en formato legible."""
    return (
        "Tabla de espera:\n"
        "- Hasta 15 minutos: sin cargo\n"
        "- De 16 a 30 minutos: RD$75 adicionales\n"
        "- De 31 a 60 minutos: RD$150 adicionales\n"
        "- De 61 a 75 minutos: RD$200 adicionales\n"
        "- De 76 a 90 minutos: RD$300 adicionales\n"
        "- Mas de 90 minutos: requiere validacion con supervisor"
    )


def get_airport_fare_text(zone: str, rate: float = 60.0) -> str:
    """Retorna texto de tarifa aeropuerto para una zona."""
    fares = AIRPORT_FARES_USD.get(zone, {})
    if not fares:
        return "Zona no encontrada en la tabla de tarifas."
    s_low, s_high = fares.get("sedan", (0, 0))
    v_low, v_high = fares.get("van", (0, 0))
    return (
        f"SDQ hacia {zone.replace('_', ' ').title()}:\n"
        f"- Sedan: US${s_low}-{s_high} (aprox. RD${int(s_low*rate):,} - RD${int(s_high*rate):,})\n"
        f"- Van: US${v_low}-{v_high} (aprox. RD${int(v_low*rate):,} - RD${int(v_high*rate):,})"
    )
