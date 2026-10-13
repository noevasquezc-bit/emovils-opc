/**
 * `JSON`/`NextResponse.json` no saben serializar `bigint`.
 * Los montos se guardan en centavos como `bigint`; para las respuestas JSON
 * los convertimos a `number` (los centavos de importes reales caben de sobra
 * dentro del entero seguro de JS).
 */
export const n = (b: bigint): number => Number(b);
