"""
Emovils OPC — Capa de Automatización Temporal (APScheduler)

Jobs programados (hora local de República Dominicana — America/Santo_Domingo):

  1. reporte_diario       — 07:15 todos los días
       generar_reporte_diario() → formato_whatsapp() → WhatsApp al dueño.
  2. followup_24h         — cada hora
       Reservas en estado BORRADOR con >24h sin confirmar → 1 follow-up
       WhatsApp máximo por lead (se marca en Notas para no hacer spam).
  3. cierre_quincena      — día 15 y último día del mes, 23:30
       cerrar_quincena() + reporte_liquidaciones_whatsapp() al dueño.
  4. alertas_documentos   — 08:00 todos los días
       Documentos_Vehiculos y Documentos_Conductores con Fecha_vencimiento
       en ≤15 días → alerta WhatsApp al dueño con la lista.
  5. plan_social_semanal  — lunes 07:00
       agente_social.planificar_semana() (cola de aprobación en Airtable)
       + resumen WhatsApp al dueño pidiendo aprobación.
  6. cpa_safeguard        — cada 6 horas
       Lee gasto/clientes de Metricas_Diarias, evalúa con config.safeguards
       (regla del $6 inviolable); si toca pausar → intenta pausar la campaña
       de Meta Ads (solo con META_ACCESS_TOKEN + META_CAMPAIGN_ID) y alerta.

DEGRADACIÓN ELEGANTE: todos los jobs están envueltos en try/except con
logging. Sin credenciales (Airtable, Green API, Meta) cada job hace skip
con log claro — NUNCA crashea el proceso.

═══════════════════════════════════════════════════════════════════════
IMPORTANTE — GUNICORN Y MÚLTIPLES WORKERS
═══════════════════════════════════════════════════════════════════════
El scheduler debe correr en UN SOLO proceso. Si gunicorn levanta varios
workers, cada worker importaría main_opc y arrancaría su propio scheduler
→ reportes y follow-ups DUPLICADOS.

Por eso:
  • El Procfile usa `--workers 1 --threads 16` (un proceso, concurrencia
    por threads — recomendado para este codebase).
  • El scheduler solo arranca si ENABLE_SCHEDULER=1 (default "1").
    En Railway: o se mantiene workers=1, o si se escala a más workers se
    debe poner ENABLE_SCHEDULER=0 en el servicio web y correr un servicio
    aparte (worker) con ENABLE_SCHEDULER=1.

ASUNCIÓN DEL FOLLOW-UP 24H (documentada porque el schema no tiene campo
"último contacto"): se usan las Reservas con Estado_reserva='BORRADOR'
(el estado "pendiente" del schema real) cuya fecha de creación
(Fecha_reserva, o CREATED_TIME() si está vacía) tiene más de 24 horas.
El envío se marca con el tag [FOLLOWUP_ENVIADO <timestamp>] en el campo
Notas de la reserva — registros con ese tag se excluyen del filtro, así
cada lead recibe UN solo follow-up.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timedelta

import pytz

logger = logging.getLogger(__name__)

TZ_RD = pytz.timezone("America/Santo_Domingo")

# Máximo de follow-ups por corrida horaria (protección anti-spam / rate limit)
MAX_FOLLOWUPS_POR_CORRIDA = 20
DIAS_ALERTA_DOCUMENTOS = 15
TAG_FOLLOWUP = "FOLLOWUP_ENVIADO"

_scheduler = None          # instancia única de BackgroundScheduler
_scheduler_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
# HELPERS COMUNES (degradación elegante)
# ─────────────────────────────────────────────────────────────

def _ahora_rd() -> datetime:
    return datetime.now(TZ_RD)


def _es_placeholder(valor: str) -> bool:
    """Detecta valores de ejemplo del .env (YOUR_..., XXXX...)."""
    v = (valor or "").strip()
    return (not v) or v.startswith("YOUR_") or "XXXX" in v


def _get_api():
    """Devuelve AirtableOPC() o None si faltan credenciales (skip + log)."""
    try:
        from opc.airtable_api_opc import AirtableOPC
        api_key = os.getenv("AIRTABLE_API_KEY", "")
        base_id = os.getenv("AIRTABLE_BASE_ID", "")
        if _es_placeholder(api_key) or _es_placeholder(base_id):
            logger.warning("⏭️  Scheduler: sin credenciales Airtable — job omitido")
            return None
        return AirtableOPC()
    except Exception as exc:
        logger.warning("⏭️  Scheduler: no pude instanciar AirtableOPC: %s", exc)
        return None


def _enviar_owner(mensaje: str) -> dict:
    """Envía un WhatsApp al dueño. Si OWNER_WHATSAPP no está, skip + log."""
    owner = os.getenv("OWNER_WHATSAPP", "").strip()
    if _es_placeholder(owner):
        logger.warning(
            "⏭️  OWNER_WHATSAPP no configurado — mensaje al dueño omitido: %s",
            mensaje[:80],
        )
        return {"enviado": False, "razon": "OWNER_WHATSAPP no configurado"}
    try:
        from opc.whatsapp_green_api import enviar_a_cliente
        resp = enviar_a_cliente(owner, mensaje)
        logger.info("📤 WhatsApp al dueño (%s): %s", owner, mensaje[:80])
        return {"enviado": True, "respuesta": resp}
    except Exception as exc:
        logger.error("❌ Fallo enviando WhatsApp al dueño: %s", exc)
        return {"enviado": False, "razon": str(exc)[:200]}


# ─────────────────────────────────────────────────────────────
# JOB 1 — REPORTE DIARIO (07:15)
# ─────────────────────────────────────────────────────────────

def job_reporte_diario() -> dict:
    """Genera el reporte diario y lo envía por WhatsApp al dueño."""
    try:
        api = _get_api()
        if api is None:
            return {"ok": False, "skip": "sin_credenciales_airtable"}

        from opc.agente_reportes import generar_reporte_diario, formato_whatsapp
        hoy = _ahora_rd().date()
        reporte = generar_reporte_diario(api, hoy=hoy)
        texto = formato_whatsapp(reporte)
        envio = _enviar_owner(texto)
        logger.info("✅ Job reporte_diario completado (%s)", hoy.isoformat())
        return {"ok": True, "fecha": hoy.isoformat(), "envio": envio}
    except Exception as exc:
        logger.error("❌ Job reporte_diario falló: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────
# JOB 2 — FOLLOW-UP 24H A LEADS PENDIENTES (cada hora)
# ─────────────────────────────────────────────────────────────

def job_followup_24h() -> dict:
    """
    Reservas BORRADOR con >24h sin confirmar → 1 follow-up WhatsApp por lead.

    ASUNCIÓN (el schema no tiene campo de "último contacto"): se usa
    Estado_reserva='BORRADOR' + antigüedad de Fecha_reserva (fallback a
    CREATED_TIME()). El envío se marca en Notas con [FOLLOWUP_ENVIADO ...]
    para que nunca se repita.
    """
    try:
        api = _get_api()
        if api is None:
            return {"ok": False, "skip": "sin_credenciales_airtable"}

        filtro = (
            "AND("
            "{Estado_reserva}='BORRADOR', "
            "DATETIME_DIFF(NOW(), IF({Fecha_reserva}, {Fecha_reserva}, CREATED_TIME()), 'hours') >= 24, "
            f"FIND('{TAG_FOLLOWUP}', {{Notas}} & '') = 0"
            ")"
        )
        reservas = api.listar("Reservas", filtro=filtro,
                              max_records=MAX_FOLLOWUPS_POR_CORRIDA)
        if not reservas:
            logger.info("✅ Job followup_24h: sin leads pendientes >24h")
            return {"ok": True, "enviados": 0}

        enviados = 0
        errores = 0
        for reserva in reservas:
            try:
                campos = reserva.get("fields", {})
                links_cliente = campos.get("Cliente_B2C") or []
                if not links_cliente:
                    continue
                cliente = api.obtener("Clientes_B2C", links_cliente[0])
                cf = cliente.get("fields", {})
                whatsapp = (cf.get("WhatsApp") or "").strip()
                if not whatsapp:
                    continue
                nombre = (cf.get("Nombre_completo") or "").split(" ")[0]
                destino = campos.get("Destino", "tu traslado")

                mensaje = (
                    f"¡Hola{' ' + nombre if nombre else ''}! 👋 Soy Monserrat de Emovils. "
                    f"Vi que ayer cotizaste {('tu viaje a ' + destino) if destino else 'un traslado'} "
                    "y quedó pendiente. ¿Te reservo el vehículo? "
                    "Respóndeme aquí y te confirmo en minutos. 🚖"
                )
                from opc.whatsapp_green_api import enviar_a_cliente
                enviar_a_cliente(whatsapp, mensaje)

                # Marcar para que NUNCA se repita el follow-up a este lead
                notas = campos.get("Notas", "") or ""
                marca = f"\n[{TAG_FOLLOWUP} {_ahora_rd().isoformat()}]"
                api.actualizar("Reservas", reserva["id"], {"Notas": notas + marca})
                enviados += 1
            except Exception as exc:
                errores += 1
                logger.warning("Follow-up falló para reserva %s: %s",
                               reserva.get("id"), exc)

        logger.info("✅ Job followup_24h: %s enviados, %s errores", enviados, errores)
        return {"ok": True, "enviados": enviados, "errores": errores}
    except Exception as exc:
        logger.error("❌ Job followup_24h falló: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────
# JOB 3 — CIERRE DE QUINCENA (día 15 y último día, 23:30)
# ─────────────────────────────────────────────────────────────

def job_cierre_quincena() -> dict:
    """Cierra la quincena y envía el resumen de liquidaciones al dueño."""
    try:
        api = _get_api()
        if api is None:
            return {"ok": False, "skip": "sin_credenciales_airtable"}

        from opc.agente_financiero import cerrar_quincena, reporte_liquidaciones_whatsapp
        fecha_corte = _ahora_rd().date()
        liquidaciones = cerrar_quincena(api, fecha_corte, dry_run=False)
        codigo = f"Q{1 if fecha_corte.day <= 15 else 2}_{fecha_corte.strftime('%b').upper()}"
        texto = reporte_liquidaciones_whatsapp(liquidaciones, codigo, fecha_corte.year)
        envio = _enviar_owner(texto)
        logger.info("✅ Job cierre_quincena %s: %s liquidaciones", codigo, len(liquidaciones))
        return {
            "ok": True,
            "quincena": codigo,
            "liquidaciones": len(liquidaciones),
            "envio": envio,
        }
    except Exception as exc:
        logger.error("❌ Job cierre_quincena falló: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────
# JOB 4 — ALERTAS DE DOCUMENTOS POR VENCER (08:00)
# ─────────────────────────────────────────────────────────────

def _documentos_por_vencer(api, tabla: str, campo_link: str, tabla_link: str,
                           campo_nombre: str) -> list[str]:
    """Lista líneas de alerta para documentos que vencen en ≤15 días."""
    filtro = (
        "AND("
        "{Fecha_vencimiento}, "
        f"DATETIME_DIFF({{Fecha_vencimiento}}, TODAY(), 'days') <= {DIAS_ALERTA_DOCUMENTOS}, "
        "DATETIME_DIFF({Fecha_vencimiento}, TODAY(), 'days') >= 0"
        ")"
    )
    lineas: list[str] = []
    for doc in api.listar(tabla, filtro=filtro):
        campos = doc.get("fields", {})
        tipo = campos.get("Tipo_documento", "DOCUMENTO")
        vence = campos.get("Fecha_vencimiento", "?")
        quien = ""
        try:
            links = campos.get(campo_link) or []
            if links:
                rec = api.obtener(tabla_link, links[0])
                quien = rec.get("fields", {}).get(campo_nombre, "")
        except Exception as exc:
            logger.debug("No pude resolver link %s: %s", campo_link, exc)
        lineas.append(f"  • {tipo} {('de ' + quien) if quien else ''} — vence {vence}")
    return lineas


def job_alertas_documentos() -> dict:
    """Escanea documentos de vehículos y conductores que vencen en ≤15 días."""
    try:
        api = _get_api()
        if api is None:
            return {"ok": False, "skip": "sin_credenciales_airtable"}

        lineas_veh = _documentos_por_vencer(
            api, "Documentos_Vehiculos", "Vehiculo", "Vehiculos", "Placa")
        lineas_con = _documentos_por_vencer(
            api, "Documentos_Conductores", "Conductor", "Conductores", "Nombre_completo")

        total = len(lineas_veh) + len(lineas_con)
        if total == 0:
            logger.info("✅ Job alertas_documentos: nada por vencer en %s días",
                        DIAS_ALERTA_DOCUMENTOS)
            return {"ok": True, "documentos_por_vencer": 0, "envio": None}

        partes = [f"⚠️ DOCUMENTOS POR VENCER (≤{DIAS_ALERTA_DOCUMENTOS} días)",
                  "━━━━━━━━━━━━━━━━━━━━━━━"]
        if lineas_veh:
            partes.append("🚐 Vehículos:")
            partes.extend(lineas_veh)
        if lineas_con:
            partes.append("👤 Conductores:")
            partes.extend(lineas_con)
        partes.append("")
        partes.append("Renueva a tiempo para evitar multas o vehículos parados.")

        envio = _enviar_owner("\n".join(partes))
        logger.info("✅ Job alertas_documentos: %s documentos por vencer", total)
        return {"ok": True, "documentos_por_vencer": total, "envio": envio}
    except Exception as exc:
        logger.error("❌ Job alertas_documentos falló: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────
# JOB 5 — PLAN SOCIAL SEMANAL (lunes 07:00)
# ─────────────────────────────────────────────────────────────

def job_plan_social_semanal() -> dict:
    """Genera el plan de 7 posts, lo encola para aprobación y avisa al dueño."""
    try:
        from opc.agente_social import planificar_semana
        resultado = planificar_semana(guardar_airtable=True)
        posts = resultado.get("posts", [])

        lineas = [
            "📅 PLAN SOCIAL DE LA SEMANA — pendiente de TU aprobación",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"Generado con: {resultado.get('generado_con', '?')} · "
            f"{resultado.get('total_posts', 0)} posts · "
            f"{resultado.get('guardados_airtable', 0)} en cola Airtable",
            "",
        ]
        for p in posts[:7]:
            lineas.append(f"  • {p.get('dia', '?')}: {p.get('tema', '')}")
        lineas.append("")
        lineas.append("Responde APROBAR PLAN para publicarlos, o dime cuáles cambiar.")

        envio = _enviar_owner("\n".join(lineas))
        logger.info("✅ Job plan_social_semanal: %s posts generados", len(posts))
        return {
            "ok": True,
            "total_posts": resultado.get("total_posts", 0),
            "guardados_airtable": resultado.get("guardados_airtable", 0),
            "modo": resultado.get("modo"),
            "envio": envio,
        }
    except Exception as exc:
        logger.error("❌ Job plan_social_semanal falló: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────
# JOB 6 — SAFEGUARD CPA (cada 6 horas) — EL $6 ES INVIOLABLE
# ─────────────────────────────────────────────────────────────

def _pausar_meta_ads() -> dict:
    """Intenta pausar la campaña de Meta Ads (solo con token + campaign id)."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    campaign_id = os.getenv("META_CAMPAIGN_ID", "")
    if _es_placeholder(token) or _es_placeholder(campaign_id):
        logger.warning(
            "⏭️  META_ACCESS_TOKEN/META_CAMPAIGN_ID no configurados — "
            "pausa de anuncios omitida (pausar manualmente en Ads Manager)")
        return {"pausado": False, "razon": "credenciales Meta no configuradas"}
    try:
        import requests
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{campaign_id}",
            data={"status": "PAUSED", "access_token": token},
            timeout=20,
        )
        if r.ok:
            logger.info("🛑 Campaña Meta %s PAUSADA por safeguard CPA", campaign_id)
            return {"pausado": True, "respuesta": r.json()}
        logger.error("❌ Meta no aceptó la pausa: %s %s", r.status_code, r.text[:200])
        return {"pausado": False, "razon": f"{r.status_code}: {r.text[:150]}"}
    except Exception as exc:
        logger.error("❌ Excepción pausando Meta Ads: %s", exc)
        return {"pausado": False, "razon": str(exc)[:200]}


