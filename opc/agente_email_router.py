"""
Emovils OPC — Agente Email Router

Maneja TODOS los correos de los 7 buzones corporativos @emovils.com:
  • info@         → Monserrat (Claude) responde y crea lead Airtable
  • reservas@     → automatico (solo envia, no se lee)
  • ventas@       → Noe directo + alerta WhatsApp
  • soporte@      → Monserrat → Supervisor → Noe (escalacion 3 niveles)
  • facturacion@  → Contadora + copia Noe
  • rrhh@         → Admin + Noe
  • alertas@      → Solo Noe

Dos modos:
  ENVIAR: SMTP (Hostinger smtp.hostinger.com:465 SSL)
  LEER:   IMAP (Hostinger imap.hostinger.com:993 SSL)

En produccion correr como cron cada 2 minutos: lee buzones, clasifica,
enruta, archiva.
"""
from __future__ import annotations
import email
import imaplib
import json
import logging
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def _cargar_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


_cargar_env()
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONFIGURACION DE BUZONES
# ─────────────────────────────────────────────────────────────

BUZONES = {
    "info": {
        "direccion": os.getenv("EMAIL_INFO", "info@emovils.com"),
        "from_name": os.getenv("EMAIL_FROM_INFO", "Emovils <info@emovils.com>"),
        "auto_responder": True,
        "manejado_por": "Monserrat (Claude)",
        "escala_a": "nvasquez@emovils.com",
    },
    "reservas": {
        "direccion": os.getenv("EMAIL_RESERVAS", "reservas@emovils.com"),
        "from_name": os.getenv("EMAIL_FROM_RESERVAS", "Emovils Reservas <reservas@emovils.com>"),
        "auto_responder": False,
        "manejado_por": "Sistema (envios automaticos)",
        "escala_a": None,
    },
    "ventas": {
        "direccion": os.getenv("EMAIL_VENTAS", "ventas@emovils.com"),
        "from_name": os.getenv("EMAIL_FROM_VENTAS", "Noe Vasquez · Emovils <ventas@emovils.com>"),
        "auto_responder": True,
        "manejado_por": "Noe Vasquez (directo)",
        "escala_a": "nvasquez@emovils.com",
    },
    "soporte": {
        "direccion": os.getenv("EMAIL_SOPORTE", "soporte@emovils.com"),
        "from_name": "Emovils Soporte <soporte@emovils.com>",
        "auto_responder": True,
        "manejado_por": "Monserrat → Supervisor → Noe",
        "escala_a": "supervisor@emovils.com",
    },
    "facturacion": {
        "direccion": os.getenv("EMAIL_FACTURACION", "facturacion@emovils.com"),
        "from_name": os.getenv("EMAIL_FROM_FACTURACION", "Emovils Facturacion <facturacion@emovils.com>"),
        "auto_responder": False,
        "manejado_por": "Contadora",
        "escala_a": "contabilidad@emovils.com",
    },
    "rrhh": {
        "direccion": os.getenv("EMAIL_RRHH", "rrhh@emovils.com"),
        "from_name": "Emovils RRHH <rrhh@emovils.com>",
        "auto_responder": False,
        "manejado_por": "Admin",
        "escala_a": "admin@emovils.com",
    },
    "alertas": {
        "direccion": os.getenv("EMAIL_ALERTAS", "alertas@emovils.com"),
        "from_name": os.getenv("EMAIL_FROM_ALERTAS", "Sistema OPC Emovils <alertas@emovils.com>"),
        "auto_responder": False,
        "manejado_por": "Sistema (alertas)",
        "escala_a": "nvasquez@emovils.com",
    },
}

# BANAHOSTING defaults (cPanel server single-7060)
# emovils.com tiene los buzones alojados en Banahosting con cPanel
SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "mail.emovils.com")
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
SMTP_SECURITY = os.getenv("EMAIL_SMTP_SECURITY", "SSL").upper()  # SSL o STARTTLS
IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "mail.emovils.com")
IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))
CPANEL_WEBMAIL = "https://single-7060.banahosting.com:2096"
CPANEL_ADMIN = "https://single-7060.banahosting.com:2083"


