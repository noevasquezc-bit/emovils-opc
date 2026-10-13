import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { n } from "@/lib/serialize";

/**
 * GET /api/v1/clientes/me?clienteId= (SPEC §9.1: /clientes/me + /me/historial).
 *
 * Perfil del cliente + QR vigente + ahorro acumulado + historial reciente.
 * Nota: en producción `clienteId` sale de la sesión del cliente.
 */
export async function GET(req: Request) {
  const clienteId = new URL(req.url).searchParams.get("clienteId")?.trim();
  if (!clienteId) {
    return NextResponse.json({ error: "clienteId es requerido" }, { status: 400 });
  }

  const cliente = await prisma.user.findUnique({
    where: { id: clienteId },
    include: { membership: true },
  });
  if (!cliente || cliente.role !== "cliente") {
    return NextResponse.json({ error: "Cliente no encontrado" }, { status: 404 });
  }

  // Ahorro acumulado (histórico) = suma de descuentos aplicados.
  const agg = await prisma.transaction.aggregate({
    _sum: { montoDescuento: true },
    where: { clienteId, estado: "registrada" },
  });

  const historial = await prisma.transaction.findMany({
    where: { clienteId, estado: "registrada" },
    orderBy: { createdAt: "desc" },
    take: 20,
    include: { merchant: { select: { razonSocial: true } } },
  });

  return NextResponse.json({
    clienteId: cliente.id,
    nombre: cliente.nombre,
    plan: cliente.membership?.tier ?? "free",
    qrToken: cliente.qrToken,
    qrVersion: cliente.qrVersion,
    ahorroAcumulado: n(agg._sum.montoDescuento ?? 0n),
    historial: historial.map((t) => ({
      id: t.id,
      comercio: t.merchant.razonSocial,
      fecha: t.createdAt.toISOString(),
      montoBruto: n(t.montoBruto),
      montoDescuento: n(t.montoDescuento),
      montoCobrado: n(t.montoCobrado),
    })),
  });
}
