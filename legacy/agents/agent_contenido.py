"""
Emovils OPC — Agente 2: Creador de Contenido
Responsabilidad: Crea publicaciones, reels, carruseles, textos,
mensajes y piezas visuales listas para publicar.
"""
import anthropic
import logging
from datetime import datetime, timedelta
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from config.products import get_active_product

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Eres el Creador de Contenido de Emovils OPC, empresa de movilidad privada en República Dominicana.

IDENTIDAD DE MARCA:
- Nombre: Emovils OPC
- Tagline: "No vendemos traslados. Vendemos certeza al llegar."
- Producto principal: Emovils Airport — traslados privados desde/hacia AILA/SDQ
- Precio: USD $25 sencillo
- Tono: Profesional, cálido, confiable, sin exageraciones
- Idioma: Español dominicano con toques de familiaridad

AUDIENCIA PRINCIPAL:
- Dominicanos en la diáspora (USA, España) que envían a sus familias o viajan
- Turistas que llegan a Santo Domingo
- Ejecutivos y familias que necesitan certeza

LO QUE CREAS:
1. Posts para Instagram/Facebook (carruseles, imágenes estáticas)
2. Scripts para Reels/TikTok (menos de 60 segundos)
3. Textos de anuncios Meta (copy + CTA hacia WhatsApp)
4. Mensajes de reactivación para clientes anteriores
5. Mensajes para aliados (hoteles, Airbnb hosts, clínicas)
6. Respuestas de seguimiento si el cliente no contestó

PRINCIPIOS:
- Cada pieza termina con un CTA claro hacia WhatsApp
- Nunca prometes lo que no puedes cumplir
- Ángulos que funcionan: certeza, seguridad, familia, precio claro, no improvisar
- NO hablas de "transporte general". Hablas de situaciones específicas.
- Métrica que importa: mensajes recibidos por WhatsApp, no likes

Responde siempre en español. Entrega el contenido listo para copiar y publicar."""


def create_instagram_post(
    hook: str = "seguridad",
    product: str = "airport",
    format_type: str = "carrusel"
) -> str:
    """Crea un post de Instagram listo para publicar."""
    hooks = {
        "seguridad": "Evitar negociar transporte al salir cansado del aeropuerto",
        "familia": "Viajar con niños o adultos mayores y necesitar certeza",
        "cansancio": "Llegar después de un vuelo largo y que todo esté listo",
        "precio_claro": "Saber el precio antes de aterrizar, sin sorpresas",
        "ejecutivo": "Traslado privado puntual para viajeros de negocios"
    }

    prompt = f"""
    Crea un post de Instagram para Emovils Airport.
    Ángulo: {hooks.get(hook, hooks['seguridad'])}
    Formato: {format_type}

    Para carrusel: entrega 5-6 slides con texto corto cada uno.
    Para imagen estática: 1 texto principal + caption para la publicación.

    Incluye:
    - Hook (primera línea que detiene el scroll)
    - Propuesta de valor clara
    - CTA específico: "Escríbenos al WhatsApp" o "Reserva antes de llegar"
    - 5 hashtags relevantes en español/inglés

    El formato debe estar listo para copiar y pegar al publicar.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def create_reel_script(angle: str = "familia") -> str:
    """Crea un guion de Reel/TikTok de 30-60 segundos."""
    prompt = f"""
    Crea un guion para un Reel de Instagram (30-60 segundos) para Emovils Airport.
    Ángulo: {angle}

    Estructura:
    - Segundos 0-3: Hook visual y verbal (¿qué dice la persona en cámara?)
    - Segundos 4-20: El problema/situación relatable
    - Segundos 21-40: La solución Emovils
    - Segundos 41-60: CTA y cierre

    Incluye:
    - Lo que dice la persona (texto exacto)
    - Lo que muestra la cámara (descripción visual)
    - Texto de overlay/subtítulos
    - Música sugerida (género/mood)
    - CTA final

    Tono: natural, como si un amigo te recomendara el servicio.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def create_reactivation_message(context: str = "cliente anterior") -> str:
    """Crea mensajes de reactivación para clientes que ya usaron el servicio."""
    prompt = f"""
    Crea un mensaje de WhatsApp para reactivar a un cliente que ya usó Emovils antes.
    Contexto: {context}

    El mensaje debe:
    - Ser corto (máximo 3-4 líneas)
    - Recordarles que existen sin ser molesto
    - Mencionar algo específico del valor del servicio
    - Tener una razón para escribir hoy (sin inventar falsas urgencias)
    - Terminar con pregunta abierta o CTA suave

    Dame 3 variaciones del mensaje.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def create_alliance_message(ally_type: str = "airbnb_host") -> str:
    """Crea mensaje de acercamiento para aliados (hoteles, Airbnb hosts, etc.)."""
    context_map = {
        "airbnb_host": "Anfitriones de Airbnb en Santo Domingo que reciben turistas extranjeros",
        "hotel_pequeno": "Hoteles pequeños o boutique que no tienen servicio de traslado propio",
        "clinica": "Clínicas y centros médicos para pacientes que necesitan transporte",
        "empresa": "Empresas que tienen empleados o clientes que viajan con frecuencia"
    }

    prompt = f"""
    Crea un mensaje de primer acercamiento para una alianza con: {context_map.get(ally_type, ally_type)}

    El mensaje debe:
    - Ser breve (máximo 5-6 líneas)
    - Proponer una alianza concreta (no vaga)
    - Explicar qué gana el aliado (comisión, valor para sus clientes)
    - No sonar a spam ni a vendedor agresivo
    - Terminar pidiendo una llamada o reunión de 15 minutos

    Incluye versión para WhatsApp y versión para email.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def create_7day_content_calendar() -> str:
    """Crea el calendario de contenido para los próximos 7 días."""
    today = datetime.now()

    prompt = f"""
    Crea un calendario de contenido de 7 días para Emovils Airport.
    Fecha inicio: {today.strftime('%d/%m/%Y')}

    Para cada día incluye:
    - Plataforma (Instagram, Facebook o ambas)
    - Tipo de contenido (post, reel, story, repost)
    - Ángulo/tema del día
    - Hora de publicación recomendada
    - Objetivo (visibilidad, conversión, engagement)

    La semana debe tener variedad de ángulos:
    - Lunes: Seguridad/certeza
    - Martes: Familia
    - Miércoles: Ejecutivos/negocios
    - Jueves: Precio claro/transparencia
    - Viernes: Experiencia del cliente
    - Sábado: Social proof/testimonial
    - Domingo: Reactivación de clientes anteriores

    Formato: tabla clara y lista para seguir.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def create_meta_ad_copy(objective: str = "mensajes_whatsapp") -> str:
    """Crea el copy para un anuncio de Meta Ads."""
    prompt = f"""
    Crea copy para un anuncio de Meta (Facebook/Instagram) para Emovils Airport.
    Objetivo: {objective}
    Destino: WhatsApp para cotización

    Entrega 3 variaciones:
    VARIACIÓN A — Ángulo Seguridad:
    - Headline (máx 30 caracteres):
    - Texto principal (máx 125 caracteres):
    - Descripción (máx 25 caracteres):
    - CTA button: "Enviar mensaje"

    VARIACIÓN B — Ángulo Familia:
    (misma estructura)

    VARIACIÓN C — Ángulo Precio Claro:
    (misma estructura)

    Para cada variación incluye también qué imagen/video recomendarías mostrar.
    """

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=900,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text
