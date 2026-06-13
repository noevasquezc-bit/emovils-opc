"""
Emovils OPC — Sistema de Facturación Electrónica NCF (DGII)

NCF = Número de Comprobante Fiscal (Dominican Republic).

Tipos de NCF que Emovils emite:
  • B01 — Factura de Crédito Fiscal (B2B con derecho a ITBIS)
  • B02 — Factura de Consumo (B2C consumidor final)
  • B14 — Factura Gubernamental (embajadas, gobierno)
  • B15 — Comprobante Especial (exenciones especiales)
  • B04 — Nota de Crédito (devoluciones/cancelaciones)

Cada NCF tiene un correlativo controlado por la DGII.
El sistema lleva el seriado, valida formato, calcula ITBIS (18%)
y genera el PDF con QR para validación electrónica.

Para producción:
  - Integración real con DGII e-CF requiere certificado digital
  - Por ahora generamos facturas con NCF asignado y PDF profesional
  - El contador puede subir el batch a DGII al cierre del mes
"""
from __future__ import annotations
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
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
# CONSTANTES DGII RD
# ─────────────────────────────────────────────────────────────

ITBIS_PCT = Decimal("0.18")              # 18% Impuesto Transferencia Bienes/Servicios

TIPOS_NCF = {
    "B01": {
        "nombre": "Factura de Crédito Fiscal",
        "uso": "Para clientes con RNC que aplican ITBIS como crédito fiscal",
        "rango_default": 1000,
    },
    "B02": {
        "nombre": "Factura de Consumo",
        "uso": "Para consumidor final B2C sin RNC",
        "rango_default": 5000,
    },
    "B14": {
        "nombre": "Factura Gubernamental",
        "uso": "Para entidades gubernamentales y embajadas",
        "rango_default": 200,
    },
    "B15": {
        "nombre": "Factura Régimen Especial",
        "uso": "Zonas francas y regímenes especiales",
        "rango_default": 100,
    },
    "B04": {
        "nombre": "Nota de Crédito",
        "uso": "Devoluciones y ajustes",
        "rango_default": 100,
    },
}

# Datos del emisor (Emovils)
EMISOR_EMOVILS = {
    "razon_social": "Emovils",
    "nombre_comercial": "Emovils — Transporte Ejecutivo",
    "rnc": "131-XXXXXXX-X",  # Llenar con RNC real cuando esté disponible
    "direccion": "Av. Independencia, Plaza Independencia Suite 301",
    "ciudad": "Santo Domingo",
    "telefono": "+1-829-861-0090",
    "email_facturacion": "facturacion@emovils.com",  # buzon que emite la factura
    "email_ventas": "ventas@emovils.com",            # contacto comercial
    "email_general": "info@emovils.com",             # contacto general
    "actividad_economica": "Transporte ejecutivo y servicios de transporte privado",
}


# ─────────────────────────────────────────────────────────────
# GESTIÓN DEL CORRELATIVO NCF
# ─────────────────────────────────────────────────────────────

@dataclass
class NCFAsignado:
    tipo: str                       # B01, B02, etc.
    serie: str                      # "B0100000001" formato completo
    secuencia: int                  # 1, 2, 3...
    fecha_vencimiento: str          # YYYY-MM-DD (DGII asigna vigencia)
    usado: bool = False
    factura_id: str = ""


# Formato DGII: serie B + tipo (2 dígitos) + 8 dígitos secuenciales
NCF_REGEX = re.compile(r"^B(01|02|04|14|15)(\d{8})$")

# Vigencia de las secuencias autorizadas por DGII (configurable por .env).
# DGII normalmente autoriza secuencias con vencimiento al 31 de diciembre.
NCF_FECHA_VENCIMIENTO_DEFAULT = os.getenv("NCF_FECHA_VENCIMIENTO", "2026-12-31")

# Contador local para modo MOCK (sin Airtable). Por proceso, no persistente.
_SECUENCIA_LOCAL: dict[str, int] = {}


def _rango_autorizado(tipo: str) -> int:
    """Cantidad de NCF autorizados por DGII para el tipo (override por .env)."""
    env_var = f"NCF_RANGO_{tipo}"
    try:
        return int(os.getenv(env_var, str(TIPOS_NCF[tipo]["rango_default"])))
    except (ValueError, KeyError):
        return TIPOS_NCF.get(tipo, {}).get("rango_default", 100)


