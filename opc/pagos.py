"""
Emovils OPC — Módulo de Pagos (PayPal)

Wrapper fino sobre lib/paypal_api.py para el flujo OPC.
Degradación elegante: si faltan las credenciales de PayPal
(PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET) devuelve None y loguea
un warning — NUNCA rompe el flujo de reserva.

Uso típico (en el Agente Coordinador):

    from opc.pagos import crear_link_pago, rd_a_usd
    link = crear_link_pago("SVC-123", rd_a_usd(2500), "AILA → Casa de Campo")
    if link:
        mensaje += f"\\n💳 Paga aquí: {link}"
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Tasa de cambio RD$ → USD para los links de pago (configurable por .env)
TASA_RD_POR_USD = float(os.getenv("TASA_RD_POR_USD", "60"))


def credenciales_paypal_ok() -> bool:
    """True si hay credenciales de PayPal configuradas en el entorno."""
    return bool(os.getenv("PAYPAL_CLIENT_ID")) and bool(os.getenv("PAYPAL_CLIENT_SECRET"))


def rd_a_usd(monto_rd: float | int) -> float:
    """Convierte pesos dominicanos a USD con la tasa configurada."""
    if not monto_rd or monto_rd <= 0:
        return 0.0
    return round(float(monto_rd) / TASA_RD_POR_USD, 2)


def crear_link_pago(booking_id: str, monto_usd: float, descripcion: str) -> Optional[str]:
    """
    Crea una orden de PayPal y devuelve el link de aprobación (checkout)
    para enviar al cliente por WhatsApp.

    Args:
        booking_id: ID del servicio/reserva (ej. record de Airtable "SVC-...").
        monto_usd: Monto a cobrar en USD.
        descripcion: Texto corto del servicio (ej. "AILA → Casa de Campo").

    Returns:
        URL de pago, o None si faltan credenciales / falla PayPal
        (en ese caso se loguea un warning y el flujo sigue sin link).
    """
    if not credenciales_paypal_ok():
        logger.warning(
            "PayPal sin configurar (PAYPAL_CLIENT_ID/PAYPAL_CLIENT_SECRET) — "
            "reserva %s sin link de pago", booking_id
        )
        return None

    if not monto_usd or monto_usd <= 0:
        logger.warning("Monto inválido (%s) para link de pago de %s", monto_usd, booking_id)
        return None

    try:
        from lib.paypal_api import create_payment_order
        orden = create_payment_order(
            product_key="airport_sencillo",
            customer_name=descripcion,
            booking_id=booking_id,
            custom_price=round(float(monto_usd), 2),
        )
        url = orden.get("approve_url")
        if url:
            logger.info("💳 Link PayPal creado para %s: $%.2f USD", booking_id, monto_usd)
        return url
    except Exception as exc:
        # Nunca tumbar la reserva por un fallo de pago — se cobra manual
        logger.warning("PayPal falló para %s: %s", booking_id, exc)
        return None
