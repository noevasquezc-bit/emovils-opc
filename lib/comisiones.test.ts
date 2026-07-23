import { describe, it, expect } from "vitest";
import { resolverTerminos, calcularComision, rangoPeriodo } from "@/lib/comisiones";

const PLAN = { cuotaFija: 49900n, tasaComisionBps: 400 }; // growth-ish: $499, 4 %

describe("términos negociados vs plan", () => {
  it("sin override, usa el default del plan", () => {
    const t = resolverTerminos({ cuotaFija: null, tasaComisionBps: null }, PLAN);
    expect(t).toEqual({ cuotaFija: 49900n, tasaComisionBps: 400 });
  });

  it("el % negociado del comercio manda sobre el plan", () => {
    const t = resolverTerminos({ cuotaFija: null, tasaComisionBps: 350 }, PLAN);
    expect(t.tasaComisionBps).toBe(350); // 3.5 % negociado
    expect(t.cuotaFija).toBe(49900n); // cuota cae al plan
  });

  it("permite negociar también la cuota fija", () => {
    const t = resolverTerminos({ cuotaFija: 0n, tasaComisionBps: 300 }, PLAN);
    expect(t).toEqual({ cuotaFija: 0n, tasaComisionBps: 300 });
  });
});

describe("cálculo de comisión con IVA 16 %", () => {
  it("volumen $20,000 con 4 % → comisión $800 + IVA $128 = $928", () => {
    const r = calcularComision(2000000n, PLAN, 16);
    expect(r.comision).toBe(80000n);
    expect(r.iva).toBe(12800n);
    expect(r.total).toBe(92800n);
  });

  it("con % negociado 3 % el mismo volumen paga menos", () => {
    const r = calcularComision(2000000n, { cuotaFija: 0n, tasaComisionBps: 300 }, 16);
    expect(r.comision).toBe(60000n); // $600
    expect(r.total).toBe(69600n); // + IVA
  });
});

describe("rangoPeriodo", () => {
  it("YYYY-MM → [primer día, primer día del mes siguiente)", () => {
    const { desde, hasta } = rangoPeriodo("2026-07");
    expect(desde.toISOString()).toBe("2026-07-01T00:00:00.000Z");
    expect(hasta.toISOString()).toBe("2026-08-01T00:00:00.000Z");
  });

  it("diciembre cruza de año", () => {
    const { hasta } = rangoPeriodo("2026-12");
    expect(hasta.toISOString()).toBe("2027-01-01T00:00:00.000Z");
  });

  it("rechaza formato inválido", () => {
    expect(() => rangoPeriodo("julio-2026")).toThrow();
    expect(() => rangoPeriodo("2026-13")).toThrow();
  });
});
