"""
Emovils OPC — Servidor Flask v2 (motor real)

Endpoints nuevos para el sistema OPC:
  /api/v2/cotizar           — Cotización rápida
  /api/v2/reservar          — Crear reserva + asignar conductor
  /api/v2/whatsapp/webhook  — Recibe mensajes de Green API
  /api/v2/qr/validar        — Valida QR escaneado (cliente o vehículo)
  /api/v2/intelcia/ingestar — Procesa Excel de Intelcia
  /api/v2/reporte/diario    — Genera reporte para el dueño
  /api/v2/liquidaciones/quincena — Cierra quincena
  /api/v2/safeguards/cpa    — Evalúa CPA (regla del $6 inviolable)
  /api/v2/social/planificar — Plan semanal de contenido (7 posts)
  /api/v2/social/publicar   — Publica post aprobado (Meta Graph API)
  /api/v2/prospeccion/buscar — Pipeline de prospección B2B
  /api/v2/ncf/emitir        — Emite factura NCF (DGII)
  /api/v2/scheduler/status  — Jobs programados + próximas ejecuciones
  /api/v2/scheduler/run/<id> — Dispara un job manualmente (pruebas)
  /health                   — Healthcheck
"""
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _cargar_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


_cargar_env()
sys.path.insert(0, str(ROOT))

from datetime import date, datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from opc.agente_coordinador import (
    procesar_mensaje_entrante,
    crear_reserva_y_asignar,
    extraer_datos_cotizacion,
    generar_cotizacion,
)
from opc.agente_ingesta import procesar_excel_intelcia
from opc.agente_financiero import cerrar_quincena, reporte_liquidaciones_whatsapp
from opc.agente_reportes import generar_reporte_diario, formato_whatsapp
from opc.qr_generator_opc import (
    parsear_payload,
    verificar_qr_cliente_para_chofer,
    verificar_qr_vehiculo_para_cliente,
)
from opc.airtable_api_opc import AirtableOPC
from opc.agente_social import planificar_semana, publicar_post, procesar_aprobacion
from opc.agente_prospector import ejecutar_pipeline_prospeccion
from opc.sistema_ncf import generar_factura
from opc.scheduler import iniciar_scheduler, listar_jobs, ejecutar_job


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ═══════════════════════════════════════════════════════════════
# SCHEDULER (automatización temporal — opc/scheduler.py)
#
# IMPORTANTE: el scheduler debe correr en UN SOLO proceso. El Procfile
# usa `--workers 1 --threads 16` justamente por esto. Si en Railway se
# escala a más workers, poner ENABLE_SCHEDULER=0 en el servicio web y
# correr un servicio worker aparte con ENABLE_SCHEDULER=1.
# Default: "1" (activado). Nunca crashea sin credenciales (mock/skip+log).
# ═══════════════════════════════════════════════════════════════
if os.getenv("ENABLE_SCHEDULER", "1") == "1":
    try:
        iniciar_scheduler()
    except Exception as _sched_exc:
        logger.error(f"No pude iniciar el scheduler: {_sched_exc}")
else:
    logger.info("ENABLE_SCHEDULER=0 — scheduler desactivado en este proceso")


# ═══════════════════════════════════════════════════════════════
# HELPERS: transcripción voz + envío audio
# ═══════════════════════════════════════════════════════════════

def _transcribir_audio_whisper(audio_url: str) -> str:
    """Descarga el audio de Green API y lo transcribe con OpenAI Whisper.
    Si Whisper no está disponible, devuelve placeholder seguro."""
    if not audio_url:
        return "Te escuche, dime de nuevo por favor"
    try:
        import requests
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return "Hola, recibí tu nota de voz. Dime en texto qué necesitas por favor"

        # Descargar audio temporal
        import tempfile
        r = requests.get(audio_url, timeout=20)
        if r.status_code != 200:
            return "No pude oir tu nota, escribeme por favor"
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(r.content)
            audio_path = f.name

        client = OpenAI(api_key=api_key)
        with open(audio_path, "rb") as af:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", file=af, language="es"
            )
        texto = (transcript.text or "").strip()
        logger.info(f"🎙️  Whisper transcribió: {texto[:120]}")
        return texto or "Te escuche pero no entendí, repite por favor"
    except Exception as exc:
        logger.warning(f"Whisper fallo: {exc}")
        return "Hola, necesito un traslado"


