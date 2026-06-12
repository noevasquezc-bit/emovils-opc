"""
Script de bootstrap: crea las 26 tablas en Airtable a partir de
la definición en airtable_schema.py.

Estrategia:
  1. Crea cada tabla SIN campos link (solo primitivos)
  2. Después agrega los campos link (que dependen de tablas existentes)
  3. Reporta el resultado
"""
from __future__ import annotations
import logging
import os
import time
from pathlib import Path

import requests


# ─────────────────────────────────────────────────────────────
# CARGA DE .env
# ─────────────────────────────────────────────────────────────

def _cargar_env() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        raise SystemExit(f"⚠️ No encontré .env en {env_file}")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


_cargar_env()

# Importar el schema (después de cargar env)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from opc.airtable_schema import TODAS_LAS_TABLAS, Tabla, Campo, FT_LINK

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


API_KEY = os.environ["AIRTABLE_API_KEY"]
BASE_ID = os.environ["AIRTABLE_BASE_ID"]
META_URL = f"https://api.airtable.com/v0/meta/bases/{BASE_ID}/tables"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


# ─────────────────────────────────────────────────────────────
# MAPEO DE CAMPOS NUESTROS → SCHEMA DE AIRTABLE
# ─────────────────────────────────────────────────────────────

def _campo_a_airtable(campo: Campo) -> dict:
    """Convierte nuestra estructura Campo → dict de Airtable API."""
    tipo = campo.tipo

    # Airtable NO permite crear autoNumber via API → reemplazamos por texto
    if tipo == "autoNumber":
        return {
            "name": campo.nombre,
            "type": "singleLineText",
            "description": "ID único (se asigna manualmente o vía script)",
        }

    base = {"name": campo.nombre, "type": tipo}

    # Opciones específicas por tipo
    if campo.tipo == "singleSelect":
        opciones = campo.opciones or ["Por definir"]
        base["options"] = {"choices": [{"name": o} for o in opciones]}

    elif campo.tipo == "multipleSelects":
        opciones = campo.opciones or ["Por definir"]
        base["options"] = {"choices": [{"name": o} for o in opciones]}

    elif campo.tipo == "currency":
        base["options"] = {"precision": 2, "symbol": "RD$"}

    elif campo.tipo == "number":
        base["options"] = {"precision": 0}

    elif campo.tipo == "percent":
        base["options"] = {"precision": 0}

    elif campo.tipo == "date":
        base["options"] = {"dateFormat": {"name": "iso"}}

    elif campo.tipo == "dateTime":
        base["options"] = {
            "dateFormat": {"name": "iso"},
            "timeFormat": {"name": "24hour"},
            "timeZone": "America/Santo_Domingo",
        }

    elif campo.tipo == "rating":
        base["options"] = {"max": 5, "icon": "star", "color": "yellowBright"}

    elif campo.tipo == "checkbox":
        base["options"] = {"icon": "check", "color": "greenBright"}

    return base


def _tabla_sin_links(tabla: Tabla) -> dict:
    """Devuelve el payload para crear la tabla SIN campos link."""
    campos_no_link = [c for c in tabla.campos if c.tipo != FT_LINK]
    if not campos_no_link:
        # Airtable exige al menos 1 campo. Agregamos uno temporal.
        campos_no_link = [Campo("Nombre", "singleLineText")]
    return {
        "name": tabla.nombre,
        "description": tabla.descripcion[:280],
        "fields": [_campo_a_airtable(c) for c in campos_no_link],
    }


def _listar_tablas_existentes() -> dict[str, str]:
    """Devuelve {nombre_tabla: tabla_id} de lo que YA existe en la base."""
    r = requests.get(META_URL, headers=HEADERS)
    r.raise_for_status()
    return {t["name"]: t["id"] for t in r.json().get("tables", [])}


def _crear_tabla(payload: dict) -> str:
    """Crea una tabla y devuelve su ID."""
    r = requests.post(META_URL, headers=HEADERS, json=payload)
    if not r.ok:
        raise RuntimeError(f"Error creando '{payload['name']}': {r.status_code} {r.text[:300]}")
    return r.json()["id"]