# ─────────────────────────────────────────────────────────────
# CLASIFICACION DE CORREOS ENTRANTES
# ─────────────────────────────────────────────────────────────

class CategoriaCorreo:
    COTIZACION = "COTIZACION_B2C"
    RESERVA = "RESERVA"
    QUEJA = "QUEJA"
    QUEJA_CRITICA = "QUEJA_CRITICA"
    PROSPECT_B2B = "PROSPECT_B2B"
    SPAM = "SPAM"
    FACTURA_CLIENTE = "FACTURA_CLIENTE_B2B"
    DOCUMENTO_INTERNO = "DOCUMENTO_INTERNO"
    OTRO = "OTRO"


KEYWORDS_QUEJA_CRITICA = [
    "demanda", "policia", "policía", "abogado", "denuncia", "robo", "accidente",
    "lesión", "lesion", "asalto", "agresión", "agresion",
]

KEYWORDS_QUEJA = [
    "queja", "reclamo", "molesto", "horrible", "pésimo", "pesimo", "nunca llegó",
    "no apareció", "no aparecio", "mal servicio", "perdí mi vuelo",
]

KEYWORDS_PROSPECT_B2B = [
    "cotización corporativa", "cotizacion corporativa", "rfp", "licitación",
    "licitacion", "contrato mensual", "empresa", "personal de", "transporte staff",
    "transporte empleados", "convenio",
]

KEYWORDS_COTIZACION_B2C = [
    "cuánto cuesta", "cuanto cuesta", "precio", "tarifa", "cotización", "cotizacion",
    "del aeropuerto", "al aeropuerto", "aila", "punta cana", "casa de campo",
]


def clasificar_correo(asunto: str, cuerpo: str, remitente: str) -> str:
    texto = (asunto + " " + cuerpo).lower()

    if any(k in texto for k in KEYWORDS_QUEJA_CRITICA):
        return CategoriaCorreo.QUEJA_CRITICA
    if any(k in texto for k in KEYWORDS_QUEJA):
        return CategoriaCorreo.QUEJA
    if any(k in texto for k in KEYWORDS_PROSPECT_B2B):
        return CategoriaCorreo.PROSPECT_B2B
    if any(k in texto for k in KEYWORDS_COTIZACION_B2C):
        return CategoriaCorreo.COTIZACION
    if "factura" in texto or "ncf" in texto or "recibo" in texto:
        return CategoriaCorreo.FACTURA_CLIENTE
    return CategoriaCorreo.OTRO


# ─────────────────────────────────────────────────────────────
# ENVIO SMTP
# ─────────────────────────────────────────────────────────────

@dataclass
class EmailSaliente:
    desde_buzon: str          # ej "info", "ventas", "reservas"
    para: str
    asunto: str
    cuerpo_texto: str
    cuerpo_html: Optional[str] = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    adjuntos: list[Path] = field(default_factory=list)