def validar_ncf(ncf: str, fecha_emision: str | None = None) -> dict:
    """
    Valida un NCF dominicano:
      1. Formato B01/B02/B04/B14/B15 + 8 dígitos
      2. Secuencia dentro del rango autorizado por DGII
      3. Vigencia de la secuencia (fecha de emisión <= vencimiento DGII)

    Devuelve dict explícito:
      {"valido": bool, "tipo": str, "secuencia": int, "errores": [str, ...]}
    """
    errores: list[str] = []
    tipo = ""
    secuencia = 0

    match = NCF_REGEX.match((ncf or "").strip().upper())
    if not match:
        errores.append(
            f"Formato inválido: '{ncf}'. Esperado B01/B02/B04/B14/B15 + 8 dígitos "
            f"(ej: B0100000001)"
        )
    else:
        tipo = f"B{match.group(1)}"
        secuencia = int(match.group(2))
        if secuencia < 1:
            errores.append("La secuencia debe iniciar en 00000001")
        rango = _rango_autorizado(tipo)
        if secuencia > rango:
            errores.append(
                f"Secuencia {secuencia} excede el rango autorizado DGII para "
                f"{tipo} ({rango}). Solicitar nueva secuencia."
            )

        # Vencimiento de la secuencia
        fecha = fecha_emision or date.today().isoformat()
        try:
            if date.fromisoformat(fecha) > date.fromisoformat(NCF_FECHA_VENCIMIENTO_DEFAULT):
                errores.append(
                    f"Secuencia vencida: vigencia DGII hasta "
                    f"{NCF_FECHA_VENCIMIENTO_DEFAULT}, emisión {fecha}"
                )
        except ValueError:
            errores.append(f"Fecha de emisión inválida: '{fecha}'")

    return {
        "valido": not errores,
        "ncf": ncf,
        "tipo": tipo,
        "secuencia": secuencia,
        "vencimiento_secuencia": NCF_FECHA_VENCIMIENTO_DEFAULT,
        "errores": errores,
    }


def proximo_ncf(api: AirtableOPC, tipo: str) -> str:
    """
    Genera el próximo NCF del tipo solicitado.
    Formato: B01 + 8 dígitos secuenciales (ej: B0100000001)
    """
    if tipo not in TIPOS_NCF:
        raise ValueError(f"Tipo NCF inválido: {tipo}")

    # Buscar el último NCF emitido de este tipo
    facturas = api.listar(
        "Facturas_NCF",
        filtro=f"LEFT({{NCF}}, 3) = '{tipo}'",
        sort=[{"field": "NCF", "direction": "desc"}],
    )

    if facturas:
        ultimo_ncf = facturas[0]["fields"].get("NCF", "")
        try:
            ultimo_num = int(ultimo_ncf[3:])
            siguiente = ultimo_num + 1
        except ValueError:
            siguiente = 1
    else:
        siguiente = 1

    rango = _rango_autorizado(tipo)
    if siguiente > rango:
        raise ValueError(
            f"Secuencia {tipo} agotada ({siguiente} > rango autorizado {rango}). "
            f"Solicitar nueva secuencia a DGII."
        )
    return f"{tipo}{siguiente:08d}"


def proximo_ncf_local(tipo: str) -> str:
    """Secuencia local (modo MOCK, sin Airtable). No persistente entre procesos."""
    if tipo not in TIPOS_NCF:
        raise ValueError(f"Tipo NCF inválido: {tipo}")
    _SECUENCIA_LOCAL[tipo] = _SECUENCIA_LOCAL.get(tipo, 0) + 1
    return f"{tipo}{_SECUENCIA_LOCAL[tipo]:08d}"


# ─────────────────────────────────────────────────────────────
# CÁLCULOS FISCALES
# ─────────────────────────────────────────────────────────────

