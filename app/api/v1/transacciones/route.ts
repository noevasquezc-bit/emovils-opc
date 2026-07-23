import { NextResponse } from "next/server";
import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/prisma";
import { montoDescuento, montoCobrado, tasaDescuentoDeTier, type Tier } from "@/lib/money";
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
 * Body: { merchantId, clienteId, montoBruto (centavos), idempotencyKey }
 *
 * Nota: en el próximo sprint, la caja identificará al cliente escaneando su QR
 * (`/qr/validar`); por ahora se recibe `clienteId` directo.
 */
export async function POST(req: Request) {
  let body: {
    merchantId?: string;
    clienteId?: string;
    montoBruto?: number;
    idempotencyKey?: string;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const { merchantId, clienteId, montoBruto, idempotencyKey } = body;

  if (!merchantId || !clienteId || !idempotencyKey) {
    return NextResponse.json(
      { error: "merchantId, clienteId e idempotencyKey son requeridos" },
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

  const merchant = await prisma.merchant.findUnique({
    where: { id: merchantId },
    include: { country: true },
  });
  if (!merchant) {
    return NextResponse.json({ error: "Comercio no encontrado" }, { status: 404 });
  }

  const cliente = await prisma.user.findUnique({
    where: { id: clienteId },
    include: { membership: true },
  });
  if (!cliente || cliente.role !== "cliente") {
    return NextResponse.json({ error: "Cliente no encontrado" }, { status: 404 });
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
