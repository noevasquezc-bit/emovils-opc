"""
Emovils OPC — Agente Prospector B2B

Detecta empresas potenciales para Emovils en República Dominicana usando:
  • Búsquedas web por categoría/zona
  • Patrones conocidos de directorios B2B locales
  • Filtrado por criterios Emovils (turno nocturno, flota corporativa, etc.)

Genera prospects estructurados que luego van a:
  • Pipeline_Comercial en Airtable (CRM interno)
  • HubSpot CRM (si está conectado vía MCP)
  • Agente Outreach para email cold (MailerLite)

Modo MVP: usa templates conocidos + URLs públicas RD para semilla inicial.
Fase 2: integrar con Apify scraping para escala.
"""
from __future__ import annotations
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
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

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# SEMILLA DE PROSPECTS — TIPOS Y EJEMPLOS REALES EN RD
# ─────────────────────────────────────────────────────────────

@dataclass
class Prospect:
    nombre_empresa: str
    tipo_empresa: str
    razon_potencial: str            # por qué es buen prospect
    contacto_principal: str = ""
    cargo: str = ""
    email: str = ""
    linkedin_url: str = ""
    whatsapp: str = ""
    sitio_web: str = ""
    direccion: str = ""
    ciudad: str = ""
    fuente_scraping: str = "Manual"
    score: int = 50                 # 0-100
    notas: str = ""
    estado_pipeline: str = "NUEVO"


# ─────────────────────────────────────────────────────────────
# CATÁLOGO SEMILLA DE PROSPECTS B2B PARA EMOVILS (RD)
# ─────────────────────────────────────────────────────────────

# Estos son tipos de empresas conocidas en RD que necesitan
# transporte ejecutivo. El agente los usa como puntos de partida
# antes de scraping automatizado.

CATEGORIAS_TARGET = [
    {
        "categoria": "Call Centers grandes RD",
        "score_base": 90,
        "razon": "Operación nocturna · cientos de empleados · necesitan transporte garantizado",
        "empresas_conocidas": [
            "Teleperformance Dominicana",
            "Alorica Dominican Republic",
            "Conduent Dominican Republic",
            "Sutherland Dominican Republic",
            "Capital BPO",
            "TLM Group",
            "Outsourcing Solutions",
            "Atento Dominican Republic",
            "Sitel Group RD",
            "iQor Dominican Republic",
        ],
        "tipo": "Call_Center",
        "ciudad": "Santo Domingo",
    },
    {
        "categoria": "Hoteles 5 estrellas Punta Cana / Bávaro",
        "score_base": 85,
        "razon": "Necesitan transfers ejecutivos AILA → resort para huéspedes premium",
        "empresas_conocidas": [
            "Hard Rock Hotel Punta Cana",
            "Excellence Punta Cana",
            "TRS Yucatan Hotel",
            "Sanctuary Cap Cana",
            "Eden Roc Cap Cana",
            "Hyatt Zilara Cap Cana",
            "Paradisus Punta Cana",
            "Royalton Punta Cana",
            "Iberostar Grand Bavaro",
            "Now Larimar Punta Cana",
        ],
        "tipo": "Hotel",
        "ciudad": "Punta Cana",
    },
    {
        "categoria": "Hoteles 5 estrellas La Romana / Casa de Campo",
        "score_base": 85,
        "razon": "Resort VIP · 14 años Emovils ya sirve esta zona · alta calidad esperada",
        "empresas_conocidas": [
            "Casa de Campo Resort & Villas",
            "Marbella Casa de Campo",
            "Be Live Collection Canoa",
            "Whala! Bayahibe",
            "Dreams La Romana",
            "Iberostar Hacienda Dominicus",
            "Viva Wyndham Dominicus Beach",
        ],
        "tipo": "Hotel",
        "ciudad": "La Romana",
    },
    {
        "categoria": "Líneas Navieras y Cruceros con escala RD",
        "score_base": 80,
        "razon": "Tripulaciones requieren transporte AILA ↔ puerto regular",
        "empresas_conocidas": [
            "Carnival Cruise Lines (escala SPM)",
            "Royal Caribbean (escala La Romana)",
            "Norwegian Cruise Line (escala SPM)",
            "MSC Cruises (escala La Romana)",
            "Costa Cruceros (escala SPM)",
            "Princess Cruises (escala La Romana)",
            "Disney Cruise Line",
        ],
        "tipo": "Naviera",
        "ciudad": "San Pedro de Macorís",
    },
    {
        "categoria": "Empresas con turno nocturno (BPO, fábricas)",
        "score_base": 75,
        "razon": "Necesitan transporte seguro para empleadas nocturnas",
        "empresas_conocidas": [
            "Liberty Travel Dominican Republic",
            "DHL Express RD",
            "FedEx Dominican Republic",
            "UPS RD",
            "Yobel SCM Dominican Republic",
        ],
        "tipo": "Corporativo_Otro",
        "ciudad": "Santo Domingo",
    },
    {
        "categoria": "Embajadas y Consulados",
        "score_base": 70,
        "razon": "Personal diplomático · servicios VIP frecuentes",
        "empresas_conocidas": [
            "Embajada de Estados Unidos en RD",
            "Embajada de España en RD",
            "Embajada de Francia en RD",
            "Embajada de Alemania en RD",
            "Embajada del Reino Unido en RD",
            "Embajada de Canadá en RD",
            "Embajada de Brasil en RD",
            "Embajada de Italia en RD",
        ],
        "tipo": "Embajada",
        "ciudad": "Santo Domingo",
    },
    {
        "categoria": "Agencias de viajes y receptivos VIP",
        "score_base": 70,
        "razon": "Revenden traslados a turistas premium",
        "empresas_conocidas": [
            "Caribbean Tours Dominican Republic",
            "Amstar DMC",
            "Apple Vacations RD",
            "Bahia Principe Travel",
            "Tropical Tours",
            "VIP Dominican Tours",
            "Maxim Dominican Tours",
        ],
        "tipo": "Agencia_Viajes",
        "ciudad": "Punta Cana",
    },
]


