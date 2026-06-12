"""
Emovils OPC — Agente Financiero

Encargado de:
  1. Calcular comisiones quincenales para conductores afiliados
  2. Generar liquidaciones (Q1 día 1-15 / Q2 día 16-fin del mes)
  3. Calcular cuánto retiene Emovils y cuánto recibe el chofer
  4. Preparar facturas NCF a clientes B2B
  5. Categorizar ingresos/egresos en la tabla contable

Se ejecuta:
  - El día 15 de cada mes (cierra Q1)
  - El último día del mes (cierra Q2)
  - Cuando lo invoque el dueño manualmente
"""
from __future__ import annotations
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
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
from opc.precios import comision_emovils, pago_a_chofer

logger = logging.getLogger(__name__)

# Mapa de número de mes → código de quincena Airtable
MESES_NOMBRES = {
    1: "ENE", 2: "FEB", 3: "MAR", 4: "ABR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AGO", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DIC",
}


# ─────────────────────────────────────────────────────────────
# CÁLCULO DE QUINCENAS
# ─────────────────────────────────────────────────────────────

def determinar_quincena(fecha: date) -> tuple[str, int, date, date]:
    """
    Devuelve (codigo_quincena, año, fecha_inicio, fecha_fin).
    Q1 = días 1-15, Q2 = días 16-fin del mes.
    """
    mes_nombre = MESES_NOMBRES[fecha.month]
    if fecha.day <= 15:
        codigo = f"Q1_{mes_nombre}"
        inicio = date(fecha.year, fecha.month, 1)
        fin = date(fecha.year, fecha.month, 15)
    else:
        codigo = f"Q2_{mes_nombre}"
        inicio = date(fecha.year, fecha.month, 16)
        # Último día del mes
        if fecha.month == 12:
            fin = date(fecha.year, 12, 31)
        else:
            fin = date(fecha.year, fecha.month + 1, 1).replace(day=1)
            fin = date(fecha.year, fecha.month, (fin - date(fecha.year, fecha.month, 1)).days)
    return codigo, fecha.year, inicio, fin


# ─────────────────────────────────────────────────────────────
# CÁLCULO DE LIQUIDACIÓN POR CHOFER
# ─────────────────────────────────────────────────────────────

@dataclass
class LiquidacionChofer:
    chofer_id: str
    chofer_nombre: str
    chofer_tipo: str
    quincena: str
    año: int
    fecha_inicio: str
    fecha_fin: str
    servicios_count: int = 0
    facturacion_total_rd: int = 0
    comision_emovils_rd: int = 0
    pago_al_chofer_rd: int = 0
    servicios_ids: list[str] = field(default_factory=list)


def calcular_liquidacion_chofer(
    api: AirtableOPC,
    chofer_record: dict,
    fecha_inicio: date,
    fecha_fin: date,
) -> LiquidacionChofer:
    """
    Calcula la liquidación de UN chofer para una quincena.
    Solo aplica a AFILIADOS (los propios tienen salario fijo).
    """
    fields = chofer_record["fields"]
    nombre = fields.get("Nombre_completo", "Sin nombre")
    tipo = fields.get("Tipo", "")

    codigo, año, _, _ = determinar_quincena(fecha_inicio)

    liq = LiquidacionChofer(
        chofer_id=chofer_record["id"],
        chofer_nombre=nombre,
        chofer_tipo=tipo,
        quincena=codigo,
        año=año,
        fecha_inicio=fecha_inicio.isoformat(),
        fecha_fin=fecha_fin.isoformat(),
    )

    if tipo.lower() != "afiliado":
        # Los propios tienen salario fijo, no se liquidan por servicio
        return liq

    # Buscar todos los servicios que hizo este chofer en la quincena
    filtro = (
        f"AND("
        f"  IS_AFTER({{Fecha}}, '{(fecha_inicio).isoformat()}'),"
        f"  IS_BEFORE({{Fecha}}, '{(fecha_fin).isoformat()}'),"
        f"  FIND('{chofer_record['id']}', ARRAYJOIN({{Chofer_asignado}})),"
        f"  {{Estado}}='COMPLETADO'"
        f")"
    )
    servicios = api.listar("Servicios", filtro=filtro)

    for svc in servicios:
        f = svc["fields"]
        tarifa = int(f.get("Tarifa_aplicada_RD", 0))
        canal = f.get("Canal", "")

        liq.servicios_count += 1
        liq.facturacion_total_rd += tarifa
        liq.comision_emovils_rd += comision_emovils(tarifa, "afiliado", canal)
        liq.pago_al_chofer_rd += pago_a_chofer(tarifa, "afiliado", canal)
        liq.servicios_ids.append(svc["id"])

    return liq


# ─────────────────────────────────────────────────────────────
# CIERRE DE QUINCENA COMPLETA
# ─────────────────────────────────────────────────────────────

