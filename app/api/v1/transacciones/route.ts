import { NextResponse } from "next/server";
import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/prisma";
import { montoDescuento, montoCobrado, tasaDescuentoDeTier, type Tier } from "@/lib/money";
import { verificarQr } from "@/lib/qr";
import { verificarSesionCaja, tokenDeHeader } from "@/lib/sesion_caja";
import { n } from "@/lib/serialize";

/**
 * POST /api/v1/transacciones — registra un consumo con descuento (SPEC §5.2/§9.2).
 *
 * NO es un cobro de Vínculo: el cliente paga directo al comercio. Aquí solo se
 * registra el consumo (para calcular después la comisión) y se aplica el
 * descuento 10 %/20 % según el plan del cliente.
 *
 * Idempotente vía `idempotencyKey`: un doble envío no duplica la transacción.
 *
 * Body: { merchantId, montoBruto (centavos), idempotencyKey,
 *         qrToken? | clienteId? }
 *
 * El cliente se identifica escaneando su QR (`qrToken`, recomendado) o pasando
 * `clienteId` directo. Si se usa `qrToken`, se valida firma y versión vigente.
 *
 * Autorización: si viene una sesión de caja (`Authorization: Bearer <token>`),
 * el comercio y la sucursal salen de la sesión (la cajera solo registra en SU
 * sucursal). Sin sesión, se usan `merchantId`/`sucursalId` del body (admin).
 */
export async function POST(req: Request) {
  let body: {
    merchantId?: string;
    sucursalId?: string;
    clienteId?: string;
    qrToken?: string;
    montoBruto?: number;
    idempotencyKey?: string;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const { montoBruto, idempotencyKey, qrToken } = body;
  let clienteId = body.clienteId;

  // Sesión de caja (opcional): si viene, acota comercio/sucursal.
  let merchantId = body.merchantId;
  let sucursalId: string | null = body.sucursalId ?? null;
  const tokenCaja = tokenDeHeader(req);
  if (tokenCaja) {
    const s = verificarSesionCaja(tokenCaja);
    if (!s.ok) {
      return NextResponse.json(
        { error: `Sesión de caja inválida: ${s.error}` },
        { status: 401 }
      );
    }
    merchantId = s.data.merchantId; // la sesión manda (least-privilege)
    sucursalId = s.data.sucursalId;
  }

  if (!merchantId || !idempotencyKey || (!clienteId && !qrToken)) {
    return NextResponse.json(
      { error: "merchantId, idempotencyKey y (qrToken o clienteId) son requeridos" },
      { status: 400 }
    );
  }
  if (
    typeof montoBruto !== "number" ||
    !Number.isInteger(montoBruto) ||
    montoBruto <= 0
  ) {
    return NextResponse.json(
      { error: "montoBruto debe ser un entero de centavos > 0" },
      { status: 400 }
    );
  }

  // Idempotencia: si ya existe, devolver el registro existente sin duplicar.
  const existente = await prisma.transaction.findUnique({
    where: { idempotencyKey },
  });
  if (existente) {
    return NextResponse.json(respuesta(existente), { status: 200 });
  }

  // Si viene qrToken, resuelve el clienteId validando firma y expiración.
  let qrTokenVersion: number | null = null;
  if (!clienteId && qrToken) {
    const r = verificarQr(qrToken);
    if (!r.ok) {
      return NextResponse.json({ error: `QR inválido: ${r.error}` }, { status: 400 });
    }
    clienteId = r.data.clienteId;
    qrTokenVersion = r.data.qrVersion;
  }
  if (!clienteId) {
    return NextResponse.json({ error: "No se pudo identificar al cliente" }, { status: 400 });
  }

  const merchant = await prisma.merchant.findUnique({
    where: { id: merchantId },
    include: { country: true },
  });
  if (!merchant) {
    return NextResponse.json({ error: "Comercio no encontrado" }, { status: 404 });
  }

  // Si hay sucursal, debe pertenecer a este comercio.
  if (sucursalId) {
    const suc = await prisma.sucursal.findUnique({ where: { id: sucursalId } });
    if (!suc || suc.merchantId !== merchant.id) {
      return NextResponse.json(
        { error: "La sucursal no pertenece al comercio" },
        { status: 400 }
      );
    }
  }

  const cliente = await prisma.user.findUnique({
    where: { id: clienteId },
    include: { membership: true },
  });
  if (!cliente || cliente.role !== "cliente") {
    return NextResponse.json({ error: "Cliente no encontrado" }, { status: 404 });
  }
  // Rotación: si se identificó por QR, la versión debe ser la vigente.
  if (qrTokenVersion !== null && qrTokenVersion !== cliente.qrVersion) {
    return NextResponse.json(
      { error: "QR caducado; el cliente debe regenerarlo" },
      { status: 401 }
    );
  }

  const tier: Tier = cliente.membership?.tier ?? "free";
  const tasaDescuentoBps = tasaDescuentoDeTier(tier);
  const bruto = BigInt(montoBruto);
  const descuento = montoDescuento(bruto, tasaDescuentoBps);
  const cobrado = montoCobrado(bruto, tasaDescuentoBps);

  try {
    const tx = await prisma.transaction.create({
      data: {
        countryId: merchant.countryId,
        clienteId: cliente.id,
        merchantId: merchant.id,
        sucursalId,
        montoBruto: bruto,
        tierAplicado: tier,
        tasaDescuentoBps,
        montoDescuento: descuento,
        montoCobrado: cobrado,
        idempotencyKey,
      },
    });
    return NextResponse.json(
      { ...respuesta(tx), moneda: merchant.country.moneda, cliente: cliente.nombre },
      { status: 201 }
    );
  } catch (e) {
    // Carrera: otro request creó la misma idempotencyKey entre el check y el create.
    if (e instanceof Prisma.PrismaClientKnownRequestError && e.code === "P2002") {
      const ya = await prisma.transaction.findUnique({ where: { idempotencyKey } });
      if (ya) return NextResponse.json(respuesta(ya), { status: 200 });
    }
    throw e;
  }
}

function respuesta(tx: {
  id: string;
  montoBruto: bigint;
  tierAplicado: string;
  tasaDescuentoBps: number;
  montoDescuento: bigint;
  montoCobrado: bigint;
}) {
  return {
    transaccionId: tx.id,
    plan: tx.tierAplicado,
    montoBruto: n(tx.montoBruto),
    tasaDescuentoBps: tx.tasaDescuentoBps,
    montoDescuento: n(tx.montoDescuento),
    montoCobrado: n(tx.montoCobrado),
  };
}
