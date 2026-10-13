import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { n } from "@/lib/serialize";

/**
 * GET /api/v1/planes?countryId=MX — planes de comercio disponibles (del seed).
 * Público: lo usa la página de afiliación para mostrar opciones y precios.
 */
export async function GET(req: Request) {
  const countryId = new URL(req.url).searchParams.get("countryId")?.trim() || "MX";

  const planes = await prisma.merchantPlanConfig.findMany({
    where: { countryId },
    orderBy: { cuotaFija: "asc" },
  });

  return NextResponse.json({
    countryId,
    planes: planes.map((p) => ({
      plan: p.plan,
      cuotaFija: n(p.cuotaFija),
      tasaComisionBps: p.tasaComisionBps,
      maxSucursales: p.maxSucursales,
    })),
  });
}