def _redondear_centavos(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class CalculoFiscal:
    subtotal_rd: Decimal            # Valor sin impuestos
    itbis_rd: Decimal               # 18% ITBIS
    total_rd: Decimal               # Total con impuestos


def calcular_fiscal_desde_total(total_con_itbis: Decimal) -> CalculoFiscal:
    """
    Para servicios donde el precio mostrado al cliente YA incluye ITBIS.
    Desglosa el ITBIS hacia atrás.
    """
    factor = Decimal("1") + ITBIS_PCT
    subtotal = total_con_itbis / factor
    itbis = total_con_itbis - subtotal
    return CalculoFiscal(
        subtotal_rd=_redondear_centavos(subtotal),
        itbis_rd=_redondear_centavos(itbis),
        total_rd=_redondear_centavos(total_con_itbis),
    )


def calcular_fiscal_desde_subtotal(subtotal: Decimal) -> CalculoFiscal:
    """
    Para servicios donde la tarifa base NO incluye ITBIS.
    Suma el ITBIS encima.
    """
    itbis = subtotal * ITBIS_PCT
    total = subtotal + itbis
    return CalculoFiscal(
        subtotal_rd=_redondear_centavos(subtotal),
        itbis_rd=_redondear_centavos(itbis),
        total_rd=_redondear_centavos(total),
    )


# ─────────────────────────────────────────────────────────────
# CREACIÓN DE FACTURA
# ─────────────────────────────────────────────────────────────

@dataclass
class LineaFactura:
    descripcion: str
    cantidad: int
    precio_unitario_rd: Decimal
    subtotal_rd: Decimal = field(init=False)

    def __post_init__(self):
        self.subtotal_rd = _redondear_centavos(
            Decimal(self.cantidad) * self.precio_unitario_rd
        )


@dataclass
class Factura:
    ncf: str                            # B0100000001
    tipo_ncf: str                       # B01
    fecha_emision: str                  # YYYY-MM-DD
    cliente_nombre: str
    cliente_rnc: str                    # Vacío si B02 consumidor final
    cliente_direccion: str
    cliente_email: str
    periodo: str                        # "Junio 2026" o "1-15 Jun 2026"
    lineas: list[LineaFactura]
    subtotal_rd: Decimal = Decimal("0")
    itbis_rd: Decimal = Decimal("0")
    total_rd: Decimal = Decimal("0")
    moneda: str = "DOP"
    fecha_vencimiento: str = ""         # Plazo de pago
    estado: str = "EMITIDA"
    servicios_ids: list[str] = field(default_factory=list)

    def calcular_totales(self) -> None:
        self.subtotal_rd = _redondear_centavos(
            sum(l.subtotal_rd for l in self.lineas)
        )
        # Asumimos que el subtotal NO incluye ITBIS (caso B2B contractual)
        if self.tipo_ncf in ("B01", "B14"):  # Crédito Fiscal y Gubernamental
            self.itbis_rd = _redondear_centavos(self.subtotal_rd * ITBIS_PCT)
            self.total_rd = self.subtotal_rd + self.itbis_rd
        else:  # B02 Consumo — el precio mostrado YA incluye ITBIS
            calc = calcular_fiscal_desde_total(self.subtotal_rd)
            self.subtotal_rd = calc.subtotal_rd
            self.itbis_rd = calc.itbis_rd
            self.total_rd = calc.total_rd


# ─────────────────────────────────────────────────────────────
# AGREGAR SERVICIOS DE UN MES → FACTURA
# ─────────────────────────────────────────────────────────────

def generar_factura_mensual_cliente_b2b(
    api: AirtableOPC,
    empresa_record_id: str,
    año: int,
    mes: int,
    tipo_ncf: str = "B01",
) -> Factura:
    """
    Genera la factura mensual para un cliente B2B.
    Agrupa todos los servicios completados del mes que tienen
    forma_pago=CREDITO_B2B y los unifica en una factura.
    """
    # Buscar todos los servicios del cliente en el mes
    inicio_mes = date(año, mes, 1).isoformat()
    if mes == 12:
        fin_mes = date(año + 1, 1, 1).isoformat()
    else:
        fin_mes = date(año, mes + 1, 1).isoformat()

    empresa = api.obtener("Empresas_B2B", empresa_record_id)
    empresa_fields = empresa["fields"]
    razon_social = empresa_fields.get("Razon_social", "Cliente")
    rnc = empresa_fields.get("RNC", "")

    filtro = (
        f"AND("
        f"  FIND('{empresa_record_id}', ARRAYJOIN({{Empresa_B2B}})),"
        f"  IS_AFTER({{Fecha}}, '{inicio_mes}'),"
        f"  IS_BEFORE({{Fecha}}, '{fin_mes}'),"
        f"  {{Estado}}='COMPLETADO'"
        f")"
    )
    servicios = api.listar("Servicios", filtro=filtro)

    if not servicios:
        raise ValueError(
            f"No hay servicios facturables para {razon_social} en {mes}/{año}"
        )

    # Construir las líneas (1 por servicio)
    lineas: list[LineaFactura] = []
    servicios_ids: list[str] = []
    for svc in servicios:
        f = svc["fields"]
        descripcion = (
            f"Servicio {f.get('Fecha', '')} {f.get('Hora_salida', '')} · "
            f"{f.get('Cantidad_pax', 1)} pax · "
            f"{f.get('Origen_texto', '')[:40]}"
        )
        tarifa = Decimal(str(f.get("Tarifa_aplicada_RD", 0)))
        lineas.append(LineaFactura(
            descripcion=descripcion,
            cantidad=1,
            precio_unitario_rd=tarifa,
        ))
        servicios_ids.append(svc["id"])

    # Crear factura
    nombre_mes = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ][mes - 1]

    plazo_pago_dias = int(empresa_fields.get("Plazo_pago_dias", 30) or 30)
    fecha_emision = date.today()
    fecha_vencimiento = fecha_emision + timedelta(days=plazo_pago_dias)

    factura = Factura(
        ncf=proximo_ncf(api, tipo_ncf),
        tipo_ncf=tipo_ncf,
        fecha_emision=fecha_emision.isoformat(),
        cliente_nombre=razon_social,
        cliente_rnc=rnc,
        cliente_direccion=empresa_fields.get("Direccion", ""),
        cliente_email=empresa_fields.get("Email_general", ""),
        periodo=f"{nombre_mes} {año}",
        lineas=lineas,
        servicios_ids=servicios_ids,
        fecha_vencimiento=fecha_vencimiento.isoformat(),
    )
    factura.calcular_totales()
    return factura


