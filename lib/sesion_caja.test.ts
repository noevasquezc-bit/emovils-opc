import { describe, it, expect, beforeAll } from "vitest";
import { firmarSesionCaja, verificarSesionCaja } from "@/lib/sesion_caja";

beforeAll(() => {
  process.env.AUTH_SECRET = "secreto-de-prueba-sesion";
});

describe("sesión de caja", () => {
  it("roundtrip: token firmado se verifica", () => {
    const token = firmarSesionCaja("suc-1", "com-1");
    const r = verificarSesionCaja(token);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.sucursalId).toBe("suc-1");
      expect(r.data.merchantId).toBe("com-1");
    }
  });

  it("rechaza firma alterada", () => {
    const token = firmarSesionCaja("suc-1", "com-1");
    const alterado = token.slice(0, -2) + "xy";
    expect(verificarSesionCaja(alterado).ok).toBe(false);
  });

  it("rechaza token expirado", () => {
    const token = firmarSesionCaja("suc-1", "com-1", -10); // ya vencido
    const r = verificarSesionCaja(token);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("expirada");
  });

  it("rechaza formato inválido", () => {
    expect(verificarSesionCaja("basura").ok).toBe(false);
  });
});
