"""
Emovils OPC — Cliente Airtable

Wrapper liviano sobre la API REST de Airtable para todas las operaciones
de la OPC. Implementa retry con backoff, manejo de errores y batching.

Uso:

    from opc.airtable_api_opc import AirtableOPC
    api = AirtableOPC()

    # Crear un servicio
    api.crear_registro("Servicios", {
        "Fecha": "2026-06-10",
        "Hora_salida": "21:00",
        ...
    })

    # Listar servicios pendientes
    pendientes = api.listar("Servicios", filtro="{Estado}='PENDIENTE'")

    # Actualizar
    api.actualizar("Servicios", record_id, {"Estado": "ASIGNADO"})
"""
from __future__ import annotations
import logging
import os
import time
from typing import Any, Iterator, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.airtable.com/v0"
MAX_BATCH = 10  # Airtable permite hasta 10 registros por request
MAX_RETRIES = 5
RETRY_BACKOFF_SECS = 1.0


class AirtableError(Exception):
    """Error específico de operaciones Airtable."""


class AirtableOPC:
    """Cliente para la base OPC de Airtable."""

    def __init__(
        self,
        api_key: str | None = None,
        base_id: str | None = None,
    ):
        self.api_key = api_key or os.getenv("AIRTABLE_API_KEY")
        self.base_id = base_id or os.getenv("AIRTABLE_BASE_ID")
        if not self.api_key:
            raise AirtableError("Falta AIRTABLE_API_KEY en variables de entorno")
        if not self.base_id:
            raise AirtableError("Falta AIRTABLE_BASE_ID en variables de entorno")

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    # ─────────────────────────────────────────────────────────
    # MÉTODOS HTTP CON RETRY
    # ─────────────────────────────────────────────────────────

    def _url(self, tabla: str, record_id: str | None = None) -> str:
        base = f"{API_BASE}/{self.base_id}/{tabla}"
        return f"{base}/{record_id}" if record_id else base

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """HTTP request con retry exponencial en rate limit y errores 5xx."""
        last_exc: Exception | None = None
        for intento in range(MAX_RETRIES):
            try:
                resp = self._session.request(method, url, timeout=30, **kwargs)
                if resp.status_code == 429:  # Rate limit
                    wait = RETRY_BACKOFF_SECS * (2 ** intento)
                    logger.warning(f"Rate limit. Esperando {wait}s...")
                    time.sleep(wait)
                    continue
                if 500 <= resp.status_code < 600:
                    wait = RETRY_BACKOFF_SECS * (2 ** intento)
                    logger.warning(f"Error {resp.status_code}. Reintentando en {wait}s...")
                    time.sleep(wait)
                    continue
                if not resp.ok:
                    raise AirtableError(
                        f"Airtable {method} {url} → {resp.status_code}: {resp.text[:200]}"
                    )
                return resp.json() if resp.text else {}
            except requests.RequestException as e:
                last_exc = e
                wait = RETRY_BACKOFF_SECS * (2 ** intento)
                logger.warning(f"Excepción de red: {e}. Reintentando en {wait}s...")
                time.sleep(wait)
        raise AirtableError(f"Falló después de {MAX_RETRIES} reintentos: {last_exc}")

    # ─────────────────────────────────────────────────────────
    # CRUD BÁSICO
    # ─────────────────────────────────────────────────────────

    def crear_registro(self, tabla: str, campos: dict) -> dict:
        """Crea un único registro en una tabla. Devuelve {id, fields, createdTime}."""
        body = {"fields": campos, "typecast": True}
        return self._request("POST", self._url(tabla), json=body)

    def crear_lote(self, tabla: str, lista_campos: list[dict]) -> list[dict]:
        """Crea hasta 10 registros en una sola request (batching automático si pasas más)."""
        creados: list[dict] = []
        for i in range(0, len(lista_campos), MAX_BATCH):
            chunk = lista_campos[i:i + MAX_BATCH]
            body = {
                "records": [{"fields": c} for c in chunk],
                "typecast": True,
            }
            resp = self._request("POST", self._url(tabla), json=body)
            creados.extend(resp.get("records", []))
            logger.info(f"Creados {len(chunk)} registros en {tabla}")
        return creados

    def obtener(self, tabla: str, record_id: str) -> dict:
        return self._request("GET", self._url(tabla, record_id))

    def actualizar(self, tabla: str, record_id: str, campos: dict) -> dict:
        body = {"fields": campos, "typecast": True}
        return self._request("PATCH", self._url(tabla, record_id), json=body)

    def actualizar_lote(self, tabla: str, actualizaciones: list[dict]) -> list[dict]:
        """
        Actualiza hasta 10 registros por request.
        actualizaciones: lista de {"id": "recXXXX", "fields": {...}}
        """
        result: list[dict] = []
        for i in range(0, len(actualizaciones), MAX_BATCH):
            chunk = actualizaciones[i:i + MAX_BATCH]
            body = {"records": chunk, "typecast": True}
            resp = self._request("PATCH", self._url(tabla), json=body)
            result.extend(resp.get("records", []))
        return result

    def eliminar(self, tabla: str, record_id: str) -> bool:
        resp = self._request("DELETE", self._url(tabla, record_id))
        return resp.get("deleted", False)

    # ─────────────────────────────────────────────────────────
    # CONSULTAS
    # ─────────────────────────────────────────────────────────

    def listar(
        self,
        tabla: str,
        filtro: str | None = None,
        max_records: int | None = None,
        view: str | None = None,
        campos: list[str] | None = None,
        sort: list[dict] | None = None,
    ) -> list[dict]:
        """
        Lista registros. Maneja paginación automática.

        filtro: filterByFormula de Airtable (ej. "{Estado}='ABIERTA'")
        view: nombre de vista personalizada
        campos: lista de campos a traer (más rápido si limitas)
        sort: [{"field":"Fecha","direction":"desc"}]
        """
        params: dict[str, Any] = {}
        if filtro:
            params["filterByFormula"] = filtro
        if view:
            params["view"] = view
        if campos:
            for i, c in enumerate(campos):
                params[f"fields[{i}]"] = c
        if sort:
            for i, s in enumerate(sort):
                params[f"sort[{i}][field]"] = s["field"]
                params[f"sort[{i}][direction]"] = s.get("direction", "asc")

        registros: list[dict] = []
        offset: str | None = None
        while True:
            if offset:
                params["offset"] = offset
            resp = self._request("GET", self._url(tabla), params=params)
            registros.extend(resp.get("records", []))
            if max_records and len(registros) >= max_records:
                return registros[:max_records]
            offset = resp.get("offset")
            if not offset:
                break
        return registros

    def buscar_por_campo(self, tabla: str, campo: str, valor: str) -> Optional[dict]:
        """Busca el primer registro donde {campo}=valor."""
        filtro = f"{{{campo}}} = '{valor}'"
        registros = self.listar(tabla, filtro=filtro, max_records=1)
        return registros[0] if registros else None

    def existe(self, tabla: str, campo: str, valor: str) -> bool:
        return self.buscar_por_campo(tabla, campo, valor) is not None

    # ─────────────────────────────────────────────────────────
    # HELPERS ESPECÍFICOS
    # ─────────────────────────────────────────────────────────

    def upsert(self, tabla: str, campo_clave: str, valor_clave: str, campos: dict) -> dict:
        """Crea si no existe, actualiza si ya existe."""
        existente = self.buscar_por_campo(tabla, campo_clave, valor_clave)
        if existente:
            return self.actualizar(tabla, existente["id"], campos)
        return self.crear_registro(tabla, campos)

    def conductores_disponibles(self) -> list[dict]:
        """Lista conductores con estado DISPONIBLE."""
        return self.listar(
            "Conductores",
            filtro="AND({Estado_actual}='DISPONIBLE', {Activo}=TRUE())",
        )

    def servicios_pendientes_asignar(self) -> list[dict]:
        """Servicios que aún no tienen chofer asignado."""
        return self.listar(
            "Servicios",
            filtro="OR({Estado}='PENDIENTE', {Estado}='BUSCANDO_CHOFER')",
            sort=[{"field": "Fecha", "direction": "asc"}],
        )

    def vehiculos_documentos_vencen_pronto(self, dias: int = 30) -> list[dict]:
        """Vehículos con marbete o seguro a punto de vencer."""
        return self.listar(
            "Vehiculos",
            filtro=(
                f"OR("
                f"  DATETIME_DIFF({{Marbete_vencimiento}}, TODAY(), 'days') <= {dias},"
                f"  DATETIME_DIFF({{Seguro_vencimiento}}, TODAY(), 'days') <= {dias}"
                f")"
            ),
        )


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 60)
    print("EMOVILS OPC — Test de cliente Airtable")
    print("=" * 60)

    # Verificar credenciales sin exponerlas
    api_key = os.getenv("AIRTABLE_API_KEY", "")
    base_id = os.getenv("AIRTABLE_BASE_ID", "")
    if not api_key:
        print("⚠️ AIRTABLE_API_KEY no está en variables de entorno")
        sys.exit(1)
    if not base_id:
        print("⚠️ AIRTABLE_BASE_ID no está en variables de entorno")
        sys.exit(1)

    print(f"✓ API key: {api_key[:8]}...{api_key[-4:]}")
    print(f"✓ Base ID: {base_id}")

    api = AirtableOPC()
    print(f"✓ Cliente AirtableOPC instanciado")
    print()
    print("Para probar consultas reales:")
    print("  python3 -c \"from opc.airtable_api_opc import AirtableOPC; ")
    print("              print(AirtableOPC().listar('Conductores', max_records=3))\"")
