"""
Emovils OPC — Módulo de Precios

Calcula la tarifa de cualquier servicio Emovils según las reglas definidas:

  PRECIO BASE (ambos modos): RD$300 (cubre 0-3 km)

  MODO CIUDAD (intra Santo Domingo, sin AILA):
      + RD$60 por km extra (después de 3 km)

  MODO LARGA DISTANCIA (AILA, Boca Chica, inter-urbano):
      + RD$110 por km del 4 al 25
      + RD$40 por km del 26 en adelante

  RECARGOS:
      +20% nocturno (11:00 PM - 6:00 AM)
      +10% cuando son 7-10 pasajeros (van H1 grande)

  EXTRAS:
      Chofer reservado por hora: RD$800/hora
      Espera dentro de servicio: 15 min gratis, luego RD$500/hora o fracción
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable, Optional


# ─────────────────────────────────────────────────────────────
# CONSTANTES DE LA FÓRMULA
# ─────────────────────────────────────────────────────────────

TARIFA_BASE = 300          # RD$ — cubre 0-3 km en ambos modos
KM_BASE = 3                # Km cubiertos por la tarifa base

# Tarifas por km
CIUDAD_POR_KM = 60         # RD$ por km después de los 3 km base

LARGA_TIER1_POR_KM = 110   # RD$ por km del 4 al 25
LARGA_TIER1_HASTA_KM = 25

LARGA_TIER2_POR_KM = 40    # RD$ por km del 26 en adelante

# Recargos
RECARGO_NOCTURNO_PCT = 0.20      # +20%
HORA_NOCTURNO_INICIO = 23        # 11 PM
HORA_NOCTURNO_FIN = 6            # 6 AM

RECARGO_H1_PCT = 0.10            # +10% cuando pasajeros >= 7
PAX_UMBRAL_H1 = 7                # 7 o más pasajeros = van H1 grande

# Extras
TARIFA_HORA_CHOFER = 800         # RD$/hora — chofer reservado
TARIFA_ESPERA = 500              # RD$/hora o fracción — después de 15 min gratis
ESPERA_GRATIS_MIN = 15           # Minutos gratis de espera


# ─────────────────────────────────────────────────────────────
# DETECCIÓN DE MODO (CIUDAD vs LARGA DISTANCIA)
# ─────────────────────────────────────────────────────────────

# Palabras clave que disparan modo LARGA DISTANCIA
KEYWORDS_LARGA_DISTANCIA = {
    # Aeropuertos
    "aila", "las américas", "las americas", "sdq",
    "punta cana airport", "puj", "stcia",
    "santiago airport", "stid", "cibao internacional",
    "puerto plata airport", "pop",
    # Costa este / sur
    "boca chica", "juan dolio", "guayacanes",
    "san pedro de macoris", "san pedro de macorís", "spm",
    "la romana", "casa de campo", "bayahibe",
    "higüey", "higuey",
    "punta cana", "bavaro", "bávaro", "uvero alto", "cap cana",
    # Norte
    "santiago", "puerto plata", "sosúa", "sosua", "cabarete",
    "samaná", "samana", "las terrenas", "el limón",
    # Cibao
    "jarabacoa", "constanza", "moca", "san francisco de macorís",
    # Sur profundo
    "barahona", "azua", "san juan de la maguana", "pedernales"
}


@dataclass
class CalculoTarifa:
    """Resultado del cálculo de tarifa con desglose."""
    precio_final: int           # RD$ redondeado
    precio_base_modo: int       # RD$ antes de recargos
    modo: str                   # "CIUDAD" o "LARGA_DISTANCIA"
    km: float
    pasajeros: int
    es_nocturno: bool
    es_h1: bool
    recargo_nocturno_rd: int
    recargo_h1_rd: int
    desglose: list[str]         # Líneas con explicación humana

    def __str__(self) -> str:
        return f"RD${self.precio_final:,} ({self.modo} · {self.km}km)"


# ─────────────────────────────────────────────────────────────
# FUNCIONES PRINCIPALES
# ─────────────────────────────────────────────────────────────

def determinar_modo(origen: str, destino: str) -> str:
    """
    Devuelve "LARGA_DISTANCIA" si origen o destino mencionan algún punto
    fuera de Santo Domingo metropolitano (AILA, Boca Chica, Punta Cana, etc).
    Devuelve "CIUDAD" en caso contrario.
    """
    if not origen and not destino:
        return "CIUDAD"
    texto = f"{origen or ''} {destino or ''}".lower()
    for keyword in KEYWORDS_LARGA_DISTANCIA:
        if keyword in texto:
            return "LARGA_DISTANCIA"
    return "CIUDAD"


def es_horario_nocturno(hora: time | datetime | int) -> bool:
    """
    True si la hora cae entre 11:00 PM y 6:00 AM (recargo nocturno).
    Acepta time, datetime o int (hora del día 0-23).
    """
    if isinstance(hora, datetime):
        h = hora.hour
    elif isinstance(hora, time):
        h = hora.hour
    elif isinstance(hora, int):
        h = hora
    else:
        raise TypeError(f"Hora debe ser time/datetime/int, no {type(hora)}")
    return h >= HORA_NOCTURNO_INICIO or h < HORA_NOCTURNO_FIN


def _precio_base_por_modo(km: float, modo: str) -> int:
    """Calcula el precio base según los km y el modo (sin recargos)."""
    if km <= 0:
        return TARIFA_BASE

    # Los primeros 3 km siempre van en la base
    if km <= KM_BASE:
        return TARIFA_BASE

    km_extras = km - KM_BASE

    if modo == "CIUDAD":
        return TARIFA_BASE + int(round(km_extras * CIUDAD_POR_KM))

    # LARGA_DISTANCIA
    km_tier1 = min(km_extras, LARGA_TIER1_HASTA_KM - KM_BASE)
    km_tier2 = max(0, km_extras - (LARGA_TIER1_HASTA_KM - KM_BASE))

    precio = TARIFA_BASE
    precio += int(round(km_tier1 * LARGA_TIER1_POR_KM))
    precio += int(round(km_tier2 * LARGA_TIER2_POR_KM))
    return precio


def calcular_tarifa(
    km: float,
    *,
    modo: str | None = None,
    origen: str | None = None,
    destino: str | None = None,
    hora: time | datetime | int = 12,
    pasajeros: int = 1,
) -> CalculoTarifa:
    """
    Calcula la tarifa completa de un servicio.

    Args:
        km: Distancia del viaje en kilómetros (de Google Maps).
        modo: "CIUDAD" o "LARGA_DISTANCIA". Si no se pasa, se infiere de
              origen/destino.
        origen, destino: Texto libre de los puntos del viaje. Se usan para
              determinar el modo si no se pasa explícito.
        hora: Hora a la que arranca el viaje. Define el recargo nocturno.
        pasajeros: Cantidad de pasajeros. 7+ activa recargo H1.

    Returns:
        CalculoTarifa con el precio final, desglose y banderas.
    """
    if modo is None:
        modo = determinar_modo(origen or "", destino or "")
    if modo not in ("CIUDAD", "LARGA_DISTANCIA"):
        raise ValueError(f"Modo inválido: {modo}")

    precio_base = _precio_base_por_modo(km, modo)
    desglose = [f"Modo {modo} · {km:g} km", f"Base: RD${precio_base:,}"]

    es_nocturno = es_horario_nocturno(hora)
    es_h1 = pasajeros >= PAX_UMBRAL_H1

    precio = precio_base
    recargo_nocturno_rd = 0
    recargo_h1_rd = 0

    if es_nocturno:
        recargo_nocturno_rd = int(round(precio_base * RECARGO_NOCTURNO_PCT))
        precio += recargo_nocturno_rd
        desglose.append(f"Recargo nocturno +20%: RD${recargo_nocturno_rd:,}")

    if es_h1:
        # El recargo H1 aplica sobre el precio CON nocturno (compuesto)
        # — más ingreso para Emovils, más justo para choferes en H1 grande
        recargo_h1_rd = int(round(precio * RECARGO_H1_PCT / (1 + RECARGO_H1_PCT)))
        # Cálculo alternativo más simple: aplicar sobre la base
        recargo_h1_rd = int(round(precio_base * RECARGO_H1_PCT))
        precio += recargo_h1_rd
        desglose.append(f"Recargo H1 (7+ pax) +10%: RD${recargo_h1_rd:,}")

    return CalculoTarifa(
        precio_final=int(round(precio)),
        precio_base_modo=precio_base,
        modo=modo,
        km=km,
        pasajeros=pasajeros,
        es_nocturno=es_nocturno,
        es_h1=es_h1,
        recargo_nocturno_rd=recargo_nocturno_rd,
        recargo_h1_rd=recargo_h1_rd,
        desglose=desglose,
    )


def calcular_chofer_por_hora(horas: float) -> int:
    """Precio para servicio 'chofer reservado por hora'."""
    if horas <= 0:
        return 0
    return int(round(horas * TARIFA_HORA_CHOFER))


def calcular_espera(minutos_totales: int) -> int:
    """
    Cobro por espera dentro de un servicio.
    15 minutos gratis, después RD$500 por hora o fracción.
    """
    if minutos_totales <= ESPERA_GRATIS_MIN:
        return 0
    minutos_cobrables = minutos_totales - ESPERA_GRATIS_MIN
    # Fracción de hora se redondea HACIA ARRIBA
    fracciones = math.ceil(minutos_cobrables / 60)
    return fracciones * TARIFA_ESPERA


# ─────────────────────────────────────────────────────────────
# COMISIÓN A AFILIADOS
# ─────────────────────────────────────────────────────────────

COMISION_AFILIADO_PCT = 0.30   # Emovils retiene 30%, afiliado recibe 70%


def comision_emovils(tarifa: int, tipo_chofer: str, canal: str) -> int:
    """
    Calcula cuánto retiene Emovils de un servicio.

    Reglas:
      - Afiliado (cualquier canal): Emovils retiene 30%
      - Propio en Call Center (Intelcia): Emovils retiene 100%
        (los propios tienen salario fijo, no comisión por viaje)
      - Propio en otros canales: TODO definir comisión, por ahora 100%

    Args:
        tarifa: Tarifa total cobrada al cliente (RD$)
        tipo_chofer: "propio" o "afiliado"
        canal: "call_center" / "naviera" / "vip" / "referido" / "redes"
    """
    if tipo_chofer.lower() == "afiliado":
        return int(round(tarifa * COMISION_AFILIADO_PCT))
    # Propio
    return tarifa


def pago_a_chofer(tarifa: int, tipo_chofer: str, canal: str) -> int:
    """Lo que va al chofer (afiliado o propio por servicio)."""
    if tipo_chofer.lower() == "afiliado":
        return tarifa - comision_emovils(tarifa, tipo_chofer, canal)
    # Propio en call center: 0 por servicio (salario fijo)
    if canal.lower() in ("call_center", "intelcia"):
        return 0
    # Propio en otros canales: TODO definir salario + comisión
    return 0


# ─────────────────────────────────────────────────────────────
# FORMATEO Y UTILIDADES
# ─────────────────────────────────────────────────────────────

def formato_rd(monto: int | float) -> str:
    """Formatea un número como tarifa dominicana."""
    return f"RD${int(round(monto)):,}".replace(",", ",")


def explicar_tarifa(c: CalculoTarifa) -> str:
    """Devuelve una explicación humana lista para WhatsApp."""
    lineas = [f"💰 Tarifa: {formato_rd(c.precio_final)}"]
    lineas.extend([f"  • {paso}" for paso in c.desglose])
    return "\n".join(lineas)


# ─────────────────────────────────────────────────────────────
# CLI BÁSICA PARA PRUEBAS
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("EMOVILS — Calculadora de Precios v1.0")
    print("=" * 60)

    casos = [
        # (descripción, km, modo o (origen, destino), hora, pax)
        ("Piantini → Naco (corto ciudad)", 3, "CIUDAD", 14, 1),
        ("Naco → Zona Colonial (medio ciudad)", 6, "CIUDAD", 14, 2),
        ("Centro SD → AILA (anclaje 2,500)", 20, "LARGA_DISTANCIA", 14, 2),
        ("AILA → Boca Chica", 30, "LARGA_DISTANCIA", 14, 3),
        ("AILA → La Romana", 110, "LARGA_DISTANCIA", 14, 4),
        ("AILA → Punta Cana", 200, "LARGA_DISTANCIA", 14, 4),
        ("AILA → Punta Cana NOCHE", 200, "LARGA_DISTANCIA", 23, 4),
        ("AILA → Casa de Campo grupo (7 pax)", 115, "LARGA_DISTANCIA", 14, 7),
        ("Ciudad nocturno 8km", 8, "CIUDAD", 1, 2),
    ]

    for desc, km, modo, hora, pax in casos:
        c = calcular_tarifa(km, modo=modo, hora=hora, pasajeros=pax)
        nocturno = " 🌙" if c.es_nocturno else ""
        h1 = " 🚐 H1" if c.es_h1 else ""
        print(f"\n{desc}{nocturno}{h1}")
        print(f"  → {formato_rd(c.precio_final)} ({c.modo}, {c.km}km)")
        for paso in c.desglose:
            print(f"    {paso}")

    print()
    print("=" * 60)
    print("Extras:")
    print(f"  Chofer 4 horas reservado: {formato_rd(calcular_chofer_por_hora(4))}")
    print(f"  Espera 45 min: {formato_rd(calcular_espera(45))}")
    print(f"  Espera 75 min: {formato_rd(calcular_espera(75))}")
    print()
    print("Comisión afiliado en servicio RD$2,500:")
    print(f"  Emovils retiene: {formato_rd(comision_emovils(2500, 'afiliado', 'vip'))}")
    print(f"  Chofer recibe: {formato_rd(pago_a_chofer(2500, 'afiliado', 'vip'))}")
