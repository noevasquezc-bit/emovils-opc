import { describe, it, expect, beforeAll } from "vitest";
import { firmarQr, verificarQr, nombreParcial } from "@/lib/qr";

beforeAll(() => {
  process.env.VINCULO_QR_SECRET_MX = "secreto-de-prueba-mx";
});

describe("firmar/verificar QR", () => {
  it("roundtrip: un token firmado se verifica", () => {
    const token = firmarQr("MX", "cliente-123", 1);
    const r = verificarQr(token);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.clienteId).toBe("cliente-123");
      expect(r.data.pais).toBe("MX");
      expect(r.data.qrVersion).toBe(1);
    }
  });

  it("no contiene datos personales (solo id + firma)", () => {
    const token = firmarQr("MX", "cliente-123", 1);
    expect(token.startsWith("VINCULO|v1|MX|cliente-123|1|0|")).toBe(true);
    expect(token).not.toContain("@");
  });

  it("rechaza firma alterada", () => {
    const token = firmarQr("MX", "cliente-123", 1);
    const alterado = token.slice(0, -2) + "xy";
    const r = verificarQr(alterado);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("firma_invalida");
  });

  it("rechaza payload manipulado (otro clienteId con firma vieja)", () => {
    const token = firmarQr("MX", "cliente-123", 1);
    const parts = token.split("|");
    parts[3] = "cliente-999"; // cambia el id, firma ya no cuadra
    const r = verificarQr(parts.join("|"));
    expect(r.ok).toBe(false);
  });

  it("una nueva versión produce un token distinto", () => {
    const v1 = firmarQr("MX", "cliente-123", 1);
    const v2 = firmarQr("MX", "cliente-123", 2);
    expect(v1).not.toBe(v2);
  });

  it("rechaza token expirado", () => {
    const ayer = Math.floor(Date.now() / 1000) - 3600;
    const token = firmarQr("MX", "cliente-123", 1, ayer);
    const r = verificarQr(token);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("expirado");
  });

  it("rechaza formato inválido", () => {
    expect(verificarQr("basura").ok).toBe(false);
    expect(verificarQr("VINCULO|v1|MX|x|1|0").ok).toBe(false); // faltan campos
  });
});

describe("nombreParcial", () => {
  it("primer nombre + inicial del apellido", () => {
    expect(nombreParcial("María González")).toBe("María G.");
  });
  it("un solo nombre se devuelve tal cual", () => {
    expect(nombreParcial("Pedro")).toBe("Pedro");
  });
  it("sin nombre → genérico", () => {
    expect(nombreParcial(null)).toBe("Cliente");
  });
});