# ═══════════════════════════════════════════════════════════════
# HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "system": "Emovils OPC",
        "version": "2.0",
        "tagline": "No vendemos traslados. Vendemos certeza al llegar.",
        "status": "online",
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "emovils-opc-v2"})


# ═══════════════════════════════════════════════════════════════
# COTIZACIÓN INSTANTÁNEA
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/cotizar", methods=["POST"])
def cotizar():
    """
    Body JSON:
      { "mensaje": "Necesito ir del Embajador al AILA, 2 pax" }
    O datos estructurados:
      { "origen": "Hotel X", "destino": "AILA", "pasajeros": 2, "hora": "20:30" }
    """
    data = request.get_json(force=True)
    try:
        if "mensaje" in data:
            datos = extraer_datos_cotizacion(data["mensaje"])
        else:
            from opc.agente_coordinador import DatosCotizacion
            datos = DatosCotizacion(
                origen=data.get("origen", ""),
                destino=data.get("destino", ""),
                pasajeros=int(data.get("pasajeros", 1)),
                hora=data.get("hora", "12:00"),
            )
            datos.completo = bool(datos.origen and datos.destino and datos.pasajeros)

        mensaje = generar_cotizacion(datos)
        return jsonify({
            "mensaje_cliente": mensaje,
            "completo": datos.completo,
            "datos_extraidos": {
                "origen": datos.origen,
                "destino": datos.destino,
                "pasajeros": datos.pasajeros,
                "hora": datos.hora,
                "es_urgente": datos.es_urgente,
                "es_vip": datos.es_vip,
            },
        })
    except Exception as e:
        logger.error(f"Error en /cotizar: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# CREAR RESERVA + ASIGNAR CONDUCTOR
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/reservar", methods=["POST"])
def reservar():
    """
    Body JSON:
      {
        "nombre": "María González",
        "whatsapp": "+18295551234",
        "origen": "Hotel X",
        "destino": "AILA",
        "pasajeros": 2,
        "hora": "20:30",
        "fecha": "2026-06-15" (opcional, default hoy)
      }
    """
    data = request.get_json(force=True)
    try:
        from opc.agente_coordinador import DatosCotizacion
        datos = DatosCotizacion(
            origen=data["origen"],
            destino=data["destino"],
            pasajeros=int(data["pasajeros"]),
            hora=data.get("hora", "12:00"),
            es_urgente=data.get("es_urgente", False),
            es_vip=data.get("es_vip", False),
        )
        datos.completo = True

        resultado = crear_reserva_y_asignar(
            datos=datos,
            nombre_cliente=data["nombre"],
            whatsapp_cliente=data["whatsapp"],
            fecha=data.get("fecha"),
        )
        # resultado incluye "link_pago" (PayPal) cuando hay credenciales;
        # el mensaje_cliente ya lo trae integrado para WhatsApp.
        return jsonify(resultado)
    except Exception as e:
        logger.error(f"Error en /reservar: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# WHATSAPP WEBHOOK (Green API)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/whatsapp/webhook", methods=["GET", "POST"])
@app.route("/webhook/whatsapp", methods=["GET", "POST"])  # alias para compatibilidad con webhook viejo de Green API
def whatsapp_webhook():
    """Recibe mensajes de WhatsApp via Green API y responde via Monserrat."""
    if request.method == "GET":
        return jsonify({"status": "Emovils webhook OPC activo"}), 200

    payload = request.get_json(force=True, silent=True) or {}
    try:
        # ───────────────────────────────────────────────────────
        # PARSER UNIVERSAL: soporta Green API (Noe usa esto) Y Meta
        # ───────────────────────────────────────────────────────
        whatsapp = ""
        texto = ""
        nombre = ""

        # FORMATO A: Green API (typeWebhook + senderData + messageData)
        if "typeWebhook" in payload or "senderData" in payload:
            tipo = payload.get("typeWebhook", "")
            # Solo procesar mensajes entrantes de texto
            if tipo and tipo != "incomingMessageReceived":
                return jsonify({"status": "ignored", "type": tipo})

            sender_data = payload.get("senderData", {})
            msg_data = payload.get("messageData", {})
            chat_id = sender_data.get("chatId", "") or sender_data.get("sender", "")
            # chatId formato: "18298610090@c.us" → +18298610090
            whatsapp = "+" + chat_id.split("@")[0] if "@" in chat_id else chat_id
            nombre = sender_data.get("senderName", "") or sender_data.get("chatName", "")

            type_msg = msg_data.get("typeMessage", "")
            cliente_uso_audio = False
            if type_msg in ("textMessage", "extendedTextMessage"):
                texto = (msg_data.get("textMessageData", {}).get("textMessage", "") or
                         msg_data.get("extendedTextMessageData", {}).get("text", ""))
            elif type_msg in ("audioMessage", "pttMessage"):
                # Cliente envia nota de voz → transcribir con Whisper
                cliente_uso_audio = True
                audio_url = (msg_data.get("fileMessageData", {}).get("downloadUrl", "") or
                             msg_data.get("audioMessageData", {}).get("downloadUrl", ""))
                texto = _transcribir_audio_whisper(audio_url) if audio_url else "Te escuche, dame un segundo"
            elif type_msg == "locationMessage":
                # Cliente envia ubicacion (mapa) — extraer lat/lng
                loc = msg_data.get("locationMessageData", {})
                lat = loc.get("latitude", "")
                lng = loc.get("longitude", "")
                nombre_lugar = loc.get("nameLocation", "") or loc.get("address", "")
                texto = f"Mi ubicacion es: {nombre_lugar} (coordenadas {lat}, {lng})" if lat else "Te mando mi ubicacion"
            elif type_msg == "imageMessage":
                texto = msg_data.get("imageMessageData", {}).get("caption", "") or "Te mando una imagen"
            else:
                # Otros tipos no procesables
                return jsonify({"status": "ignored", "type_msg": type_msg})

        # FORMATO B: Meta WhatsApp Business API (entry > changes > value)
        elif "entry" in payload:
            msg_data = (
                payload.get("entry", [{}])[0]
                .get("changes", [{}])[0]
                .get("value", {})
            )
            messages = msg_data.get("messages", [])
            if not messages:
                return jsonify({"status": "no_message_meta"})
            msg = messages[0]
            whatsapp = msg.get("from", "")
            texto = msg.get("text", {}).get("body", "")
            contact = msg_data.get("contacts", [{}])[0]
            nombre = contact.get("profile", {}).get("name", "")

        else:
            logger.warning(f"Webhook payload formato desconocido: {list(payload.keys())[:5]}")
            return jsonify({"status": "unknown_format", "keys": list(payload.keys())[:10]})

        if not texto:
            return jsonify({"status": "no_text", "whatsapp": whatsapp})

        if not whatsapp:
            return jsonify({"status": "no_sender"})

        logger.info(f"📨 Mensaje de {nombre or 'sin nombre'} ({whatsapp}): {texto[:80]}")

        resultado = procesar_mensaje_entrante(
            mensaje=texto,
            whatsapp_cliente=whatsapp,
            nombre_cliente=nombre,
        )

        # ENVIO REAL al WhatsApp del cliente via Green API
        # Si el cliente usó audio → respondemos con audio (gTTS)
        usar_voz = locals().get("cliente_uso_audio", False)
        envio_status = "no_intentado"
        envio_detalle = ""
        try:
            from opc.whatsapp_green_api import enviar_a_cliente, enviar_audio_a_cliente, get_client
            # Verificar estado de la instancia ANTES
            try:
                estado = get_client().estado_instancia()
                state_value = estado.get("stateInstance", "desconocido")
                logger.info(f"🔌 Estado Green API instancia: {state_value}")
                if state_value != "authorized":
                    envio_status = "instancia_no_autorizada"
                    envio_detalle = f"Instancia Green API estado={state_value}. Reconectar QR en console.green-api.com"
                    logger.error(f"❌ {envio_detalle}")
            except Exception as st_err:
                logger.warning(f"No pude consultar estado instancia: {st_err}")

            logger.info(f"📤 Enviando a {whatsapp} (voz={usar_voz}): {resultado.respuesta[:80]}")
            if usar_voz:
                ok = enviar_audio_a_cliente(whatsapp, resultado.respuesta)
                if ok:
                    envio_status = "audio_enviado"
                else:
                    fallback_resp = enviar_a_cliente(whatsapp, resultado.respuesta)
                    envio_status = "audio_fallo_fallback_texto"
                    envio_detalle = str(fallback_resp)[:200]
            else:
                resp = enviar_a_cliente(whatsapp, resultado.respuesta)
                envio_status = "texto_enviado"
                envio_detalle = str(resp)[:200]
            logger.info(f"✅ Envío OK: {envio_status} · {envio_detalle[:120]}")
        except Exception as send_err:
            envio_status = "excepcion"
            envio_detalle = str(send_err)[:300]
            logger.error(f"❌ ENVIO FALLO: {envio_detalle}")

        return jsonify({
            "respuesta": resultado.respuesta,
            "intencion": resultado.intencion,
            "accion_disparada": resultado.accion_disparada,
            "envio_voz": usar_voz,
            "envio_status": envio_status,
            "envio_detalle": envio_detalle[:200],
        })
    except Exception as e:
        logger.error(f"Error WhatsApp webhook: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# VALIDACIÓN QR (bidireccional)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/qr/validar", methods=["POST"])
def validar_qr():
    """
    Body JSON:
      { "payload": "EMV1|...", "rol": "CHOFER", "servicio_esperado_id": "SVC-..." }
    O para validación cliente↔vehículo:
      { "payload": "EMV1|...", "rol": "CLIENTE", "placa_esperada": "A123456" }
    """
    data = request.get_json(force=True)
    try:
        rol = data.get("rol", "").upper()
        payload = data["payload"]

        if rol == "CHOFER":
            r = verificar_qr_cliente_para_chofer(payload, data["servicio_esperado_id"])
        elif rol == "CLIENTE":
            r = verificar_qr_vehiculo_para_cliente(payload, data["placa_esperada"])
        else:
            return jsonify({"error": "rol debe ser CHOFER o CLIENTE"}), 400

        return jsonify({
            "match": r.match,
            "firma_valida": r.firma_valida,
            "expirado": r.expirado,
            "tipo": r.tipo,
            "mensaje": r.mensaje,
            "datos": r.datos,
        })
    except Exception as e:
        logger.error(f"Error /qr/validar: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# INGESTA EXCEL DE INTELCIA
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/intelcia/ingestar", methods=["POST"])
def ingestar_intelcia():
    """
    Body JSON: { "filepath": "/path/al/excel.xlsx" }
    o multipart con archivo.
    """
    try:
        if request.is_json:
            data = request.get_json()
            filepath = data["filepath"]
        else:
            # multipart con archivo
            file = request.files.get("file")
            if not file:
                return jsonify({"error": "Falta archivo"}), 400
            # Guardar temporal y procesar
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
            file.save(tmp.name)
            filepath = tmp.name

        servicios = procesar_excel_intelcia(filepath)
        api = AirtableOPC()

        # Crear servicios en Airtable
        records = [s.como_record_airtable() for s in servicios]
        creados = api.crear_lote("Servicios", records)

        return jsonify({
            "servicios_extraidos": len(servicios),
            "servicios_creados_en_airtable": len(creados),
            "facturacion_total_rd": sum(s.tarifa_rd for s in servicios),
        })
    except Exception as e:
        logger.error(f"Error /intelcia/ingestar: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# REPORTE DIARIO PARA EL DUEÑO
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/reporte/diario", methods=["GET"])
def reporte_diario():
    """Devuelve el reporte WhatsApp para el dueño (texto listo)."""
    try:
        hoy_str = request.args.get("hoy")
        hoy = date.fromisoformat(hoy_str) if hoy_str else date.today()

        api = AirtableOPC()
        reporte = generar_reporte_diario(api, hoy=hoy)
        return jsonify({
            "fecha": reporte.fecha,
            "mensaje_whatsapp": formato_whatsapp(reporte),
            "datos": {
                "servicios_completados_ayer": reporte.servicios_completados_ayer,
                "facturacion_ayer_rd": reporte.facturacion_ayer_rd,
                "ganancia_operativa_ayer_rd": reporte.ganancia_operativa_ayer_rd,
                "servicios_programados_hoy": reporte.servicios_programados_hoy,
                "conductores_activos": reporte.conductores_activos,
                "incidencias_abiertas": reporte.incidencias_abiertas,
            },
        })
    except Exception as e:
        logger.error(f"Error /reporte/diario: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# CIERRE DE QUINCENA (LIQUIDACIONES)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/liquidaciones/quincena", methods=["POST"])
def cerrar_quincena_endpoint():
    """
    Body JSON: { "fecha_corte": "2026-06-15", "dry_run": true }
    """
    data = request.get_json(force=True) or {}
    try:
        fecha_corte = date.fromisoformat(data["fecha_corte"])
        dry_run = data.get("dry_run", True)

        api = AirtableOPC()
        liquidaciones = cerrar_quincena(api, fecha_corte, dry_run=dry_run)

        codigo = f"Q{1 if fecha_corte.day <= 15 else 2}_{fecha_corte.strftime('%b').upper()}"

        return jsonify({
            "quincena": codigo,
            "año": fecha_corte.year,
            "afiliados_con_servicios": len(liquidaciones),
            "total_servicios": sum(l.servicios_count for l in liquidaciones),
            "total_facturacion_rd": sum(l.facturacion_total_rd for l in liquidaciones),
            "total_comision_emovils_rd": sum(l.comision_emovils_rd for l in liquidaciones),
            "total_pagar_afiliados_rd": sum(l.pago_al_chofer_rd for l in liquidaciones),
            "dry_run": dry_run,
            "mensaje_whatsapp": reporte_liquidaciones_whatsapp(
                liquidaciones, codigo, fecha_corte.year
            ),
        })
    except Exception as e:
        logger.error(f"Error /liquidaciones/quincena: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# SAFEGUARD CPA — EL $6 ES INVIOLABLE
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/safeguards/cpa", methods=["POST"])
def safeguards_cpa():
    """
    Evalúa el CPA del piloto con la regla del $6 máximo (config/safeguards.py).

    Body JSON: { "gasto_usd": 45.0, "clientes_nuevos": 10 }
    Respuesta: { "cpa": 4.5, "estado": "ok|alerta|pausar", "mensaje": "..." }
    """
    data = request.get_json(force=True) or {}
    try:
        gasto_usd = float(data.get("gasto_usd", 0))
        clientes_nuevos = int(data.get("clientes_nuevos", 0))

        from config.safeguards import CPAEvaluator, CPAStatus

        evaluacion = CPAEvaluator().evaluate(
            total_spent=gasto_usd, total_clients=clientes_nuevos
        )

        # Mapear estados internos → contrato simple del endpoint
        mapa_estado = {
            CPAStatus.SALUDABLE: "ok",
            CPAStatus.ALERTA_AMARILLA: "alerta",
            CPAStatus.ALERTA_ROJA: "pausar",
            CPAStatus.CATASTROFE: "pausar",
        }

        return jsonify({
            "cpa": evaluacion["cpa"],
            "estado": mapa_estado.get(evaluacion["status"], "alerta"),
            "mensaje": evaluacion["message"],
            "accion": evaluacion["action"],
            "pausar_anuncios": evaluacion["pause_ads"],
            "alertar_dueno": evaluacion["alert_owner"],
            "margen_usd": evaluacion.get("margin_usd"),
            "roi_pct": evaluacion.get("roi_percent"),
        })
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Datos inválidos: {e}"}), 400
    except Exception as e:
        logger.error(f"Error /safeguards/cpa: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# AGENTE SOCIAL — PLAN SEMANAL + PUBLICACIÓN META
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/social/planificar", methods=["POST"])
def social_planificar():
    """
    Genera el plan de contenido de 7 días (caption + imagen + hashtags) y lo
    guarda en la cola de aprobación de Airtable (estado Pendiente_Aprobacion).

    Body JSON (todo opcional):
      { "fecha_inicio": "2026-06-15", "guardar_airtable": true }

    Sin tokens (LLM/Airtable) responde igual en modo mock con plantillas.
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        resultado = planificar_semana(
            fecha_inicio=data.get("fecha_inicio"),
            guardar_airtable=bool(data.get("guardar_airtable", True)),
        )
        return jsonify(resultado)
    except Exception as e:
        logger.error(f"Error /social/planificar: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v2/social/publicar", methods=["POST"])
def social_publicar():
    """
    Aprueba y/o publica un post en Facebook/Instagram via Meta Graph API.

    Body JSON:
      { "post_id": "recXXXX",                  # opcional: aprueba en Airtable
        "aprobado": true,                       # default true
        "post": { "caption": "...", "hashtags": "...", "image_url": "..." } }

    Sin META_ACCESS_TOKEN responde {"modo": "mock", ...} sin llamadas reales.
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        post = data.get("post") or {}
        post_id = data.get("post_id")
        aprobado = bool(data.get("aprobado", True))

        resultado_aprobacion = None
        if post_id:
            resultado_aprobacion = procesar_aprobacion(post_id, aprobado)
            if not aprobado:
                return jsonify({
                    "modo": resultado_aprobacion.get("modo", "mock"),
                    "aprobacion": resultado_aprobacion,
                    "publicacion": None,
                    "mensaje": "Post rechazado, no se publica.",
                })
            # Si no mandaron el post explícito, usar el de Airtable
            if not post and resultado_aprobacion.get("post"):
                post = {"caption": resultado_aprobacion["post"].get("caption", "")}

        if not post.get("caption"):
            return jsonify({"error": "Falta post.caption (o post_id válido)"}), 400

        resultado_pub = publicar_post(post)
        return jsonify({
            "modo": resultado_pub.get("modo", "mock"),
            "aprobacion": resultado_aprobacion,
            "publicacion": resultado_pub,
        })
    except Exception as e:
        logger.error(f"Error /social/publicar: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# PROSPECCIÓN B2B — PIPELINE COMPLETO
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/prospeccion/buscar", methods=["POST"])
def prospeccion_buscar():
    """
    Corre el pipeline: buscar (Apify o mock) → enriquecer (Apollo o mock)
    → generar emails outreach → guardar en Pipeline_Comercial.

    Body JSON (todo opcional):
      { "fuente": "google_maps" | "hoteles" | "call_centers",
        "busqueda": "call center Santo Domingo",
        "max_resultados": 10,
        "generar_emails": true,
        "guardar": true }

    Sin APIFY/APOLLO/Airtable responde {"modo": "mock", ...} con datos semilla.
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        resultado = ejecutar_pipeline_prospeccion(
            fuente=data.get("fuente", "google_maps"),
            busqueda=data.get("busqueda"),
            max_resultados=int(data.get("max_resultados", 10)),
            generar_emails=bool(data.get("generar_emails", True)),
            guardar=bool(data.get("guardar", True)),
        )
        return jsonify(resultado)
    except Exception as e:
        logger.error(f"Error /prospeccion/buscar: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# FACTURACIÓN NCF (DGII)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/ncf/emitir", methods=["POST"])
def ncf_emitir():
    """
    Emite una factura con NCF dominicano (B01 si hay RNC, B02 si no).

    Body JSON:
      { "servicio": { "descripcion": "Transfer AILA → Casa de Campo",
                      "monto_rd": 8500, "fecha": "2026-06-12" },
        "cliente":  { "nombre": "Empresa X", "email": "pagos@x.com" },
        "rnc": "131123456" }

    Sin Airtable responde {"modo": "mock", ...} con secuencia local.
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        resultado = generar_factura(
            servicio=data.get("servicio") or {},
            cliente=data.get("cliente") or {},
            rnc=data.get("rnc", ""),
        )
        if not resultado.get("ok"):
            return jsonify(resultado), 400
        return jsonify(resultado)
    except Exception as e:
        logger.error(f"Error /ncf/emitir: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# ONBOARDING — CONDUCTORES Y VEHÍCULOS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/conductor/crear", methods=["POST"])
def crear_conductor():
    """Crea un nuevo conductor en Airtable."""
    data = request.get_json(force=True)
    try:
        api = AirtableOPC()
        record = api.crear_registro("Conductores", data)
        return jsonify({"id": record["id"], "fields": record.get("fields", {})})
    except Exception as e:
        logger.error(f"Error /conductor/crear: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v2/vehiculo/crear", methods=["POST"])
def crear_vehiculo():
    """Crea un nuevo vehículo en Airtable."""
    data = request.get_json(force=True)
    try:
        api = AirtableOPC()
        record = api.crear_registro("Vehiculos", data)
        return jsonify({"id": record["id"], "fields": record.get("fields", {})})
    except Exception as e:
        logger.error(f"Error /vehiculo/crear: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/onboarding", methods=["GET"])
def onboarding_html():
    """Sirve el formulario HTML de onboarding."""
    html_path = ROOT / "opc" / "web" / "onboarding.html"
    if not html_path.exists():
        return "Formulario no encontrado", 404
    return html_path.read_text(encoding="utf-8")


@app.route("/dashboard", methods=["GET"])
def dashboard_html():
    """Sirve el dashboard ejecutivo del dueño."""
    html_path = ROOT / "opc" / "web" / "dashboard.html"
    if not html_path.exists():
        return "Dashboard no encontrado", 404
    return html_path.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# SCHEDULER — STATUS + EJECUCIÓN MANUAL DE JOBS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/scheduler/status", methods=["GET"])
def scheduler_status():
    """Lista los jobs registrados y su próxima ejecución (hora RD)."""
    try:
        return jsonify(listar_jobs())
    except Exception as e:
        logger.error(f"Error /scheduler/status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v2/scheduler/run/<job_id>", methods=["POST"])
def scheduler_run(job_id: str):
    """
    Dispara un job manualmente (útil para pruebas). Solo corre si el
    job_id existe en el registro; devuelve el resumen de la ejecución.
    """
    try:
        resultado = ejecutar_job(job_id)
        if resultado is None:
            return jsonify({
                "error": f"Job '{job_id}' no existe",
                "jobs_disponibles": [j["job_id"] for j in listar_jobs()["jobs"]],
            }), 404
        return jsonify(resultado)
    except Exception as e:
        logger.error(f"Error /scheduler/run/{job_id}: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5002))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    logger.info(f"Emovils OPC v2 iniciado en puerto {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