# ─────────────────────────────────────────────────────────────
# GENERACIÓN DE PROSPECTS DESDE SEMILLA
# ─────────────────────────────────────────────────────────────

def generar_prospects_semilla() -> list[Prospect]:
    """Convierte el catálogo en Prospects listos para CRM."""
    prospects: list[Prospect] = []
    for categoria in CATEGORIAS_TARGET:
        for empresa in categoria["empresas_conocidas"]:
            prospects.append(Prospect(
                nombre_empresa=empresa,
                tipo_empresa=categoria["tipo"],
                razon_potencial=categoria["razon"],
                ciudad=categoria["ciudad"],
                fuente_scraping="Catalogo_RD_Manual",
                score=categoria["score_base"],
                notas=f"Categoría: {categoria['categoria']}",
                estado_pipeline="NUEVO",
            ))
    return prospects


# ─────────────────────────────────────────────────────────────
# PLANTILLAS DE EMAIL OUTREACH (LISTAS PARA MAILERLITE)
# ─────────────────────────────────────────────────────────────

PLANTILLAS_OUTREACH = {
    "Call_Center": {
        "asunto": "Reducir 30% el costo de transporte nocturno de su staff (RD)",
        "cuerpo": (
            "Buenos días equipo de [EMPRESA],\n\n"
            "Soy Noe Vásquez, dueño de Emovils — transporte ejecutivo en RD con 14 años "
            "operando para call centers locales (incluyendo Intelcia).\n\n"
            "Vi que [EMPRESA] tiene operación nocturna en Santo Domingo. Quería compartirles "
            "cómo estamos ayudando a empresas como la suya a:\n\n"
            "  ✅ Reducir 30% el costo total de transporte de empleados nocturnos\n"
            "  ✅ 99.5% de cumplimiento puntual en los últimos 12 meses\n"
            "  ✅ Reporte automático del cumplimiento cada mañana\n"
            "  ✅ Flota de 28 vans Caravan + H1 con choferes verificados\n\n"
            "¿Tendrían 15 minutos esta semana para una conversación? Les comparto un caso "
            "real de un call center con 50+ empleadas nocturnas que está ahorrando RD$120,000/mes.\n\n"
            "Si prefieren, también podemos arrancar con una semana piloto sin compromiso.\n\n"
            "Saludos,\n"
            "Noe Vásquez\n"
            "Emovils · WhatsApp 829-861-0090\n"
            "🔗 emovils.com"
        ),
    },
    "Hotel": {
        "asunto": "Sus huéspedes VIP merecen llegar al resort sin estrés ✈️",
        "cuerpo": (
            "Estimado equipo de [EMPRESA],\n\n"
            "Soy Noe Vásquez de Emovils — transporte ejecutivo en RD con 14 años atendiendo "
            "huéspedes premium de hoteles 5 estrellas en la región.\n\n"
            "Quería ofrecerles nuestro servicio de transfers ejecutivos AILA → [HOTEL]:\n\n"
            "  ✅ Vans Caravan & H1 con chofer profesional uniformado\n"
            "  ✅ Tracking de vuelo en tiempo real (esperamos retrasos sin costo)\n"
            "  ✅ Tarifa cerrada negociada para su hotel\n"
            "  ✅ Facturación NCF · Reportes mensuales\n"
            "  ✅ Servicio 24/7 con WhatsApp directo al supervisor\n\n"
            "Ofrecemos comisión 10-15% sobre cada transfer reservado por su concierge.\n\n"
            "¿Podemos coordinar 20 minutos esta semana para presentarles nuestra propuesta?\n\n"
            "Saludos cordiales,\n"
            "Noe Vásquez\n"
            "Emovils · 829-861-0090 · emovils.com"
        ),
    },
    "Naviera": {
        "asunto": "Logística de tripulación AILA ↔ puerto · 14 años en RD",
        "cuerpo": (
            "Estimado equipo de [EMPRESA],\n\n"
            "Soy Noe Vásquez de Emovils. Llevamos 14 años manejando logística de "
            "transporte de tripulación (ENROLO/DESENROLO) para líneas navieras con escala "
            "en La Romana, San Pedro de Macorís y Casa de Campo.\n\n"
            "Si están buscando un operador local confiable para sus necesidades de transfer "
            "de crew, ofrecemos:\n\n"
            "  ✅ Coordinación nocturna 24/7 con su superintendente\n"
            "  ✅ Vans Caravan & H1 con capacidad para grupos\n"
            "  ✅ Tarifa por servicio o contrato mensual cerrado\n"
            "  ✅ Reportes con QR de verificación por crew member\n\n"
            "Atendemos actualmente a varias líneas con servicio en estos puertos.\n\n"
            "¿Conversamos por WhatsApp 829-861-0090 o por email?\n\n"
            "Saludos,\n"
            "Noe Vásquez\n"
            "Emovils RD · emovils.com"
        ),
    },
    "Corporativo_Otro": {
        "asunto": "Transporte ejecutivo confiable para su equipo (14 años RD)",
        "cuerpo": (
            "Buenos días [EMPRESA],\n\n"
            "Soy Noe Vásquez de Emovils — transporte ejecutivo en RD con 14 años de operación.\n\n"
            "Si su equipo necesita servicios recurrentes de transporte (aeropuerto, reuniones, "
            "eventos, traslados nocturnos), podemos ofrecerles:\n\n"
            "  ✅ Cuenta corporativa con facturación NCF mensual\n"
            "  ✅ Tarifas preferenciales por volumen\n"
            "  ✅ Vans ejecutivas con chofer profesional\n"
            "  ✅ Servicio 24/7 vía WhatsApp\n\n"
            "¿Podríamos agendar 15 minutos esta semana?\n\n"
            "Saludos,\n"
            "Noe Vásquez · Emovils\n"
            "829-861-0090 · emovils.com"
        ),
    },
    "Embajada": {
        "asunto": "Transporte VIP para personal diplomático en RD",
        "cuerpo": (
            "Distinguido equipo de [EMPRESA],\n\n"
            "Soy Noe Vásquez, dueño de Emovils — transporte ejecutivo con 14 años de "
            "experiencia en RD.\n\n"
            "Ofrecemos servicios de transporte VIP para personal diplomático con:\n\n"
            "  ✅ Discreción y profesionalismo total\n"
            "  ✅ Choferes con antecedentes verificados\n"
            "  ✅ Vans Caravan & H1 ejecutivas\n"
            "  ✅ Tarifa cerrada o contrato mensual\n"
            "  ✅ Facturación NCF\n\n"
            "Estamos disponibles para presentar nuestra propuesta cuando deseen.\n\n"
            "Saludos cordiales,\n"
            "Noe Vásquez · Emovils RD\n"
            "829-861-0090 · emovils.com"
        ),
    },
    "Agencia_Viajes": {
        "asunto": "Comisión 15% por cada transfer reservado · Emovils",
        "cuerpo": (
            "Hola equipo de [EMPRESA],\n\n"
            "Soy Noe Vásquez de Emovils. Llevamos 14 años en RD ofreciendo transfers "
            "ejecutivos premium.\n\n"
            "Queremos ofrecerles convenio de receptivo con:\n\n"
            "  ✅ Comisión 15% por cada transfer reservado\n"
            "  ✅ Tarifa preferencial confidencial\n"
            "  ✅ Vans Caravan & H1 con servicio premium\n"
            "  ✅ Reservas vía WhatsApp con confirmación instantánea\n"
            "  ✅ Tracking en tiempo real para sus clientes\n\n"
            "¿Conversamos esta semana? 15 minutos.\n\n"
            "Saludos,\n"
            "Noe Vásquez · Emovils\n"
            "829-861-0090 · emovils.com"
        ),
    },
}


