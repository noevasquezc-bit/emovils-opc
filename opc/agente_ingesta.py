"""
Emovils OPC — Agente Ingesta

Orquestador central que recibe solicitudes de servicio de CUALQUIER canal:

    1. Excel diario de Intelcia (call center corporativo)
    2. Tabla estructurada de navieras (crucero ENROLO/DESENROLO)
    3. Texto libre de WhatsApp / email (VIP, multi-leg)
    4. Llamada telefónica (transcrita por Whisper)
    5. Form web emovils.com
    6. Mensaje de Instagram / Facebook DM

Para cada entrada:
    1. Detecta el canal (clasificación)
    2. Normaliza al formato Servicio estándar
    3. Calcula tarifa (usando precios.py)
    4. Crea registro en Airtable (vía airtable_api_opc.py)
    5. Dispara al Agente Despachador para asignar conductor

Este es el punto único de entrada. Cualquier nueva forma de recibir
solicitudes se agrega aquí como un nuevo método `procesar_*`.
"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from opc.intelcia_parser import (
    ServicioIntelcia,
    parse_intelcia_excel,
)
from opc.precios import calcular_tarifa, comision_emovils, pago_a_chofer

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CANALES SOPORTADOS
# ─────────────────────────────────────────────────────────────

class Canal:
    INTELCIA = "Call_Center_Intelcia"
    OTRO_CALL_CENTER = "Call_Center_Otro"
    NAVIERA = "Naviera"
    VIP = "VIP"
    REFERIDO = "Referido"
    REDES_SOCIALES = "Redes_Sociales"
    WEB = "Web"
    LLAMADA = "Llamada_Directa"
    WHATSAPP = "WhatsApp"


# ─────────────────────────────────────────────────────────────
# REPRESENTACIÓN NORMALIZADA DE UN SERVICIO
# ─────────────────────────────────────────────────────────────

@dataclass
class ServicioNormalizado:
    """Lo que el Agente Ingesta devuelve para cualquier canal."""
    canal: str
    fecha: str                          # YYYY-MM-DD
    hora_salida: str                    # HH:MM
    origen: str
    destino: str
    pasajeros: int
    tarifa_rd: int
    modo_calculo: str                   # "CIUDAD", "LARGA_DISTANCIA", "INTELCIA"

    # Cliente
    cliente_b2c_nombre: str = ""
    cliente_b2c_whatsapp: str = ""
    empresa_b2b_nombre: str = ""
    empleados_lista: list[dict] = field(default_factory=list)  # B2B

    # Operacional
    ruta_intelcia_id: Optional[int] = None
    es_inmediato: bool = False
    es_vip: bool = False
    forma_pago_inicial: str = ""        # CREDITO_B2B, PENDIENTE, etc.

    # Metadata
    fuente_raw: str = ""                # archivo o mensaje original (para auditoría)
    notas: str = ""

    def como_record_airtable(self) -> dict:
        """Convierte a campos listos para crear el registro en Airtable."""
        return {
            "Fecha": self.fecha,
            "Hora_salida": self.hora_salida,
            "Canal": self.canal,
            "Modo_servicio": "Inmediato" if self.es_inmediato else "Programado",
            "Origen_texto": self.origen,
            "Destino_texto": self.destino,
            "Cantidad_pax": self.pasajeros,
            "Tarifa_aplicada_RD": self.tarifa_rd,
            "Modo_calculo_precio": self.modo_calculo,
            "Estado": "PENDIENTE",
            "Estado_pago": self.forma_pago_inicial or "PENDIENTE",
            "Pasajeros_lista": self._render_pax_lista(),
            "Notas": self.notas,
            "Creado_en": datetime.now().isoformat(timespec="seconds"),
        }

    def _render_pax_lista(self) -> str:
        if self.empleados_lista:
            return "\n".join(
                f"{e.get('z_id', '')} — {e.get('nombre_validado', '')}"
                for e in self.empleados_lista
            )
        if self.cliente_b2c_nombre:
            return f"{self.cliente_b2c_nombre} ({self.cliente_b2c_whatsapp})"
        return ""


# ─────────────────────────────────────────────────────────────
# DETECTOR DE URGENCIA (modo Uber)
# ─────────────────────────────────────────────────────────────

KEYWORDS_URGENCIA = [
    "ahora", "ya", "urgente", "rápido", "rapido",
    "lo más pronto", "lo mas pronto", "asap",
    "estoy en", "me espera", "ya casi", "salgo ya",
    "right now", "now", "asap",
]

KEYWORDS_VIP = [
    "vip", "ejecutivo", "premium", "casa de campo",
    "punta cana", "cap cana", "bávaro", "bavaro",
    "embajador", "embajada", "consulado",
]


def es_solicitud_urgente(texto: str) -> bool:
    texto_lower = texto.lower()
    return any(k in texto_lower for k in KEYWORDS_URGENCIA)


def es_solicitud_vip(texto: str) -> bool:
    texto_lower = texto.lower()
    return any(k in texto_lower for k in KEYWORDS_VIP)


# ─────────────────────────────────────────────────────────────
# PROCESADOR DE EXCEL INTELCIA
# ─────────────────────────────────────────────────────────────

def procesar_excel_intelcia(filepath: str | Path) -> list[ServicioNormalizado]:
    """
    Lee el Excel de Intelcia y devuelve servicios normalizados.
    Cada Ruta-Hora del Excel se convierte en un ServicioNormalizado.
    """
    servicios_intelcia = parse_intelcia_excel(filepath)
    normalizados: list[ServicioNormalizado] = []

    for si in servicios_intelcia:
        # Extraer origen "lógico" de las zonas (para que el Despachador entienda)
        primer_empleado = si.empleados[0] if si.empleados else None
        origen_legible = (
            primer_empleado.direccion if primer_empleado else f"Ruta {si.ruta_id}"
        )

        sn = ServicioNormalizado(
            canal=Canal.INTELCIA,
            fecha=si.fecha,
            hora_salida=si.hora_servicio,
            origen=f"Ruta {si.ruta_id} — {origen_legible[:60]}",
            destino="Intelcia · Punta Arrecife",
            pasajeros=si.cantidad_pax,
            tarifa_rd=si.tarifa_aplicada_rd,
            modo_calculo="INTELCIA",
            empresa_b2b_nombre="Intelcia",
            empleados_lista=[
                {
                    "z_id": e.z_id,
                    "nombre_validado": e.nombre_validado,
                    "direccion": e.direccion,
                    "hora_salida": e.hora_salida,
                }
                for e in si.empleados
            ],
            ruta_intelcia_id=si.ruta_id,
            es_inmediato=False,
            es_vip=False,
            forma_pago_inicial="CREDITO_B2B",
            fuente_raw=str(filepath),
            notas=si.notas,
        )
        normalizados.append(sn)

    logger.info(
        f"Intelcia: {len(normalizados)} servicios normalizados desde {filepath}"
    )
    return normalizados


# ─────────────────────────────────────────────────────────────
# PROCESADOR DE TEXTO LIBRE (WhatsApp / Email VIP)
# ─────────────────────────────────────────────────────────────

@dataclass
class ResultadoExtraccionTexto:
    """Datos extraídos de un mensaje libre."""
    origen: str = ""
    destino: str = ""
    fecha: str = ""
    hora: str = ""
    pasajeros: int = 0
    nombre_cliente: str = ""
    whatsapp_cliente: str = ""
    es_multi_leg: bool = False
    legs: list[dict] = field(default_factory=list)
    confianza: float = 0.0  # 0-1


# Patrones simples para MVP. En producción esto lo hace un agente LLM.
PATRON_VUELO = re.compile(r"\b([A-Z]{2,3}\s?\d{2,4})\b")
PATRON_HORA = re.compile(r"\b(\d{1,2})[:\.]?(\d{2})?\s*(am|pm)?\b", re.IGNORECASE)
PATRON_PAX = re.compile(r"(\d+)\s+(pasajeros?|personas|adultos?|pax|somos)", re.IGNORECASE)


def extraer_de_texto_libre(texto: str) -> ResultadoExtraccionTexto:
    """
    Extracción básica de info de un mensaje libre.
    Para MVP. En Fase 2, el Agente Coordinador con Claude lo hace mejor.
    """
    r = ResultadoExtraccionTexto()
    texto_lower = texto.lower()

    # Pasajeros
    m = PATRON_PAX.search(texto)
    if m:
        try:
            r.pasajeros = int(m.group(1))
        except ValueError:
            pass

    # Detectar si es multi-leg
    legs_keywords = ["recoger", "llevar", "después", "luego", "al final", "regreso"]
    if sum(1 for k in legs_keywords if k in texto_lower) >= 2:
        r.es_multi_leg = True

    # AILA / aeropuerto detectado
    if "aila" in texto_lower or "aeropuerto" in texto_lower or "sdq" in texto_lower:
        if "del aeropuerto" in texto_lower or "desde aila" in texto_lower:
            r.origen = "AILA"
        elif "al aeropuerto" in texto_lower or "hacia aila" in texto_lower:
            r.destino = "AILA"

    r.confianza = 0.5 if r.pasajeros and (r.origen or r.destino) else 0.2
    return r


# ─────────────────────────────────────────────────────────────
# PROCESADOR DE SOLICITUD B2C (WhatsApp / Web / Llamada)
# ─────────────────────────────────────────────────────────────

def procesar_solicitud_b2c(
    origen: str,
    destino: str,
    fecha: str,
    hora: str,
    pasajeros: int,
    nombre_cliente: str,
    whatsapp_cliente: str,
    km_estimados: Optional[float] = None,
    canal: str = Canal.WHATSAPP,
    es_inmediato: bool = False,
    es_vip: bool = False,
) -> ServicioNormalizado:
    """
    Procesa una solicitud B2C de cualquier canal individual.
    Si pasas km_estimados, se usa para calcular. Si no, se infiere
    del par origen-destino (TODO: integrar Google Maps).
    """
    if km_estimados is None:
        # MVP: estimación basada en presencia de keywords.
        # Reemplazar con Google Maps en producción.
        km_estimados = _estimar_km_fallback(origen, destino)

    # Hora como int (para detectar nocturno)
    try:
        hora_int = int(hora.split(":")[0]) if ":" in hora else int(hora)
    except (ValueError, IndexError):
        hora_int = 12

    calculo = calcular_tarifa(
        km=km_estimados,
        origen=origen,
        destino=destino,
        hora=hora_int,
        pasajeros=pasajeros,
    )

    return ServicioNormalizado(
        canal=canal,
        fecha=fecha,
        hora_salida=hora,
        origen=origen,
        destino=destino,
        pasajeros=pasajeros,
        tarifa_rd=calculo.precio_final,
        modo_calculo=calculo.modo,
        cliente_b2c_nombre=nombre_cliente,
        cliente_b2c_whatsapp=whatsapp_cliente,
        es_inmediato=es_inmediato,
        es_vip=es_vip,
        forma_pago_inicial="PENDIENTE",
        fuente_raw=f"{canal}:{nombre_cliente}:{datetime.now().isoformat()}",
    )


def _estimar_km_fallback(origen: str, destino: str) -> float:
    """
    Fallback simple cuando no hay Google Maps disponible.
    Devuelve una estimación gruesa según keywords.
    """
    texto = (origen + " " + destino).lower()
    if "punta cana" in texto or "bavaro" in texto:
        return 200.0
    if "la romana" in texto or "casa de campo" in texto:
        return 110.0
    if "puerto plata" in texto or "sosua" in texto:
        return 235.0
    if "santiago" in texto:
        return 155.0
    if "samaná" in texto or "las terrenas" in texto:
        return 240.0
    if "boca chica" in texto:
        return 30.0
    if "juan dolio" in texto:
        return 45.0
    if "san pedro" in texto:
        return 60.0
    if "aila" in texto or "las américas" in texto:
        return 25.0
    return 10.0  # ciudad por defecto


# ─────────────────────────────────────────────────────────────
# RESUMEN DE FACTURACIÓN (cualquier lote)
# ─────────────────────────────────────────────────────────────

def resumen_lote(servicios: list[ServicioNormalizado]) -> dict:
    """Estadísticas de un lote de servicios normalizados."""
    if not servicios:
        return {"total": 0}

    total_facturacion = sum(s.tarifa_rd for s in servicios)
    total_pax = sum(s.pasajeros for s in servicios)
    por_canal: dict[str, int] = {}
    por_hora: dict[str, int] = {}
    for s in servicios:
        por_canal[s.canal] = por_canal.get(s.canal, 0) + 1
        por_hora[s.hora_salida] = por_hora.get(s.hora_salida, 0) + 1

    return {
        "total_servicios": len(servicios),
        "total_pasajeros": total_pax,
        "facturacion_rd": total_facturacion,
        "por_canal": por_canal,
        "por_hora": dict(sorted(por_hora.items())),
    }


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 70)
    print("EMOVILS OPC — Test de Agente Ingesta")
    print("=" * 70)

    # CASO 1: Excel Intelcia real
    print("\n📥 CASO 1: Procesando Excel Intelcia 06-jun-2026")
    print("-" * 70)
    excel_path = "/Users/noevasquez/Desktop/EMOVILS/Transporte Jun-06th -  2026.xlsx"
    servicios = procesar_excel_intelcia(excel_path)

    for s in servicios[:3]:
        print(f"  • Ruta {s.ruta_intelcia_id} · {s.hora_salida} · "
              f"{s.pasajeros} pax · RD${s.tarifa_rd:,}")
    if len(servicios) > 3:
        print(f"  ... +{len(servicios) - 3} servicios más")

    resumen = resumen_lote(servicios)
    print(f"\n📊 Total: {resumen['total_servicios']} servicios, "
          f"{resumen['total_pasajeros']} pax, RD${resumen['facturacion_rd']:,}")

    # CASO 2: Solicitud B2C inmediata
    print("\n📥 CASO 2: Solicitud B2C inmediata (WhatsApp)")
    print("-" * 70)
    s_b2c = procesar_solicitud_b2c(
        origen="Hotel El Embajador",
        destino="AILA",
        fecha="2026-06-10",
        hora="20:30",
        pasajeros=2,
        nombre_cliente="María González",
        whatsapp_cliente="+18295551234",
        canal=Canal.WHATSAPP,
        es_inmediato=True,
    )
    print(f"  Canal: {s_b2c.canal}")
    print(f"  Cliente: {s_b2c.cliente_b2c_nombre} ({s_b2c.cliente_b2c_whatsapp})")
    print(f"  Ruta: {s_b2c.origen} → {s_b2c.destino}")
    print(f"  Modo: {s_b2c.modo_calculo}")
    print(f"  Tarifa: RD${s_b2c.tarifa_rd:,}")
    print(f"  Inmediato: {s_b2c.es_inmediato}")

    # CASO 3: VIP largo (Punta Cana)
    print("\n📥 CASO 3: VIP a Punta Cana (4 pax)")
    print("-" * 70)
    s_vip = procesar_solicitud_b2c(
        origen="AILA Terminal Privada",
        destino="Cap Cana Punta Cana Resort",
        fecha="2026-06-12",
        hora="11:00",
        pasajeros=4,
        nombre_cliente="VIP Walker",
        whatsapp_cliente="+18495559999",
        canal=Canal.VIP,
        es_inmediato=False,
        es_vip=True,
    )
    print(f"  Canal: {s_vip.canal} (VIP={s_vip.es_vip})")
    print(f"  Ruta: {s_vip.origen} → {s_vip.destino}")
    print(f"  Tarifa: RD${s_vip.tarifa_rd:,}")

    # CASO 4: Servicio nocturno con grupo grande
    print("\n📥 CASO 4: Grupo 7 pax nocturno (H1 con recargos)")
    print("-" * 70)
    s_grupo = procesar_solicitud_b2c(
        origen="Bávaro Hotel",
        destino="AILA",
        fecha="2026-06-12",
        hora="23:30",
        pasajeros=7,
        nombre_cliente="Familia Ejecutiva",
        whatsapp_cliente="+18095557777",
        canal=Canal.VIP,
        es_inmediato=False,
        es_vip=True,
    )
    print(f"  Pasajeros: {s_grupo.pasajeros} (H1 con +10%)")
    print(f"  Hora: {s_grupo.hora_salida} (nocturno +20%)")
    print(f"  Tarifa: RD${s_grupo.tarifa_rd:,}")

    # CASO 5: Detección de urgencia
    print("\n📥 CASO 5: Detección de urgencia en texto")
    print("-" * 70)
    mensajes = [
        "Necesito un servicio ahora mismo del Embajador al AILA",
        "Me gustaría reservar para el viernes",
        "URGENTE, ya estoy en el aeropuerto",
        "Quisiera cotizar para Casa de Campo el próximo mes",
    ]
    for m in mensajes:
        u = "🚨 URGENTE" if es_solicitud_urgente(m) else "📅 normal"
        v = " · VIP" if es_solicitud_vip(m) else ""
        print(f"  {u}{v}: \"{m[:50]}...\"")

    # CASO 6: Convertir a registro Airtable
    print("\n📥 CASO 6: Conversión a registro Airtable")
    print("-" * 70)
    record = s_b2c.como_record_airtable()
    print(json.dumps(record, indent=2, ensure_ascii=False)[:500])

    print()
    print("=" * 70)
    print("✓ Agente Ingesta procesando todos los canales correctamente")
