"""
Emovils OPC — Agente Social (estrategia Reels-first con Higgsfield AI)

Genera contenido VIDEO UGC para Instagram Reels, TikTok y Facebook.
Los videos UGC auténticos convierten 5-10x más que imágenes estáticas en
redes para transporte ejecutivo.

Funciones:
  1. Genera guiones de Reels (shots, captions, hashtags)
  2. Envía los guiones a Higgsfield para producción (vía API o Chrome MCP)
  3. Programa calendario de publicación
  4. Guarda en Airtable como cola de aprobación del dueño
  5. Cuando Meta token esté configurado, publica vía Graph API

Sin token: modo MOCK que prepara TODO listo y muestra al dueño.
"""
from __future__ import annotations
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
from opc.higgsfield_client import HiggsfieldClient

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# PLAYBOOK DE 10 REELS UGC
# ─────────────────────────────────────────────────────────────

PLAYBOOK_REELS = [
    {
        "id": "reel_01_pov_casa_de_campo",
        "tema": "POV: Llegando a Casa de Campo",
        "duracion": 12,
        "shots": [
            "Toma 1 (3s): POV interior van Caravan saliendo de AILA con maletas visibles",
            "Toma 2 (3s): Carretera dominicana con palmeras a 60 km/h (POV ventana)",
            "Toma 3 (3s): Portón Casa de Campo, vista interior del vehículo acercándose",
            "Toma 4 (3s): POV bajando del vehículo, vista al resort con bell-boy",
        ],
        "prompt_higgsfield": (
            "POV inside an executive Hyundai H1 luxury van. First-person perspective from passenger seat. "
            "Starting view: leaving Santo Domingo Airport (AILA) with luggage visible. "
            "Cut to scenic Dominican Republic highway with palm trees at 60 km/h, tropical sunset. "
            "Cut to arrival at Casa de Campo resort gate, smooth interior view approaching. "
            "Final shot: POV stepping out of van, view of luxury resort entrance with bellboy. "
            "Authentic UGC handheld feel, natural cinematography, golden hour lighting."
        ),
        "musica": "Lounge / chill electronic suave",
        "caption": (
            "POV: tu transfer de AILA a Casa de Campo con Emovils 🚖✨\n"
            "Cero estrés, máxima comodidad. 14 años haciéndolo bien."
        ),
        "hashtags": "#CasaDeCampo #VIPTransfer #EmovilsRD #PuntaCana #LaRomana",
        "cta": "📲 Reserva: 829-861-0090",
    },
    {
        "id": "reel_02_behind_the_scenes",
        "tema": "Antes de tu llegada (Behind the scenes)",
        "duracion": 20,
        "shots": [
            "Toma 1 (3s): Chofer limpiando interior van con paño microfibra, close-up de manos",
            "Toma 2 (3s): Revisión exterior, limpieza de espejos, plano medio",
            "Toma 3 (3s): Chofer uniformándose, abrochando camisa formal, primer plano cuello",
            "Toma 4 (3s): Colocando 4 botellas de agua en cada asiento, top-down",
            "Toma 5 (4s): Chofer con cartel del nombre del cliente en sala de llegadas AILA",
            "Toma 6 (3s): Texto en pantalla: 'Tu viaje empieza antes de que lleguemos'",
        ],
        "prompt_higgsfield": (
            "Documentary-style sequence of a Dominican professional driver preparing his executive van. "
            "Shots: hands cleaning van interior with microfiber cloth close-up. "
            "Exterior detail: mirror cleaning, medium shot. "
            "Driver buttoning his white formal shirt, neck close-up showing collar. "
            "Top-down view placing 4 water bottles in each seat. "
            "Driver standing in airport arrivals hall holding a printed name sign. "
            "Authentic UGC documentary style, natural lighting."
        ),
        "musica": "Upbeat instrumental motivacional",
        "caption": (
            "Mientras tú vuelas, nosotros preparamos todo.\n\n"
            "Emovils — listos antes que tú 🚖"
        ),
        "hashtags": "#BehindTheScenes #TransporteEjecutivo #EmovilsRD #ServicioPremium",
        "cta": "📲 829-861-0090 · emovils.com",
    },
    {
        "id": "reel_03_no_hagas_esto",
        "tema": "Cómo NO contratar transporte en AILA",
        "duracion": 30,
        "shots": [
            "Toma 1 (3s): Texto pantalla: 'No hagas esto al salir del aeropuerto'",
            "Toma 2 (5s): Actor cansado con maletas, alguien gritando ofertas de transporte",
            "Toma 3 (4s): Texto: 'Precio confuso, vehículo desconocido, sin factura'",
            "Toma 4 (5s): Corte a cliente sonriendo reservando en su celular antes de aterrizar",
            "Toma 5 (5s): Cliente saliendo de AILA, chofer Emovils profesional con cartel",
            "Toma 6 (4s): Cliente subiendo a van limpia, chofer abriéndole la puerta",
            "Toma 7 (4s): Texto final: 'Reserva antes. Llega tranquilo. 829-861-0090'",
        ],
        "prompt_higgsfield": (
            "Two-part contrast video. Part 1 (dramatic): tired exhausted traveler with luggage at airport exit, "
            "people yelling transportation offers in chaotic scene. Dim, stressful lighting. "
            "Part 2 (calm): same kind of traveler smoothly reserving on phone before landing. "
            "Then traveler walking out of AILA airport, professional uniformed driver holding name sign, "
            "guiding to clean executive van, opens door. Bright, professional lighting. "
            "Authentic UGC style with split-screen comparison vibe."
        ),
        "musica": "Trending dramatic suave que cambia a calmado",
        "caption": (
            "Llegar a RD ya es bastante estrés. No agregues más.\n\n"
            "Reserva antes de aterrizar 🚖✈️"
        ),
        "hashtags": "#TipsTuristas #AILA #SantoDomingo #ViajarSeguro #TurismoRD",
        "cta": "📲 Reserva ANTES de tu vuelo: 829-861-0090",
    },
    {
        "id": "reel_04_14_años",
        "tema": "14 años en 30 segundos",
        "duracion": 30,
        "shots": [
            "Toma 1 (3s): Fotos antiguas estáticas animadas (primer vehículo, oficina antigua)",
            "Toma 2 (3s): Transición a vehículo actual moderno",
            "Toma 3 (3s): Choferes propios uniformados frente a flota",
            "Toma 4 (3s): Van entrando a AILA en zona privada",
            "Toma 5 (3s): Cliente VIP saliendo de van en hotel premium",
            "Toma 6 (3s): Logo '14 años' con animación elegante",
            "Toma 7 (12s): Texto narrativo: 'Sigue siendo el mismo compromiso. Más vehículos, más equipo, más tú.'",
        ],
        "prompt_higgsfield": (
            "Cinematic timeline video for Emovils 14 years anniversary. "
            "Start with vintage photo of single old van animated with subtle zoom. "
            "Transition to modern executive Hyundai H1 fleet. "
            "Multiple uniformed professional Dominican drivers standing proud in front of vans. "
            "Van entering AILA airport private VIP zone. "
            "Wealthy VIP guest stepping out of van at luxury hotel entrance. "
            "Elegant logo animation '14 años' with gold accents. "
            "Inspirational Dominican music, cinematic golden hour."
        ),
        "musica": "Inspiracional dominicana orquestal",
        "caption": (
            "Llevamos 14 años en RD haciendo lo que sabemos mejor.\n\n"
            "Gracias a cada cliente que ha confiado 🙏"
        ),
        "hashtags": "#Emovils14Años #HistoriaRD #TransporteRD #DominicanaQueAvanza",
        "cta": "📲 829-861-0090 · 🔗 emovils.com",
    },
    {
        "id": "reel_05_lo_que_no_ves",
        "tema": "Lo que NO ves de un viaje Emovils",
        "duracion": 20,
        "shots": [
            "Toma 1 (2s): Wi-Fi router activándose, plano detalle LED verde",
            "Toma 2 (3s): Cargadores Lightning + USB-C colocados en cada asiento",
            "Toma 3 (2s): Botella de agua siendo colocada con guante blanco",
            "Toma 4 (3s): Limpieza profunda con paño microfibra de piel del asiento",
            "Toma 5 (3s): Chequeo de presión de llantas con manómetro digital",
            "Toma 6 (2s): GPS encendiendo con ruta marcada hacia AILA",
            "Toma 7 (5s): Texto: 'Detrás de cada viaje cómodo hay 30 minutos de preparación'",
        ],
        "prompt_higgsfield": (
            "Macro detail sequence showing premium executive van preparation. "
            "Close-up of Wi-Fi router LED activating green. "
            "Lightning and USB-C cables placed in each leather seat. "
            "White-gloved hands placing premium bottled water. "
            "Microfiber cloth deep cleaning leather seats, close-up. "
            "Digital tire pressure gauge checking tire. "
            "GPS screen lighting up with route to AILA. "
            "Sophisticated cinematography, attention to detail, ASMR-style macros."
        ),
        "musica": "Sofisticada electrónica suave",
        "caption": (
            "Por eso te llegamos a tiempo y con todo listo 🚖💼\n\n"
            "30 minutos de preparación antes de cada viaje."
        ),
        "hashtags": "#EmovilsRD #ServicioEjecutivo #DetallesQueImportan",
        "cta": "📲 829-861-0090",
    },
    {
        "id": "reel_06_emovils_vs_uber",
        "tema": "Emovils vs Uber (comparativa visual)",
        "duracion": 15,
        "shots": [
            "Toma 1 (3s): Pantalla dividida — izq: Uber X compacto · der: Van Emovils",
            "Toma 2 (3s): Izq: chofer en jeans con app · der: chofer Emovils uniformado",
            "Toma 3 (3s): Izq: interior compacto · der: interior van con Wi-Fi y agua",
            "Toma 4 (3s): Izq: recibo digital simple · der: factura NCF formal Emovils",
            "Toma 5 (3s): Texto: '¿Cliente importante o factura para empresa? No improvises.'",
        ],
        "prompt_higgsfield": (
            "Split-screen comparison video. Left side: small compact car Uber-style driver in casual clothes, "
            "small cramped interior, simple digital receipt on phone. "
            "Right side: professional executive Emovils van with uniformed Dominican driver, "
            "spacious interior with Wi-Fi indicator and bottled water visible, formal NCF tax invoice. "
            "Clear visual contrast between casual rideshare and professional executive transport. "
            "Clean comparison style with subtle motion graphics."
        ),
        "musica": "Punchy minimalista",
        "caption": (
            "\"¿Por qué no usar Uber?\" — nos preguntan.\n\n"
            "Respuesta corta: porque no es lo mismo.\n\n"
            "Cuando viajas con familia, cliente VIP o necesitas factura — no improvises."
        ),
        "hashtags": "#EmovilsVsUber #TransporteEjecutivo #FacturaEmpresarial",
        "cta": "📲 829-861-0090",
    },
    {
        "id": "reel_07_nocturno_seguro",
        "tema": "Servicio nocturno para call centers",
        "duracion": 18,
        "shots": [
            "Toma 1 (3s): Drone aéreo de Santo Domingo de noche con luces",
            "Toma 2 (3s): Van Emovils llegando frente a call center iluminado",
            "Toma 3 (3s): Empleadas saliendo del call center y subiendo a van",
            "Toma 4 (3s): Interior van con empleadas chequeando celular tranquilas",
            "Toma 5 (3s): Van dejando a empleada frente a casa con luz encendida",
            "Toma 6 (3s): Texto: '50 empleados a salvo cada noche. Servicio corporativo Emovils.'",
        ],
        "prompt_higgsfield": (
            "Night-time documentary sequence in Santo Domingo. "
            "Aerial drone shot of Santo Domingo city lights at night. "
            "Emovils executive van arriving at a brightly-lit call center building. "
            "Female call center employees walking out and boarding the van calmly. "
            "Interior shot: relaxed women checking phones in well-lit van. "
            "Van pulling up to a residential house with porch light on, employee stepping out safely. "
            "Reassuring, safe, professional vibe. Soft documentary cinematography."
        ),
        "musica": "Calmada protectora",
        "caption": (
            "50 empleados a salvo cada noche.\n\n"
            "Servicio corporativo nocturno · 14 años cumpliendo."
        ),
        "hashtags": "#TransporteCorporativo #CallCenterRD #SeguridadFemenina #SantoDomingo",
        "cta": "📲 Cotizar para tu empresa: 829-861-0090",
    },
    {
        "id": "reel_08_aila_realtime",
        "tema": "Seguimiento de vuelo en tiempo real",
        "duracion": 15,
        "shots": [
            "Toma 1 (3s): Pantalla del celular del chofer con app de vuelos abierta",
            "Toma 2 (3s): Vuelo aterrizando en AILA en pantalla 'Just landed'",
            "Toma 3 (3s): Chofer caminando hacia zona de llegadas",
            "Toma 4 (3s): Chofer con cartel del cliente esperando en zona",
            "Toma 5 (3s): Texto: 'Sabemos cuando aterrizaste antes que tú salgas'",
        ],
        "prompt_higgsfield": (
            "Real-time flight tracking experience. Driver's phone screen showing flight tracking app. "
            "Flight status updating to 'Just landed' notification. "
            "Driver walking through AILA airport towards arrivals zone with confident pace. "
            "Driver standing professionally with printed name sign at arrivals gate. "
            "Modern tech-savvy professional service vibe."
        ),
        "musica": "Tech minimalista",
        "caption": (
            "Tracking de vuelo en tiempo real.\n\n"
            "Sabemos cuándo aterrizas antes que tú salgas. ✈️🚖"
        ),
        "hashtags": "#AILA #SDQ #TrackingDeVuelo #TurismoRD #TransporteEjecutivo",
        "cta": "📲 829-861-0090",
    },
    {
        "id": "reel_09_evento_boda",
        "tema": "Eventos: bodas y celebraciones",
        "duracion": 15,
        "shots": [
            "Toma 1 (3s): Flota de 3 vans negras Emovils alineadas para evento",
            "Toma 2 (3s): Novia subiendo a van con cola del vestido",
            "Toma 3 (3s): Choferes uniformados abriendo puertas en sincronía",
            "Toma 4 (3s): Van llegando al salón decorado con luces",
            "Toma 5 (3s): Texto: 'Eventos sin preocupaciones. Cotiza tu fecha.'",
        ],
        "prompt_higgsfield": (
            "Wedding event transportation showcase. Three Emovils executive black vans lined up at fancy venue. "
            "Bride boarding a van wearing white dress with train. "
            "Uniformed drivers opening van doors in synchronized choreography. "
            "Van arriving at luxury decorated wedding venue with string lights. "
            "Elegant celebratory mood, cinematic event coverage style."
        ),
        "musica": "Romántica elegante",
        "caption": (
            "Eventos sin preocupaciones.\n\n"
            "Bodas · Conferencias · Reuniones corporativas · Cumpleaños VIP."
        ),
        "hashtags": "#BodasRD #EventosRD #TransporteParaBodas #EmovilsEventos",
        "cta": "📲 Cotiza tu fecha: 829-861-0090",
    },
    {
        "id": "reel_10_cierre_cta",
        "tema": "Cierre con CTA fuerte",
        "duracion": 10,
        "shots": [
            "Toma 1 (2s): Logo 'e' Emovils animado entrando",
            "Toma 2 (2s): Van H1 ejecutiva en estudio con iluminación dramática",
            "Toma 3 (2s): Chofer profesional saludando a cámara",
            "Toma 4 (4s): Texto: '14 años · Vans premium · Choferes verificados · WhatsApp 829-861-0090'",
        ],
        "prompt_higgsfield": (
            "Brand showcase video. Emovils 'e' logo animating in with dynamic motion graphics. "
            "Hyundai H1 executive van rotating in studio with dramatic lighting. "
            "Professional Dominican driver waving to camera with confident smile. "
            "Final text overlay with key value props and contact info. "
            "High-end commercial production value."
        ),
        "musica": "Épica corporativa elegante",
        "caption": (
            "Si has llegado hasta aquí, ya sabes lo que ofrecemos.\n\n"
            "Lo único que falta es tu mensaje."
        ),
        "hashtags": "#Emovils #ReservaYa #TransporteEjecutivoRD",
        "cta": "📲 WhatsApp: 829-861-0090 · 🔗 emovils.com",
    },
]


