import { NextResponse } from "next/server";
import { randomInt } from "node:crypto";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";

/**
 * POST /api/v1/clientes/registro — inicia registro del cliente (SPEC §5.1).
 *
 * Recibe un destino (email o teléfono) + país, genera un OTP de 6 dígitos,
 * lo guarda hasheado con vencimiento y lo "envía".
 *
 * Nota: el envío real (SMS/WhatsApp/email) se integra después; en no-producción
 * el código se devuelve en `devCodigo` para poder probar el flujo.
 *
 * Body: { destino, pais, canal? }
 */
const OTP_TTL_MIN = 10;

export async function POST(req: Request) {
  let body: { destino?: string; pais?: string; canal?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const destino = body.destino?.trim();
  const pais = body.pais?.trim();
  if (!destino || !pais) {
    return NextResponse.json({ error: "destino y pais son requeridos" }, { status: 400 });
  }

  const country = await prisma.countryConfig.findUnique({ where: { id: pais } });
  if (!country || !country.activo) {
    return NextResponse.json({ error: `País ${pais} no disponible` }, { status: 400 });
  }

  const esEmail = destino.includes("@");
  const canal = body.canal ?? (esEmail ? "email" : "sms");

  const codigo = String(randomInt(0, 1_000_000)).padStart(6, "0");
  const codigoHash = await bcrypt.hash(codigo, 10);
  const expiresAt = new Date(Date.now() + OTP_TTL_MIN * 60_000);

  await prisma.otpChallenge.create({
    data: { destino, canal, pais, codigoHash, expiresAt },
  });

  // TODO: enviar `codigo` por `canal` (Green API / SMS / email).
  const res: Record<string, unknown> = {
    ok: true,
    destino,
    canal,
    expiraEnMin: OTP_TTL_MIN,
  };
  if (process.env.NODE_ENV !== "production") {
    res.devCodigo = codigo; // solo dev: facilita probar sin proveedor de envío
  }
  return NextResponse.json(res, { status: 201 });
}