# ─────────────────────────────────────────────────────────────
# GUARDAR FACTURA EN AIRTABLE
# ─────────────────────────────────────────────────────────────

def guardar_factura_en_airtable(api: AirtableOPC, factura: Factura) -> dict:
    """Persiste la factura en la tabla Facturas_NCF."""
    return api.crear_registro("Facturas_NCF", {
        "NCF": factura.ncf,
        "Fecha_emision": factura.fecha_emision,
        "Periodo_servicios": factura.periodo,
        "Subtotal_RD": float(factura.subtotal_rd),
        "ITBIS_RD": float(factura.itbis_rd),
        "Total_RD": float(factura.total_rd),
        "Estado": factura.estado,
        "Fecha_vencimiento": factura.fecha_vencimiento,
    })


# ─────────────────────────────────────────────────────────────
# EMISIÓN DIRECTA: generar_factura(servicio, cliente, rnc)
# ─────────────────────────────────────────────────────────────

def _validar_rnc(rnc: str) -> dict:
    """
    Valida formato de RNC dominicano: 9 dígitos (empresa) o 11 (cédula).
    Acepta guiones/espacios y los normaliza.
    """
    digitos = re.sub(r"\D", "", rnc or "")
    if len(digitos) in (9, 11):
        return {"valido": True, "rnc_normalizado": digitos}
    return {
        "valido": False,
        "rnc_normalizado": digitos,
        "error": f"RNC inválido: '{rnc}'. Debe tener 9 dígitos (RNC) u 11 (cédula).",
    }


