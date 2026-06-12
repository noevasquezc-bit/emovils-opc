"""
Emovils OPC — QR Generator via QR.io
Genera QR codes para verificación de recogida de pasajeros.
API: https://api.qr.io
"""
import requests
import logging
import os

logger = logging.getLogger(__name__)

QRIO_API_KEY = os.getenv("QRIO_API_KEY", "pyvwUlSI1QFACKbD2WXu")
QRIO_BASE_URL = "https://api.qr.io/v1"
BACKEND_URL = os.getenv("BACKEND_URL", "https://emovils-bot-v2-production.up.railway.app")


def generate_pickup_qr(booking_id: str, token: str, nombre: str, fecha: str) -> dict:
    """
    Genera un QR de recogida para el pasajero via QR.io.
    El chofer escanea este QR al llegar para verificar identidad.
    Retorna: { "qr_url": "...", "verify_url": "...", "image_url": "..." }
    """
    verify_url = f"{BACKEND_URL}/verify/{booking_id}?t={token}"

    payload = {
        "data": verify_url,
        "config": {
            "body": "square",
            "eye": "frame0",
            "eyeBall": "ball0",
            "bodyColor": "#1a1a2e",
            "bgColor": "#FFFFFF",
            "eye1Color": "#4f9cf9",
            "eye2Color": "#4f9cf9",
            "eye3Color": "#4f9cf9",
            "eyeBall1Color": "#1a1a2e",
            "eyeBall2Color": "#1a1a2e",
            "eyeBall3Color": "#1a1a2e",
            "logo": "",
            "logoMode": "default"
        },
        "size": 400,
        "download": False,
        "file": "png"
    }

    try:
        resp = requests.post(
            f"{QRIO_BASE_URL}/create",
            json=payload,
            headers={
                "Authorization": f"Bearer {QRIO_API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        image_url = data.get("imageUrl") or data.get("url") or data.get("qr_url") or ""
        logger.info(f"QR generado para reserva {booking_id}: {image_url}")

        return {
            "qr_url": image_url,
            "verify_url": verify_url,
            "booking_id": booking_id,
            "pasajero": nombre,
            "fecha": fecha
        }

    except Exception as e:
        logger.error(f"Error generando QR via QR.io: {e}")
        # Fallback: usar Google Charts QR (siempre disponible)
        fallback_url = f"https://chart.googleapis.com/chart?cht=qr&chs=400x400&chl={requests.utils.quote(verify_url)}"
        return {
            "qr_url": fallback_url,
            "verify_url": verify_url,
            "booking_id": booking_id,
            "pasajero": nombre,
            "fecha": fecha
        }


def generate_qr_message(booking_data: dict) -> str:
    """
    Genera el mensaje de WhatsApp con el QR de recogida.
    Se envía al pasajero al confirmar la reserva.
    """
    nombre = booking_data.get("pasajero", "")
    booking_id = booking_data.get("booking_id", "")
    fecha = booking_data.get("fecha", "")
    qr_url = booking_data.get("qr_url", "")

    return (
        f"✅ *Reserva Confirmada — Emovils*\n\n"
        f"Estimado/a {nombre},\n\n"
        f"Su traslado ha sido confirmado. Guarde este código QR:\n"
        f"👉 {qr_url}\n\n"
        f"📋 *Reserva:* {booking_id}\n"
        f"📅 *Fecha:* {fecha}\n\n"
        f"Al llegar su chofer, mostrará el QR para verificar que es el conductor asignado a su reserva.\n\n"
        f"¿Alguna pregunta? Estamos disponibles 24/7."
    )
