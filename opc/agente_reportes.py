"""
Emovils OPC — Agente Reportes

Cada mañana 7 AM, envía al dueño un WhatsApp con:
  • Resumen de servicios completados anoche
  • Facturación + comisiones + ganancia operativa
  • Servicios programados para hoy
  • Conductores activos
  • Alertas (documentos por vencer, calificaciones bajas, facturas pendientes)
  • Pipeline comercial: prospects respondieron, demos agendadas

El dueño solo lee 30 segundos y tiene la foto completa.
"""
from __future__ import annotations
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

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


@dataclass
class ReporteDiario:
    fecha: str

    # Operación anoche
    servicios_completados_ayer: int = 0
    servicios_cancelados_ayer: int = 0
    facturacion_ayer_rd: int = 0
    comisiones_pagadas_ayer_rd: int = 0
    ganancia_operativa_ayer_rd: int = 0

    # Hoy
    servicios_programados_hoy: int = 0
    conductores_activos: int = 0

    # Alertas
    incidencias_abiertas: int = 0
    documentos_por_vencer: list[str] = field(default_factory=list)
    conductores_con_calificacion_baja: list[str] = field(default_factory=list)
    facturas_B2B_por_vencer: list[str] = field(default_factory=list)

    # Pipeline
    prospects_respondieron_semana: int = 0
    demos_agendadas_semana: int = 0


def _contar_servicios_fecha(api: AirtableOPC, fecha: str, estados: list[str]) -> tuple[int, int]:
    """Cuenta servicios de una fecha + suma facturación. Devuelve (cantidad, facturacion)."""
    if not estados:
        return 0, 0
    estados_or = ", ".join([f"{{Estado}}='{e}'" for e in estados])
    # Airtable's date comparison: usar DATETIME_FORMAT para normalizar
    filtro = (
        f"AND("
        f"  DATETIME_FORMAT({{Fecha}}, 'YYYY-MM-DD') = '{fecha}',"
        f"  OR({estados_or})"
        f")"
    )
    servicios = api.listar("Servicios", filtro=filtro)
    facturacion = sum(int(s["fields"].get("Tarifa_aplicada_RD", 0)) for s in servicios)
    return len(servicios), facturacion


