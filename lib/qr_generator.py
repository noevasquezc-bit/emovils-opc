"""
Emovils — QR Generator
Genera imagenes QR para clientes y vehiculos.
Usa qrcode si esta disponible, sino retorna solo la URL.
"""
import logging
import base64
from typing import Optional
import io

logger = logging.getLogger(__name__)


def generate_qr_base64(url: str) -> Optional[str]:
    """
    Genera un QR en base64 a partir de una URL.
    Retorna string base64 o None si la libreria no esta disponible.
    """
    try:
        import qrcode
        from PIL import Image
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except ImportError:
        logger.warning("qrcode/PIL no instalado. Retornando URL directamente.")
        return None
    except Exception as e:
        logger.error("Error generando QR: %s", e)
        return None


def build_customer_qr_payload(booking_id: str, token: str, base_url: str = "https://emovils.com") -> dict:
    """
    Construye el payload del QR del cliente.
    Solo contiene booking_id, token y URL de verificacion.
    NO incluye datos personales sensibles.
    """
    verification_url = f"{base_url}/verify/{booking_id}?token={token}"
    return {
        "booking_id": booking_id,
        "secure_token": token,
        "verification_url": verification_url
    }


def build_vehicle_qr_payload(vehicle_id: str, token: str, base_url: str = "https://emovils.com") -> dict:
    """
    Construye el payload del QR fisico del vehiculo.
    Solo contiene vehicle_id, token y URL de verificacion.
    """
    verification_url = f"{base_url}/vehicle/verify/{vehicle_id}?token={token}"
    return {
        "vehicle_id": vehicle_id,
        "secure_vehicle_token": token,
        "verification_url": verification_url
    }


def get_qr_whatsapp_message(booking_id: str, qr_url: str) -> str:
    """Mensaje para enviar al cliente con el QR via WhatsApp."""
    return (
        f"Su reserva ha sido confirmada. Codigo: {booking_id}\n\n"
        f"Le enviamos su codigo QR de servicio. "
        f"Al momento de la recogida, el chofer escaneara este QR desde su aplicacion "
        f"para confirmar que el servicio inicio correctamente.\n\n"
        f"Acceda a su QR aqui:\n{qr_url}\n\n"
        f"Al llegar el vehiculo, escanee tambien el QR pegado en la puerta. "
        f"Solo aborde si aparece el check verde de Emovils."
    )
