"""
Emovils OPC — Voz Dominicana (ElevenLabs)

Convierte texto a voz con acento dominicano usando ElevenLabs.
Cuando no hay API key, devuelve modo MOCK con fallback a gTTS (genérico).

Para activar voz dominicana real:
  1. Suscribirse a ElevenLabs ($99/mes plan Starter)
  2. Clonar una voz dominicana (subir 30 min de audio)
  3. Configurar en .env:
       ELEVENLABS_API_KEY=tu_key
       ELEVENLABS_VOICE_ID=tu_voice_id_dominicana
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv(
    "EMOVILS_VOZ_DIR",
    str(Path(__file__).resolve().parent / "audios"),
))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class VozDominicana:
    """Cliente para generar audio con ElevenLabs (voz dominicana clonada)."""

    BASE_URL = "https://api.elevenlabs.io/v1"

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
    ):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
        # Voz dominicana clonada. Solo se usa ElevenLabs si key + voice_id existen.
        self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "")

        # Modelo: el más nuevo que soporta español rico
        self.model_id = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

    def _elevenlabs_configurado(self) -> bool:
        """ElevenLabs real solo si hay API key Y voice id configurados."""
        return bool(self.api_key and self.voice_id)

    def hablar(self, texto: str, nombre_archivo: str = "audio.mp3") -> Path | None:
        """
        Convierte texto a MP3.

        Con ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID usa ElevenLabs (REST,
        sin SDK). Sin tokens — o si ElevenLabs falla — cae a gTTS.
        Devuelve el Path del archivo generado, o None si nada disponible.
        """
        if not self._elevenlabs_configurado():
            return self._modo_mock(texto, nombre_archivo)

        url = f"{self.BASE_URL}/text-to-speech/{self.voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = {
            "text": texto,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.85,
                "style": 0.4,
                "use_speaker_boost": True,
            },
        }

        try:
            r = requests.post(url, headers=headers, json=body, timeout=30)
            if not r.ok:
                logger.error(f"ElevenLabs {r.status_code}: {r.text[:200]}")
                return self._modo_mock(texto, nombre_archivo)

            output_path = OUTPUT_DIR / nombre_archivo
            output_path.write_bytes(r.content)
            logger.info(f"Audio ElevenLabs generado: {output_path}")
            return output_path
        except requests.RequestException as e:
            logger.error(f"Error ElevenLabs: {e} — fallback a gTTS")
            return self._modo_mock(texto, nombre_archivo)

    def _modo_mock(self, texto: str, nombre_archivo: str) -> Path | None:
        """Si no hay ElevenLabs configurado, intenta gTTS como fallback básico."""
        try:
            from gtts import gTTS  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "📢 [MOCK] Voz no generada — sin ElevenLabs ni gTTS. "
                f"Texto: '{texto[:60]}...'"
            )
            return None

        try:
            tts = gTTS(text=texto, lang="es", tld="com.do", slow=False)
            output_path = OUTPUT_DIR / nombre_archivo
            tts.save(str(output_path))
            logger.info(f"Audio gTTS (genérico) generado: {output_path}")
            logger.info(
                "Para voz dominicana auténtica: configurar ELEVENLABS_API_KEY"
            )
            return output_path
        except Exception as e:
            logger.warning(f"gTTS falló: {e}")
            return None

    def estado(self) -> dict:
        """Devuelve si el cliente está configurado correctamente."""
        configurado = self._elevenlabs_configurado()
        info = {
            "elevenlabs_configurado": configurado,
            "voice_id": self.voice_id if configurado else "no_configurado",
            "modelo": self.model_id,
            "modo": "ELEVENLABS" if configurado else "MOCK (gTTS fallback)",
        }
        if not configurado:
            info["mensaje"] = (
                "Para voz dominicana auténtica: suscribirse a ElevenLabs ($99/mes), "
                "clonar una voz dominicana, y configurar ELEVENLABS_API_KEY + "
                "ELEVENLABS_VOICE_ID en .env."
            )
        return info


# ─────────────────────────────────────────────────────────
# HELPERS DE ALTO NIVEL
# ─────────────────────────────────────────────────────────

_default_voz: VozDominicana | None = None


def get_voz() -> VozDominicana:
    global _default_voz
    if _default_voz is None:
        _default_voz = VozDominicana()
    return _default_voz


def texto_a_audio(texto: str, nombre_archivo: str = "monserrat.mp3") -> Path | None:
    return get_voz().hablar(texto, nombre_archivo)


# ─────────────────────────────────────────────────────────
# FRASES PRE-FABRICADAS DE MONSERRAT
# ─────────────────────────────────────────────────────────

FRASES_MONSERRAT = {
    "saludo": "¡Hola! Soy Monserrat de Emovils. ¿En qué te puedo ayudar?",
    "cotizar": (
        "Claro que sí. Para cotizar dime: desde dónde sales, a dónde vas, "
        "cuántos son, y a qué hora. Yo te calculo al instante."
    ),
    "reserva_confirmada": (
        "Listo, tu reserva está confirmada. En breve te paso los datos "
        "del conductor y el QR de tu servicio. Tranquilo que ya casi."
    ),
    "buscar_conductor": (
        "Dame un momentito que estoy buscando al conductor más cercano. "
        "Te aviso enseguida."
    ),
    "conductor_asignado": (
        "Listo. Ya tienes conductor asignado. Te paso sus datos por aquí."
    ),
    "queja_disculpa": (
        "Lamento mucho lo que pasó. Cuéntame con detalle lo sucedido para "
        "escalarlo al dueño de la empresa. Vamos a resolver esto."
    ),
    "despedida": "Gracias por confiar en Emovils. Buen viaje.",
}


def saludo() -> Path | None:
    return texto_a_audio(FRASES_MONSERRAT["saludo"], "monserrat_saludo.mp3")


def confirmacion_reserva() -> Path | None:
    return texto_a_audio(FRASES_MONSERRAT["reserva_confirmada"], "monserrat_reserva.mp3")


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
    print("EMOVILS OPC — Test Voz Dominicana (ElevenLabs)")
    print("=" * 60)

    voz = VozDominicana()
    estado = voz.estado()
    print(f"\nEstado: {estado}")

    print("\n🎤 Generando frases de Monserrat...")
    for nombre, texto in FRASES_MONSERRAT.items():
        path = texto_a_audio(texto, f"monserrat_{nombre}.mp3")
        if path:
            print(f"  ✓ {nombre}: {path}")
        else:
            print(f"  📢 [mock] {nombre}: \"{texto[:60]}...\"")

    print()
    print("=" * 60)
    print(f"Audios en: {OUTPUT_DIR}")
    print("=" * 60)
