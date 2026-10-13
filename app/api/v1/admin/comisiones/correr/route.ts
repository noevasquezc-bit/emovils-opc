import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { resolverTerminos, calcularComision, rangoPeriodo } from "@/lib/comisiones";
import { n } from "@/lib/serialize";

/**
 * POST /api/v1/admin/comisiones/correr — corrida mensual de comisiones (SPEC §4.4/§9.4).
 *
 * Para cada comercio: suma el volumen transaccionado del periodo y calcula la
 * comisión con los términos NEGOCIADOS del comercio (o, si no hay, los del plan).
 * Genera/actualiza la factura de comisión (idempotente por comercio+periodo).
 *
 * Body: { periodo: "YYYY-MM", countryId?, merchantId? }
 */
export async function POST(req: Request) {
  let body: { periodo?: string; countryId?: string; merchantId?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const { periodo, countryId, merchantId } = body;
  if (!periodo) {
    return NextResponse.json({ error: "periodo (YYYY-MM) es requerido" }, { status: 400 });
  }

  let desde: Date, hasta: Date;
  try {
    ({ desde, hasta } = rangoPeriodo(periodo));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }

  const merchants = await prisma.merchant.findMany({
    where: {
      ...(merchantId ? { id: merchantId } : {}),
      ...(countryId ? { countryId } : {}),
    },
    include: { country: true },
  });

  const resultados = [];

  for (const m of merchants) {
    // Volumen del periodo = suma de montoBruto de transacciones registradas.
    const agg = await prisma.transaction.aggregate({
      _sum: { montoBruto: true },
      where: {
        merchantId: m.id,
        estado: "registrada",
        createdAt: { gte: desde, lt: hasta },
      },
    });
    const volumen = agg._sum.montoBruto ?? 0n;

    // Términos del plan (default) para este país+plan.
    const planCfg = await prisma.merchantPlanConfig.findUnique({
      where: { countryId_plan: { countryId: m.countryId, plan: m.plan } },
    });
    if (!planCfg) {
      resultados.push({
        merchantId: m.id,
        error: `Sin configuración de plan ${m.plan} para ${m.countryId}`,
      });
      continue;
    }

    // Negociados del comercio ganan sobre el default del plan.
    const terminos = resolverTerminos(
      { cuotaFija: m.cuotaFija, tasaComisionBps: m.tasaComisionBps },
      { cuotaFija: planCfg.cuotaFija, tasaComisionBps: planCfg.tasaComisionBps }
    );

    const r = calcularComision(volumen, terminos, m.country.ivaPct);

    const invoice = await prisma.commissionInvoice.upsert({
      where: { merchantId_periodo: { merchantId: m.id, periodo } },
      update: {
        montoTransaccionado: r.volumen,
        cuotaFija: r.cuotaFija,
        tasaComisionBps: r.tasaComisionBps,
        comision: r.comision,
        iva: r.iva,
        total: r.total,
      },
      create: {
        merchantId: m.id,
        periodo,
        montoTransaccionado: r.volumen,
        cuotaFija: r.cuotaFija,
        tasaComisionBps: r.tasaComisionBps,
        comision: r.comision,
        iva: r.iva,
        total: r.total,
      },
    });

    resultados.push({
      merchantId: m.id,
      razonSocial: m.razonSocial,
      invoiceId: invoice.id,
      negociado: m.tasaComisionBps !== null || m.cuotaFija !== null,
      volumen: n(r.volumen),
      tasaComisionBps: r.tasaComisionBps,
      comision: n(r.comision),
      iva: n(r.iva),
      total: n(r.total),
      moneda: m.country.moneda,
    });
  }

  return NextResponse.json({ periodo, comercios: resultados.length, resultados });
}
