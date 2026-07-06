"""
Emovils OPC — Sistema de cuentas (auth) para 3 roles: empresa, chofer, cliente.

Qué hace:
  • Registro de cuenta (crear usuario + contraseña).
  • Inicio de sesión con contraseña.
  • Recuperación de contraseña por WhatsApp (código de 6 dígitos que vence).

Seguridad:
  • Las contraseñas se guardan con hash PBKDF2-SHA256 (NUNCA en texto plano).
  • Las sesiones son tokens firmados (HMAC), sin estado (no se guardan en BD).
  • No requiere librerías nuevas: solo la librería estándar de Python.

Almacenamiento:
  • Tabla "Accounts" en el MISMO Airtable donde viven los choferes
    (reutiliza los ayudantes de opc.mvp, así apunta siempre a la base correcta).
"""
from __future__ import annotations

import os
import re
import time
import hmac
import base64
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Reutilizamos los ayudantes de Airtable + teléfono del módulo mvp (misma base).
from opc import mvp

TABLA = "Accounts"
ROLES = ("empresa", "chofer", "cliente")

_PBKDF2_ITER = 200_000   # iteraciones de cifrado de la contraseña
_CODE_TTL_MIN = 15       # minutos de validez del código de recuperación
_TOKEN_DAYS = 30         # días de validez de la sesión

# Clave para firmar tokens de sesión y códigos. OBLIGATORIA en producción: si no,
# reutiliza la de los QR (que ya es obligatoria en prod). En local, como último
# recurso, usa una clave de desarrollo insegura.
_AUTH_SECRET = (
    os.getenv("AUTH_SECRET", "").strip()
    or os.getenv("QR_SIGNING_KEY", "").strip()
)
if not _AUTH_SECRET:
    if mvp._es_entorno_produccion():
        raise RuntimeError(
            "AUTH_SECRET (o QR_SIGNING_KEY) no está configurada. Es obligatoria "
            "en producción para firmar las sesiones de forma segura."
        )
    _AUTH_SECRET = "dev-only-insecure-auth-key-no-usar-en-produccion"


# ═══════════════════════════════════════════════════════════════
# CONTRASEÑAS (hash con librería estándar — sin dependencias nuevas)
# ═══════════════════════════════════════════════════════════════
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ITER)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITER,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# TOKENS DE SESIÓN (firmados, sin estado)
# ═══════════════════════════════════════════════════════════════
def _sign(b: bytes) -> str:
    return hmac.new(_AUTH_SECRET.encode("utf-8"), b, hashlib.sha256).hexdigest()[:32]


def make_token(account_id: str, role: str, days: int = _TOKEN_DAYS) -> str:
    exp = int(time.time()) + days * 86400
    raw = "{}|{}|{}".format(account_id, role, exp)
    b = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return "{}.{}".format(b, _sign(b.encode()))


def read_token(token: str):
    try:
        b, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(b.encode())):
            return None
        pad = "=" * (-len(b) % 4)
        raw = base64.urlsafe_b64decode(b + pad).decode()
        account_id, role, exp = raw.split("|")
        if int(exp) < time.time():
            return None
        return {"account_id": account_id, "role": role}
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code() -> str:
    return "{:06d}".format(secrets.randbelow(1_000_000))


def _hash_code(code: str) -> str:
    return hashlib.sha256((_AUTH_SECRET + ":" + str(code)).encode("utf-8")).hexdigest()


def _last10(tel) -> str:
    return re.sub(r"\D", "", str(tel or ""))[-10:]


def _buscar_cuenta(telefono: str, role: str = None):
    """Registro de Airtable (o None) que coincide por últimos 10 dígitos del
    teléfono y, si se indica, por rol."""
    last10 = _last10(telefono)
    if not last10:
        return None
    for rec in mvp._at_get(TABLA, max_records=1000):
        f = rec.get("fields", {})
        if _last10(f.get("phone", "")) != last10:
            continue
        if role and f.get("role") != role:
            continue
        return rec
    return None


