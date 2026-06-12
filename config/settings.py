"""
Emovils OPC — Configuración Global del Sistema
One Person Mobility Company | República Dominicana
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# IDENTIDAD DEL NEGOCIO
# ─────────────────────────────────────────────
BUSINESS_NAME = "Emovils OPC"
BUSINESS_TAGLINE = "No vendemos traslados. Vendemos certeza al llegar."
BUSINESS_COUNTRY = "República Dominicana"
BUSINESS_AIRPORT = "AILA/SDQ"  # Aeropuerto Internacional Las Américas
BUSINESS_WHATSAPP = os.getenv("BUSINESS_WHATSAPP_NUMBER", "+1809XXXXXXX")
BUSINESS_EMAIL = os.getenv("BUSINESS_EMAIL", "hola@emovils.com")
BUSINESS_TIMEZONE = "America/Santo_Domingo"

# ─────────────────────────────────────────────
# PILOTO 21 DÍAS — PARÁMETROS FINANCIEROS
# ─────────────────────────────────────────────
PILOT_TOTAL_BUDGET_USD = 100.0          # Presupuesto total del piloto
PILOT_DURATION_DAYS = 21               # Duración del piloto
PILOT_DAILY_BUDGET_USD = 4.0           # $100 / 21 días ≈ $4/día
PILOT_TARGET_CLIENTS = 17              # $100 / $6 CPA ≈ 16-17 clientes
PILOT_START_DATE = os.getenv("PILOT_START_DATE", "2026-05-10")

# ─────────────────────────────────────────────
# APIs EXTERNAS
# ─────────────────────────────────────────────
# WhatsApp Business API — GREEN API ✅ CONECTADO
WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL", "https://7107.api.greenapi.com")
WHATSAPP_INSTANCE_ID = os.getenv("WHATSAPP_INSTANCE_ID", "7107644324")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")  # No requerido por Green API

# Meta / Facebook Ads
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
META_PIXEL_ID = os.getenv("META_PIXEL_ID", "")

# Airtable (CRM + Dashboard) ✅ CONECTADO
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")
AIRTABLE_LEADS_TABLE = os.getenv("AIRTABLE_LEADS_TABLE", "Leads")
AIRTABLE_BOOKINGS_TABLE = os.getenv("AIRTABLE_BOOKINGS_TABLE", "Bookings")
AIRTABLE_CONVERSATIONS_TABLE = os.getenv("AIRTABLE_CONVERSATIONS_TABLE", "Conversations")
AIRTABLE_POSTS_TABLE = os.getenv("AIRTABLE_POSTS_TABLE", "Posts")
AIRTABLE_CAMPAIGNS_TABLE = os.getenv("AIRTABLE_CAMPAIGNS_TABLE", "Campaigns")
AIRTABLE_METRICS_TABLE = os.getenv("AIRTABLE_METRICS_TABLE", "Daily_Metrics")
# Aliases para compatibilidad
AIRTABLE_RESERVAS_TABLE = AIRTABLE_BOOKINGS_TABLE
AIRTABLE_METRICAS_TABLE = AIRTABLE_METRICS_TABLE
AIRTABLE_CONTENIDO_TABLE = AIRTABLE_POSTS_TABLE

# PayPal (Pagos digitales) ✅ CONECTADO — usado en RD en lugar de Stripe
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "live")  # "sandbox" o "live"

# Google Maps / Places
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# n8n Cloud (Automatización)
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "https://app.n8n.cloud")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")

# Claude AI (Agentes IA)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# ─────────────────────────────────────────────
# NOTIFICACIONES AL DUEÑO
# ─────────────────────────────────────────────
OWNER_WHATSAPP = os.getenv("OWNER_WHATSAPP", "")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "")
DAILY_REPORT_HOUR = 7    # 7:15 AM Santo Domingo
DAILY_REPORT_MINUTE = 15

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "logs/emovils.log")