# ─────────────────────────────────────────────────────────────
# ESTRUCTURAS
# ─────────────────────────────────────────────────────────────

@dataclass
class ReelProgramado:
    reel_id: str
    tema: str
    duracion: int
    shots: list[str]
    prompt_higgsfield: str
    musica: str
    caption: str
    hashtags: str
    cta: str
    fecha_programada: str            # YYYY-MM-DD HH:MM
    plataformas: list[str]           # ["IG_REELS", "TIKTOK", "FB_REELS"]
    estado: str = "PENDIENTE_GENERAR_VIDEO"
    video_path: str = ""             # Path al MP4 cuando se genere
    aprobado_dueno: bool = False


def generar_calendario_reels(fecha_inicio: date) -> list[ReelProgramado]:
    """
    Calendario de 10 Reels distribuidos en 4-5 semanas.
    Frecuencia: 2-3 Reels por semana en horarios óptimos.
    """
    HORARIOS_OPTIMOS = {
        0: "12:00",  # Lunes mediodía
        2: "19:00",  # Miércoles tarde-noche
        4: "20:00",  # Viernes noche
    }

    cal: list[ReelProgramado] = []
    fecha = fecha_inicio
    idx = 0

    while idx < len(PLAYBOOK_REELS):
        wd = fecha.weekday()
        if wd in HORARIOS_OPTIMOS:
            reel = PLAYBOOK_REELS[idx]
            cal.append(ReelProgramado(
                reel_id=reel["id"],
                tema=reel["tema"],
                duracion=reel["duracion"],
                shots=reel["shots"],
                prompt_higgsfield=reel["prompt_higgsfield"],
                musica=reel["musica"],
                caption=reel["caption"],
                hashtags=reel["hashtags"],
                cta=reel["cta"],
                fecha_programada=f"{fecha.isoformat()} {HORARIOS_OPTIMOS[wd]}",
                plataformas=["IG_REELS", "TIKTOK", "FB_REELS"],
            ))
            idx += 1
        fecha += timedelta(days=1)

    return cal


