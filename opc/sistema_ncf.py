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

    return f"{tipo}{siguiente:08d}"


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
