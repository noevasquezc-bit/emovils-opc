import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { resolverTerminos, calcularComision, rangoPeriodo } from "@/lib/comisiones";
import { n } from "@/lib/serialize";

/**
 * GET /api/v1/comercio/dashboard?merchantId=&periodo=YYYY-MM (SPEC §9.3).
 *
 * Volumen del periodo, número de transacciones, ahorro dado a clientes y la
 * comisión estimada (mismo motor que la corrida mensual, usando el % negociado
 * o el del plan). Incluye desglose por sucursal.
 *
 * Nota: en producción `merchantId` sale de la sesión de comercio_admin.
 */
function periodoActual(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const merchantId = url.searchParams.get("merchantId")?.trim();
  const periodo = url.searchParams.get("periodo")?.trim() || periodoActual();

  if (!merchantId) {
    return NextResponse.json({ error: "merchantId es requerido" }, { status: 400 });
  }

  let desde: Date, hasta: Date;
  try {
    ({ desde, hasta } = rangoPeriodo(periodo));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }

  const merchant = await prisma.merchant.findUnique({
    where: { id: merchantId },
    include: { country: true },
  });
  if (!merchant) {
    return NextResponse.json({ error: "Comercio no encontrado" }, { status: 404 });
  }

  const where = {
    merchantId,
    estado: "registrada" as const,
    createdAt: { gte: desde, lt: hasta },
  };

  const agg = await prisma.transaction.aggregate({
    _sum: { montoBruto: true, montoDescuento: true },
    _count: true,
    where,
  });
  const volumen = agg._sum.montoBruto ?? 0n;
  const ahorroClientes = agg._sum.montoDescuento ?? 0n;
  const transacciones = agg._count;

  // Comisión estimada con el motor (negociado o plan).
  const planCfg = await prisma.merchantPlanConfig.findUnique({
    where: { countryId_plan: { countryId: merchant.countryId, plan: merchant.plan } },
  });
  const planTerminos =
    planCfg != null
      ? { cuotaFija: planCfg.cuotaFija, tasaComisionBps: planCfg.tasaComisionBps }
      : merchant.cuotaFija != null && merchant.tasaComisionBps != null
        ? { cuotaFija: merchant.cuotaFija, tasaComisionBps: merchant.tasaComisionBps }
        : null;

  let comision = null;
  if (planTerminos) {
    const terminos = resolverTerminos(
      { cuotaFija: merchant.cuotaFija, tasaComisionBps: merchant.tasaComisionBps },
      planTerminos
    );
    const r = calcularComision(volumen, terminos, merchant.country.ivaPct);
    comision = {
      tasaComisionBps: r.tasaComisionBps,
      comision: n(r.comision),
      iva: n(r.iva),
      total: n(r.total),
    };
  }

  // Desglose por sucursal.
  const porSuc = await prisma.transaction.groupBy({
    by: ["sucursalId"],
    _sum: { montoBruto: true },
    _count: true,
    where,
  });
  const sucIds = porSuc.map((s) => s.sucursalId).filter((x): x is string => !!x);
  const sucs = await prisma.sucursal.findMany({ where: { id: { in: sucIds } } });
  const nombreSuc = new Map(sucs.map((s) => [s.id, s.nombre]));
  const sucursales = porSuc.map((s) => ({
    sucursal: s.sucursalId ? (nombreSuc.get(s.sucursalId) ?? "—") : "Sin sucursal",
    volumen: n(s._sum.montoBruto ?? 0n),
    transacciones: s._count,
  }));

  return NextResponse.json({
    comercio: { id: merchant.id, razonSocial: merchant.razonSocial, plan: merchant.plan },
    periodo,
    moneda: merchant.country.moneda,
    negociado: merchant.tasaComisionBps !== null || merchant.cuotaFija !== null,
    volumen: n(volumen),
    transacciones,
    ahorroClientes: n(ahorroClientes),
    comisionEstimada: comision,
    sucursales,
  });
}