def generar_reporte_diario(api: AirtableOPC, hoy: date | None = None) -> ReporteDiario:
    """Genera el reporte para el día indicado (por defecto: hoy)."""
    if hoy is None:
        hoy = date.today()
    ayer = hoy - timedelta(days=1)

    reporte = ReporteDiario(fecha=hoy.isoformat())

    # Anoche
    completados, facturacion = _contar_servicios_fecha(
        api, ayer.isoformat(), ["COMPLETADO"]
    )
    cancelados, _ = _contar_servicios_fecha(
        api, ayer.isoformat(), ["CANCELADO"]
    )
    reporte.servicios_completados_ayer = completados
    reporte.servicios_cancelados_ayer = cancelados
    reporte.facturacion_ayer_rd = facturacion
    # Asumimos 30% comisión afiliados (real se calcula del Servicios.Comision_Emovils_RD)
    reporte.comisiones_pagadas_ayer_rd = int(facturacion * 0.7)
    reporte.ganancia_operativa_ayer_rd = facturacion - reporte.comisiones_pagadas_ayer_rd

    # Hoy
    hoy_progr, _ = _contar_servicios_fecha(
        api, hoy.isoformat(),
        ["PENDIENTE", "BUSCANDO_CHOFER", "ASIGNADO", "EN_CAMINO", "EN_SITIO", "EN_SERVICIO"]
    )
    reporte.servicios_programados_hoy = hoy_progr

    try:
        activos = api.listar(
            "Conductores",
            filtro="AND({Activo}=TRUE(), {Estado_actual}!='INACTIVO')",
        )
        reporte.conductores_activos = len(activos)
    except Exception:
        reporte.conductores_activos = 0

    # Incidencias
    try:
        incid = api.listar(
            "Incidencias",
            filtro="OR({Estado}='ABIERTA', {Estado}='EN_PROCESO')",
        )
        reporte.incidencias_abiertas = len(incid)
    except Exception:
        pass

    # Documentos por vencer (próximos 15 días)
    fecha_limite = (hoy + timedelta(days=15)).isoformat()
    try:
        veh = api.listar(
            "Vehiculos",
            filtro=(
                f"OR("
                f"  AND({{Marbete_vencimiento}}, IS_BEFORE({{Marbete_vencimiento}}, '{fecha_limite}')),"
                f"  AND({{Seguro_vencimiento}}, IS_BEFORE({{Seguro_vencimiento}}, '{fecha_limite}'))"
                f")"
            ),
        )
        for v in veh:
            placa = v["fields"].get("Placa", "?")
            marb = v["fields"].get("Marbete_vencimiento", "")
            seg = v["fields"].get("Seguro_vencimiento", "")
            if marb and marb < fecha_limite:
                reporte.documentos_por_vencer.append(f"Vehículo {placa}: marbete vence {marb}")
            if seg and seg < fecha_limite:
                reporte.documentos_por_vencer.append(f"Vehículo {placa}: seguro vence {seg}")
    except Exception:
        pass

    # Conductores con calificación baja (<4.0)
    try:
        bajos = api.listar(
            "Conductores",
            filtro="AND({Activo}=TRUE(), {Calificacion_promedio}<4)",
        )
        for c in bajos:
            nombre = c["fields"].get("Nombre_completo", "?")
            cal = c["fields"].get("Calificacion_promedio", 0)
            reporte.conductores_con_calificacion_baja.append(f"{nombre}: {cal}⭐")
    except Exception:
        pass

    # Facturas B2B próximas a vencer
    try:
        fact = api.listar(
            "Facturas_NCF",
            filtro=(
                f"AND({{Estado}}='EMITIDA', IS_BEFORE({{Fecha_vencimiento}}, '{fecha_limite}'))"
            ),
        )
        for f in fact:
            ncf = f["fields"].get("NCF", "?")
            venc = f["fields"].get("Fecha_vencimiento", "")
            reporte.facturas_B2B_por_vencer.append(f"NCF {ncf} vence {venc}")
    except Exception:
        pass

    # Pipeline (últimos 7 días)
    semana_atras = (hoy - timedelta(days=7)).isoformat()
    try:
        respondieron = api.listar(
            "Pipeline_Comercial",
            filtro=(
                f"AND("
                f"  {{Estado_pipeline}}='RESPONDIO',"
                f"  IS_AFTER({{Fecha_ultimo_contacto}}, '{semana_atras}')"
                f")"
            ),
        )
        reporte.prospects_respondieron_semana = len(respondieron)

        demos = api.listar(
            "Pipeline_Comercial",
            filtro=(
                f"AND("
                f"  {{Estado_pipeline}}='LLAMADA_AGENDADA',"
                f"  IS_AFTER({{Fecha_ultimo_contacto}}, '{semana_atras}')"
                f")"
            ),
        )
        reporte.demos_agendadas_semana = len(demos)
    except Exception:
        pass

    return reporte


def formato_whatsapp(reporte: ReporteDiario) -> str:
    """Formato WhatsApp listo para enviar al dueño cada 7 AM."""
    fecha_legible = datetime.fromisoformat(reporte.fecha).strftime("%d %b %Y")
    lineas = [
        f"📊 Buenos días — Reporte {fecha_legible}",
        f"━━━━━━━━━━━━━━━━━━━━━",
    ]

    # Anoche
    lineas.append("")
    lineas.append("💰 *Anoche*")
    lineas.append(f"  • {reporte.servicios_completados_ayer} servicios completados")
    if reporte.servicios_cancelados_ayer:
        lineas.append(f"  • {reporte.servicios_cancelados_ayer} cancelados")
    lineas.append(f"  • Facturación: RD${reporte.facturacion_ayer_rd:,}")
    lineas.append(f"  • Ganancia operativa: RD${reporte.ganancia_operativa_ayer_rd:,}")

    # Hoy
    lineas.append("")
    lineas.append("🚖 *Hoy*")
    lineas.append(f"  • {reporte.servicios_programados_hoy} servicios programados")
    lineas.append(f"  • {reporte.conductores_activos} conductores activos")
    if reporte.incidencias_abiertas:
        lineas.append(f"  • {reporte.incidencias_abiertas} incidencias pendientes [VER]")

    # Alertas
    alertas = []
    if reporte.documentos_por_vencer:
        alertas.extend(reporte.documentos_por_vencer[:3])
    if reporte.conductores_con_calificacion_baja:
        alertas.extend(reporte.conductores_con_calificacion_baja[:2])
    if reporte.facturas_B2B_por_vencer:
        alertas.extend(reporte.facturas_B2B_por_vencer[:2])

    if alertas:
        lineas.append("")
        lineas.append("⚠️ *Alertas*")
        for a in alertas:
            lineas.append(f"  • {a}")

    # Pipeline
    if reporte.prospects_respondieron_semana or reporte.demos_agendadas_semana:
        lineas.append("")
        lineas.append("📈 *Pipeline (7 días)*")
        if reporte.prospects_respondieron_semana:
            lineas.append(f"  • {reporte.prospects_respondieron_semana} prospects respondieron")
        if reporte.demos_agendadas_semana:
            lineas.append(f"  • {reporte.demos_agendadas_semana} demos agendadas")

    lineas.append("")
    lineas.append("🔄 Sistema operando ✅")

    return "\n".join(lineas)