def enviar_email(envio: EmailSaliente) -> bool:
    """Envia via SMTP Hostinger SSL. Devuelve True si exitoso."""
    buzon = BUZONES.get(envio.desde_buzon)
    if not buzon:
        logger.error("Buzon desconocido: %s", envio.desde_buzon)
        return False

    smtp_user = buzon["direccion"]
    smtp_pass = os.getenv("EMAIL_SMTP_PASS", "")
    if not smtp_pass:
        logger.error("EMAIL_SMTP_PASS no configurada en .env")
        return False

    msg = EmailMessage()
    msg["From"] = buzon["from_name"]
    msg["To"] = envio.para
    msg["Subject"] = envio.asunto
    if envio.cc:
        msg["Cc"] = ", ".join(envio.cc)
    if envio.reply_to:
        msg["Reply-To"] = envio.reply_to
    msg.set_content(envio.cuerpo_texto)
    if envio.cuerpo_html:
        msg.add_alternative(envio.cuerpo_html, subtype="html")

    for ruta in envio.adjuntos:
        if ruta.exists():
            with open(ruta, "rb") as f:
                data = f.read()
            msg.add_attachment(
                data, maintype="application", subtype="octet-stream",
                filename=ruta.name,
            )

    destinatarios = [envio.para] + envio.cc + envio.bcc
    try:
        ctx = ssl.create_default_context()
        if SMTP_SECURITY == "STARTTLS":
            # Microsoft 365 con GoDaddy → puerto 587
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg, to_addrs=destinatarios)
        else:
            # GoDaddy Workspace Email (SSL) → puerto 465
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg, to_addrs=destinatarios)
        logger.info("✓ Email enviado: %s → %s [%s]", smtp_user, envio.para, envio.asunto)
        return True
    except Exception as exc:
        logger.exception("Fallo SMTP: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────
# AUTO-RESPONDER POR BUZON
# ─────────────────────────────────────────────────────────────

PLANTILLAS_AUTO_RESPUESTA = {
    "info": (
        "Hola,\n\n"
        "Recibimos tu mensaje. Para una respuesta mas rapida escribenos por "
        "WhatsApp 829-861-0090 (Monserrat te atiende en segundos). Si tu "
        "consulta requiere atencion personalizada, te respondemos por aqui "
        "en menos de 4 horas.\n\n"
        "Saludos,\nEquipo Emovils\nemovils.com"
    ),
    "ventas": (
        "Recibimos tu interes en Emovils.\n\n"
        "Un especialista corporativo te responde personalmente en menos de "
        "24 horas para coordinar reunion. Si es urgente, WhatsApp 829-861-0090.\n\n"
        "Noe Vasquez\nEmovils RD\nemovils.com"
    ),
    "soporte": (
        "Recibimos tu reporte y lo estamos revisando.\n\n"
        "Para casos urgentes (cliente sin chofer, accidente, queja critica), "
        "llama directo al 829-861-0090. Respondemos por aqui en menos de 2 horas "
        "en horario laboral.\n\n"
        "Equipo Soporte Emovils\nemovils.com"
    ),
}


def enviar_auto_respuesta(buzon: str, para: str, asunto_original: str) -> bool:
    plantilla = PLANTILLAS_AUTO_RESPUESTA.get(buzon)
    if not plantilla:
        return False
    envio = EmailSaliente(
        desde_buzon=buzon, para=para,
        asunto=f"Re: {asunto_original}",
        cuerpo_texto=plantilla,
    )
    return enviar_email(envio)


# ─────────────────────────────────────────────────────────────
# LECTURA IMAP + ROUTING
# ─────────────────────────────────────────────────────────────

@dataclass
class CorreoEntrante:
    uid: str
    buzon: str
    remitente: str
    asunto: str
    cuerpo: str
    fecha: datetime
    categoria: str = ""


def leer_buzon_imap(buzon: str, max_correos: int = 20) -> list[CorreoEntrante]:
    """Conecta IMAP y lee correos NO LEIDOS del buzon."""
    cfg = BUZONES.get(buzon)
    if not cfg:
        return []

    direccion = cfg["direccion"]
    password = os.getenv("EMAIL_SMTP_PASS", "")
    if not password:
        logger.warning("Sin password SMTP, no se puede leer IMAP")
        return []

    correos: list[CorreoEntrante] = []
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(direccion, password)
            imap.select("INBOX")
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                return []
            ids = data[0].split()[-max_correos:]
            for uid in ids:
                status, msg_data = imap.fetch(uid, "(RFC822)")
                if status != "OK":
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                asunto = msg.get("Subject", "")
                remitente = parseaddr(msg.get("From", ""))[1]
                try:
                    fecha = parsedate_to_datetime(msg.get("Date", ""))
                except Exception:
                    fecha = datetime.now()
                cuerpo = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            cuerpo += part.get_payload(decode=True).decode(errors="ignore")
                else:
                    cuerpo = msg.get_payload(decode=True).decode(errors="ignore")
                correos.append(CorreoEntrante(
                    uid=uid.decode(), buzon=buzon, remitente=remitente,
                    asunto=asunto, cuerpo=cuerpo, fecha=fecha,
                ))
    except Exception as exc:
        logger.exception("Error IMAP en %s: %s", buzon, exc)
    return correos


def procesar_correo_entrante(correo: CorreoEntrante) -> dict:
    """Clasifica + enruta + decide acciones."""
    categoria = clasificar_correo(correo.asunto, correo.cuerpo, correo.remitente)
    correo.categoria = categoria
    acciones = []

    # 1. Auto-respuesta si el buzon la tiene activada
    if BUZONES[correo.buzon]["auto_responder"]:
        if enviar_auto_respuesta(correo.buzon, correo.remitente, correo.asunto):
            acciones.append("auto_respuesta_enviada")

    # 2. Escalacion segun categoria
    if categoria == CategoriaCorreo.QUEJA_CRITICA:
        # Alerta inmediata al dueno
        try:
            from opc.whatsapp_green_api import enviar_whatsapp
            owner_wa = os.getenv("OWNER_WHATSAPP", "+18298610090")
            enviar_whatsapp(
                owner_wa,
                f"🚨 QUEJA CRITICA en {correo.buzon}@\n"
                f"De: {correo.remitente}\n"
                f"Asunto: {correo.asunto}\n"
                f"--- responde URGENTE ---",
            )
            acciones.append("alerta_whatsapp_dueno")
        except Exception as exc:
            logger.warning("Fallo alerta WhatsApp: %s", exc)

        # Copia a nvasquez@
        enviar_email(EmailSaliente(
            desde_buzon="alertas",
            para=os.getenv("EMAIL_OWNER", "nvasquez@emovils.com"),
            asunto=f"🚨 QUEJA CRITICA: {correo.asunto}",
            cuerpo_texto=f"Cliente: {correo.remitente}\n\n{correo.cuerpo[:2000]}",
        ))
        acciones.append("escalado_dueno_email")

    elif categoria == CategoriaCorreo.PROSPECT_B2B:
        # Forward a ventas@ y crear lead en HubSpot (TODO)
        enviar_email(EmailSaliente(
            desde_buzon="alertas",
            para=os.getenv("EMAIL_VENTAS", "ventas@emovils.com"),
            asunto=f"[Prospect B2B desde {correo.buzon}@] {correo.asunto}",
            cuerpo_texto=f"De: {correo.remitente}\n\n{correo.cuerpo[:3000]}",
        ))
        acciones.append("forward_ventas")

    elif categoria == CategoriaCorreo.COTIZACION:
        # Aqui podria invocar Monserrat para responder con cotizacion
        acciones.append("cotizacion_pendiente_monserrat")

    # 3. Log en Airtable Conversations (TODO)
    return {"categoria": categoria, "acciones": acciones, "remitente": correo.remitente}


def ciclo_router_completo() -> dict:
    """Lee todos los buzones leibles y procesa. Devuelve resumen."""
    resumen = {}
    for buzon, cfg in BUZONES.items():
        # Solo leemos los que tienen sentido leer
        if buzon in ("info", "ventas", "soporte", "facturacion", "rrhh"):
            correos = leer_buzon_imap(buzon, max_correos=20)
            resultados = [procesar_correo_entrante(c) for c in correos]
            resumen[buzon] = {"recibidos": len(correos), "resultados": resultados}
    return resumen


# ─────────────────────────────────────────────────────────────
# CLI / TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("=" * 70)
    print("EMOVILS OPC — Email Router")
    print("=" * 70)
    print(f"\n7 buzones configurados:")
    for buzon, cfg in BUZONES.items():
        ar = "✓ auto-resp" if cfg["auto_responder"] else "—"
        print(f"  • {cfg['direccion']:<35} {ar:<12} → {cfg['manejado_por']}")
    print(f"\nSMTP: {SMTP_HOST}:{SMTP_PORT}")
    print(f"IMAP: {IMAP_HOST}:{IMAP_PORT}")
    print(f"Password configurada: {'✓' if os.getenv('EMAIL_SMTP_PASS') else '✗ FALTA en .env'}")
    print()
    print("Test clasificacion:")
    casos = [
        ("Queja sobre chofer", "El chofer nunca llegó y perdí mi vuelo a Miami"),
        ("Cotización empresa", "Necesitamos transporte mensual para 50 empleados del call center"),
        ("Cotización rápida", "¿Cuánto cuesta del aeropuerto a Casa de Campo?"),
        ("URGENTE policia", "Voy a poner una denuncia con la policia por su servicio"),
    ]
    for asunto, cuerpo in casos:
        cat = clasificar_correo(asunto, cuerpo, "test@cliente.com")
        print(f"  [{cat:<25}] {asunto}")
    print()
    print("=" * 70)
