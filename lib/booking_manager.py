"""
Emovils — Booking Manager
Gestiona el ciclo completo de una reserva: creacion, estados, QR y validaciones.
"""
import uuid
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ENUMS DE ESTADO
# ─────────────────────────────────────────────
class PaymentMethod(str, Enum):
    CASH = "cash"
    CARD = "card"
    ONLINE = "online"

class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    CASH_PENDING = "cash_pending"
    CARD_PENDING = "card_pending"
    FAILED = "failed"
    REFUNDED = "refunded"

class BookingStatus(str, Enum):
    DRAFT = "draft"
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    DRIVER_ASSIGNED = "driver_assigned"
    VEHICLE_ASSIGNED = "vehicle_assigned"
    PICKUP_CONFIRMED = "pickup_confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class CustomerQRStatus(str, Enum):
    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"
    INVALID = "invalid"

class VehicleVerificationStatus(str, Enum):
    NOT_VERIFIED = "not_verified"
    GREEN_OK = "green_ok"
    RED_FAILED = "red_failed"
    YELLOW_PENDING = "yellow_pending"
    EXPIRED = "expired"


# ─────────────────────────────────────────────
# MODELO DE RESERVA
# ─────────────────────────────────────────────
@dataclass
class Booking:
    # Identificadores
    booking_id: str = ""
    # Cliente
    customer_name: str = ""
    customer_phone: str = ""
    customer_whatsapp: str = ""
    # Viaje
    origin: str = ""
    destination: str = ""
    service_date: str = ""
    service_time: str = ""
    passengers: int = 0
    vehicle_type: str = ""
    notes: str = ""
    # Precio
    final_price: float = 0.0
    currency: str = "DOP"
    # Pago
    payment_method: str = ""
    payment_status: str = PaymentStatus.PENDING
    payment_link: str = ""
    # Reserva
    booking_status: str = BookingStatus.DRAFT
    # Asignacion
    driver_id: str = ""
    driver_name: str = ""
    driver_phone: str = ""
    vehicle_id: str = ""
    vehicle_plate: str = ""
    vehicle_color: str = ""
    vehicle_brand: str = ""
    # QR del cliente
    customer_qr_token: str = ""
    customer_qr_url: str = ""
    customer_qr_status: str = CustomerQRStatus.INVALID
    qr_created_at: str = ""
    qr_valid_from: str = ""
    qr_expires_at: str = ""
    # Confirmacion de recogida
    pickup_confirmed: bool = False
    pickup_confirmed_at: str = ""
    pickup_confirmed_by_driver_id: str = ""
    pickup_confirmed_location: str = ""
    # Verificacion del vehiculo por el cliente
    vehicle_verified_by_customer: bool = False
    vehicle_verified_at: str = ""
    vehicle_verification_status: str = VehicleVerificationStatus.NOT_VERIFIED
    # Timestamps
    created_at: str = ""
    updated_at: str = ""


def generate_booking_id() -> str:
    """Genera un booking_id unico con formato EMV-YYYY-XXXXXX."""
    year = datetime.now().year
    unique = secrets.token_hex(3).upper()
    return f"EMV-{year}-{unique}"


def generate_secure_token() -> str:
    """Genera un token seguro de 32 bytes."""
    return secrets.token_urlsafe(32)


def create_booking(
    customer_name: str,
    customer_phone: str,
    origin: str,
    destination: str,
    service_date: str,
    service_time: str,
    passengers: int,
    vehicle_type: str,
    final_price: float,
    payment_method: str,
    customer_whatsapp: str = "",
    notes: str = "",
    base_url: str = "https://emovils.com"
) -> Booking:
    """
    Crea una nueva reserva con todos los campos requeridos.
    Determina el estado inicial segun la forma de pago.
    """
    now = datetime.utcnow().isoformat()
    booking_id = generate_booking_id()

    # Estado inicial segun forma de pago
    if payment_method == PaymentMethod.CASH:
        payment_status = PaymentStatus.CASH_PENDING
        booking_status = BookingStatus.CONFIRMED
    elif payment_method == PaymentMethod.CARD:
        payment_status = PaymentStatus.CARD_PENDING
        booking_status = BookingStatus.CONFIRMED
    elif payment_method == PaymentMethod.ONLINE:
        payment_status = PaymentStatus.PENDING
        booking_status = BookingStatus.PENDING_PAYMENT
    else:
        payment_status = PaymentStatus.PENDING
        booking_status = BookingStatus.DRAFT

    booking = Booking(
        booking_id=booking_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_whatsapp=customer_whatsapp or customer_phone,
        origin=origin,
        destination=destination,
        service_date=service_date,
        service_time=service_time,
        passengers=passengers,
        vehicle_type=vehicle_type,
        final_price=final_price,
        currency="DOP",
        payment_method=payment_method,
        payment_status=payment_status,
        booking_status=booking_status,
        notes=notes,
        created_at=now,
        updated_at=now
    )

    # Generar QR si la reserva esta confirmada (no online pendiente)
    if booking_status == BookingStatus.CONFIRMED:
        booking = _attach_qr(booking, base_url, service_date, service_time)

    return booking


