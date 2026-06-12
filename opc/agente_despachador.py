"""
Emovils OPC — Agente Despachador

El cerebro que asigna conductores estilo Uber:

  1. Recibe un servicio nuevo (cualquier canal: B2C inmediato, programado, Intelcia)
  2. Busca conductores DISPONIBLES con capacidad correcta
  3. Ordena por: zona base más cercana al origen, calificación, rotación justa
  4. Notifica al conductor #1 vía WhatsApp con 60 seg para aceptar
  5. Si rechaza o no responde → pasa al #2 automático
  6. Si 5 rechazan → escala al dueño + notifica cliente

Este módulo NO se conecta solo a WhatsApp todavía — eso lo hace el
Agente Coordinador. Aquí está la lógica de matching pura.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# ESTRUCTURAS
# ─────────────────────────────────────────────────────────────

@dataclass
class CandidatoChofer:
    """Un conductor que podría tomar el servicio, con su score."""
    chofer_id: str
    nombre: str
    whatsapp: str
    tipo: str                   # "Propio" o "Afiliado"
    zona_base: list[str]
    calificacion: float         # 0-5
    capacidad_max_pax: int      # 4 (Caravan) o 10 (H1)
    tipo_vehiculo: str          # "Caravan" o "H1"
    placa_vehiculo: str
    servicios_dia: int = 0      # Cuántos servicios hizo hoy (rotación justa)
    score_distancia: float = 0.0  # 0-100, mayor = más cerca
    score_total: float = 0.0      # Score compuesto


@dataclass
class SolicitudServicio:
    """Lo que llega al Despachador para asignar."""
    servicio_id: str
    origen: str
    destino: str
    pasajeros: int
    fecha_hora: datetime
    canal: str                  # "INTELCIA", "VIP", "B2C_AEROPUERTO", etc.
    tarifa_rd: int
    prioridad: str = "NORMAL"   # "URGENTE" (inmediato) o "NORMAL" (programado)
    es_vip: bool = False        # Si es VIP, preferir propios


@dataclass
class ResultadoAsignacion:
    """Resultado del intento de asignación."""
    asignado: bool
    chofer_seleccionado: Optional[CandidatoChofer] = None
    candidatos_evaluados: list[CandidatoChofer] = field(default_factory=list)
    razon_no_asignado: str = ""


# ─────────────────────────────────────────────────────────────
# DICCIONARIO ZONA → CERCANÍA
# ─────────────────────────────────────────────────────────────

# Para un MVP sin GPS en vivo, usamos la zona base del conductor
# vs zona del origen del servicio. Más adelante se reemplaza con
# distancia real de Google Maps si compartimos ubicación.

ZONAS_VS_ORIGEN = {
    # Origen aproximado en el texto → zonas cercanas (ranking)
    "aila": ["Santo Domingo Este", "Boca Chica", "San Pedro de Macorís"],
    "las américas": ["Santo Domingo Este", "Boca Chica"],
    "boca chica": ["Boca Chica", "Santo Domingo Este", "San Pedro de Macorís"],
    "punta cana": ["Punta Cana", "La Romana"],
    "la romana": ["La Romana", "Punta Cana", "San Pedro de Macorís"],
    "san pedro de macorís": ["San Pedro de Macorís", "La Romana", "Santo Domingo Este"],
    "centro": ["Distrito Nacional", "Santo Domingo Este", "Santo Domingo Oeste"],
    "piantini": ["Distrito Nacional"],
    "naco": ["Distrito Nacional"],
    "polígono": ["Distrito Nacional"],
    "bella vista": ["Distrito Nacional"],
    "zona colonial": ["Distrito Nacional", "Santo Domingo Este"],
    "villa mella": ["Villa Mella", "Santo Domingo Norte"],
    "santo domingo norte": ["Santo Domingo Norte", "Villa Mella"],
    "santo domingo este": ["Santo Domingo Este"],
    "santo domingo oeste": ["Santo Domingo Oeste", "Distrito Nacional"],
    "santiago": ["Santiago"],
    "puerto plata": ["Puerto Plata", "Santiago"],
    # Zonas Intelcia
    "almirante": ["Santo Domingo Este"],
    "invivienda": ["Santo Domingo Este"],
    "san luis": ["Santo Domingo Este"],
    "guerra": ["Santo Domingo Este"],
    "alma rosa": ["Santo Domingo Este"],
    "toronja": ["Santo Domingo Este"],
}


def _zonas_cercanas_a_origen(origen_texto: str) -> list[str]:
    """Devuelve la lista priorizada de zonas más cercanas al origen del servicio."""
    if not origen_texto:
        return []
    origen_lower = origen_texto.lower()
    for clave, zonas in ZONAS_VS_ORIGEN.items():
        if clave in origen_lower:
            return zonas
    return []


def _score_distancia(chofer: CandidatoChofer, origen: str) -> float:
    """
    Calcula score de cercanía 0-100.
    100 = chofer está en zona exacta del origen.
    50 = chofer en zona vecina.
    10 = chofer en zona lejana pero no completamente fuera.
    0 = no hay coincidencia identificable.
    """
    zonas_cercanas = _zonas_cercanas_a_origen(origen)
    if not zonas_cercanas or not chofer.zona_base:
        return 30.0  # Score neutro si no podemos calcular

    # ¿Está la zona base del chofer en la lista de zonas cercanas?
    for idx, zona_cercana in enumerate(zonas_cercanas):
        if zona_cercana in chofer.zona_base:
            return 100.0 - (idx * 25.0)  # 100, 75, 50, 25...
    return 10.0  # No está en ninguna zona cercana


def _score_calificacion(chofer: CandidatoChofer) -> float:
    """Calificación en escala 0-100."""
    if chofer.calificacion <= 0:
        return 60.0  # Score neutro si no tiene historial todavía
    return (chofer.calificacion / 5.0) * 100.0


def _score_rotacion(chofer: CandidatoChofer) -> float:
    """
    Score que premia a conductores con menos servicios hoy (rotación justa).
    100 = no ha hecho ninguno hoy
    0 = ya hizo muchos
    """
    if chofer.servicios_dia == 0:
        return 100.0
    return max(0.0, 100.0 - (chofer.servicios_dia * 10.0))


# ─────────────────────────────────────────────────────────────
# FILTRADO DE CANDIDATOS
# ─────────────────────────────────────────────────────────────

def _filtra_por_capacidad(chofer: CandidatoChofer, pax: int) -> bool:
    """El conductor debe tener capacidad suficiente."""
    return chofer.capacidad_max_pax >= pax


def _filtra_por_tipo_servicio(chofer: CandidatoChofer, solicitud: SolicitudServicio) -> bool:
    """
    Filtros especiales según el tipo de servicio:
      - VIP: preferir Propios (mayor estándar)
      - Intelcia: cualquiera (afiliado o propio)
      - Inmediato: cualquiera disponible
    """
    if solicitud.es_vip and chofer.tipo == "Afiliado":
        # No bloqueamos completamente, solo penalizamos en score
        return True
    return True


def _ajusta_score_por_canal(chofer: CandidatoChofer, solicitud: SolicitudServicio) -> float:
    """Ajustes específicos según el canal del servicio."""
    ajuste = 0.0

    # VIP: bonus para propios
    if solicitud.es_vip and chofer.tipo == "Propio":
        ajuste += 15.0

    # Intelcia: no hay preferencia especial
    if solicitud.canal == "INTELCIA":
        pass

    # Pasajeros 5-10: bonus para H1
    if solicitud.pasajeros >= 5 and chofer.tipo_vehiculo == "H1":
        ajuste += 10.0

    return ajuste


# ─────────────────────────────────────────────────────────────
# RANKING DE CANDIDATOS
# ─────────────────────────────────────────────────────────────

# Pesos del score compuesto
PESO_DISTANCIA = 0.50
PESO_CALIFICACION = 0.30
PESO_ROTACION = 0.20


def calcular_score(chofer: CandidatoChofer, solicitud: SolicitudServicio) -> float:
    """Score compuesto 0-100 para ordenar candidatos."""
    chofer.score_distancia = _score_distancia(chofer, solicitud.origen)
    score = (
        PESO_DISTANCIA * chofer.score_distancia
        + PESO_CALIFICACION * _score_calificacion(chofer)
        + PESO_ROTACION * _score_rotacion(chofer)
    )
    score += _ajusta_score_por_canal(chofer, solicitud)
    chofer.score_total = round(score, 2)
    return chofer.score_total


def rankear_candidatos(
    choferes_disponibles: list[CandidatoChofer],
    solicitud: SolicitudServicio,
) -> list[CandidatoChofer]:
    """Filtra y ordena candidatos del mejor al peor para esta solicitud."""
    candidatos = [
        c for c in choferes_disponibles
        if _filtra_por_capacidad(c, solicitud.pasajeros)
        and _filtra_por_tipo_servicio(c, solicitud)
    ]
    for c in candidatos:
        calcular_score(c, solicitud)
    candidatos.sort(key=lambda c: c.score_total, reverse=True)
    return candidatos


# ─────────────────────────────────────────────────────────────
# ALGORITMO DE ASIGNACIÓN (intentar N candidatos)
# ─────────────────────────────────────────────────────────────

MAX_INTENTOS_AUTO = 5  # Si 5 rechazan, escalamos al dueño


def asignar(
    solicitud: SolicitudServicio,
    choferes_disponibles: list[CandidatoChofer],
    aceptados: set[str] | None = None,  # IDs que ya aceptaron — para no re-asignar
) -> ResultadoAsignacion:
    """
    Realiza el ranking y devuelve el chofer #1 propuesto.
    El Agente Coordinador es quien envía el WhatsApp con la oferta y maneja
    el timeout de 60 segundos. Si el chofer rechaza, este módulo se vuelve
    a llamar excluyendo a los que ya rechazaron.

    aceptados: Set de IDs de choferes que YA fueron contactados (para excluir).
    """
    aceptados = aceptados or set()

    candidatos = rankear_candidatos(choferes_disponibles, solicitud)
    # Excluir los que ya fueron contactados
    candidatos = [c for c in candidatos if c.chofer_id not in aceptados]

    if not candidatos:
        return ResultadoAsignacion(
            asignado=False,
            razon_no_asignado="No quedan conductores disponibles con capacidad suficiente",
        )

    elegido = candidatos[0]
    logger.info(
        f"Despachador propone: {elegido.nombre} (score {elegido.score_total}) "
        f"para servicio {solicitud.servicio_id}"
    )
    return ResultadoAsignacion(
        asignado=True,
        chofer_seleccionado=elegido,
        candidatos_evaluados=candidatos,
    )


# ─────────────────────────────────────────────────────────────
# FORMATEO DE OFERTA PARA WHATSAPP
# ─────────────────────────────────────────────────────────────

def mensaje_oferta_chofer(solicitud: SolicitudServicio, chofer: CandidatoChofer) -> str:
    """Mensaje que el Coordinador envía al chofer por WhatsApp."""
    tipo_servicio = "🔥 SERVICIO INMEDIATO" if solicitud.prioridad == "URGENTE" else "📅 Servicio programado"
    pago = (
        f"RD${round(solicitud.tarifa_rd * 0.7):,} (70% si afiliado)"
        if chofer.tipo == "Afiliado"
        else "Salario fijo + bono"
    )

    return (
        f"{tipo_servicio}\n"
        f"📍 Recoger: {solicitud.origen}\n"
        f"📍 Llevar a: {solicitud.destino}\n"
        f"👥 {solicitud.pasajeros} pasajeros\n"
        f"⏰ {solicitud.fecha_hora.strftime('%d %b %H:%M')}\n"
        f"💰 Tarifa cliente: RD${solicitud.tarifa_rd:,}\n"
        f"💵 Tú cobras: {pago}\n"
        f"\n"
        f"Responde SÍ en 60 segundos o pasa al siguiente conductor.\n"
        f"Servicio: {solicitud.servicio_id}"
    )


def mensaje_cliente_chofer_asignado(
    solicitud: SolicitudServicio,
    chofer: CandidatoChofer,
    eta_minutos: int = 10,
) -> str:
    """Mensaje que se envía al cliente cuando el conductor acepta."""
    return (
        f"✅ Listo. Tu conductor asignado:\n"
        f"👤 {chofer.nombre} ⭐ {chofer.calificacion:.1f}\n"
        f"🚐 Van {chofer.tipo_vehiculo} · Placa {chofer.placa_vehiculo}\n"
        f"📱 {chofer.whatsapp}\n"
        f"⏱️ Llega en {eta_minutos} min\n"
        f"\n"
        f"🔗 Tu QR de servicio: [link al QR]\n"
        f"Al verlo llegar, escanea el QR del lateral de su vehículo\n"
        f"para confirmar identidad.\n"
        f"\n"
        f"Servicio: {solicitud.servicio_id}"
    )


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 60)
    print("EMOVILS OPC — Test de Agente Despachador")
    print("=" * 60)

    # Simulación: 6 choferes disponibles, distintas zonas
    choferes_demo = [
        CandidatoChofer(
            chofer_id="C001", nombre="Gilberto", whatsapp="+18290000001",
            tipo="Propio", zona_base=["Santo Domingo Este"],
            calificacion=4.7, capacidad_max_pax=10, tipo_vehiculo="H1",
            placa_vehiculo="A111111", servicios_dia=2,
        ),
        CandidatoChofer(
            chofer_id="C002", nombre="Camilo", whatsapp="+18290000002",
            tipo="Propio", zona_base=["Distrito Nacional"],
            calificacion=4.9, capacidad_max_pax=4, tipo_vehiculo="Caravan",
            placa_vehiculo="A222222", servicios_dia=0,
        ),
        CandidatoChofer(
            chofer_id="A001", nombre="Pedro (afiliado)", whatsapp="+18290000003",
            tipo="Afiliado", zona_base=["Santo Domingo Este", "Boca Chica"],
            calificacion=4.6, capacidad_max_pax=8, tipo_vehiculo="Caravan",
            placa_vehiculo="B333333", servicios_dia=1,
        ),
        CandidatoChofer(
            chofer_id="C003", nombre="Noel", whatsapp="+18290000004",
            tipo="Propio", zona_base=["Villa Mella"],
            calificacion=4.4, capacidad_max_pax=4, tipo_vehiculo="Caravan",
            placa_vehiculo="A444444", servicios_dia=3,
        ),
        CandidatoChofer(
            chofer_id="A002", nombre="Luis (afiliado)", whatsapp="+18290000005",
            tipo="Afiliado", zona_base=["Punta Cana"],
            calificacion=4.8, capacidad_max_pax=10, tipo_vehiculo="H1",
            placa_vehiculo="B555555", servicios_dia=0,
        ),
        CandidatoChofer(
            chofer_id="C004", nombre="Edward", whatsapp="+18290000006",
            tipo="Propio", zona_base=["Santo Domingo Norte"],
            calificacion=4.5, capacidad_max_pax=4, tipo_vehiculo="Caravan",
            placa_vehiculo="A666666", servicios_dia=1,
        ),
    ]

    # Caso 1: Servicio inmediato del Hotel El Embajador al AILA
    print("\n— CASO 1: Servicio inmediato a AILA (2 pax) —")
    sol = SolicitudServicio(
        servicio_id="SVC-001",
        origen="Hotel El Embajador, Av. Sarasota",
        destino="AILA Terminal A",
        pasajeros=2,
        fecha_hora=datetime.now(),
        canal="B2C_INMEDIATO",
        tarifa_rd=2940,
        prioridad="URGENTE",
    )
    r = asignar(sol, choferes_demo)
    if r.asignado:
        print(f"  ✅ Elegido: {r.chofer_seleccionado.nombre} (score {r.chofer_seleccionado.score_total})")
        print(f"  Top 3:")
        for c in r.candidatos_evaluados[:3]:
            print(f"    - {c.nombre}: score {c.score_total} "
                  f"(dist {c.score_distancia:.0f}, ⭐ {c.calificacion}, srv_hoy {c.servicios_dia})")
    else:
        print(f"  ❌ {r.razon_no_asignado}")

    # Caso 2: Servicio Intelcia con 4 pax desde Almirante
    print("\n— CASO 2: Intelcia ruta 7 (4 pax desde El Almirante) —")
    sol2 = SolicitudServicio(
        servicio_id="SVC-002",
        origen="El Almirante, Santo Domingo Este",
        destino="Punta Arrecife (call center)",
        pasajeros=4,
        fecha_hora=datetime.now(),
        canal="INTELCIA",
        tarifa_rd=750,
    )
    r = asignar(sol2, choferes_demo)
    if r.asignado:
        print(f"  ✅ Elegido: {r.chofer_seleccionado.nombre} (score {r.chofer_seleccionado.score_total})")

    # Caso 3: VIP de AILA a Punta Cana (7 pax)
    print("\n— CASO 3: VIP a Punta Cana (7 pax, requiere H1) —")
    sol3 = SolicitudServicio(
        servicio_id="SVC-003",
        origen="AILA Terminal Privada",
        destino="Punta Cana Cap Cana Resort",
        pasajeros=7,
        fecha_hora=datetime.now(),
        canal="VIP",
        tarifa_rd=10692,
        es_vip=True,
    )
    r = asignar(sol3, choferes_demo)
    if r.asignado:
        print(f"  ✅ Elegido: {r.chofer_seleccionado.nombre} (score {r.chofer_seleccionado.score_total})")
        print(f"     Vehículo: {r.chofer_seleccionado.tipo_vehiculo} (cap {r.chofer_seleccionado.capacidad_max_pax})")

    # Caso 4: Simulación de rechazo en cadena
    print("\n— CASO 4: Cadena de rechazos (los 3 mejores rechazan) —")
    rechazados: set[str] = set()
    for ronda in range(1, 5):
        r = asignar(sol, choferes_demo, aceptados=rechazados)
        if r.asignado:
            print(f"  Ronda {ronda}: Ofreciendo a {r.chofer_seleccionado.nombre} (rechaza)")
            rechazados.add(r.chofer_seleccionado.chofer_id)
        else:
            print(f"  Ronda {ronda}: {r.razon_no_asignado}")
            break

    # Caso 5: Mensaje de oferta a WhatsApp
    print("\n— CASO 5: Mensaje WhatsApp al chofer —")
    sol_msg = SolicitudServicio(
        servicio_id="SVC-100",
        origen="Hotel Lina, Av. Máximo Gómez",
        destino="AILA",
        pasajeros=3,
        fecha_hora=datetime.now(),
        canal="B2C_INMEDIATO",
        tarifa_rd=2920,
        prioridad="URGENTE",
    )
    r = asignar(sol_msg, choferes_demo)
    print()
    print(mensaje_oferta_chofer(sol_msg, r.chofer_seleccionado))
    print()
    print("— Mensaje al cliente cuando acepte —")
    print()
    print(mensaje_cliente_chofer_asignado(sol_msg, r.chofer_seleccionado, eta_minutos=12))