def job_cpa_safeguard() -> dict:
    """
    Lee gasto y clientes nuevos acumulados de Metricas_Diarias, evalúa el CPA
    con config.safeguards y, si el estado exige pausar, intenta pausar la
    campaña de Meta Ads y alerta al dueño por WhatsApp.

    Campos de gasto/clientes leídos de forma defensiva (el schema base no
    incluye gasto de ads): Gasto_ads_USD / Gasto_USD y
    Clientes_nuevos / Leads_nuevos. Sin datos de gasto → skip con log.
    """
    try:
        api = _get_api()
        if api is None:
            return {"ok": False, "skip": "sin_credenciales_airtable"}

        registros = api.listar("Metricas_Diarias")
        if not registros:
            logger.info("⏭️  Job cpa_safeguard: Metricas_Diarias vacía — skip")
            return {"ok": True, "skip": "metricas_vacias"}

        gasto_total = 0.0
        clientes_total = 0
        for reg in registros:
            campos = reg.get("fields", {})
            gasto_total += float(
                campos.get("Gasto_ads_USD") or campos.get("Gasto_USD") or 0)
            clientes_total += int(
                campos.get("Clientes_nuevos") or campos.get("Leads_nuevos") or 0)

        if gasto_total <= 0:
            logger.info("⏭️  Job cpa_safeguard: sin datos de gasto en Metricas_Diarias — skip")
            return {"ok": True, "skip": "sin_datos_gasto"}

        from config.safeguards import CPAEvaluator
        evaluacion = CPAEvaluator().evaluate(
            total_spent=gasto_total, total_clients=clientes_total)

        resultado = {
            "ok": True,
            "gasto_usd": round(gasto_total, 2),
            "clientes": clientes_total,
            "cpa": evaluacion["cpa"],
            "estado": evaluacion["status"].value,
            "pausa": None,
        }

        if evaluacion["pause_ads"]:
            pausa = _pausar_meta_ads()
            resultado["pausa"] = pausa
            _enviar_owner(
                f"🔴 SAFEGUARD CPA — {evaluacion['message']}\n"
                f"Gasto: ${gasto_total:.2f} · Clientes: {clientes_total}\n"
                f"Anuncios: {'PAUSADOS automáticamente ✅' if pausa.get('pausado') else 'NO pude pausarlos — pausa MANUAL ya: ' + str(pausa.get('razon'))}\n"
                f"Acción: {evaluacion['action']}"
            )
        elif evaluacion["alert_owner"]:
            _enviar_owner(
                f"⚠️ SAFEGUARD CPA — {evaluacion['message']}\n"
                f"Acción sugerida: {evaluacion['action']}"
            )

        logger.info("✅ Job cpa_safeguard: CPA=%s estado=%s",
                    evaluacion["cpa"], evaluacion["status"].value)
        return resultado
    except Exception as exc:
        logger.error("❌ Job cpa_safeguard falló: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────
# REGISTRO DE JOBS + ARRANQUE DEL SCHEDULER
# ─────────────────────────────────────────────────────────────

# Registro central: el endpoint de ejecución manual usa este dict aunque
# el scheduler no esté corriendo (útil para probar en local/CI).
JOBS: dict[str, dict] = {
    "reporte_diario": {
        "func": job_reporte_diario,
        "descripcion": "Reporte diario al dueño por WhatsApp",
        "horario": "07:15 todos los días (RD)",
    },
    "followup_24h": {
        "func": job_followup_24h,
        "descripcion": "Follow-up a reservas BORRADOR con >24h (1 por lead)",
        "horario": "Cada hora",
    },
    "cierre_quincena": {
        "func": job_cierre_quincena,
        "descripcion": "Cierre de quincena + liquidaciones al dueño",
        "horario": "Día 15 y último día del mes, 23:30 (RD)",
    },
    "alertas_documentos": {
        "func": job_alertas_documentos,
        "descripcion": "Documentos de vehículos/conductores por vencer (≤15 días)",
        "horario": "08:00 todos los días (RD)",
    },
    "plan_social_semanal": {
        "func": job_plan_social_semanal,
        "descripcion": "Plan social de 7 posts + aprobación del dueño",
        "horario": "Lunes 07:00 (RD)",
    },
    "cpa_safeguard": {
        "func": job_cpa_safeguard,
        "descripcion": "Evalúa CPA ($6 inviolable); pausa Meta Ads si toca",
        "horario": "Cada 6 horas",
    },
}


def iniciar_scheduler():
    """
    Arranca el BackgroundScheduler (singleton, thread-safe).

    Llamar SOLO desde un proceso (ver nota gunicorn arriba). Devuelve la
    instancia, o None si APScheduler no está instalado (degradación elegante).
    """
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            logger.info("Scheduler ya estaba iniciado — no se duplica")
            return _scheduler
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError as exc:
            logger.warning(
                "⏭️  APScheduler no instalado (%s) — scheduler desactivado. "
                "pip install apscheduler", exc)
            return None

        try:
            sched = BackgroundScheduler(
                timezone=TZ_RD,
                job_defaults={
                    "coalesce": True,        # si se acumulan corridas, una sola
                    "max_instances": 1,      # nunca dos instancias del mismo job
                    "misfire_grace_time": 3600,
                },
            )
            # OJO: el timezone se pasa a CADA trigger explícitamente —
            # APScheduler resuelve el tz del trigger al construirlo (no
            # hereda el del scheduler), y el servidor puede estar en UTC.
            sched.add_job(job_reporte_diario,
                          CronTrigger(hour=7, minute=15, timezone=TZ_RD),
                          id="reporte_diario", name=JOBS["reporte_diario"]["descripcion"])
            sched.add_job(job_followup_24h, IntervalTrigger(hours=1, timezone=TZ_RD),
                          id="followup_24h", name=JOBS["followup_24h"]["descripcion"])
            # day='15,last' → día 15 y último día de cada mes
            sched.add_job(job_cierre_quincena,
                          CronTrigger(day="15,last", hour=23, minute=30, timezone=TZ_RD),
                          id="cierre_quincena", name=JOBS["cierre_quincena"]["descripcion"])
            sched.add_job(job_alertas_documentos,
                          CronTrigger(hour=8, minute=0, timezone=TZ_RD),
                          id="alertas_documentos", name=JOBS["alertas_documentos"]["descripcion"])
            sched.add_job(job_plan_social_semanal,
                          CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=TZ_RD),
                          id="plan_social_semanal", name=JOBS["plan_social_semanal"]["descripcion"])
            sched.add_job(job_cpa_safeguard, IntervalTrigger(hours=6, timezone=TZ_RD),
                          id="cpa_safeguard", name=JOBS["cpa_safeguard"]["descripcion"])

            sched.start()
            _scheduler = sched
            logger.info("⏰ Scheduler OPC iniciado — %s jobs registrados (tz=%s)",
                        len(sched.get_jobs()), TZ_RD)
            return _scheduler
        except Exception as exc:
            logger.error("❌ No pude iniciar el scheduler: %s", exc)
            return None


