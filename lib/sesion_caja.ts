import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * Token de sesión de caja (SPEC §9.2) — corto, firmado con HMAC-SHA256.
 *
 * No es una sesión de usuario (NextAuth es para staff/admin). La caja se
 * autentica con el PIN de su sucursal y recibe este token, que acota lo que
 * puede hacer a SU sucursal (least-privilege, SPEC §10).
 *
 * Formato: base64url(JSON payload) + "." + firma
 */

const TTL_SEGUNDOS = 8 * 60 * 60; // 8 h (turno)

function secreto(): string {
  const s = process.env.AUTH_SECRET;
  if (!s) throw new Error("Falta AUTH_SECRET para firmar la sesión de caja");
  return s;
}

function firma(secret: string, cuerpo: string): string {
  return createHmac("sha256", secret).update(cuerpo).digest("base64url");
}

export interface SesionCaja {
  sucursalId: string;
  merchantId: string;
  exp: number; // unix seconds
}

/** Firma un token de sesión de caja con vencimiento corto. */
export function firmarSesionCaja(
  sucursalId: string,
  merchantId: string,
  ttlSegundos = TTL_SEGUNDOS
): string {
  const payload: SesionCaja = {
    sucursalId,
    merchantId,
    exp: Math.floor(Date.now() / 1000) + ttlSegundos,
  };
  const cuerpo = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `${cuerpo}.${firma(secreto(), cuerpo)}`;
}

export type SesionResultado =
  | { ok: true; data: SesionCaja }
  | { ok: false; error: string };

/** Verifica firma y expiración de un token de sesión de caja. */
export function verificarSesionCaja(token: string): SesionResultado {
  const partes = token.split(".");
  if (partes.length !== 2) return { ok: false, error: "formato" };
  const [cuerpo, sig] = partes;

  const esperado = firma(secreto(), cuerpo);
  const a = Buffer.from(sig);
  const b = Buffer.from(esperado);
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    return { ok: false, error: "firma_invalida" };
  }

  let data: SesionCaja;
  try {
    data = JSON.parse(Buffer.from(cuerpo, "base64url").toString("utf8"));
  } catch {
    return { ok: false, error: "payload" };
  }
  if (!data.sucursalId || !data.merchantId || typeof data.exp !== "number") {
    return { ok: false, error: "payload" };
  }
  if (Date.now() / 1000 > data.exp) return { ok: false, error: "expirada" };

  return { ok: true, data };
}

/** Extrae el token del header `Authorization: Bearer <token>`. */
export function tokenDeHeader(req: Request): string | null {
  const h = req.headers.get("authorization");
  if (!h?.startsWith("Bearer ")) return null;
  return h.slice(7).trim() || null;
}
