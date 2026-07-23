import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * QR del cliente — firmado HMAC-SHA256 por país (SPEC §7).
 *
 * Formato del payload:
 *   VINCULO|v1|{pais}|{clienteId}|{qrVersion}|{expUnix}|{sig}
 *
 * - `sig` = HMAC_SHA256(secreto_pais, "{pais}|{clienteId}|{qrVersion}|{expUnix}")
 *   truncado a 16 bytes en base64url.
 * - El QR NO contiene datos personales: solo id + firma.
 * - `expUnix = 0` → sin expiración (tarjeta estática); la rotación se hace por
 *   `qrVersion` (se compara contra el valor vigente del cliente en la DB).
 */

const PREFIX = "VINCULO";
const VERSION = "v1";

/** Secreto HMAC del país, desde env `VINCULO_QR_SECRET_<PAIS>`. Uno por país. */
function secretoPais(pais: string): string {
  const key = `VINCULO_QR_SECRET_${pais.toUpperCase()}`;
  const secret = process.env[key];
  if (!secret) {
    throw new Error(`Falta el secreto HMAC del QR para ${pais} (env ${key})`);
  }
  return secret;
}

function firma(secret: string, base: string): string {
  return createHmac("sha256", secret)
    .update(base)
    .digest()
    .subarray(0, 16)
    .toString("base64url");
}

/** Genera el token firmado del QR de un cliente. */
export function firmarQr(
  pais: string,
  clienteId: string,
  qrVersion: number,
  expUnix = 0
): string {
  const base = `${pais}|${clienteId}|${qrVersion}|${expUnix}`;
  const sig = firma(secretoPais(pais), base);
  return `${PREFIX}|${VERSION}|${base}|${sig}`;
}

export interface QrValido {
  pais: string;
  clienteId: string;
  qrVersion: number;
  expUnix: number;
}

export type QrResultado =
  | { ok: true; data: QrValido }
  | { ok: false; error: string };

/**
 * Verifica firma, prefijo/versión y expiración del token.
 * NO valida que `qrVersion` sea la vigente del cliente — eso se hace en la DB
 * (comparando contra `user.qrVersion`), porque la rotación es un dato de estado.
 */
export function verificarQr(token: string): QrResultado {
  const parts = token.split("|");
  if (parts.length !== 7) return { ok: false, error: "formato" };

  const [prefix, version, pais, clienteId, qrVersionStr, expStr, sig] = parts;
  if (prefix !== PREFIX || version !== VERSION) {
    return { ok: false, error: "prefijo_o_version" };
  }

  const base = `${pais}|${clienteId}|${qrVersionStr}|${expStr}`;
  let esperado: string;
  try {
    esperado = firma(secretoPais(pais), base);
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }

  const a = Buffer.from(sig);
  const b = Buffer.from(esperado);
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    return { ok: false, error: "firma_invalida" };
  }

  const expUnix = Number(expStr);
  if (!Number.isFinite(expUnix)) return { ok: false, error: "exp_invalido" };
  if (expUnix !== 0 && Date.now() / 1000 > expUnix) {
    return { ok: false, error: "expirado" };
  }

  return {
    ok: true,
    data: { pais, clienteId, qrVersion: Number(qrVersionStr), expUnix },
  };
}

/** Nombre parcial para confirmar identidad en caja, sin exponer PII completa. */
export function nombreParcial(nombre: string | null | undefined): string {
  if (!nombre) return "Cliente";
  const partes = nombre.trim().split(/\s+/);
  if (partes.length === 1) return partes[0];
  return `${partes[0]} ${partes[1][0].toUpperCase()}.`;
}
