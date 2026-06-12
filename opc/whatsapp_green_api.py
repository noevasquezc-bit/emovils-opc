"""
Emovils OPC — Cliente Green API WhatsApp

Envía mensajes de texto, archivos (PDFs, imágenes, QRs) y audio
desde el sistema OPC al WhatsApp de cliente o conductor.

Documentación Green API: https://green-api.com/docs/api/
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class GreenAPIError(Exception):
    pass


class GreenAPIClient:
    """Cliente liviano de Green API para WhatsApp Business."""

    def __init__(
        self,
        api_url: str | None = None,
        token: str | None = None,
        instance_id: str | None = None,
    ):
        # Soporta tanto GREEN_API_* como WHATSAPP_API_* (alias) para
        # compatibilidad con .env actuales.
        self.api_url = (
            api_url
            or os.getenv("GREEN_API_URL")
            or os.getenv("WHATSAPP_API_URL", "")
        )
        self.token = (
            token
            or os.getenv("GREEN_API_TOKEN")
            or os.getenv("WHATSAPP_API_KEY", "")
        )
        self.instance_id = (
            instance_id
            or os.getenv("GREEN_API_INSTANCE_ID")
            or os.getenv("WHATSAPP_PHONE_ID")
            or "7107644324"
        )

        # Construir base URL con la instancia
        if self.api_url:
            # Si ya viene con la instancia, OK. Si no, agregamos.
            self.base_url = self.api_url.rstrip("/")
            if f"waInstance{self.instance_id}" not in self.base_url:
                self.base_url = f"{self.base_url}/waInstance{self.instance_id}"
        else:
            self.base_url = ""

    # ─────────────────────────────────────────────────────────
    # ENVIAR TEXTO
    # ─────────────────────────────────────────────────────────

    def enviar_texto(
        self,
        whatsapp: str,
        mensaje: str,
        ttl: int | None = None,
    ) -> dict:
        """
        Envía un mensaje de texto.

        Args:
            whatsapp: Número con código de país sin +, ej: '18295551234'
            mensaje: Texto del mensaje
            ttl: TTL del mensaje en segundos (opcional)
        """
        if not self._is_configured():
            return self._mock_response("texto", whatsapp, mensaje)

        chat_id = self._format_chat_id(whatsapp)
        body = {"chatId": chat_id, "message": mensaje}
        if ttl:
            body["quotedMessageId"] = None

        return self._post("sendMessage", body)

    # ─────────────────────────────────────────────────────────
    # ENVIAR ARCHIVO (QR, PDF, IMAGEN)
    # ─────────────────────────────────────────────────────────

    def enviar_archivo(
        self,
        whatsapp: str,
        path_archivo: str | Path,
        caption: str = "",
    ) -> dict:
        """Envía un archivo (sube vía URL o upload local)."""
        if not self._is_configured():
            return self._mock_response("archivo", whatsapp, str(path_archivo))

        chat_id = self._format_chat_id(whatsapp)
        path = Path(path_archivo)
        if not path.exists():
            raise GreenAPIError(f"Archivo no existe: {path}")

        # Green API: sendFileByUpload — multipart
        url = f"{self.base_url}/sendFileByUpload/{self.token}"
        with open(path, "rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            data = {"chatId": chat_id, "caption": caption}
            r = requests.post(url, files=files, data=data, timeout=30)

        if not r.ok:
            raise GreenAPIError(f"sendFileByUpload {r.status_code}: {r.text[:200]}")
        return r.json()

    # ─────────────────────────────────────────────────────────
    # ENVIAR AUDIO (voz de Monserrat)
    # ─────────────────────────────────────────────────────────

    def enviar_audio(self, whatsapp: str, path_mp3: str | Path) -> dict:
        """Envía un audio MP3 (típicamente generado por ElevenLabs)."""
        return self.enviar_archivo(whatsapp, path_mp3, caption="")

    # ─────────────────────────────────────────────────────────
    # ESTADO DE LA INSTANCIA
    # ─────────────────────────────────────────────────────────

    def estado_instancia(self) -> dict:
        """Verifica si la instancia de Green API está autorizada y activa."""
        if not self._is_configured():
            return {"status": "no_configured", "message": "GREEN_API_TOKEN o URL no configurados"}
        return self._get("getStateInstance")

    # ─────────────────────────────────────────────────────────
    # HELPERS INTERNOS
    # ─────────────────────────────────────────────────────────

    def _is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _format_chat_id(self, numero: str) -> str:
        """Limpia el número y le agrega @c.us para Green API."""
        n = numero.strip().replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        if not n.endswith("@c.us"):
            return f"{n}@c.us"
        return n

    def _post(self, accion: str, body: dict) -> dict:
        url = f"{self.base_url}/{accion}/{self.token}"
        r = requests.post(url, json=body, timeout=20)
        if not r.ok:
            raise GreenAPIError(f"{accion} {r.status_code}: {r.text[:200]}")
        return r.json()

    def _get(self, accion: str) -> dict:
        url = f"{self.base_url}/{accion}/{self.token}"
        r = requests.get(url, timeout=20)
        if not r.ok:
            raise GreenAPIError(f"{accion} {r.status_code}: {r.text[:200]}")
        return r.json()

    def _mock_response(self, tipo: str, destino: str, contenido: str) -> dict:
        """Cuando no hay credenciales, simula el envío para que el código funcione."""
        logger.warning(
            f"📲 [MOCK] WhatsApp {tipo} a {destino}: {contenido[:80]}..."
        )
        return {
            "mock": True,
            "tipo": tipo,
            "destino": destino,
            "contenido_preview": contenido[:80],
            "razon": "GREEN_API_TOKEN o URL no configurados — modo simulación",
        }


# ─────────────────────────────────────────────────────────
# HELPERS DE ALTO NIVEL
# ─────────────────────────────────────────────────────────

_default_client: GreenAPIClient | None = None


def get_client() -> GreenAPIClient:
    """Singleton para reutilizar el cliente."""
    global _default_client
    if _default_client is None:
        _default_client = GreenAPIClient()
    return _default_client


def enviar_a_cliente(whatsapp: str, mensaje: str) -> dict:
    """Atajo para enviar texto al cliente final."""
    return get_client().enviar_texto(whatsapp, mensaje)


def enviar_audio_a_cliente(whatsapp: str, texto: str) -> bool:
    """Genera audio con gTTS (voz dominicana) y lo envía como nota de voz."""
    try:
        import tempfile
        from gtts import gTTS
        # Limpiar emojis y markdown para que la voz suene natural
        import re as _re
        limpio = _re.sub(r"[*_`#>]", "", texto)
        limpio = _re.sub(r"[^\w\s\.\,\!\?¿¡:;\-\$\náéíóúÁÉÍÓÚñÑüÜ]", " ", limpio)
        limpio = _re.sub(r"\s+", " ", limpio).strip()
        if not limpio:
            return False
        # gTTS soporta español; "com.mx" da acento neutro-latino, lo más cercano a RD
        tts = gTTS(text=limpio, lang="es", tld="com.mx", slow=False)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tts.save(f.name)
            audio_path = f.name
        get_client().enviar_archivo(whatsapp, audio_path, caption="")
        return True
    except Exception as exc:
        logger.warning(f"No se pudo enviar audio: {exc}")
        return False


def enviar_qr_a_cliente(whatsapp: str, path_qr_png: str | Path, contexto_servicio: str) -> dict:
    """Atajo para enviar el QR del servicio al cliente."""
    return get_client().enviar_archivo(
        whatsapp,
        path_qr_png,
        caption=f"🔗 Tu QR del servicio: {contexto_servicio}\nAl ver llegar el vehículo, escanea su QR para confirmar identidad.",
    )


def notificar_chofer(whatsapp_chofer: str, mensaje_oferta: str) -> dict:
    """Atajo para enviar oferta de servicio al chofer."""
    return get_client().enviar_texto(whatsapp_chofer, mensaje_oferta)


# ─────────────────────────────────────────────────────────
# CLI DE PRUEBA
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    # Cargar .env
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

    print("=" * 60)
    print("EMOVILS OPC — Test Green API Client")
    print("=" * 60)

    client = GreenAPIClient()
    print(f"\nConfigurado: {client._is_configured()}")
    print(f"Base URL: {client.base_url[:60]}...")
    print(f"Instance ID: {client.instance_id}")

    # Verificar estado
    print("\n📡 Estado de la instancia:")
    estado = client.estado_instancia()
    print(f"  {estado}")

    # Envío de prueba en modo MOCK
    print("\n📤 Envío de prueba (mock si no configurado):")
    r = client.enviar_texto(
        "18295551234",
        "Prueba de Monserrat desde el sistema OPC. 🚖",
    )
    print(f"  Resultado: {r}")

    print()
    print("=" * 60)
    print("Para activar envíos reales, configurar en .env:")
    print("  GREEN_API_URL=https://7107.api.greenapi.com")
    print("  GREEN_API_TOKEN=tu_token_aqui")
    print("=" * 60)
