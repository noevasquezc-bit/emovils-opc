import { NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";
import { firmarQr } from "@/lib/qr";

/**
 * POST /api/v1/clientes/verificar — verifica el OTP y crea/activa el cliente (SPEC §5.1).
 *
 * Si el código es válido: crea el cliente (plan free) si no existía, emite su
 * QR firmado y lo devuelve.
 *
 * Body: { destino, codigo, pais, nombre? }
 */
const MAX_INTENTOS = 5;

export async function POST(req: Request) {
  let body: { destino?: string; codigo?: string; pais?: string; nombre?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const destino = body.destino?.trim();
  const codigo = body.codigo?.trim();
  const pais = body.pais?.trim();
  if (!destino || !codigo || !pais) {
    return NextResponse.json(
      { error: "destino, codigo y pais son requeridos" },
      { status: 400 }
    );
  }

  // Último reto vigente para ese destino.
  const reto = await prisma.otpChallenge.findFirst({
    where: { destino, pais, consumido: false, expiresAt: { gt: new Date() } },
    orderBy: { createdAt: "desc" },
  });
  if (!reto) {
    return NextResponse.json(
      { error: "No hay un código vigente; solicita uno nuevo" },
      { status: 400 }
    );
  }
  if (reto.intentos >= MAX_INTENTOS) {
    return NextResponse.json(
      { error: "Demasiados intentos; solicita un código nuevo" },
      { status: 429 }
    );
  }

  const ok = await bcrypt.compare(codigo, reto.codigoHash);
  if (!ok) {
    await prisma.otpChallenge.update({
      where: { id: reto.id },
      data: { intentos: { increment: 1 } },
    });
    return NextResponse.json({ error: "Código incorrecto" }, { status: 401 });
  }

  // Marca el reto como consumido.
  await prisma.otpChallenge.update({
    where: { id: reto.id },
    data: { consumido: true },
  });

  const esEmail = destino.includes("@");

  // Busca cliente existente por el destino usado.
  let cliente = await prisma.user.findFirst({
    where: {
      role: "cliente",
      ...(esEmail ? { email: destino } : { telefono: destino }),
    },
    include: { membership: true },
  });

  if (!cliente) {
    cliente = await prisma.user.create({
      data: {
        role: "cliente",
        countryId: pais,
        nombre: body.nombre ?? null,
        email: esEmail ? destino : null,
        telefono: esEmail ? null : destino,
        qrVersion: 1,
        membership: { create: { tier: "free", estadoPago: "pendiente" } },
      },
      include: { membership: true },
    });
  }

  // Emite (o re-emite) el QR firmado con la versión vigente.
  const qrToken = firmarQr(pais, cliente.id, cliente.qrVersion);
  await prisma.user.update({
    where: { id: cliente.id },
    data: { qrToken },
  });

  return NextResponse.json(
    {
      clienteId: cliente.id,
      nombre: cliente.nombre,
      plan: cliente.membership?.tier ?? "free",
      qrToken,
      qrVersion: cliente.qrVersion,
    },
    { status: 200 }
  );
}