# ─────────────────────────────────────────────────────────────
# PIPELINE DE PRODUCCIÓN
# ─────────────────────────────────────────────────────────────

def producir_reel(reel: ReelProgramado, higgsfield: HiggsfieldClient | None = None) -> dict:
    """
    Envía el prompt del reel a Higgsfield para generar el MP4.
    Si Higgsfield no está configurado, devuelve modo MOCK con prompt listo.
    """
    cliente = higgsfield or HiggsfieldClient()
    resultado = cliente.generar_video_text_to_video(
        prompt=reel.prompt_higgsfield,
        duracion_segundos=reel.duracion,
        aspect_ratio="9:16",
        estilo="ugc_authentic",
        nombre_archivo=f"{reel.reel_id}.mp4",
    )

    if resultado.get("path"):
        reel.video_path = resultado["path"]
        reel.estado = "VIDEO_GENERADO_PENDIENTE_APROBACION"

    return resultado


def guardar_reel_en_airtable(api: AirtableOPC, reel: ReelProgramado) -> dict:
    """Guarda el reel en la cola de aprobación del dueño."""
    return api.crear_registro("Email_Campañas", {
        "Campaña": "Instagram_Reels_Emovils",
        "Asunto": f"REEL · {reel.tema}",
        "Cuerpo": (
            f"GUIÓN:\n" +
            "\n".join(f"  · {s}" for s in reel.shots) +
            f"\n\nMÚSICA: {reel.musica}\n\n"
            f"CAPTION:\n{reel.caption}\n\n"
            f"HASHTAGS: {reel.hashtags}\n\n"
            f"CTA: {reel.cta}\n\n"
            f"PROMPT HIGGSFIELD:\n{reel.prompt_higgsfield}\n\n"
            f"DURACIÓN: {reel.duracion}s · 9:16 vertical"
        ),
        "Fecha_envio": reel.fecha_programada.replace(" ", "T") + ":00",
        "Estado": reel.estado,
        "Plataforma": "Manual",
    })


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 70)
    print("EMOVILS OPC — Test de Agente Social (Reels-first con Higgsfield)")
    print("=" * 70)

    # Calendario
    print("\n📅 Generando calendario de 10 Reels (~5 semanas)...")
    cal = generar_calendario_reels(date(2026, 6, 15))
    print(f"  ✓ {len(cal)} Reels programados\n")

    print("Cronograma:")
    for r in cal:
        print(f"  • {r.fecha_programada} · {r.tema} ({r.duracion}s)")

    # Pipeline de producción
    print("\n🎬 Pipeline de producción Higgsfield (mock):")
    primer_reel = cal[0]
    print(f"  Reel: {primer_reel.tema}")
    print(f"  Duración: {primer_reel.duracion}s")
    print(f"  Shots: {len(primer_reel.shots)}")
    print(f"  Música: {primer_reel.musica}")
    print(f"  Plataformas: {', '.join(primer_reel.plataformas)}")

    resultado = producir_reel(primer_reel)
    print(f"\n  Resultado Higgsfield: {resultado}")

    print("\n📋 Caption listo:")
    print(f"  {primer_reel.caption}")
    print(f"\n  Hashtags: {primer_reel.hashtags}")
    print(f"  CTA: {primer_reel.cta}")

    print()
    print("=" * 70)
    print("✓ Agente Social Reels-first operativo")
    print("=" * 70)