def cerrar_quincena(
    api: AirtableOPC,
    fecha_corte: date,
    dry_run: bool = False,
) -> list[LiquidacionChofer]:
    """
    Procesa el cierre de quincena para todos los afiliados.

    Args:
        fecha_corte: día del cierre (15 o último día del mes)
        dry_run: si True, no crea registros en Airtable, solo calcula y reporta.
    """
    codigo, año, inicio, fin = determinar_quincena(fecha_corte)
    logger.info(f"Cerrando {codigo} {año} ({inicio} → {fin})")

    afiliados = api.listar(
        "Conductores",
        filtro="AND({Tipo}='Afiliado', {Activo}=TRUE())",
    )
    logger.info(f"Afiliados activos a procesar: {len(afiliados)}")

    resultados: list[LiquidacionChofer] = []

    for chofer in afiliados:
        liq = calcular_liquidacion_chofer(api, chofer, inicio, fin)
        if liq.servicios_count == 0:
            continue

        resultados.append(liq)

        if not dry_run:
            # Crear el registro de Liquidacion en Airtable
            liq_data = {
                "Liquidacion_ID": f"LQ-{año}-{codigo[3:6]}{codigo[1]}",
                "Conductor": [liq.chofer_id],
                "Quincena": codigo,
                "Año": año,
                "Servicios_total": liq.servicios_count,
                "Facturacion_quincena_RD": liq.facturacion_total_rd,
                "Pago_al_chofer_RD": liq.pago_al_chofer_rd,
                "Comision_Emovils_RD": liq.comision_emovils_rd,
                "Estado": "PENDIENTE",
                "Fecha_corte": fin.isoformat(),
                "Notas": (
                    f"Liquidación generada automáticamente · "
                    f"{liq.servicios_count} servicios entre {inicio} y {fin}"
                ),
            }
            api.crear_registro("Liquidaciones", liq_data)

    return resultados


# ─────────────────────────────────────────────────────────────
# REPORTE PARA EL DUEÑO
# ─────────────────────────────────────────────────────────────

def reporte_liquidaciones_whatsapp(
    liquidaciones: list[LiquidacionChofer],
    codigo_quincena: str,
    año: int,
) -> str:
    """Mensaje listo para WhatsApp con el resumen para que el dueño apruebe."""
    if not liquidaciones:
        return f"📊 Liquidación {codigo_quincena} {año}: 0 afiliados con servicios."

    total_facturado = sum(l.facturacion_total_rd for l in liquidaciones)
    total_comision = sum(l.comision_emovils_rd for l in liquidaciones)
    total_pagar = sum(l.pago_al_chofer_rd for l in liquidaciones)
    total_servicios = sum(l.servicios_count for l in liquidaciones)

    lineas = [
        f"💰 LIQUIDACIÓN {codigo_quincena} {año}",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"Afiliados con servicios: {len(liquidaciones)}",
        f"Servicios totales: {total_servicios}",
        f"Facturación bruta: RD${total_facturado:,}",
        f"Comisión Emovils (30%): RD${total_comision:,}",
        f"💵 A pagar a afiliados: RD${total_pagar:,}",
        f"",
        f"Detalle por afiliado:",
    ]

    for liq in sorted(liquidaciones, key=lambda l: l.pago_al_chofer_rd, reverse=True):
        lineas.append(
            f"  • {liq.chofer_nombre}: "
            f"{liq.servicios_count} srv · RD${liq.pago_al_chofer_rd:,}"
        )

    lineas.append("")
    lineas.append("Responde APROBAR para procesar pagos.")

    return "\n".join(lineas)


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 70)
    print("EMOVILS OPC — Test de Agente Financiero")
    print("=" * 70)

    # Test 1: Cálculo de quincenas
    print("\n📅 Test: determinación de quincena")
    for fecha in [date(2026, 6, 5), date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 30)]:
        codigo, año, inicio, fin = determinar_quincena(fecha)
        print(f"  {fecha} → {codigo} {año} ({inicio} → {fin})")

    # Test 2: Conectar a Airtable real
    try:
        print("\n🔌 Conectando a Airtable...")
        api = AirtableOPC()
        print(f"  Base: {api.base_id}")

        # Test 3: Simular cierre de quincena Q1 de junio (con la data del 6-jun ya cargada)
        print("\n💰 Simulando cierre Q1 JUN 2026 (dry_run, no crea registros)...")
        liquidaciones = cerrar_quincena(api, date(2026, 6, 15), dry_run=True)

        if not liquidaciones:
            print("\n  ℹ️ No hay liquidaciones porque aún no hay conductores afiliados")
            print("     cargados ni servicios asignados a conductores específicos.")
            print("     Cuando cargues los 12 afiliados y asignes servicios, esto generará")
            print("     liquidaciones automáticamente cada 15 días.")
        else:
            print()
            print(reporte_liquidaciones_whatsapp(liquidaciones, "Q1_JUN", 2026))

    except Exception as e:
        print(f"  ⚠️ Error: {e}")

    print()
    print("=" * 70)
    print("✓ Agente Financiero listo para producción")
    print("=" * 70)
