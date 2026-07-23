import { describe, it, expect } from "vitest";
import {
  montoDescuento,
  montoCobrado,
  tasaDescuentoDeTier,
  comisionMensual,
  iva,
  totalFactura,
} from "@/lib/money";

describe("descuento en caja", () => {
  it("aplica 10 % al plan free", () => {
    // $250.00 = 25000 centavos
    expect(tasaDescuentoDeTier("free")).toBe(1000);
    expect(montoDescuento(25000n, 1000)).toBe(2500n);
    expect(montoCobrado(25000n, 1000)).toBe(22500n);
  });

  it("aplica 20 % al plan plus", () => {
    expect(tasaDescuentoDeTier("plus")).toBe(2000);
    expect(montoDescuento(25000n, 2000)).toBe(5000n);
    expect(montoCobrado(25000n, 2000)).toBe(20000n);
  });

  it("cobrado + descuento == bruto (exacto en centavos)", () => {
    const bruto = 13337n;
    expect(montoCobrado(bruto, 1000) + montoDescuento(bruto, 1000)).toBe(bruto);
  });
});

describe("comisión mensual (SPEC §4.2, tasa 4 %, piso $499)", () => {
  const CUOTA = 49900n; // $499
  const TASA = 400; // 4 %

  it("$5,000 → aplica el piso $499", () => {
    expect(comisionMensual(500000n, CUOTA, TASA)).toBe(49900n);
  });
  it("$20,000 → $800 (4 % gana al piso)", () => {
    expect(comisionMensual(2000000n, CUOTA, TASA)).toBe(80000n);
  });
  it("$50,000 → $2,000", () => {
    expect(comisionMensual(5000000n, CUOTA, TASA)).toBe(200000n);
  });
});

describe("IVA y total de factura", () => {
  it("16 % sobre la comisión", () => {
    expect(iva(80000n, 16)).toBe(12800n);
    expect(totalFactura(80000n, 16)).toBe(92800n);
  });
});