def plantilla_para_tipo(tipo_empresa: str) -> dict:
    """Devuelve la plantilla de email para el tipo de empresa."""
    return PLANTILLAS_OUTREACH.get(tipo_empresa, PLANTILLAS_OUTREACH["Corporativo_Otro"])


# ─────────────────────────────────────────────────────────────
# CARGA EN AIRTABLE PIPELINE_COMERCIAL
# ─────────────────────────────────────────────────────────────

def guardar_prospects_en_airtable(prospects: list[Prospect]) -> int:
    """Inserta los prospects en la tabla Pipeline_Comercial."""
    api = AirtableOPC()
    creados = 0
    for p in prospects:
        try:
            # Evitar duplicados
            existente = api.buscar_por_campo(
                "Pipeline_Comercial", "Empresa_nombre", p.nombre_empresa
            )
            if existente:
                continue

            api.crear_registro("Pipeline_Comercial", {
                "Empresa_nombre": p.nombre_empresa,
                "Tipo_empresa": p.tipo_empresa,
                "Fuente_scraping": "Manual",
                "Contacto_principal": p.contacto_principal,
                "Cargo": p.cargo,
                "Email": p.email,
                "LinkedIn_url": p.linkedin_url,
                "WhatsApp": p.whatsapp,
                "Estado_pipeline": p.estado_pipeline,
                "Score_calificacion": p.score,
                "Fecha_primer_contacto": datetime.now().isoformat(timespec="seconds"),
                "Notas": f"{p.razon_potencial} · Ciudad: {p.ciudad} · {p.notas}",
            })
            creados += 1
        except Exception as e:
            logger.error(f"Error con {p.nombre_empresa}: {e}")
    return creados


