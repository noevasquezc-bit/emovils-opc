import { comisionMensual, iva } from "@/lib/money";

/**
 * Motor de comisiones de Vínculo (SPEC §4.2/§4.3/§8).
 * Funciones puras — la lectura/escritura en DB vive en los route handlers.
 */

export interface TerminosComision {
  cuotaFija: bigint;
  tasaComisionBps: number;
}

/**
 * Resuelve los términos efectivos de comisión de un comercio.
 *
 * Los valores NEGOCIADOS con el comercio (override) mandan; si son null,
 * se cae al default del plan. Esto da el espacio para acordar un % distinto
 * por comercio en lugar de un monto siempre fijo.
 */
export function resolverTerminos(
  override: { cuotaFija: bigint | null; tasaComisionBps: number | null },
  plan: { cuotaFija: bigint; tasaComisionBps: number }
): TerminosComision {
  return {
    cuotaFija: override.cuotaFija ?? plan.cuotaFija,
    tasaComisionBps: override.tasaComisionBps ?? plan.tasaComisionBps,
  };
}

export interface ResultadoComision {
  volumen: bigint;
  cuotaFija: bigint;
  tasaComisionBps: number;
  comision: bigint;
  iva: bigint;
  total: bigint;
}

/**
 * Comisión de un periodo dado el volumen transaccionado, los términos
 * (negociados o del plan) y el IVA del país.
 *   comision = max(cuotaFija, volumen * tasa / 10000)
 *   total    = comision + IVA
 */
export function calcularComision(
  volumen: bigint,
  terminos: TerminosComision,
  ivaPct: number
): ResultadoComision {
  const comision = comisionMensual(volumen, terminos.cuotaFija, terminos.tasaComisionBps);
  const impuesto = iva(comision, ivaPct);
  return {
    volumen,
    cuotaFija: terminos.cuotaFija,
    tasaComisionBps: terminos.tasaComisionBps,
    comision,
    iva: impuesto,
    total: comision + impuesto,
  };
}

/**
 * Rango [desde, hasta) en UTC de un periodo "YYYY-MM".
 * `hasta` es exclusivo (primer instante del mes siguiente).
 */
export function rangoPeriodo(periodo: string): { desde: Date; hasta: Date } {
  const match = /^(\d{4})-(\d{2})$/.exec(periodo);
  if (!match) {
    throw new Error(`Periodo inválido: "${periodo}" (formato esperado YYYY-MM)`);
  }
  const anio = Number(match[1]);
  const mes = Number(match[2]); // 1–12
  if (mes < 1 || mes > 12) {
    throw new Error(`Mes inválido en periodo: "${periodo}"`);
  }
  const desde = new Date(Date.UTC(anio, mes - 1, 1));
  const hasta = new Date(Date.UTC(anio, mes, 1));
  return { desde, hasta };
}