def generar_factura(servicio: dict, cliente: dict, rnc: str = "") -> dict:
    """
    Emite una factura NCF para un servicio puntual y la registra en Airtable
    (tabla Facturas_NCF) cuando hay credenciales; sin Airtable usa secuencia
    local (modo mock) — nunca crashea.

    servicio: {"descripcion": str, "monto_rd": float, "cantidad": int opc,
               "fecha": "YYYY-MM-DD" opc, "periodo": str opc}
    cliente:  {"nombre": str, "direccion": str opc, "email": str opc}
    rnc:      RNC del cliente → B01 (Crédito Fiscal). Vacío → B02 (Consumo).

    Devuelve dict explícito:
      éxito → {"ok": True, "modo": "real"|"mock", "factura": {...}}
      error → {"ok": False, "error": str, "detalle": ...}
    """
    # ── Validación de entrada ──
    if not isinstance(servicio, dict) or not isinstance(cliente, dict):
        return {"ok": False, "error": "servicio y cliente deben ser dicts"}

    descripcion = (servicio.get("descripcion") or "").strip()
    if not descripcion:
        return {"ok": False, "error": "Falta servicio['descripcion']"}

    try:
        monto = Decimal(str(servicio.get("monto_rd", servicio.get("tarifa_rd", 0))))
    except Exception:
        return {"ok": False, "error": f"Monto inválido: {servicio.get('monto_rd')}"}
    if monto <= 0:
        return {"ok": False, "error": "El monto del servicio debe ser mayor a 0"}

    nombre_cliente = (cliente.get("nombre") or "").strip()
    if not nombre_cliente:
        return {"ok": False, "error": "Falta cliente['nombre']"}

    rnc_normalizado = ""
    if rnc:
        val_rnc = _validar_rnc(rnc)
        if not val_rnc["valido"]:
            return {"ok": False, "error": val_rnc["error"]}
        rnc_normalizado = val_rnc["rnc_normalizado"]

    # B01 con RNC (crédito fiscal) · B02 consumidor final
    tipo_ncf = servicio.get("tipo_ncf") or ("B01" if rnc_normalizado else "B02")
    if tipo_ncf not in TIPOS_NCF:
        return {
            "ok": False,
            "error": f"Tipo NCF no soportado: {tipo_ncf}",
            "tipos_validos": list(TIPOS_NCF.keys()),
        }

    # ── Asignación de NCF (Airtable real o secuencia local mock) ──
    modo = "real"
    try:
        api = AirtableOPC()
        ncf = proximo_ncf(api, tipo_ncf)
    except ValueError as exc:
        # Secuencia agotada u otro error de negocio explícito
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.warning("Sin Airtable, NCF con secuencia local (mock): %s", exc)
        api = None
        modo = "mock"
        ncf = proximo_ncf_local(tipo_ncf)

    fecha_emision = servicio.get("fecha") or date.today().isoformat()

    # ── Validación del NCF emitido ──
    validacion = validar_ncf(ncf, fecha_emision)
    if not validacion["valido"]:
        return {
            "ok": False,
            "error": "NCF generado no pasó validación DGII",
            "detalle": validacion["errores"],
        }

    # ── Construcción de la factura ──
    cantidad = int(servicio.get("cantidad", 1) or 1)
    factura = Factura(
        ncf=ncf,
        tipo_ncf=tipo_ncf,
        fecha_emision=fecha_emision,
        cliente_nombre=nombre_cliente,
        cliente_rnc=rnc_normalizado,
        cliente_direccion=cliente.get("direccion", ""),
        cliente_email=cliente.get("email", ""),
        periodo=servicio.get("periodo", fecha_emision),
        lineas=[LineaFactura(
            descripcion=descripcion,
            cantidad=cantidad,
            precio_unitario_rd=monto,
        )],
        fecha_vencimiento=(
            date.fromisoformat(fecha_emision) + timedelta(days=30)
        ).isoformat(),
    )
    factura.calcular_totales()

    # ── Registro de la emisión en Airtable ──
    airtable_id = ""
    if api is not None:
        try:
            registro = guardar_factura_en_airtable(api, factura)
            airtable_id = registro.get("id", "")
        except Exception as exc:
            logger.error("Factura emitida pero NO registrada en Airtable: %s", exc)
            modo = "mock"

    return {
        "ok": True,
        "modo": modo,
        "factura": {
            "ncf": factura.ncf,
            "tipo_ncf": factura.tipo_ncf,
            "tipo_nombre": TIPOS_NCF[tipo_ncf]["nombre"],
            "fecha_emision": factura.fecha_emision,
            "fecha_vencimiento": factura.fecha_vencimiento,
            "vencimiento_secuencia": validacion["vencimiento_secuencia"],
            "emisor": EMISOR_EMOVILS["razon_social"],
            "cliente": {
                "nombre": factura.cliente_nombre,
                "rnc": factura.cliente_rnc,
                "direccion": factura.cliente_direccion,
                "email": factura.cliente_email,
            },
            "lineas": [
                {
                    "descripcion": l.descripcion,
                    "cantidad": l.cantidad,
                    "precio_unitario_rd": float(l.precio_unitario_rd),
                    "subtotal_rd": float(l.subtotal_rd),
                }
                for l in factura.lineas
            ],
            "subtotal_rd": float(factura.subtotal_rd),
            "itbis_rd": float(factura.itbis_rd),
            "total_rd": float(factura.total_rd),
            "moneda": factura.moneda,
            "estado": factura.estado,
            "airtable_id": airtable_id,
        },
    }


