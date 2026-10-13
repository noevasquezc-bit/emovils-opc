import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { n } from "@/lib/serialize";
import type { MerchantPlan, CollectionMethod } from "@prisma/client";

const PLANES: MerchantPlan[] = ["starter", "growth", "scale"];
const METODOS: CollectionMethod[] = ["spei", "tarjeta", "domiciliacion"];

/**
 * POST /api/v1/admin/comercios — alta de comercio + plan (SPEC §9.4).
 *
 * Aquí es donde se captura el % de comisión NEGOCIADO con el comercio:
 *  - `tasaComisionBps` (ej. 350 = 3.5 %) y/o `cuotaFija` (centavos) son opcionales.
 *  - Si se envían, mandan sobre el default del plan; si no, se usa el del plan.
 *
 * Body: { razonSocial, countryId, plan?, rfc?, diaCorte?, metodoCobro?,
 *         tasaComisionBps?, cuotaFija? }
 */
export async function POST(req: Request) {
  let body: {
    razonSocial?: string;
    countryId?: string;
    plan?: MerchantPlan;
    rfc?: string;
    diaCorte?: number;
    metodoCobro?: CollectionMethod;
    tasaComisionBps?: number;
    cuotaFija?: number;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const { razonSocial, countryId } = body;
  if (!razonSocial || !countryId) {
    return NextResponse.json(
      { error: "razonSocial y countryId son requeridos" },
      { status: 400 }
    );
  }

  const country = await prisma.countryConfig.findUnique({ where: { id: countryId } });
  if (!country) {
    return NextResponse.json({ error: `País ${countryId} no configurado` }, { status: 400 });
  }

  const plan: MerchantPlan = body.plan ?? "starter";
  if (!PLANES.includes(plan)) {
    return NextResponse.json({ error: `plan inválido: ${plan}` }, { status: 400 });
  }
  const metodoCobro: CollectionMethod = body.metodoCobro ?? "spei";
  if (!METODOS.includes(metodoCobro)) {
    return NextResponse.json({ error: `metodoCobro inválido: ${metodoCobro}` }, { status: 400 });
  }

  const diaCorte = body.diaCorte ?? 1;
  if (!Number.isInteger(diaCorte) || diaCorte < 1 || diaCorte > 28) {
    return NextResponse.json({ error: "diaCorte debe ser un entero 1–28" }, { status: 400 });
  }

  // Términos negociados (opcionales).
  let tasaComisionBps: number | null = null;
  if (body.tasaComisionBps !== undefined) {
    if (
      !Number.isInteger(body.tasaComisionBps) ||
      body.tasaComisionBps < 0 ||
      body.tasaComisionBps > 10000
    ) {
      return NextResponse.json(
        { error: "tasaComisionBps debe ser un entero 0–10000 (bps)" },
        { status: 400 }
      );
    }
    tasaComisionBps = body.tasaComisionBps;
  }
  let cuotaFija: bigint | null = null;
  if (body.cuotaFija !== undefined) {
    if (!Number.isInteger(body.cuotaFija) || body.cuotaFija < 0) {
      return NextResponse.json(
        { error: "cuotaFija debe ser un entero de centavos ≥ 0" },
        { status: 400 }
      );
    }
    cuotaFija = BigInt(body.cuotaFija);
  }

  const merchant = await prisma.merchant.create({
    data: {
      razonSocial,
      rfc: body.rfc ?? null,
      countryId,
      plan,
      diaCorte,
      metodoCobro,
      tasaComisionBps,
      cuotaFija,
    },
  });

  return NextResponse.json(
    {
      id: merchant.id,
      razonSocial: merchant.razonSocial,
      plan: merchant.plan,
      negociado: tasaComisionBps !== null || cuotaFija !== null,
      tasaComisionBps: merchant.tasaComisionBps,
      cuotaFija: merchant.cuotaFija !== null ? n(merchant.cuotaFija) : null,
    },
    { status: 201 }
  );
}