def confirm_online_payment(booking: Booking, base_url: str = "https://emovils.com") -> Booking:
    """
    Confirma el pago en linea y actualiza estados.
    Genera el QR del cliente solo en este momento.
    """
    booking.payment_status = PaymentStatus.PAID
    booking.booking_status = BookingStatus.CONFIRMED
    booking.updated_at = datetime.utcnow().isoformat()
    booking = _attach_qr(booking, base_url, booking.service_date, booking.service_time)
    return booking


def _attach_qr(booking: Booking, base_url: str, service_date: str, service_time: str) -> Booking:
    """Genera y adjunta el QR del cliente a la reserva."""
    token = generate_secure_token()
    qr_url = f"{base_url}/verify/{booking.booking_id}?token={token}"
    now = datetime.utcnow()

    # Calcular ventana de validez del QR
    try:
        service_dt_str = f"{service_date} {service_time}"
        service_dt = datetime.strptime(service_dt_str, "%Y-%m-%d %H:%M")
        qr_valid_from = (service_dt - timedelta(hours=2)).isoformat()
        qr_expires_at = (service_dt + timedelta(hours=3)).isoformat()
    except Exception:
        qr_valid_from = now.isoformat()
        qr_expires_at = (now + timedelta(hours=5)).isoformat()

    booking.customer_qr_token = token
    booking.customer_qr_url = qr_url
    booking.customer_qr_status = CustomerQRStatus.ACTIVE
    booking.qr_created_at = now.isoformat()
    booking.qr_valid_from = qr_valid_from
    booking.qr_expires_at = qr_expires_at
    return booking


def assign_driver_and_vehicle(
    booking: Booking,
    driver_id: str,
    driver_name: str,
    driver_phone: str,
    vehicle_id: str,
    vehicle_plate: str,
    vehicle_color: str,
    vehicle_brand: str = ""
) -> Booking:
    """Asigna conductor y vehiculo a una reserva confirmada."""
    booking.driver_id = driver_id
    booking.driver_name = driver_name
    booking.driver_phone = driver_phone
    booking.vehicle_id = vehicle_id
    booking.vehicle_plate = vehicle_plate
    booking.vehicle_color = vehicle_color
    booking.vehicle_brand = vehicle_brand
    booking.booking_status = BookingStatus.DRIVER_ASSIGNED
    booking.updated_at = datetime.utcnow().isoformat()
    return booking


def validate_customer_qr(
    booking: Booking,
    token: str,
    driver_id: str
) -> dict:
    """
    Valida el QR del cliente cuando el conductor lo escanea.
    Retorna resultado de la validacion.
    """
    now = datetime.utcnow()

    # Verificar que la reserva existe y esta confirmada
    if booking.booking_status not in (
        BookingStatus.CONFIRMED, BookingStatus.DRIVER_ASSIGNED,
        BookingStatus.VEHICLE_ASSIGNED
    ):
        return {
            "valid": False,
            "reason": "La reserva no esta confirmada.",
            "show_driver": "Este QR no es valido. Contacte a un supervisor."
        }

    # Verificar token
    if booking.customer_qr_token != token:
        return {
            "valid": False,
            "reason": "Token invalido.",
            "show_driver": "Este QR no es valido o esta adulterado. Contacte a un supervisor."
        }

    # Verificar que el QR no fue usado
    if booking.customer_qr_status == CustomerQRStatus.USED:
        return {
            "valid": False,
            "reason": "QR ya utilizado.",
            "show_driver": "Este QR ya fue utilizado para confirmar la recogida."
        }

    # Verificar vencimiento
    try:
        expires = datetime.fromisoformat(booking.qr_expires_at)
        if now > expires:
            return {
                "valid": False,
                "reason": "QR vencido.",
                "show_driver": "Este QR esta vencido. Contacte a un supervisor."
            }
    except Exception:
        pass

    # Verificar que el conductor esta asignado
    if booking.driver_id and booking.driver_id != driver_id:
        return {
            "valid": False,
            "reason": "Conductor no asignado a esta reserva.",
            "show_driver": "Usted no esta asignado a esta reserva. Contacte a un supervisor."
        }

    # Todo correcto — confirmar recogida
    return {
        "valid": True,
        "booking_id": booking.booking_id,
        "customer_name": booking.customer_name,
        "origin": booking.origin,
        "destination": booking.destination,
        "passengers": booking.passengers,
        "vehicle_type": booking.vehicle_type,
        "payment_method": booking.payment_method,
        "show_driver": (
            f"QR validado. Cliente: {booking.customer_name} | "
            f"Ruta: {booking.origin} -> {booking.destination} | "
            f"Pasajeros: {booking.passengers} | Pago: {booking.payment_method}"
        )
    }


