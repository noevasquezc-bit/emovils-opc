import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { verificarQr, nombreParcial } from "@/lib/qr";
import { tasaDescuentoDeTier, type Tier } from "@/lib/money";

/**
 * POST /api/v1/qr/validar — la caja escanea el QR del cliente (SPEC §7.2/§9.2).
 *
 * Verifica firma y expiración, confirma que la versión del QR sea la vigente
 * (rotación) y que el cliente esté activo. Devuelve SOLO lo necesario para la
 * caja: nombre parcial, plan y tasa de descuento. Nada sensible.
 *
 * Body: { qrToken }
 */
export async function POST(req: Request) {
  let body: { qrToken?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const qrToken = body.qrToken?.trim();
  if (!qrToken) {
    return NextResponse.json({ error: "qrToken es requerido" }, { status: 400 });
  }

  const r = verificarQr(qrToken);
  if (!r.ok) {
    return NextResponse.json({ valido: false, error: r.error }, { status: 400 });
  }

  const cliente = await prisma.user.findUnique({
    where: { id: r.data.clienteId },
    include: { membership: true },
  });
  if (!cliente || cliente.role !== "cliente") {
    return NextResponse.json({ valido: false, error: "cliente_no_encontrado" }, { status: 404 });
  }
  if (cliente.estado !== "activo") {
    return NextResponse.json({ valido: false, error: "cliente_suspendido" }, { status: 403 });
  }
  // Rotación: la versión del QR debe ser la vigente del cliente.
  if (r.data.qrVersion !== cliente.qrVersion) {
    return NextResponse.json(
      { valido: false, error: "qr_caducado" },
      { status: 401 }
    );
  }

  const tier: Tier = cliente.membership?.tier ?? "free";

  return NextResponse.json({
    valido: true,
    clienteId: cliente.id,
    nombre: nombreParcial(cliente.nombre),
    plan: tier,
    tasaDescuentoBps: tasaDescuentoDeTier(tier),
  });
}
