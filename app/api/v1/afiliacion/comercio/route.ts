import { NextResponse } from "next/server";
import { Prisma, type MerchantPlan } from "@prisma/client";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";

/**
 * POST /api/v1/afiliacion/comercio — alta pública (self-service) de un comercio.
 *
 * Un negocio se afilia solo: crea el comercio, su cuenta de administrador
 * (email + contraseña, rol comercio_admin) y, opcionalmente, su primera
 * sucursal con PIN de caja. Todo en una transacción.
 *
 * Planes self-service: starter | growth. `scale` es negociado → contactar ventas.
 *
 * Body: {
 *   razonSocial, countryId, email, password,
 *   rfc?, telefono?, nombreContacto?, plan?,
 *   sucursal?: { nombre, pin }
 * }
 */
const PLANES_SELF_SERVICE: MerchantPlan[] = ["starter", "growth"];

export async function POST(req: Request) {
  let body: {
    razonSocial?: string;
    countryId?: string;
    email?: string;
    password?: string;
    rfc?: string;
    telefono?: string;
    nombreContacto?: string;
    plan?: MerchantPlan;
    sucursal?: { nombre?: string; pin?: string };
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON inválido" }, { status: 400 });
  }

  const razonSocial = body.razonSocial?.trim();
  const countryId = body.countryId?.trim() || "MX";
  const email = body.email?.trim().toLowerCase();
  const password = body.password;

  if (!razonSocial || !email || !password) {
    return NextResponse.json(
      { error: "razonSocial, email y password son requeridos" },
      { status: 400 }
    );
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return NextResponse.json({ error: "Email inválido" }, { status: 400 });
  }
  if (password.length < 8) {
    return NextResponse.json(
      { error: "La contraseña debe tener al menos 8 caracteres" },
      { status: 400 }
    );
  }

  const country = await prisma.countryConfig.findUnique({ where: { id: countryId } });
  if (!country || !country.activo) {
    return NextResponse.json({ error: `País ${countryId} no disponible` }, { status: 400 });
  }

  const plan: MerchantPlan = body.plan ?? "starter";
  if (!PLANES_SELF_SERVICE.includes(plan)) {
    return NextResponse.json(
      { error: "Ese plan es negociado; contacta a ventas para afiliarte al plan scale" },
      { status: 400 }
    );
  }

  // Sucursal opcional.
  let sucursalData: { nombre: string; pinHash: string } | null = null;
  if (body.sucursal?.nombre || body.sucursal?.pin) {
    const nombre = body.sucursal?.nombre?.trim();
    const pin = body.sucursal?.pin?.trim();
    if (!nombre || !pin) {
      return NextResponse.json(
        { error: "La sucursal requiere nombre y pin" },
        { status: 400 }
      );
    }
    if (!/^\d{4,6}$/.test(pin)) {
      return NextResponse.json({ error: "El PIN debe ser de 4 a 6 dígitos" }, { status: 400 });
    }
    sucursalData = { nombre, pinHash: await bcrypt.hash(pin, 10) };
  }

  // Email no debe estar en uso.
  const yaExiste = await prisma.user.findUnique({ where: { email } });
  if (yaExiste) {
    return NextResponse.json(
      { error: "Ya existe una cuenta con ese email" },
      { status: 409 }
    );
  }

  const passwordHash = await bcrypt.hash(password, 10);

  try {
    const result = await prisma.$transaction(async (tx) => {
      const merchant = await tx.merchant.create({
        data: {
          razonSocial,
          rfc: body.rfc?.trim() || null,
          countryId,
          plan,
        },
      });

      await tx.user.create({
        data: {
          role: "comercio_admin",
          email,
          telefono: body.telefono?.trim() || null,
          nombre: body.nombreContacto?.trim() || null,
          passwordHash,
          countryId,
          merchantId: merchant.id,
        },
      });

      let sucursalId: string | null = null;
      if (sucursalData) {
        const suc = await tx.sucursal.create({
          data: {
            merchantId: merchant.id,
            nombre: sucursalData.nombre,
            pinHash: sucursalData.pinHash,
          },
        });
        sucursalId = suc.id;
      }

      return { merchant, sucursalId };
    });

    return NextResponse.json(
      {
        ok: true,
        merchantId: result.merchant.id,
        razonSocial: result.merchant.razonSocial,
        plan: result.merchant.plan,
        adminEmail: email,
        sucursalId: result.sucursalId,
      },
      { status: 201 }
    );
  } catch (e) {
    if (e instanceof Prisma.PrismaClientKnownRequestError && e.code === "P2002") {
      return NextResponse.json(
        { error: "Email o teléfono ya registrado" },
        { status: 409 }
      );
    }
    throw e;
  }
}