def _public(f: dict) -> dict:
    """Datos de la cuenta seguros para devolver al frontend (sin contraseña)."""
    return {
        "account_id": f.get("account_id", ""),
        "role": f.get("role", ""),
        "name": f.get("name", ""),
        "phone": f.get("phone", ""),
        "email": f.get("email", ""),
        "linked_driver_id": f.get("linked_driver_id", ""),
        "status": f.get("status", "active"),
    }


# ═══════════════════════════════════════════════════════════════
# REGISTRO
# ═══════════════════════════════════════════════════════════════
def registrar(role, nombre, telefono, password, email="") -> dict:
    role = (role or "").strip().lower()
    if role not in ROLES:
        return {"ok": False, "razon": "Tipo de cuenta inválido."}

    nombre = (nombre or "").strip()
    tel = mvp._fmt_tel_rd(telefono)
    if not nombre:
        return {"ok": False, "razon": "Escribe tu nombre."}
    if len(_last10(tel)) < 10:
        return {"ok": False, "razon": "Escribe un número de teléfono válido (10 dígitos)."}
    if not password or len(password) < 4:
        return {"ok": False, "razon": "La contraseña debe tener al menos 4 caracteres."}

    if _buscar_cuenta(tel, role):
        return {"ok": False, "razon": "Ya existe una cuenta con ese número. Inicia sesión."}

    linked_driver_id = ""
    if role == "chofer":
        drivers = mvp._buscar_driver_por_tel(tel)
        if not drivers:
            return {
                "ok": False,
                "razon": "Tu número no está registrado como chofer. Pide a la empresa que te agregue primero.",
            }
        linked_driver_id = drivers[0]["fields"].get("driver_id", "")

    if role == "empresa":
        # Solo el dueño puede crear cuenta de empresa: o es la PRIMERA cuenta
        # de empresa (arranque), o el número coincide con OWNER_WHATSAPP.
        existentes = mvp._at_get(TABLA, formula="{role}='empresa'", max_records=10)
        owner = _last10(os.getenv("OWNER_WHATSAPP", ""))
        if existentes and (not owner or _last10(tel) != owner):
            return {
                "ok": False,
                "razon": "El registro de empresa está restringido. Contacta al administrador.",
            }

    now = _now()
    account_id = mvp._siguiente_codigo(TABLA, "ACC", "account_id")
    fields = {
        "account_id": account_id,
        "role": role,
        "name": nombre,
        "phone": tel,
        "email": (email or "").strip(),
        "password_hash": hash_password(password),
        "status": "active",
        "linked_driver_id": linked_driver_id,
        "created_at": now,
        "updated_at": now,
    }
    mvp._at_create(TABLA, fields)
    logger.info("Cuenta creada: %s (%s)", account_id, role)
    return {"ok": True, "account": _public(fields), "token": make_token(account_id, role)}


# ═══════════════════════════════════════════════════════════════
# INICIO DE SESIÓN
# ═══════════════════════════════════════════════════════════════
def login(role, telefono, password) -> dict:
    role = (role or "").strip().lower()
    rec = _buscar_cuenta(telefono, role)
    if not rec:
        return {"ok": False, "razon": "No encontramos una cuenta con ese número. Regístrate primero."}
    f = rec["fields"]
    if f.get("status") == "suspended":
        return {"ok": False, "razon": "Tu cuenta está suspendida. Contacta a la empresa."}
    if not verify_password(password or "", f.get("password_hash", "")):
        return {"ok": False, "razon": "Contraseña incorrecta."}
    try:
        mvp._at_update(TABLA, rec["id"], {"last_login": _now()})
    except Exception:
        pass
    return {
        "ok": True,
        "account": _public(f),
        "token": make_token(f.get("account_id", ""), role),
    }


