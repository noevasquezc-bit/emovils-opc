"""
Emovils OPC — Cliente Higgsfield AI

Genera videos UGC auténticos para Instagram Reels, TikTok y Facebook.
Estrategia "Reels-first" porque los videos UGC convierten 5-10x más
que imágenes estáticas en redes para transporte ejecutivo.

Higgsfield AI:
  • Modelo: Image-to-video y Text-to-video
  • Output: Videos de 5-15 segundos optimizados para vertical (9:16)
  • Calidad: Cinematográfica con movimiento natural
  • Costo: ~$15-60/mes según plan

API endpoints típicos:
  POST /api/v1/generations  — crear video
  GET  /api/v1/generations/{id} — estado del video
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv(
    "EMOVILS_VIDEOS_DIR",
    str(Path(__file__).resolve().parent / "videos"),
))
try:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Ruta no escribible en este entorno → fallback local junto al módulo
    OUTPUT_DIR = Path(__file__).resolve().parent / "videos"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class HiggsfieldError(Exception):
    pass


class HiggsfieldClient:
    """Cliente para generar videos UGC con Higgsfield AI."""

    BASE_URL = "https://api.higgsfield.ai/v1"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("HIGGSFIELD_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    # ─────────────────────────────────────────────────────────
    # GENERACIÓN DE VIDEO DESDE TEXTO
    # ─────────────────────────────────────────────────────────

    def generar_video_text_to_video(
        self,
        prompt: str,
        duracion_segundos: int = 10,
        aspect_ratio: str = "9:16",
        estilo: str = "ugc_authentic",
        nombre_archivo: str = "video.mp4",
    ) -> dict:
        """
        Genera un video desde texto descriptivo.

        Args:
            prompt: Descripción detallada de la escena (incluye sujetos, acciones, ambiente, cámara)
            duracion_segundos: 5, 10 o 15
            aspect_ratio: "9:16" (Reels/TikTok), "16:9" (YouTube), "1:1" (feed)
            estilo: "ugc_authentic" (UGC natural), "cinematic" (cine), "advertising" (anuncio)
            nombre_archivo: nombre del MP4 de salida
        """
        if not self.is_configured():
            return self._mock_response("text_to_video", prompt, nombre_archivo)

        body = {
            "prompt": prompt,
            "duration": duracion_segundos,
            "aspect_ratio": aspect_ratio,
            "style": estilo,
        }
        return self._iniciar_y_esperar(body, nombre_archivo)

    # ─────────────────────────────────────────────────────────
    # IMAGEN A VIDEO (animar foto)
    # ─────────────────────────────────────────────────────────

    def generar_video_image_to_video(
        self,
        image_url_o_path: str,
        movimiento_prompt: str = "",
        duracion_segundos: int = 5,
        nombre_archivo: str = "video.mp4",
    ) -> dict:
        """
        Anima una foto fija (foto de vehículo, oficina, etc).

        Args:
            image_url_o_path: URL pública o path local de la imagen
            movimiento_prompt: Cómo se debe mover ("zoom in slow", "dolly forward", etc)
            duracion_segundos: 5, 10
            nombre_archivo: salida MP4
        """
        if not self.is_configured():
            return self._mock_response("image_to_video", movimiento_prompt or image_url_o_path, nombre_archivo)

        body = {
            "image": image_url_o_path,
            "motion_prompt": movimiento_prompt,
            "duration": duracion_segundos,
        }
        return self._iniciar_y_esperar(body, nombre_archivo, endpoint="image-to-video")

    # ─────────────────────────────────────────────────────────
    # INTERNOS
    # ─────────────────────────────────────────────────────────

    def _iniciar_y_esperar(self, body: dict, nombre_archivo: str,
                            endpoint: str = "generations",
                            max_polls: int = 60,
                            poll_interval: int = 5) -> dict:
        """Crea el job y hace polling hasta que esté listo."""
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            r = requests.post(url, headers=headers, json=body, timeout=20)
            if not r.ok:
                raise HiggsfieldError(f"POST {endpoint} {r.status_code}: {r.text[:200]}")
            data = r.json()
            generation_id = data.get("id") or data.get("generation_id")
            if not generation_id:
                return {"error": "Sin ID de generación en la respuesta", "raw": data}

            # Polling
            import time
            for _ in range(max_polls):
                time.sleep(poll_interval)
                r2 = requests.get(f"{url}/{generation_id}", headers=headers, timeout=20)
                if not r2.ok:
                    continue
                estado_data = r2.json()
                estado = estado_data.get("status", "pending")
                if estado == "completed":
                    video_url = estado_data.get("video_url") or estado_data.get("url")
                    if video_url:
                        path = OUTPUT_DIR / nombre_archivo
                        video_resp = requests.get(video_url, timeout=60)
                        path.write_bytes(video_resp.content)
                        logger.info(f"Video descargado: {path}")
                        return {"path": str(path), "url": video_url, "generation_id": generation_id}
                    return estado_data
                if estado == "failed":
                    return {"error": "Generación falló", "raw": estado_data}

            return {"error": "Timeout esperando generación", "generation_id": generation_id}

        except requests.RequestException as e:
            raise HiggsfieldError(f"Network: {e}")

    def _mock_response(self, tipo: str, prompt: str, nombre_archivo: str) -> dict:
        logger.warning(
            f"🎬 [MOCK] Higgsfield {tipo} — prompt: '{prompt[:80]}...' → {nombre_archivo}"
        )
        return {
            "mock": True,
            "tipo": tipo,
            "prompt_preview": prompt[:80],
            "archivo_destino": str(OUTPUT_DIR / nombre_archivo),
            "razon": "HIGGSFIELD_API_KEY no configurado — modo simulación",
            "siguiente_paso": (
                "Para activar generación real de videos UGC: suscribirse a Higgsfield AI "
                "(~$15-60/mes) y agregar HIGGSFIELD_API_KEY en .env"
            ),
        }


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

_default_client: HiggsfieldClient | None = None


def get_client() -> HiggsfieldClient:
    global _default_client
    if _default_client is None:
        _default_client = HiggsfieldClient()
    return _default_client


# ─────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

    print("=" * 60)
    print("EMOVILS OPC — Test Higgsfield AI Client (Videos UGC)")
    print("=" * 60)

    client = HiggsfieldClient()
    print(f"\n✓ Configurado: {client.is_configured()}")
    if not client.is_configured():
        print("  → Para activar: agregar HIGGSFIELD_API_KEY en .env")
        print("  → Costo estimado: $15-60/mes según plan")

    print("\n🎬 Test text-to-video (mock):")
    r = client.generar_video_text_to_video(
        prompt=(
            "POV inside an Emovils executive van approaching Casa de Campo gate at dusk. "
            "Professional driver in uniform. Calm, luxurious feeling. UGC handheld style."
        ),
        duracion_segundos=10,
        nombre_archivo="test_casa_de_campo.mp4",
    )
    print(f"  Resultado: {r}")

    print()
    print("=" * 60)
    print(f"Videos se guardan en: {OUTPUT_DIR}")
    print("=" * 60)