def obtener_scheduler():
    """Devuelve la instancia activa del scheduler (o None)."""
    return _scheduler


def listar_jobs() -> dict:
    """Estado de los jobs: registrados + próxima ejecución si el scheduler corre."""
    activo = _scheduler is not None and getattr(_scheduler, "running", False)
    proximas: dict[str, str | None] = {}
    if activo:
        for job in _scheduler.get_jobs():
            nrt = getattr(job, "next_run_time", None)
            proximas[job.id] = nrt.isoformat() if nrt else None

    jobs = []
    for job_id, info in JOBS.items():
        jobs.append({
            "job_id": job_id,
            "descripcion": info["descripcion"],
            "horario": info["horario"],
            "proxima_ejecucion": proximas.get(job_id),
        })
    return {
        "scheduler_activo": activo,
        "enable_scheduler": os.getenv("ENABLE_SCHEDULER", "1"),
        "timezone": str(TZ_RD),
        "hora_actual_rd": _ahora_rd().isoformat(),
        "total_jobs": len(jobs),
        "jobs": jobs,
    }


def ejecutar_job(job_id: str) -> dict | None:
    """
    Ejecuta un job manualmente (síncrono) y devuelve su resumen.
    Devuelve None si el job_id no existe. Funciona aunque el scheduler
    esté apagado — útil para pruebas.
    """
    info = JOBS.get(job_id)
    if info is None:
        return None
    logger.info("▶️  Ejecución manual del job '%s'", job_id)
    inicio = _ahora_rd()
    try:
        resultado = info["func"]()
    except Exception as exc:  # los jobs ya capturan, esto es cinturón extra
        logger.error("❌ Ejecución manual de '%s' falló: %s", job_id, exc)
        resultado = {"ok": False, "error": str(exc)[:300]}
    return {
        "job_id": job_id,
        "ejecutado_en": inicio.isoformat(),
        "duracion_seg": round((_ahora_rd() - inicio).total_seconds(), 2),
        "resultado": resultado,
    }