def _agregar_campo_link(tabla_id: str, campo: Campo, tabla_destino_id: str) -> None:
    """Agrega un campo link a una tabla existente."""
    url = f"{META_URL}/{tabla_id}/fields"
    payload = {
        "name": campo.nombre,
        "type": "multipleRecordLinks",
        "options": {"linkedTableId": tabla_destino_id},
    }
    r = requests.post(url, headers=HEADERS, json=payload)
    if not r.ok:
        logger.warning(
            f"  ⚠️ No agregué link '{campo.nombre}' → {campo.link_a}: "
            f"{r.status_code} {r.text[:150]}"
        )
    else:
        logger.info(f"  🔗 {campo.nombre} → {campo.link_a}")


def main() -> None:
    print("=" * 70)
    print(f"EMOVILS OPC — Bootstrap de Airtable")
    print(f"Base ID: {BASE_ID}")
    print("=" * 70)

    print(f"\n📋 Verificando estado actual...")
    existentes = _listar_tablas_existentes()
    print(f"   Tablas ya en la base: {len(existentes)}")
    for nombre, tid in existentes.items():
        print(f"     - {nombre} ({tid})")

    # FASE 1: Crear todas las tablas sin links
    print(f"\n🔨 FASE 1: Creando tablas (sin links primero)...")
    tabla_ids: dict[str, str] = dict(existentes)
    creadas = 0
    saltadas = 0
    fallidas: list[str] = []

    for tabla in TODAS_LAS_TABLAS:
        if tabla.nombre in tabla_ids:
            print(f"  ⏭️  '{tabla.nombre}' ya existe, salto")
            saltadas += 1
            continue
        try:
            payload = _tabla_sin_links(tabla)
            tid = _crear_tabla(payload)
            tabla_ids[tabla.nombre] = tid
            print(f"  ✓ Creada: '{tabla.nombre}' ({tid})")
            creadas += 1
            time.sleep(0.3)  # ser amable con el API
        except Exception as e:
            print(f"  ❌ FALLÓ '{tabla.nombre}': {str(e)[:200]}")
            fallidas.append(tabla.nombre)

    # FASE 2: Agregar links (necesita tablas creadas)
    print(f"\n🔗 FASE 2: Agregando campos link entre tablas...")
    links_agregados = 0
    links_fallidos = 0

    for tabla in TODAS_LAS_TABLAS:
        if tabla.nombre not in tabla_ids:
            continue
        tabla_id = tabla_ids[tabla.nombre]
        campos_link = [c for c in tabla.campos if c.tipo == FT_LINK and c.link_a]
        if not campos_link:
            continue
        print(f"\n  Tabla: {tabla.nombre}")
        for campo in campos_link:
            destino_id = tabla_ids.get(campo.link_a)
            if not destino_id:
                logger.warning(
                    f"  ⚠️ No agregué link '{campo.nombre}': "
                    f"tabla destino '{campo.link_a}' no existe"
                )
                links_fallidos += 1
                continue
            try:
                _agregar_campo_link(tabla_id, campo, destino_id)
                links_agregados += 1
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"  ⚠️ Error link {campo.nombre}: {str(e)[:150]}")
                links_fallidos += 1

    # RESUMEN
    print("\n" + "=" * 70)
    print("📊 RESUMEN")
    print("=" * 70)
    print(f"  Tablas creadas: {creadas}")
    print(f"  Tablas saltadas (ya existían): {saltadas}")
    print(f"  Tablas fallidas: {len(fallidas)}")
    print(f"  Links agregados: {links_agregados}")
    print(f"  Links fallidos: {links_fallidos}")
    if fallidas:
        print(f"\n  Tablas con error:")
        for t in fallidas:
            print(f"    - {t}")
    print(f"\n  Base URL: https://airtable.com/{BASE_ID}")
    print("=" * 70)


if __name__ == "__main__":
    main()
