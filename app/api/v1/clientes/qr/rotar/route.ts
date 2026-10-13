import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { firmarQr } from "@/lib/qr";

/**
 * POST /api/v1/clientes/qr/rotar — regenera el QR del cliente (SPEC §7.3).
 *
 * Incrementa `qrVersion` (invalida los QR anteriores) y re-firma. Útil ante
 * pérdida del teléfono o sospecha de fraude.
 *
 * Body: { clienteId }
 * Nota: en producción se protege con sesión del cliente; por ahora recibe el id.
 */
export async function POST(req: Request) {
  let body: { clienteId?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const clienteId = body.clienteId?.trim();
  if (!clienteId) {
    return NextResponse.json({ error: "clienteId es requerido" }, { status: 400 });
  }

  const cliente = await prisma.user.findUnique({ where: { id: clienteId } });
  if (!cliente || cliente.role !== "cliente") {
    return NextResponse.json({ error: "Cliente no encontrado" }, { status: 404 });
  }
  if (!cliente.countryId) {
    return NextResponse.json({ error: "Cliente sin país configurado" }, { status: 409 });
  }

  const nuevaVersion = cliente.qrVersion + 1;
  const qrToken = firmarQr(cliente.countryId, cliente.id, nuevaVersion);

  await prisma.user.update({
    where: { id: cliente.id },
    data: { qrVersion: nuevaVersion, qrToken },
  });

  return NextResponse.json({ clienteId: cliente.id, qrVersion: nuevaVersion, qrToken });
}
