/**
 * Aritmética de importes de Vínculo (SPEC §8).
 *
 * Reglas invariantes:
 *  - Todo monto se maneja en CENTAVOS como `bigint` (nunca float).
 *  - `bps` = basis points (1 % = 100 bps). 10 % = 1000, 20 % = 2000.
 *  - Redondeo por truncamiento (división entera), consistente en todo el sistema.
 *
 * Funciones puras, sin dependencias — base para el motor de descuentos/comisiones.
 */

export const TASA_DESCUENTO_BPS = {
  free: 1000, // 10 %
  plus: 2000, // 20 %
} as const;

export type Tier = keyof typeof TASA_DESCUENTO_BPS;

/** Monto de descuento en caja: `bruto * tasaBps / 10000`. */
export function montoDescuento(montoBruto: bigint, tasaBps: number): bigint {
  return (montoBruto * BigInt(tasaBps)) / 10000n;
}

/** Lo que el cliente paga al comercio: `bruto - descuento`. */
export function montoCobrado(montoBruto: bigint, tasaBps: number): bigint {
  return montoBruto - montoDescuento(montoBruto, tasaBps);
}

/** Tasa de descuento (bps) según el plan del cliente. */
export function tasaDescuentoDeTier(tier: Tier): number {
  return TASA_DESCUENTO_BPS[tier];
}

/**
 * Comisión mensual al comercio (el ingreso de Vínculo):
 *   `max(cuotaFija, volumen * tasaComisionBps / 10000)`.
 */
export function comisionMensual(
  volumen: bigint,
  cuotaFija: bigint,
  tasaComisionBps: number
): bigint {
  const porTasa = (volumen * BigInt(tasaComisionBps)) / 10000n;
  return porTasa > cuotaFija ? porTasa : cuotaFija;
}

/** IVA sobre un monto, dado el porcentaje entero del país (ej. 16). */
export function iva(monto: bigint, ivaPct: number): bigint {
  return (monto * BigInt(ivaPct)) / 100n;
}

/** Total de factura de comisión: `comision + iva`. */
export function totalFactura(comision: bigint, ivaPct: number): bigint {
  return comision + iva(comision, ivaPct);
}
