"""
Emovils OPC — Voice Agent (Monserrat)
Convierte respuestas de texto a audio via OpenAI TTS y las envía por WhatsApp.
Modelo: tts-1 (más económico) | Voz: alloy (neutral) o nova (femenina)
"""
import os
import io
import logging
import requests
import tempfile
from openai import OpenAI

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL", "https://7107.api.greenapi.com")
WHATSAPP_INSTANCE_ID = os.getenv("WHATSAPP_INSTANCE_ID", "")
WHATSAPP_API_KEY_ENV = os.getenv("WHATSAPP_API_KEY", "")

# Voz de Monserrat — "nova" es femenina y cálida
MONSERRAT_VOICE = "nova"
TTS_MODEL = "tts-1"  # Más económico: $0.015/1K chars


def text_to_audio(text: str) -> bytes:
    """
    Convierte texto a audio MP3 via OpenAI TTS.
    Retorna bytes del archivo MP3.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=MONSERRAT_VOICE,
        input=text,
        response_format="mp3"
    )
    return response.content


def send_voice_message(to: str, text: str) -> dict:
    """
    Genera audio de Monserrat y lo envía como mensaje de voz por WhatsApp.
    Green API acepta MP3 directamente via sendFileByUpload.
    """
    try:
        # 1. Generar audio
        audio_bytes = text_to_audio(text)
        logger.info(f"Audio generado: {len(audio_bytes)} bytes para {to[:6]}***")

        # 2. Enviar como archivo de audio via Green API
        chat_id = f"{to}@c.us" if "@" not in to else to
        url = f"{WHATSAPP_API_URL}/waInstance{WHATSAPP_INSTANCE_ID}/sendFileByUpload/{WHATSAPP_API_KEY_ENV}"

        files = {
            "file": ("monserrat_audio.mp3", io.BytesIO(audio_bytes), "audio/mpeg")
        }
        data = {
            "chatId": chat_id,
            "caption": ""
        }

        resp = requests.post(url, files=files, data=data, timeout=30)
        resp.raise_for_status()
        logger.info(f"Mensaje de voz enviado a {to[:6]}***")
        return resp.json()

    except Exception as e:
        logger.error(f"Error enviando voz a {to[:6]}***: {e}")
        return {"error": str(e)}


def should_send_voice(message_text: str, conversation_turn: int) -> bool:
    """
    Decide si Monserrat responde con voz o texto.
    Estrategia: voz en el saludo inicial, texto para el resto (más rápido).
    Puede ajustarse según preferencia del dueño.
    """
    # Voz activada — Monserrat responde con audio en el primer saludo
    # Para desactivar: return False
    return conversation_turn == 0
