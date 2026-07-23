import { NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";

/**
 * POST /api/v1/admin/sucursales — alta de sucursal con PIN de caja (SPEC §6.4).
 *
 * Respeta el máximo de sucursales del plan del comercio (MerchantPlanConfig);
 * null = ilimitado.
 *
 * Body: { merchantId, nombre, pin, direccion?, lat?, lng? }
 */
export async function POST(req: Request) {
  let body: {
    merchantId?: string;
    nombre?: string;
    pin?: string;
    direccion?: string;
    lat?: number;
    lng?: number;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const { merchantId, nombre, pin } = body;
  if (!merchantId || !nombre || !pin) {
    return NextResponse.json(
      { error: "merchantId, nombre y pin son requeridos" },
      { status: 400 }
    );
  }
  if (!/^\d{4,6}$/.test(pin)) {
    return NextResponse.json({ error: "pin debe ser de 4 a 6 dígitos" }, { status: 400 });
  }

  const merchant = await prisma.merchant.findUnique({ where: { id: merchantId } });
  if (!merchant) {
    return NextResponse.json({ error: "Comercio no encontrado" }, { status: 404 });
  }

  // Límite de sucursales según el plan del comercio.
  const planCfg = await prisma.merchantPlanConfig.findUnique({
    where: { countryId_plan: { countryId: merchant.countryId, plan: merchant.plan } },
  });
  if (planCfg?.maxSucursales != null) {
    const actuales = await prisma.sucursal.count({ where: { merchantId } });
    if (actuales >= planCfg.maxSucursales) {
      return NextResponse.json(
        {
          error: `El plan ${merchant.plan} permite hasta ${planCfg.maxSucursales} sucursal(es)`,
        },
        { status: 409 }
      );
    }
  }

  const pinHash = await bcrypt.hash(pin, 10);
  const sucursal = await prisma.sucursal.create({
    data: {
      merchantId,
      nombre,
      direccion: body.direccion ?? null,
      lat: body.lat ?? null,
      lng: body.lng ?? null,
      pinHash,
    },
  });

  return NextResponse.json(
    {
      id: sucursal.id,
      merchantId: sucursal.merchantId,
      nombre: sucursal.nombre,
      activa: sucursal.activa,
    },
    { status: 201 }
  );
}
