"""
Emovils OPC — Agente de Leads Diario ("cazador de clientes")

QUÉ HACE (en palabras simples): todos los días busca empresas del nicho de
Emovils (hoteles, call centers, tour operadores, eventos...), les consigue el
correo, les escribe el primer email de venta, les da seguimiento a los 3 días
si no respondieron, y le avisa a Noe por WhatsApp qué consiguió. No para de
buscar (rota de nicho en nicho) hasta llegar a la META del día (5 leads).

CÓMO SE ENTERA NOE: (1) resumen por WhatsApp cada vez que hay leads nuevos,
(2) tabla Pipeline_Comercial en Airtable con el estado de cada empresa,
(3) las respuestas llegan a ventas@emovils.com.

DECISIONES DE SEGURIDAD (deliberadas):
  • NO se envía WhatsApp frío a desconocidos: WhatsApp banea números que lo
    hacen y el número de Monserrat es el canal de reservas del negocio.
    El outreach frío va SOLO por email (con pie de baja voluntaria).
  • Nunca se contacta 2 veces a la misma empresa como "nueva": el filtro es
    por Empresa_nombre en Pipeline_Comercial (histórico completo).
  • Máximo META_DIARIA correos fríos al día y MAX_FOLLOWUPS seguimientos:
    volumen bajo = buena reputación del dominio emovils.com.

DEGRADACIÓN ELEGANTE (mismo patrón que el resto del sistema):
  • Sin APIFY_API_TOKEN → usa el catálogo semilla RD (modo mock).
  • Sin RESEND_API_KEY/EMAIL_SMTP_PASS → el correo queda PREPARADO, no enviado.
  • Sin Airtable → cuenta simulada, nada persiste, nunca crashea.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

import pytz

from opc.agente_prospector import (
    buscar_prospectos,
    enriquecer_prospecto,
    generar_email_outreach,
)

logger = logging.getLogger(__name__)

TZ_RD = pytz.timezone("America/Santo_Domingo")

# Meta de leads NUEVOS contactados por día (ajustable por env var).
META_DIARIA = int(os.getenv("LEADS_META_DIARIA", "5") or "5")
# Seguimiento: a los N días sin respuesta, UN solo recordatorio por lead.
DIAS_PARA_FOLLOWUP = int(os.getenv("LEADS_DIAS_FOLLOWUP", "3") or "3")
MAX_FOLLOWUPS_POR_CICLO = 10
# Tags en Notas (mismo truco que el follow-up de reservas del scheduler).
TAG_OUTREACH = "OUTREACH_ENVIADO"
TAG_FOLLOWUP = "FOLLOWUP_LEAD_ENVIADO"

# Pie legal/ético de todo correo frío: baja voluntaria en una línea.
PIE_OPTOUT = (
    "\n\n—\nSi prefieren no recibir más correos de Emovils, respondan "
    "\"BAJA\" a este mensaje y los removemos de inmediato."
)

# ─────────────────────────────────────────────────────────────
# ROTACIÓN DE BÚSQUEDAS — el "sigue buscando todo el día"
# Cada ciclo arranca en una búsqueda distinta (según el día) y va
# rotando de nicho/ciudad hasta juntar la meta. Así una misma
# búsqueda no se agota en una semana.
# ─────────────────────────────────────────────────────────────
BUSQUEDAS_ROTACION: list[dict] = [
    {"fuente": "call_centers", "busqueda": "call center BPO Santo Domingo República Dominicana"},
    {"fuente": "hoteles", "busqueda": "hotel 5 estrellas Punta Cana Bávaro República Dominicana"},
    {"fuente": "google_maps", "busqueda": "tour operador excursiones Punta Cana República Dominicana"},
    {"fuente": "google_maps", "busqueda": "agencia de viajes Santo Domingo República Dominicana"},
    {"fuente": "google_maps", "busqueda": "organizador de eventos y bodas Santo Domingo"},
    {"fuente": "hoteles", "busqueda": "hotel boutique Zona Colonial Santo Domingo"},
    {"fuente": "google_maps", "busqueda": "empresa zona franca San Pedro de Macorís República Dominicana"},
    {"fuente": "google_maps", "busqueda": "clínica privada Santo Domingo República Dominicana"},
    {"fuente": "hoteles", "busqueda": "resort hotel La Romana Bayahibe República Dominicana"},
    {"fuente": "google_maps", "busqueda": "DMC receptivo turismo Punta Cana República Dominicana"},
    {"fuente": "google_maps", "busqueda": "torre empresarial oficinas Piantini Naco Santo Domingo"},
    {"fuente": "google_maps", "busqueda": "escuela internacional colegio bilingüe Santo Domingo"},
]


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_api():
    """AirtableOPC() o None si faltan credenciales (modo mock)."""
    try:
        api_key = os.getenv("AIRTABLE_API_KEY", "")
        base_id = os.getenv("AIRTABLE_BASE_ID", "")
        if not api_key or not base_id or api_key.startswith("YOUR_"):
            return None
        from opc.airtable_api_opc import AirtableOPC
        return AirtableOPC()
    except Exception as exc:
        logger.warning("Leads: sin Airtable (%s) — modo mock", exc)
        return None


def _ahora() -> datetime:
    return datetime.now(TZ_RD)


def contar_contactados_hoy(api) -> int:
    """Cuántos leads nuevos ya se contactaron HOY (para respetar la meta)."""
    if not api:
        return 0
    try:
        hoy = _ahora().strftime("%Y-%m-%d")
        regs = api.listar(
            "Pipeline_Comercial",
            filtro=(
                "AND({Estado_pipeline}='CONTACTADO', "
                f"DATETIME_FORMAT({{Fecha_primer_contacto}}, 'YYYY-MM-DD')='{hoy}')"
            ),
            campos=["Empresa_nombre"],
        )
        return len(regs)
    except Exception as exc:
        logger.warning("Leads: no pude contar contactados de hoy: %s", exc)
        return 0


def _dominio_de(url: str) -> str:
    if not url:
        return ""
    d = re.sub(r"^https?://(www\.)?", "", url.strip().lower()).split("/")[0]
    # Descarta redes sociales / agregadores: su buzón no es de la empresa.
    if any(s in d for s in ("facebook.", "instagram.", "tripadvisor.", "google.", "linktr.ee")):
        return ""
    return d


def _email_para_envio(p: dict) -> str:
    """Devuelve un email al que SÍ vale la pena escribir, o "" para saltar.

    Prioridad: (1) email real que trajo el scraping/Apollo; (2) info@<dominio
    del sitio web real de la empresa>. Se descarta el email "adivinado" por el
    enriquecimiento mock (info@<slug>.com.do) porque rebotaría y quemaría la
    reputación del dominio de Emovils.
    """
    email = (p.get("email") or "").replace("mailto:", "").strip().lower()
    dominio_sitio = _dominio_de(p.get("sitio_web", ""))
    if email and "@" in email:
        dominio_email = email.split("@", 1)[1]
        # Email del propio sitio de la empresa, o enriquecido con Apollo → confiable.
        if p.get("enriquecido_con") == "apollo" or not dominio_email.endswith(".com.do") \
                or (dominio_sitio and dominio_email == dominio_sitio):
            # El patrón mock es info@<slugpegado>.com.do sin sitio que lo respalde.
            slug_mock = re.sub(r"[^a-z0-9]+", "", (p.get("nombre_empresa") or "").lower())[:30]
            if email == f"info@{slug_mock}.com.do" and dominio_sitio != f"{slug_mock}.com.do":
                email = ""
            if email:
                return email
    if dominio_sitio:
        return f"info@{dominio_sitio}"
    return ""


def _enviar_correo(para: str, asunto: str, cuerpo: str) -> bool:
    """Envía por el buzón de ventas (Resend → SMTP → mock=False)."""
    try:
        from opc.agente_email_router import EmailSaliente, enviar_email
        return enviar_email(EmailSaliente(
            desde_buzon="ventas",
            para=para,
            asunto=asunto,
            cuerpo_texto=cuerpo + PIE_OPTOUT,
        ))
    except Exception as exc:
        logger.warning("Leads: envío de correo falló (%s)", exc)
        return False


def _registrar_email_campana(api, prospect_record_id: str, asunto: str,
                             cuerpo: str, enviado: bool) -> None:
    """Deja constancia del correo en Email_Campañas (historial auditable)."""
    if not api:
        return
    try:
        campos = {
            "Campaña": "Leads_Diario",
            "Asunto": asunto,
            "Cuerpo": cuerpo,
            "Fecha_envio": _ahora().isoformat(timespec="seconds"),
            "Estado": "ENVIADO" if enviado else "PROGRAMADO",
            "Plataforma": "Manual",
        }
        if prospect_record_id:
            campos["Prospect"] = [prospect_record_id]
        api.crear_registro("Email_Campañas", campos)
    except Exception as exc:
        logger.warning("Leads: no pude registrar Email_Campañas: %s", exc)


def _avisar_owner(mensaje: str) -> dict:
    """Resumen al WhatsApp de Noe (reusa el helper del scheduler)."""
    try:
        from opc.scheduler import _enviar_owner
        return _enviar_owner(mensaje)
    except Exception as exc:
        logger.warning("Leads: no pude avisar al dueño: %s", exc)
        return {"enviado": False, "razon": str(exc)[:120]}


# ─────────────────────────────────────────────────────────────
# SEGUIMIENTOS (follow-up a los 3 días, UNO por lead)
# ─────────────────────────────────────────────────────────────

def _cuerpo_followup(nombre_empresa: str) -> tuple[str, str]:
    asunto = f"¿Lo vieron? Transporte ejecutivo para {nombre_empresa}"
    cuerpo = (
        f"Hola equipo de {nombre_empresa},\n\n"
        "Hace unos días les escribí sobre nuestro servicio de transporte "
        "ejecutivo en RD (14 años de operación, flota propia de vans con "
        "choferes verificados).\n\n"
        "Sé que están ocupados — solo quería dejar el tema arriba en su "
        "bandeja. Si les interesa, respondan este correo o escríbannos al "
        "WhatsApp 829-861-0090 y coordinamos 15 minutos.\n\n"
        "Si no es el momento, no hay problema: no volveremos a insistir.\n\n"
        "Saludos,\nNoe Vásquez · Emovils · emovils.com"
    )
    return asunto, cuerpo


def enviar_followups(api, max_envios: int = MAX_FOLLOWUPS_POR_CICLO) -> dict:
    """Busca leads CONTACTADOS hace ≥N días sin respuesta ni follow-up previo
    y les manda UN recordatorio. Marca el tag en Notas para nunca repetir."""
    if not api:
        return {"modo": "mock", "enviados": 0}
    limite = (_ahora() - timedelta(days=DIAS_PARA_FOLLOWUP)).isoformat(timespec="seconds")
    try:
        regs = api.listar(
            "Pipeline_Comercial",
            filtro=(
                "AND({Estado_pipeline}='CONTACTADO', "
                f"{{Fecha_primer_contacto}}<='{limite}', "
                f"FIND('{TAG_FOLLOWUP}', {{Notas}}&'')=0, "
                "{Email}!='')"
            ),
            max_records=max_envios,
        )
    except Exception as exc:
        logger.warning("Leads: no pude listar candidatos a follow-up: %s", exc)
        return {"modo": "real", "enviados": 0, "error": str(exc)[:120]}

    enviados = 0
    for r in regs:
        f = r.get("fields", {})
        empresa = f.get("Empresa_nombre", "")
        email = f.get("Email", "")
        if not email:
            continue
        asunto, cuerpo = _cuerpo_followup(empresa)
        ok = _enviar_correo(email, asunto, cuerpo)
        try:
            api.actualizar("Pipeline_Comercial", r["id"], {
                "Fecha_ultimo_contacto": _ahora().isoformat(timespec="seconds"),
                "Notas": (f.get("Notas", "") +
                          f"\n[{TAG_FOLLOWUP} {_ahora().strftime('%Y-%m-%d %H:%M')}"
                          f" {'ok' if ok else 'preparado'}]"),
            })
        except Exception as exc:
            logger.warning("Leads: no pude marcar follow-up de %s: %s", empresa, exc)
        _registrar_email_campana(api, r["id"], asunto, cuerpo + PIE_OPTOUT, ok)
        if ok:
            enviados += 1
    return {"modo": "real", "candidatos": len(regs), "enviados": enviados}


# ─────────────────────────────────────────────────────────────
# CICLO PRINCIPAL — buscar → filtrar → escribir → avisar
# ─────────────────────────────────────────────────────────────

def ejecutar_ciclo_leads(
    meta: int | None = None,
    max_busquedas: int = 3,
    max_por_busqueda: int = 12,
    enviar_correos: bool = True,
    avisar_owner: bool = True,
) -> dict:
    """Un ciclo del cazador de leads. El scheduler lo corre varias veces al
    día; cada ciclo revisa cuánto falta para la meta y busca solo eso.

    Devuelve un dict con: modo, meta, contactados_antes, nuevos (enviados),
    preparados (sin credenciales de correo), followups, aviso_whatsapp.
    """
    api = _get_api()
    meta = int(meta or META_DIARIA)
    ya_hoy = contar_contactados_hoy(api)

    resultado: dict = {
        "modo": "real" if api else "mock",
        "meta_diaria": meta,
        "contactados_antes_del_ciclo": ya_hoy,
        "nuevos": [],
        "preparados": [],
        "busquedas_usadas": [],
    }

    if ya_hoy >= meta:
        resultado["meta_cumplida"] = True
        # Aun con la meta cumplida, los follow-ups del día sí corren.
        resultado["followups"] = enviar_followups(api)
        return resultado

    # Rotación: el punto de partida cambia cada día (día del año) para no
    # machacar siempre la misma búsqueda.
    idx0 = _ahora().timetuple().tm_yday
    vistos_en_ciclo: set[str] = set()
    intentos = 0

    for i in range(len(BUSQUEDAS_ROTACION)):
        if ya_hoy + len(resultado["nuevos"]) >= meta or intentos >= max_busquedas:
            break
        q = BUSQUEDAS_ROTACION[(idx0 + i) % len(BUSQUEDAS_ROTACION)]
        intentos += 1
        resultado["busquedas_usadas"].append(q["busqueda"])

        try:
            res = buscar_prospectos(
                fuente=q.get("fuente", "google_maps"),
                busqueda=q["busqueda"],
                max_resultados=max_por_busqueda,
            )
        except Exception as exc:
            logger.warning("Leads: búsqueda falló (%s): %s", q["busqueda"], exc)
            continue

        for prospecto in res.get("prospectos", []):
            if ya_hoy + len(resultado["nuevos"]) >= meta:
                break
            p = enriquecer_prospecto(prospecto)
            nombre = (p.get("nombre_empresa") or "").strip()
            if not nombre or nombre.lower() in vistos_en_ciclo:
                continue
            vistos_en_ciclo.add(nombre.lower())

            # Dedupe histórico: nunca contactar 2 veces la misma empresa.
            try:
                if api and api.existe("Pipeline_Comercial", "Empresa_nombre", nombre):
                    continue
            except Exception as exc:
                logger.warning("Leads: dedupe falló para %s: %s", nombre, exc)

            email = _email_para_envio(p)
            if not email:
                continue  # sin correo confiable no se dispara nada

            correo = generar_email_outreach(p)
            enviado = _enviar_correo(email, correo["asunto"], correo["cuerpo"]) \
                if enviar_correos else False

            ahora_iso = _ahora().isoformat(timespec="seconds")
            record_id = ""
            if api:
                try:
                    reg = api.crear_registro("Pipeline_Comercial", {
                        "Empresa_nombre": nombre,
                        "Tipo_empresa": p.get("tipo_empresa", "Corporativo_Otro"),
                        "Fuente_scraping": p.get("fuente_scraping", "Manual")
                            if p.get("fuente_scraping") in (
                                "Apify_Google_Maps", "Apify_LinkedIn", "Apollo",
                                "Manual", "Referido_Cliente") else "Manual",
                        "Contacto_principal": p.get("contacto_principal", ""),
                        "Cargo": p.get("cargo", ""),
                        "Email": email,
                        "LinkedIn_url": p.get("linkedin_url", ""),
                        "WhatsApp": p.get("whatsapp", ""),
                        "Estado_pipeline": "CONTACTADO" if enviado else "NUEVO",
                        "Score_calificacion": int(p.get("score", 50)),
                        "Fecha_primer_contacto": ahora_iso if enviado else None,
                        "Notas": (
                            f"{p.get('razon_potencial', '')} · Ciudad: {p.get('ciudad', '')}"
                            f" · {p.get('notas', '')}"
                            + (f"\n[{TAG_OUTREACH} {_ahora().strftime('%Y-%m-%d %H:%M')}]"
                               if enviado else "\n[CORREO PREPARADO — faltan credenciales de envío]")
                        ),
                    })
                    record_id = reg.get("id", "") if isinstance(reg, dict) else ""
                except Exception as exc:
                    logger.error("Leads: no pude guardar %s en Airtable: %s", nombre, exc)
                _registrar_email_campana(api, record_id, correo["asunto"],
                                         correo["cuerpo"] + PIE_OPTOUT, enviado)

            ficha = {
                "empresa": nombre,
                "tipo": p.get("tipo_empresa", ""),
                "email": email,
                "ciudad": p.get("ciudad", ""),
                "asunto": correo["asunto"],
            }
            if enviado:
                resultado["nuevos"].append(ficha)
            else:
                resultado["preparados"].append(ficha)
                # Sin credenciales de correo, los "preparados" también cuentan
                # para cerrar el ciclo (evita bucle infinito en modo mock).
                if not enviar_correos or resultado["modo"] == "mock" or \
                        not (os.getenv("RESEND_API_KEY") or os.getenv("EMAIL_SMTP_PASS")):
                    ya_hoy += 1

    resultado["followups"] = enviar_followups(api)
    total_hoy = resultado["contactados_antes_del_ciclo"] + len(resultado["nuevos"])
    resultado["contactados_total_hoy"] = total_hoy
    resultado["meta_cumplida"] = total_hoy >= meta

    # Aviso a Noe solo si hubo actividad (no spamearlo con ciclos vacíos).
    if avisar_owner and (resultado["nuevos"] or resultado["preparados"]):
        lineas = [f"🎯 *Leads Emovils* — {total_hoy}/{meta} de hoy"]
        for l in resultado["nuevos"]:
            lineas.append(f"✉️ {l['empresa']} ({l['tipo']}, {l['ciudad']}) → {l['email']}")
        for l in resultado["preparados"]:
            lineas.append(f"📝 {l['empresa']} — correo PREPARADO (faltan credenciales de envío)")
        fw = resultado["followups"].get("enviados", 0)
        if fw:
            lineas.append(f"🔁 {fw} seguimiento(s) enviados a leads de hace {DIAS_PARA_FOLLOWUP}+ días")
        lineas.append("Las respuestas llegan a ventas@emovils.com — te aviso cualquier movimiento.")
        resultado["aviso_whatsapp"] = _avisar_owner("\n".join(lineas))

    return resultado