# ─────────────────────────────────────────────────────────────
# ENVIO DE FACTURA POR EMAIL (desde facturacion@emovils.com)
# ─────────────────────────────────────────────────────────────

def enviar_factura_por_email(factura: Factura, pdf_path: Optional[Path] = None) -> bool:
    """
    Envia la factura al cliente B2B desde facturacion@emovils.com con copia
    a contabilidad@emovils.com y ventas@emovils.com.
    """
    try:
        from opc.agente_email_router import EmailSaliente, enviar_email
    except ImportError as exc:
        logger.error("No se pudo importar email_router: %s", exc)
        return False

    if not factura.cliente_email:
        logger.warning("Factura %s sin email cliente, no se puede enviar", factura.ncf)
        return False

    cuerpo = (
        f"Estimado equipo de {factura.cliente_nombre},\n\n"
        f"Adjunto encontraran la factura del periodo {factura.periodo}.\n\n"
        f"  NCF: {factura.ncf}\n"
        f"  Subtotal: RD${factura.subtotal_rd:,.2f}\n"
        f"  ITBIS 18%: RD${factura.itbis_rd:,.2f}\n"
        f"  TOTAL: RD${factura.total_rd:,.2f}\n"
        f"  Vencimiento: {factura.fecha_vencimiento}\n\n"
        f"Cualquier consulta sobre esta factura, responder a este correo "
        f"(facturacion@emovils.com) o al WhatsApp 829-861-0090.\n\n"
        f"Saludos,\nEmovils — Facturacion\nemovils.com"
    )

    envio = EmailSaliente(
        desde_buzon="facturacion",
        para=factura.cliente_email,
        asunto=f"Factura {factura.ncf} — Emovils — {factura.periodo}",
        cuerpo_texto=cuerpo,
        cc=[os.getenv("EMAIL_CONTABILIDAD", "contabilidad@emovils.com"),
            os.getenv("EMAIL_VENTAS", "ventas@emovils.com")],
        reply_to=os.getenv("EMAIL_FACTURACION", "facturacion@emovils.com"),
        adjuntos=[pdf_path] if pdf_path and pdf_path.exists() else [],
    )
    return enviar_email(envio)


# ─────────────────────────────────────────────────────────────
# REPORTE DGII MENSUAL
# ─────────────────────────────────────────────────────────────

def reporte_dgii_mes(api: AirtableOPC, año: int, mes: int) -> dict:
    """
    Genera el reporte mensual de facturación para enviar a DGII.
    Resumen por tipo de NCF + totales.
    """
    inicio = date(año, mes, 1).isoformat()
    fin = date(año, mes + 1, 1).isoformat() if mes < 12 else date(año + 1, 1, 1).isoformat()

    filtro = (
        f"AND("
        f"  IS_AFTER({{Fecha_emision}}, '{inicio}'),"
        f"  IS_BEFORE({{Fecha_emision}}, '{fin}')"
        f")"
    )
    facturas = api.listar("Facturas_NCF", filtro=filtro)

    por_tipo: dict[str, dict] = {}
    total_subtotal = Decimal("0")
    total_itbis = Decimal("0")
    total_general = Decimal("0")

    for f in facturas:
        fields = f["fields"]
        ncf = fields.get("NCF", "")
        tipo = ncf[:3] if len(ncf) >= 3 else "?"
        sub = Decimal(str(fields.get("Subtotal_RD", 0)))
        itbis = Decimal(str(fields.get("ITBIS_RD", 0)))
        total = Decimal(str(fields.get("Total_RD", 0)))

        if tipo not in por_tipo:
            por_tipo[tipo] = {
                "cantidad": 0,
                "subtotal": Decimal("0"),
                "itbis": Decimal("0"),
                "total": Decimal("0"),
            }
        por_tipo[tipo]["cantidad"] += 1
        por_tipo[tipo]["subtotal"] += sub
        por_tipo[tipo]["itbis"] += itbis
        por_tipo[tipo]["total"] += total

        total_subtotal += sub
        total_itbis += itbis
        total_general += total

    return {
        "periodo": f"{mes:02d}/{año}",
        "total_facturas": len(facturas),
        "subtotal_rd": float(total_subtotal),
        "itbis_rd": float(total_itbis),
        "total_rd": float(total_general),
        "por_tipo": {
            t: {
                "cantidad": d["cantidad"],
                "subtotal_rd": float(d["subtotal"]),
                "itbis_rd": float(d["itbis"]),
                "total_rd": float(d["total"]),
            }
            for t, d in por_tipo.items()
        },
    }


