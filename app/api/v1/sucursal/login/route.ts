import { NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";
import { firmarSesionCaja } from "@/lib/sesion_caja";

/**
 * POST /api/v1/sucursal/login — la caja inicia sesión con el PIN (SPEC §9.2).
 *
 * PIN correcto → token de sesión de caja (corto), que autoriza a registrar
 * transacciones solo en esa sucursal.
 *
 * Body: { sucursalId, pin }
 */
export async function POST(req: Request) {
  let body: { sucursalId?: string; pin?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const { sucursalId, pin } = body;
  if (!sucursalId || !pin) {
    return NextResponse.json({ error: "sucursalId y pin son requeridos" }, { status: 400 });
  }

  const sucursal = await prisma.sucursal.findUnique({ where: { id: sucursalId } });
  if (!sucursal || !sucursal.activa) {
    return NextResponse.json({ error: "Sucursal no disponible" }, { status: 404 });
  }

  const ok = await bcrypt.compare(pin, sucursal.pinHash);
  if (!ok) {
    return NextResponse.json({ error: "PIN incorrecto" }, { status: 401 });
  }

  const token = firmarSesionCaja(sucursal.id, sucursal.merchantId);
  return NextResponse.json({
    token,
    sucursalId: sucursal.id,
    merchantId: sucursal.merchantId,
    nombre: sucursal.nombre,
  });
}
