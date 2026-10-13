import { NextResponse } from "next/server";

// Healthcheck (SPEC §9.4). No toca la base de datos para responder rápido.
export async function GET() {
  return NextResponse.json({
    status: "ok",
    service: "vinculo",
    ts: new Date().toISOString(),
  });
}
