import { PrismaClient, MerchantPlan } from "@prisma/client";

const prisma = new PrismaClient();

/**
 * Seed base de Vínculo.
 *
 * Puebla la configuración mínima para operar en México:
 *  - CountryConfig MX (MXN, IVA 16 %, español).
 *  - Los 3 planes de comercio (SPEC §4.3) con su economía.
 *
 * Idempotente: usa upsert, se puede correr múltiples veces sin duplicar.
 */

// Helper: pesos → centavos (BigInt). $499 -> 49900n
const pesos = (mxn: number): bigint => BigInt(Math.round(mxn * 100));

async function main() {
  // ── País: México ──
  const mx = await prisma.countryConfig.upsert({
    where: { id: "MX" },
    update: {
      nombre: "México",
      moneda: "MXN",
      procesadorPago: "spei",
      idiomaDefault: "es",
      ivaPct: 16,
      activo: true,
    },
    create: {
      id: "MX",
      nombre: "México",
      moneda: "MXN",
      procesadorPago: "spei",
      idiomaDefault: "es",
      ivaPct: 16,
      activo: true,
    },
  });
  console.log(`✔ CountryConfig: ${mx.id} (${mx.moneda}, IVA ${mx.ivaPct}%)`);

  // ── Planes de comercio para MX (SPEC §4.3) ──
  const planes: Array<{
    plan: MerchantPlan;
    cuotaFija: bigint;
    tasaComisionBps: number;
    maxSucursales: number | null;
  }> = [
    { plan: "starter", cuotaFija: pesos(499), tasaComisionBps: 500, maxSucursales: 1 },
    { plan: "growth", cuotaFija: pesos(999), tasaComisionBps: 400, maxSucursales: 5 },
    // "scale" = negociado; cuota base 0 y tasa tope 3 %, sin límite de sucursales.
    { plan: "scale", cuotaFija: pesos(0), tasaComisionBps: 300, maxSucursales: null },
  ];

  for (const p of planes) {
    await prisma.merchantPlanConfig.upsert({
      where: { countryId_plan: { countryId: "MX", plan: p.plan } },
      update: {
        cuotaFija: p.cuotaFija,
        tasaComisionBps: p.tasaComisionBps,
        maxSucursales: p.maxSucursales,
      },
      create: {
        countryId: "MX",
        plan: p.plan,
        cuotaFija: p.cuotaFija,
        tasaComisionBps: p.tasaComisionBps,
        maxSucursales: p.maxSucursales,
      },
    });
    console.log(
      `✔ Plan ${p.plan}: cuota $${Number(p.cuotaFija) / 100}/mes, ` +
        `${p.tasaComisionBps / 100}% comisión, ` +
        `sucursales ${p.maxSucursales ?? "∞"}`
    );
  }

  console.log("Seed completado.");
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