def confirm_pickup(booking: Booking, driver_id: str, location: str = "") -> Booking:
    """Confirma la recogida del cliente y cambia estado a in_progress."""
    now = datetime.utcnow().isoformat()
    booking.pickup_confirmed = True
    booking.pickup_confirmed_at = now
    booking.pickup_confirmed_by_driver_id = driver_id
    booking.pickup_confirmed_location = location
    booking.booking_status = BookingStatus.IN_PROGRESS
    booking.customer_qr_status = CustomerQRStatus.USED
    booking.updated_at = now
    return booking


def get_confirmation_message(booking: Booking) -> str:
    """Genera el mensaje de confirmacion para el cliente segun forma de pago."""
    price_fmt = "{:,}".format(int(booking.final_price))

    base = (
        f"Su reserva ha sido confirmada.\n\n"
        f"Vehiculo: {booking.vehicle_type.title()}\n"
        f"Fecha: {booking.service_date}\n"
        f"Hora: {booking.service_time}\n"
        f"Recogida: {booking.origin}\n"
        f"Destino: {booking.destination}\n"
        f"Pasajeros: {booking.passengers}\n"
        f"Precio: RD${price_fmt}\n"
        f"Pago: {booking.payment_method}\n"
        f"Codigo de reserva: {booking.booking_id}\n\n"
    )

    qr_note = (
        "Le enviaremos su codigo QR de servicio. "
        "Al llegar el vehiculo, escanee el QR colocado en la puerta. "
        "Solo aborde si aparece el check verde de Emovils."
    )

    if booking.payment_method == PaymentMethod.ONLINE and booking.payment_status != PaymentStatus.PAID:
        return (
            f"Su solicitud esta registrada. Codigo: {booking.booking_id}\n\n"
            f"Para confirmar la reserva, complete el pago en linea.\n"
            f"Una vez confirmado el pago, recibira su codigo QR de servicio."
        )

    return base + qr_note


def get_driver_assignment_message(booking: Booking) -> str:
    """Mensaje al cliente cuando se asigna conductor y vehiculo."""
    return (
        f"Su servicio Emovils ha sido asignado.\n\n"
        f"Conductor: {booking.driver_name}\n"
        f"Vehiculo: {booking.vehicle_type.title()}\n"
        f"Color: {booking.vehicle_color}\n"
        f"Placa: {booking.vehicle_plate}\n"
        f"Codigo de reserva: {booking.booking_id}\n\n"
        f"Al llegar la unidad, escanee el QR de la puerta del vehiculo. "
        f"Check verde = puede abordar con confianza. "
        f"Si no aparece verde, no aborde y llame a nuestra central."
    )


def validate_required_fields(data: dict) -> list:
    """
    Verifica que esten todos los datos obligatorios para confirmar reserva.
    Retorna lista de campos faltantes.
    """
    required = [
        ("customer_name", "Nombre completo del cliente"),
        ("customer_phone", "Numero de telefono"),
        ("origin", "Punto de recogida"),
        ("destination", "Destino"),
        ("service_date", "Fecha del servicio"),
        ("service_time", "Hora del servicio"),
        ("passengers", "Cantidad de pasajeros"),
        ("vehicle_type", "Tipo de vehiculo"),
        ("final_price", "Precio final"),
        ("payment_method", "Forma de pago"),
    ]

    missing = []
    for field_key, field_label in required:
        val = data.get(field_key)
        if not val and val != 0:
            missing.append(field_label)

    return missing
