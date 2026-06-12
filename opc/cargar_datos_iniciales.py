"""
Carga los datos iniciales en las tablas de Airtable de la base Emovils OPC:
  1. Rutas_Intelcia (las 13 rutas)
  2. Tarifas_Referencia (40+ trayectos)
  3. Empresas_B2B (crea Intelcia como cliente)
  4. Servicios (procesa el Excel real de Intelcia del 6-jun)
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests


# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "opc" / "data"


def _cargar_env() -> None:
    env_file = ROOT / ".env"
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


_cargar_env()
sys.path.insert(0, str(ROOT))

from opc.intelcia_parser import parse_intelcia_excel
from opc.precios import comision_emovils

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.environ["AIRTABLE_API_KEY"]
BASE_ID = os.environ["AIRTABLE_BASE_ID"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}
API_BASE = f"https://api.airtable.com/v0/{BASE_ID}"


def _crear_lote(tabla: str, registros: list[dict], paso: int = 10) -> list[dict]:
    """Crea registros en lotes de hasta 10. Devuelve los creados."""
    creados: list[dict] = []
    for i in range(0, len(registros), paso):
        chunk = registros[i:i + paso]
        body = {"records": [{"fields": c} for c in chunk], "typecast": True}
        r = requests.post(f"{API_BASE}/{tabla}", headers=HEADERS, json=body)
        if not r.ok:
            print(f"  ❌ Error creando en {tabla}: {r.status_code} {r.text[:300]}")
            continue
        creados.extend(r.json().get("records", []))
        time.sleep(0.3)
    return creados


def _existe_registro_por_campo(tabla: str, campo: str, valor) -> str | None:
    """Devuelve el record_id si existe, None si no."""
    # Para campos numéricos no podemos hacer string interpolation, usar como número
    if isinstance(valor, (int, float)):
        formula = f"{{{campo}}} = {valor}"
    else:
        formula = f"{{{campo}}} = '{valor}'"
    r = requests.get(
        f"{API_BASE}/{tabla}",
        headers=HEADERS,
        params={"filterByFormula": formula, "maxRecords": 1},
    )
    if r.ok:
        records = r.json().get("records", [])
        return records[0]["id"] if records else None
    return None


# ─────────────────────────────────────────────────────────────
# 1. CARGAR TARIFARIO INTELCIA (13 rutas)
# ─────────────────────────────────────────────────────────────

def cargar_rutas_intelcia() -> dict[int, str]:
    """Carga las 13 rutas y devuelve mapping {ruta_id: record_id}."""
    print("\n📊 Cargando Tarifario Intelcia (13 rutas)...")
    with open(DATA_DIR / "tarifario_intelcia.json") as f:
        data = json.load(f)

    registros = []
    for ruta in data["rutas"]:
        nombre_zonas = ", ".join(ruta["zonas"][:3]) + (
            f" +{len(ruta['zonas']) - 3} más" if len(ruta["zonas"]) > 3 else ""
        )
        registros.append({
            "Ruta_ID": ruta["ruta_id"],
            "Nombre_ruta": ruta["nombre"],
            "Tarifa_1_4_pax_RD": ruta["tarifa_1_4_pax"],
            "Tarifa_5_10_pax_RD": ruta["tarifa_5_10_pax"],
            "Notas_especiales": ruta.get("notas") or "",
            "Activa": True,
        })

    creados = _crear_lote("Rutas_Intelcia", registros)
    mapping = {
        int(r["fields"]["Ruta_ID"]): r["id"]
        for r in creados
        if "Ruta_ID" in r["fields"]
    }
    print(f"  ✓ {len(creados)} rutas creadas")
    return mapping


# ─────────────────────────────────────────────────────────────
# 2. CARGAR TARIFARIO REFERENCIA (40+ trayectos)
# ─────────────────────────────────────────────────────────────

def cargar_tarifario_referencia() -> int:
    """Carga los trayectos pre-calculados."""
    print("\n📊 Cargando Tarifario de Referencia (~40 trayectos)...")
    with open(DATA_DIR / "tarifario_referencia.json") as f:
        data = json.load(f)

    registros: list[dict] = []

    # Desde/Hacia AILA
    for t in data["desde_hacia_aila"]:
        registros.append({
            "Origen": "AILA",
            "Destino": t["destino"],
            "Km": t["km"],
            "Tarifa_dia_RD": t["dia_rd"],
            "Tarifa_noche_RD": t["noche_rd"],
            "Modo": "LARGA_DISTANCIA",
            "Categoria": "AILA_Hacia",
        })

    # Desde Centro SD
    for t in data["desde_centro_sd"]:
        registros.append({
            "Origen": "Centro Santo Domingo",
            "Destino": t["destino"],
            "Km": t["km"],
            "Tarifa_dia_RD": t["dia_rd"],
            "Tarifa_noche_RD": t["noche_rd"],
            "Modo": "LARGA_DISTANCIA",
            "Categoria": "Centro_SD_Hacia",
        })

    # Dentro de SD
    for t in data["dentro_santo_domingo"]:
        partes = t["trayecto"].split("→") if "→" in t["trayecto"] else (t["trayecto"], "")
        if isinstance(partes, tuple):
            origen, destino = partes
        else:
            origen, destino = partes[0].strip(), partes[1].strip() if len(partes) > 1 else ""
        registros.append({
            "Origen": origen,
            "Destino": destino or t["trayecto"],
            "Km": t["km"],
            "Tarifa_dia_RD": t["dia_rd"],
            "Tarifa_noche_RD": t["noche_rd"],
            "Modo": "CIUDAD",
            "Categoria": "Intra_SD",
        })

    creados = _crear_lote("Tarifas_Referencia", registros)
    print(f"  ✓ {len(creados)} tarifas de referencia cargadas")
    return len(creados)


# ─────────────────────────────────────────────────────────────
# 3. CREAR INTELCIA COMO EMPRESA B2B
# ─────────────────────────────────────────────────────────────

def crear_empresa_intelcia() -> str:
    """Crea la empresa Intelcia en Empresas_B2B."""
    print("\n🏢 Creando empresa Intelcia en Empresas_B2B...")

    existente = _existe_registro_por_campo("Empresas_B2B", "Razon_social", "Intelcia")
    if existente:
        print(f"  ⚠️ Intelcia ya existe ({existente}), salto")
        return existente

    body = {
        "fields": {
            "Razon_social": "Intelcia",
            "Nombre_comercial": "Intelcia · Punta Arrecife",
            "Tipo": "Call_Center",
            "Codigo_corporativo": "INTELCIA-2026",
            "Plazo_pago_dias": 30,
            "Forma_recepcion_pedidos": "Excel_diario",
            "Fecha_inicio_relacion": "2024-01-01",
            "Activo": True,
            "Notas": "Cliente principal · Excel nocturno con rutas pre-acordadas (13 rutas). Cobro mensual con NCF.",
        },
        "typecast": True,
    }
    r = requests.post(f"{API_BASE}/Empresas_B2B", headers=HEADERS, json=body)
    if not r.ok:
        print(f"  ❌ Error: {r.status_code} {r.text[:300]}")
        return ""
    rec_id = r.json()["id"]
    print(f"  ✓ Intelcia creada ({rec_id})")
    return rec_id


# ─────────────────────────────────────────────────────────────
# 4. CARGAR SERVICIOS REALES DEL EXCEL DE 6-JUNIO
# ─────────────────────────────────────────────────────────────

def cargar_servicios_intelcia(
    ruta_mapping: dict[int, str],
    empresa_intelcia_id: str,
) -> int:
    """Procesa el Excel real y crea los 16 servicios en la tabla Servicios."""
    print("\n🚖 Procesando Excel real de Intelcia (6-jun-2026)...")

    excel_path = "/Users/noevasquez/Desktop/EMOVILS/Transporte Jun-06th -  2026.xlsx"
    if not Path(excel_path).exists():
        print(f"  ⚠️ No encontré Excel en {excel_path}")
        return 0

    servicios_intelcia = parse_intelcia_excel(excel_path)
    print(f"  ✓ {len(servicios_intelcia)} servicios extraídos del Excel")

    registros = []
    for s in servicios_intelcia:
        nombres = []
        for emp in s.empleados:
            nombres.append(f"{emp.z_id} — {emp.nombre_validado}")

        registros.append({
            "Fecha": s.fecha,
            "Hora_salida": s.hora_servicio,
            "Canal": "Call_Center_Intelcia",
            "Modo_servicio": "Programado",
            "Empresa_B2B": [empresa_intelcia_id] if empresa_intelcia_id else None,
            "Ruta_Intelcia": (
                [ruta_mapping[s.ruta_id]]
                if s.ruta_id in ruta_mapping else None
            ),
            "Origen_texto": (
                s.empleados[0].direccion[:200]
                if s.empleados and s.empleados[0].direccion
                else f"Ruta {s.ruta_id} Intelcia"
            ),
            "Destino_texto": "Intelcia · Punta Arrecife",
            "Cantidad_pax": s.cantidad_pax,
            "Tarifa_aplicada_RD": s.tarifa_aplicada_rd,
            "Total_a_cobrar_RD": s.tarifa_aplicada_rd,
            "Modo_calculo_precio": "INTELCIA",
            "Pasajeros_lista": "\n".join(nombres),
            "Estado": "COMPLETADO",
            "Estado_pago": "A_CREDITO",
            "Forma_pago": "CREDITO_B2B",
            "Comision_Emovils_RD": int(s.tarifa_aplicada_rd * 0.30),  # Asumimos afiliado
            "Pago_al_chofer_RD": int(s.tarifa_aplicada_rd * 0.70),
            "Creado_en": "2026-06-06T16:30:00",
            "Notas": f"Servicio del 6-jun 2026 · Cargado desde Excel real",
        })

    # Limpiar nulls
    for r in registros:
        for k in list(r.keys()):
            if r[k] is None:
                del r[k]

    creados = _crear_lote("Servicios", registros)
    print(f"  ✓ {len(creados)} servicios cargados en Airtable")
    total_facturado = sum(s.tarifa_aplicada_rd for s in servicios_intelcia)
    print(f"  📊 Total facturado del 6-jun: RD${total_facturado:,}")
    return len(creados)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("EMOVILS OPC — Carga de Datos Iniciales")
    print(f"Base: {BASE_ID}")
    print("=" * 70)

    rutas_mapping = cargar_rutas_intelcia()
    cargar_tarifario_referencia()
    intelcia_id = crear_empresa_intelcia()
    n_servicios = cargar_servicios_intelcia(rutas_mapping, intelcia_id)

    print()
    print("=" * 70)
    print("📊 RESUMEN FINAL")
    print("=" * 70)
    print(f"  ✓ Rutas Intelcia cargadas: {len(rutas_mapping)}")
    print(f"  ✓ Empresa Intelcia: {'creada' if intelcia_id else 'error'}")
    print(f"  ✓ Servicios reales del 6-jun: {n_servicios}")
    print()
    print(f"🔗 Ver en vivo: https://airtable.com/{BASE_ID}")
    print("=" * 70)


if __name__ == "__main__":
    main()