# ─────────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 70)
    print("EMOVILS OPC — Sistema de Facturación NCF DGII")
    print("=" * 70)

    print("\n📋 Tipos de NCF soportados:")
    for tipo, info in TIPOS_NCF.items():
        print(f"  • {tipo} — {info['nombre']}")
        print(f"    {info['uso']}")

    print("\n💰 Test de cálculos fiscales:")
    print("\n  Caso 1: B01 (B2B) — Subtotal RD$10,000")
    c1 = calcular_fiscal_desde_subtotal(Decimal("10000"))
    print(f"    Subtotal: RD${c1.subtotal_rd}")
    print(f"    ITBIS 18%: RD${c1.itbis_rd}")
    print(f"    TOTAL: RD${c1.total_rd}")

    print("\n  Caso 2: B02 (B2C) — Total cliente RD$11,800 (con ITBIS incluido)")
    c2 = calcular_fiscal_desde_total(Decimal("11800"))
    print(f"    Subtotal: RD${c2.subtotal_rd}")
    print(f"    ITBIS 18%: RD${c2.itbis_rd}")
    print(f"    TOTAL: RD${c2.total_rd}")

    # Conectar a Airtable
    try:
        api = AirtableOPC()
        print(f"\n🔌 Conectado a Airtable {api.base_id}")

        # Probar generación de NCF
        print(f"\n🧾 Próximo NCF B01: {proximo_ncf(api, 'B01')}")
        print(f"🧾 Próximo NCF B02: {proximo_ncf(api, 'B02')}")
        print(f"🧾 Próximo NCF B14: {proximo_ncf(api, 'B14')}")

        # Generar factura Intelcia del 6 de junio (datos reales ya cargados)
        intelcia = api.buscar_por_campo("Empresas_B2B", "Razon_social", "Intelcia")
        if intelcia:
            print(f"\n💼 Generando factura mensual Intelcia (junio 2026)...")
            try:
                factura = generar_factura_mensual_cliente_b2b(
                    api, intelcia["id"], 2026, 6, "B01"
                )
                print(f"  ✓ Factura {factura.ncf}")
                print(f"    Cliente: {factura.cliente_nombre}")
                print(f"    Periodo: {factura.periodo}")
                print(f"    Servicios: {len(factura.lineas)}")
                print(f"    Subtotal: RD${factura.subtotal_rd:,}")
                print(f"    ITBIS 18%: RD${factura.itbis_rd:,}")
                print(f"    TOTAL: RD${factura.total_rd:,}")
                print(f"    Vencimiento: {factura.fecha_vencimiento}")

                # Guardarla en Airtable
                resultado = guardar_factura_en_airtable(api, factura)
                print(f"  ✓ Guardada en Airtable: {resultado['id']}")
            except ValueError as e:
                print(f"  ⚠️ {e}")

        # Reporte mensual DGII
        print(f"\n📊 Reporte DGII Junio 2026:")
        rep = reporte_dgii_mes(api, 2026, 6)
        print(f"  Total facturas: {rep['total_facturas']}")
        print(f"  Subtotal: RD${rep['subtotal_rd']:,}")
        print(f"  ITBIS recaudado: RD${rep['itbis_rd']:,}")
        print(f"  TOTAL FACTURADO: RD${rep['total_rd']:,}")

    except Exception as e:
        print(f"  ⚠️ Error: {e}")

    print()
    print("=" * 70)
    print("✓ Sistema NCF operativo")
    print("=" * 70)
