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
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

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
# PLAN SEMANAL DE CONTENIDO (captions + imágenes + hashtags)
# ─────────────────────────────────────────────────────────────

# Tabla Airtable usada como cola de aprobación de posts del dueño.
TABLA_POSTS = "Email_Campañas"
ESTADO_PENDIENTE_APROBACION = "Pendiente_Aprobacion"

META_GRAPH_BASE = "https://graph.facebook.com/v19.0"

# Plantillas de respaldo (modo sin LLM): 7 días de contenido listo.
PLANTILLAS_SEMANA = [
    {
        "dia": "Lunes",
        "tema": "Transfer AILA → Casa de Campo",
        "caption": (
            "Empieza la semana sin estrés. Tu transfer de AILA a Casa de Campo "
            "con Emovils: chofer uniformado, van ejecutiva, agua fría y Wi-Fi. "
            "14 años haciéndolo bien. 📲 829-861-0090"
        ),
        "descripcion_imagen": (
            "Van ejecutiva Hyundai H1 negra estacionada frente al portón de "
            "Casa de Campo al atardecer, chofer uniformado abriendo la puerta."
        ),
        "hashtags": "#EmovilsRD #CasaDeCampo #VIPTransfer #LaRomana #TurismoRD",
    },
    {
        "dia": "Martes",
        "tema": "Detrás de cámaras: preparación del vehículo",
        "caption": (
            "Mientras tú vuelas, nosotros preparamos todo: limpieza profunda, "
            "agua en cada asiento, cargadores y tracking de tu vuelo en tiempo "
            "real. Tu viaje empieza antes de que llegues. 🚖"
        ),
        "descripcion_imagen": (
            "Close-up de manos con guante blanco colocando botellas de agua en "
            "los asientos de cuero de una van ejecutiva impecable."
        ),
        "hashtags": "#BehindTheScenes #TransporteEjecutivo #EmovilsRD #ServicioPremium",
    },
    {
        "dia": "Miércoles",
        "tema": "Servicio nocturno corporativo (call centers)",
        "caption": (
            "50 empleados a salvo cada noche. Transporte corporativo nocturno "
            "con choferes verificados y reporte de cumplimiento cada mañana. "
            "Cotiza para tu empresa: 829-861-0090"
        ),
        "descripcion_imagen": (
            "Van Emovils iluminada frente a un edificio de call center de noche "
            "en Santo Domingo, empleadas subiendo tranquilas."
        ),
        "hashtags": "#TransporteCorporativo #CallCenterRD #SantoDomingo #SeguridadLaboral",
    },
    {
        "dia": "Jueves",
        "tema": "Tracking de vuelo en tiempo real",
        "caption": (
            "Sabemos cuándo aterrizas antes de que salgas del avión. ✈️ "
            "Tracking de vuelo en tiempo real: si tu vuelo se retrasa, te "
            "esperamos sin costo extra. Así de simple."
        ),
        "descripcion_imagen": (
            "Chofer profesional en la sala de llegadas del AILA sosteniendo un "
            "cartel con nombre de cliente, celular con app de vuelos en mano."
        ),
        "hashtags": "#AILA #SDQ #TrackingDeVuelo #TransporteEjecutivoRD #EmovilsRD",
    },
    {
        "dia": "Viernes",
        "tema": "Eventos y bodas",
        "caption": (
            "Tu boda o evento corporativo sin preocupaciones de transporte. "
            "Flota de vans ejecutivas, choferes uniformados y coordinación "
            "completa. Cotiza tu fecha: 829-861-0090 🥂"
        ),
        "descripcion_imagen": (
            "Tres vans negras Emovils alineadas frente a un salón de eventos "
            "decorado con luces cálidas, novia subiendo a la primera van."
        ),
        "hashtags": "#BodasRD #EventosRD #TransporteParaBodas #EmovilsEventos",
    },
    {
        "dia": "Sábado",
        "tema": "Testimonio / prueba social",
        "caption": (
            "\"El chofer ya estaba esperándome con mi nombre en el cartel. "
            "Cero estrés después de 8 horas de vuelo.\" — Cliente VIP, Punta "
            "Cana. 14 años de clientes que vuelven. 🙏"
        ),
        "descripcion_imagen": (
            "Cliente sonriente con maletas saliendo del aeropuerto, chofer "
            "Emovils recibiéndolo con cartel personalizado."
        ),
        "hashtags": "#Testimonios #ClientesFelices #PuntaCana #EmovilsRD",
    },
    {
        "dia": "Domingo",
        "tema": "Cierre de semana con CTA",
        "caption": (
            "¿Viajas esta semana? Reserva tu transfer antes de aterrizar y "
            "llega tranquilo. WhatsApp 829-861-0090 · emovils.com 🚖✨"
        ),
        "descripcion_imagen": (
            "Logo 'e' de Emovils sobre fondo de carretera dominicana con "
            "palmeras al atardecer, van ejecutiva en movimiento."
        ),
        "hashtags": "#ReservaYa #Emovils #TransporteEjecutivoRD #RD",
    },
]