# ─────────────────────────────────────────────────────────────
# REPORTE DEL PIPELINE
# ─────────────────────────────────────────────────────────────

def resumen_pipeline() -> dict:
    """Estadísticas del pipeline actual."""
    api = AirtableOPC()
    todos = api.listar("Pipeline_Comercial")

    por_estado: dict[str, int] = {}
    por_tipo: dict[str, int] = {}
    for p in todos:
        f = p["fields"]
        estado = f.get("Estado_pipeline", "DESCONOCIDO")
        tipo = f.get("Tipo_empresa", "DESCONOCIDO")
        por_estado[estado] = por_estado.get(estado, 0) + 1
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1

    return {
        "total_prospects": len(todos),
        "por_estado": por_estado,
        "por_tipo": por_tipo,
    }


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 70)
    print("EMOVILS OPC — Agente Prospector B2B")
    print("=" * 70)

    print("\n🔍 Generando prospects semilla...")
    prospects = generar_prospects_semilla()
    print(f"  ✓ {len(prospects)} prospects identificados")

    # Mostrar distribución
    por_tipo: dict[str, int] = {}
    for p in prospects:
        por_tipo[p.tipo_empresa] = por_tipo.get(p.tipo_empresa, 0) + 1

    print("\n📊 Distribución por tipo:")
    for tipo, count in sorted(por_tipo.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {tipo}: {count}")

    # Guardar en Airtable
    print("\n💾 Guardando en Airtable Pipeline_Comercial...")
    creados = guardar_prospects_en_airtable(prospects)
    print(f"  ✓ {creados} prospects nuevos cargados (los duplicados se ignoraron)")

    # Mostrar plantillas disponibles
    print("\n📧 Plantillas email outreach disponibles:")
    for tipo in PLANTILLAS_OUTREACH.keys():
        print(f"  • {tipo}")

    # Resumen
    resumen = resumen_pipeline()
    print(f"\n📊 PIPELINE COMERCIAL ACTUAL:")
    print(f"  Total: {resumen['total_prospects']} prospects")
    print(f"  Por estado: {resumen['por_estado']}")

    print()
    print("=" * 70)
    print("✓ Agente Prospector operativo")
    print(f"🔗 Ver en Airtable: https://airtable.com/{os.environ['AIRTABLE_BASE_ID']}")
    print("=" * 70)
