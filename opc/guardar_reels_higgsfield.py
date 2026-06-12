"""
Guarda los 10 Reels del playbook en Airtable como cola lista
para generar con Higgsfield cuando los créditos estén disponibles.
"""
from __future__ import annotations
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cargar_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


_cargar_env()
sys.path.insert(0, str(ROOT))

from opc.airtable_api_opc import AirtableOPC
from opc.agente_social import PLAYBOOK_REELS, generar_calendario_reels


def main() -> None:
    api = AirtableOPC()
    cal = generar_calendario_reels(date(2026, 6, 15))

    print(f"Guardando {len(cal)} Reels en cola Airtable...")
    creados = 0

    for reel in cal:
        try:
            shots_texto = "\n".join(f"  · {s}" for s in reel.shots)
            cuerpo = (
                f"📹 GUIÓN ({reel.duracion}s · 9:16):\n{shots_texto}\n\n"
                f"🎵 MÚSICA: {reel.musica}\n\n"
                f"📝 CAPTION:\n{reel.caption}\n\n"
                f"🏷️ HASHTAGS: {reel.hashtags}\n\n"
                f"📲 CTA: {reel.cta}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 PROMPT HIGGSFIELD (text-to-video, marketing_studio_video):\n\n"
                f"{reel.prompt_higgsfield}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ Estado: PENDIENTE_GENERAR_VIDEO\n"
                f"💰 Costo estimado: ~50 créditos (~$2.50 con plan PLUS)\n"
                f"🎬 Plataformas: Instagram Reels · TikTok · Facebook Reels"
            )
            api.crear_registro("Email_Campañas", {
                "Campaña": "Instagram_Reels_UGC_Higgsfield",
                "Asunto": f"REEL · {reel.tema}",
                "Cuerpo": cuerpo,
                "Fecha_envio": reel.fecha_programada.replace(" ", "T") + ":00",
                "Estado": "PROGRAMADO",
                "Plataforma": "Manual",
            })
            creados += 1
            print(f"  ✓ {reel.tema} · {reel.fecha_programada}")
        except Exception as e:
            print(f"  ❌ Error con {reel.tema}: {e}")

    print(f"\n📊 Total guardados: {creados}/{len(cal)}")
    print(f"🔗 Ver en Airtable: https://airtable.com/{api.base_id}")
    print(f"\n💡 Cuando tengas créditos en Higgsfield (plan PLUS $49):")
    print(f"   → Yo recorro la cola y genero los 10 videos automático")
    print(f"   → Tiempo estimado: 30-45 minutos para los 10")


if __name__ == "__main__":
    main()