def _generar_texto_llm(prompt: str, max_tokens: int = 2500) -> str | None:
    """
    Genera texto con Anthropic (preferido) u OpenAI vía REST (sin SDK).
    Devuelve None si no hay API key o si la llamada falla (→ modo plantillas).
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if anthropic_key:
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            if r.ok:
                return r.json()["content"][0]["text"]
            logger.warning("Anthropic %s: %s", r.status_code, r.text[:200])
        except Exception as exc:
            logger.warning("Fallo Anthropic: %s", exc)

    if openai_key:
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            if r.ok:
                return r.json()["choices"][0]["message"]["content"]
            logger.warning("OpenAI %s: %s", r.status_code, r.text[:200])
        except Exception as exc:
            logger.warning("Fallo OpenAI: %s", exc)

    return None


def _extraer_json(texto: str) -> list | None:
    """Extrae el primer array JSON de una respuesta de LLM."""
    try:
        inicio = texto.index("[")
        fin = texto.rindex("]") + 1
        return json.loads(texto[inicio:fin])
    except (ValueError, json.JSONDecodeError):
        return None


def _plan_con_llm(fecha_inicio: date) -> list[dict] | None:
    """Pide al LLM un plan de 7 días. None si no hay key o falla."""
    prompt = (
        "Eres el community manager de Emovils, empresa dominicana de transporte "
        "ejecutivo (transfers AILA, Casa de Campo, Punta Cana; servicio nocturno "
        "para call centers; 14 años de operación; WhatsApp 829-861-0090; "
        "emovils.com). Genera un plan de contenido de 7 días para Instagram y "
        "Facebook empezando el "
        f"{fecha_inicio.isoformat()}.\n\n"
        "Responde SOLO con un array JSON de 7 objetos, cada uno con las llaves: "
        '"dia" (Lunes..Domingo), "tema", "caption" (español dominicano cercano, '
        "máx 350 caracteres, con CTA), \"descripcion_imagen\" (descripción "
        'detallada de la imagen a producir) y "hashtags" (5-6 hashtags).'
    )
    respuesta = _generar_texto_llm(prompt)
    if not respuesta:
        return None
    plan = _extraer_json(respuesta)
    if not plan or len(plan) < 7:
        logger.warning("LLM devolvió plan inválido, usando plantillas")
        return None
    return plan[:7]


def planificar_semana(
    fecha_inicio: date | str | None = None,
    guardar_airtable: bool = True,
) -> dict:
    """
    Genera el plan de contenido de 7 días (caption + descripción de imagen +
    hashtags por día) y lo guarda en Airtable como cola de aprobación
    (estado Pendiente_Aprobacion).

    Con ANTHROPIC_API_KEY u OPENAI_API_KEY genera contenido fresco con LLM;
    sin keys usa las plantillas locales (modo mock, nunca falla).
    """
    if fecha_inicio is None:
        fecha_inicio = date.today()
    elif isinstance(fecha_inicio, str):
        fecha_inicio = date.fromisoformat(fecha_inicio)

    plan_llm = _plan_con_llm(fecha_inicio)
    generado_con = "llm" if plan_llm else "plantillas"
    base = plan_llm or PLANTILLAS_SEMANA

    posts: list[dict] = []
    for i, item in enumerate(base):
        fecha_post = fecha_inicio + timedelta(days=i)
        posts.append({
            "fecha": fecha_post.isoformat(),
            "dia": item.get("dia", fecha_post.strftime("%A")),
            "tema": item.get("tema", ""),
            "caption": item.get("caption", ""),
            "descripcion_imagen": item.get("descripcion_imagen", ""),
            "hashtags": item.get("hashtags", ""),
            "estado": ESTADO_PENDIENTE_APROBACION,
            "plataformas": ["INSTAGRAM", "FACEBOOK"],
        })

    # Cola de aprobación en Airtable (si hay credenciales)
    guardados = 0
    airtable_ids: list[str] = []
    error_airtable = ""
    if guardar_airtable:
        try:
            api = AirtableOPC()
            for post in posts:
                record = api.crear_registro(TABLA_POSTS, {
                    "Campaña": "Plan_Semanal_Social",
                    "Asunto": f"POST {post['dia']} · {post['tema']}",
                    "Cuerpo": (
                        f"CAPTION:\n{post['caption']}\n\n"
                        f"IMAGEN:\n{post['descripcion_imagen']}\n\n"
                        f"HASHTAGS: {post['hashtags']}\n\n"
                        f"PLATAFORMAS: {', '.join(post['plataformas'])}"
                    ),
                    "Fecha_envio": f"{post['fecha']}T12:00:00",
                    "Estado": ESTADO_PENDIENTE_APROBACION,
                    "Plataforma": "Manual",
                })
                post["airtable_id"] = record.get("id", "")
                airtable_ids.append(post["airtable_id"])
                guardados += 1
        except Exception as exc:
            error_airtable = str(exc)
            logger.warning("Sin Airtable, plan no persistido: %s", exc)

    modo = "real" if (plan_llm or guardados) else "mock"
    resultado = {
        "modo": modo,
        "generado_con": generado_con,
        "fecha_inicio": fecha_inicio.isoformat(),
        "total_posts": len(posts),
        "guardados_airtable": guardados,
        "posts": posts,
    }
    if error_airtable:
        resultado["nota_airtable"] = f"No persistido en Airtable: {error_airtable[:150]}"
    return resultado


# ─────────────────────────────────────────────────────────────
# PUBLICACIÓN VÍA META GRAPH API (Facebook Page + Instagram)
# ─────────────────────────────────────────────────────────────

def _meta_configurado() -> bool:
    return bool(
        os.getenv("META_ACCESS_TOKEN")
        and (os.getenv("META_PAGE_ID") or os.getenv("META_IG_USER_ID"))
    )


def publicar_post(post: dict) -> dict:
    """
    Publica un post aprobado en Facebook Page e Instagram vía Meta Graph API.

    post: {"caption": str, "hashtags": str, "image_url": str opcional}

    Con META_ACCESS_TOKEN + META_PAGE_ID/META_IG_USER_ID hace la llamada HTTP
    real; sin tokens devuelve resultado mock (nunca falla).
    """
    caption = (post.get("caption") or "").strip()
    hashtags = (post.get("hashtags") or "").strip()
    mensaje = f"{caption}\n\n{hashtags}".strip()
    image_url = post.get("image_url", "")

    if not mensaje:
        return {"modo": "error", "error": "El post no tiene caption"}

    if not _meta_configurado():
        logger.info("📢 [MOCK] Post listo para publicar (sin tokens Meta): %s", caption[:60])
        return {
            "modo": "mock",
            "mensaje": "Tokens Meta no configurados (META_ACCESS_TOKEN + "
                       "META_PAGE_ID/META_IG_USER_ID). Post simulado.",
            "post_preparado": {"texto": mensaje, "image_url": image_url},
            "publicado_facebook": False,
            "publicado_instagram": False,
        }

    token = os.getenv("META_ACCESS_TOKEN", "")
    page_id = os.getenv("META_PAGE_ID", "")
    ig_user_id = os.getenv("META_IG_USER_ID", "")
    resultados: dict = {"modo": "real", "facebook": None, "instagram": None}

    # Facebook Page: /feed (texto) o /photos (con imagen)
    if page_id:
        try:
            if image_url:
                url = f"{META_GRAPH_BASE}/{page_id}/photos"
                payload = {"url": image_url, "caption": mensaje, "access_token": token}
            else:
                url = f"{META_GRAPH_BASE}/{page_id}/feed"
                payload = {"message": mensaje, "access_token": token}
            r = requests.post(url, data=payload, timeout=30)
            resultados["facebook"] = r.json() if r.ok else {"error": r.text[:200]}
        except Exception as exc:
            resultados["facebook"] = {"error": str(exc)}

    # Instagram: requiere imagen → contenedor de media + publish
    if ig_user_id:
        if not image_url:
            resultados["instagram"] = {
                "skipped": "Instagram requiere image_url; post solo-texto no soportado"
            }
        else:
            try:
                r1 = requests.post(
                    f"{META_GRAPH_BASE}/{ig_user_id}/media",
                    data={"image_url": image_url, "caption": mensaje, "access_token": token},
                    timeout=30,
                )
                contenedor = r1.json().get("id") if r1.ok else None
                if contenedor:
                    r2 = requests.post(
                        f"{META_GRAPH_BASE}/{ig_user_id}/media_publish",
                        data={"creation_id": contenedor, "access_token": token},
                        timeout=30,
                    )
                    resultados["instagram"] = r2.json() if r2.ok else {"error": r2.text[:200]}
                else:
                    resultados["instagram"] = {"error": r1.text[:200]}
            except Exception as exc:
                resultados["instagram"] = {"error": str(exc)}

    resultados["publicado_facebook"] = bool(
        resultados.get("facebook") and "error" not in (resultados["facebook"] or {})
    )
    resultados["publicado_instagram"] = bool(
        resultados.get("instagram") and "error" not in (resultados["instagram"] or {})
        and "skipped" not in (resultados["instagram"] or {})
    )
    return resultados


def procesar_aprobacion(post_id: str, aprobado: bool) -> dict:
    """
    Procesa la decisión del dueño sobre un post en cola.

    post_id: record id de Airtable (tabla Email_Campañas).
    aprobado: True → estado Aprobado (listo para publicar);
              False → estado Rechazado.

    Sin Airtable configurado devuelve resultado mock.
    """
    nuevo_estado = "Aprobado" if aprobado else "Rechazado"
    try:
        api = AirtableOPC()
        registro = api.actualizar(TABLA_POSTS, post_id, {"Estado": nuevo_estado})
        fields = registro.get("fields", {})
        return {
            "modo": "real",
            "post_id": post_id,
            "aprobado": aprobado,
            "estado": nuevo_estado,
            "post": {
                "asunto": fields.get("Asunto", ""),
                "caption": fields.get("Cuerpo", ""),
            },
        }
    except Exception as exc:
        logger.warning("Aprobación en modo mock (sin Airtable): %s", exc)
        return {
            "modo": "mock",
            "post_id": post_id,
            "aprobado": aprobado,
            "estado": nuevo_estado,
            "mensaje": "Sin credenciales Airtable; aprobación simulada.",
        }


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

    # Plan semanal (captions + imágenes + hashtags)
    print("\n📅 Plan semanal de posts (planificar_semana):")
    plan = planificar_semana(guardar_airtable=False)
    print(f"  Modo: {plan['modo']} · Generado con: {plan['generado_con']}")
    for p in plan["posts"]:
        print(f"  • {p['fecha']} {p['dia']}: {p['tema']}")

    print()
    print("=" * 70)
    print("✓ Agente Social Reels-first operativo")
    print("=" * 70)