# ═══════════════════════════════════════════════════════════════
# RECUPERACIÓN DE CONTRASEÑA POR WHATSAPP
# ═══════════════════════════════════════════════════════════════
def solicitar_codigo(role, telefono) -> dict:
    """Genera un código de 6 dígitos y lo envía por WhatsApp al dueño de la cuenta."""
    role = (role or "").strip().lower()
    rec = _buscar_cuenta(telefono, role)
    # Respuesta genérica: no revelamos si el número tiene cuenta o no.
    generico = {
        "ok": True,
        "mensaje": "Si el número tiene una cuenta, te enviamos un código por WhatsApp.",
    }
    if not rec:
        return generico

    code = _code()
    exp = (datetime.now(timezone.utc) + timedelta(minutes=_CODE_TTL_MIN)).isoformat()
    try:
        mvp._at_update(TABLA, rec["id"], {
            "reset_code_hash": _hash_code(code),
            "reset_expires": exp,
            "updated_at": _now(),
        })
    except Exception as e:
        logger.error("No pude guardar el código de recuperación: %s", e)
        return {"ok": False, "razon": "No pudimos generar el código. Intenta de nuevo."}

    tel = rec["fields"].get("phone", telefono)
    msg = (
        "🔐 *Emovils* — Tu código para cambiar la contraseña es: *{}*\n\n"
        "Vence en {} minutos. Si no fuiste tú, ignora este mensaje."
    ).format(code, _CODE_TTL_MIN)
    try:
        from lib import whatsapp_api
        whatsapp_api.send_text(mvp._norm_tel(tel), msg)
    except Exception as e:
        logger.error("No pude enviar el código por WhatsApp: %s", e)
    return generico


def restablecer(role, telefono, codigo, nueva_password) -> dict:
    """Verifica el código recibido por WhatsApp y cambia la contraseña."""
    role = (role or "").strip().lower()
    rec = _buscar_cuenta(telefono, role)
    if not rec:
        return {"ok": False, "razon": "No encontramos esa cuenta."}
    f = rec["fields"]
    stored = f.get("reset_code_hash", "")
    exp = f.get("reset_expires", "")
    if not stored or not exp:
        return {"ok": False, "razon": "Pide un código nuevo."}
    try:
        venc = datetime.fromisoformat(exp)
        if venc.tzinfo is None:
            venc = venc.replace(tzinfo=timezone.utc)
    except Exception:
        return {"ok": False, "razon": "Pide un código nuevo."}
    if datetime.now(timezone.utc) > venc:
        return {"ok": False, "razon": "El código venció. Pide uno nuevo."}
    if not hmac.compare_digest(stored, _hash_code(str(codigo or "").strip())):
        return {"ok": False, "razon": "Código incorrecto."}
    if not nueva_password or len(nueva_password) < 4:
        return {"ok": False, "razon": "La nueva contraseña debe tener al menos 4 caracteres."}

    mvp._at_update(TABLA, rec["id"], {
        "password_hash": hash_password(nueva_password),
        "reset_code_hash": "",
        "reset_expires": "",
        "updated_at": _now(),
    })
    logger.info("Contraseña restablecida: %s", f.get("account_id", ""))
    return {
        "ok": True,
        "account": _public(f),
        "token": make_token(f.get("account_id", ""), role),
    }


# ═══════════════════════════════════════════════════════════════
# SESIÓN ACTUAL (validar token)
# ═══════════════════════════════════════════════════════════════
def cuenta_desde_token(token: str) -> dict:
    data = read_token(token or "")
    if not data:
        return {"ok": False, "razon": "Sesión inválida o expirada."}
    rec = None
    for r in mvp._at_get(TABLA, formula="{{account_id}}='{}'".format(data["account_id"]), max_records=1):
        rec = r
    if not rec:
        return {"ok": False, "razon": "Cuenta no encontrada."}
    return {"ok": True, "account": _public(rec["fields"])}