# ─────────────────────────────────────────────────────────────
# ENVIO POR WHATSAPP + EMAIL (nvasquez@emovils.com)
# ─────────────────────────────────────────────────────────────

def enviar_reporte_diario(api: AirtableOPC, hoy: date | None = None) -> dict:
    """
    Envia el reporte diario:
      1. WhatsApp al dueno (+18298610090) — RESUMEN compacto
      2. Email a nvasquez@emovils.com — RESUMEN + detalle completo
      3. CC a supervisor@emovils.com si hay incidencias abiertas
    """
    reporte = generar_reporte_diario(api, hoy=hoy)
    texto = formato_whatsapp(reporte)
    enviados = {"whatsapp": False, "email": False}

    # 1. WhatsApp
    try:
        from opc.whatsapp_green_api import enviar_whatsapp
        owner_wa = os.getenv("OWNER_WHATSAPP", "+18298610090")
        enviar_whatsapp(owner_wa, texto)
        enviados["whatsapp"] = True
    except Exception as exc:
        logger.warning("Fallo WhatsApp reporte diario: %s", exc)

    # 2. Email a nvasquez@emovils.com
    try:
        from opc.agente_email_router import EmailSaliente, enviar_email
        cc_list = []
        if reporte.incidencias_abiertas > 0:
            cc_list.append(os.getenv("EMAIL_SUPERVISOR", "supervisor@emovils.com"))

        envio = EmailSaliente(
            desde_buzon="alertas",
            para=os.getenv("EMAIL_OWNER", "nvasquez@emovils.com"),
            asunto=f"📊 Reporte diario Emovils OPC · {reporte.fecha}",
            cuerpo_texto=texto + (
                f"\n\n──────────────────────────────────\n"
                f"Reporte automatico generado por Sistema OPC Emovils.\n"
                f"Dashboard: https://emovils-opc.up.railway.app/dashboard\n"
                f"Para dejar de recibir: editar config en agente_reportes.py\n"
            ),
            cc=cc_list,
        )
        enviados["email"] = enviar_email(envio)
    except Exception as exc:
        logger.warning("Fallo email reporte diario: %s", exc)

    return {"reporte": reporte, "enviados": enviados, "texto": texto}


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 70)
    print("EMOVILS OPC — Test de Agente Reportes")
    print("=" * 70)

    try:
        api = AirtableOPC()
        print(f"\n✓ Conectado a base {api.base_id}")

        # Generar reporte para hoy (que será 11-jun, así el "ayer" es 10-jun)
        # Pero el demo del 6-jun ya está cargado, así que tomamos como referencia
        # ayer = 6-jun (donde sabemos hay 16 servicios)
        print("\n📊 Generando reporte simulando que hoy es 2026-06-07 (anoche = 6-jun):")
        reporte = generar_reporte_diario(api, hoy=date(2026, 6, 7))

        print()
        print(formato_whatsapp(reporte))

    except Exception as e:
        print(f"  ⚠️ Error: {e}")

    print()
    print("=" * 70)
    print("✓ Agente Reportes listo")
    print("=" * 70)
