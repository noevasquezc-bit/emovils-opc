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
    crear_reserva_y_asignar,
    extraer_datos_cotizacion,
    generar_cotizacion,
)
# MVP: usar monserrat_mvp en vez del coordinador completo
from opc.monserrat_mvp import procesar as procesar_mensaje_entrante
import opc.mvp as mvp_core
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
from opc import auth


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

def _transcribir_audio_whisper(audio_url: str):
    """Descarga el audio de Green API y lo transcribe con OpenAI Whisper.
    Devuelve el texto transcrito, o None si no se pudo transcribir (sin clave,
    sin saldo, error de red, audio inaudible). El webhook usa None para pedir
    al cliente que escriba su solicitud por texto, en vez de inventar un mensaje."""
    if not audio_url:
        return None
    try:
        import requests
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None

        # Descargar audio temporal
        import tempfile
        r = requests.get(audio_url, timeout=20)
        if r.status_code != 200:
            return None
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
        return texto or None
    except Exception as exc:
        logger.warning(f"Whisper fallo: {exc}")
        return None


# Dominios de enlaces de Google Maps / compartir que vale la pena expandir.
_MAPS_DOMINIOS = ("share.google", "maps.app.goo.gl", "goo.gl/maps",
                  "google.com/maps", "maps.google.com", "g.co/kgs")


def _coords_de_texto(s: str):
    """Extrae 'lat,lng' de una URL/HTML de Google Maps si esta presente."""
    import re
    pats = [
        r'@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)',
        r'!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)',
        r'[?&](?:q|ll|sll|destination|daddr|center)=(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)',
    ]
    for p in pats:
        m = re.search(p, s or "")
        if m:
            return f"{m.group(1)},{m.group(2)}"
    return None


def _expandir_maps_links(texto: str) -> str:
    """Convierte enlaces de Google Maps en algo geocodificable.

    - Si el enlace lleva a un pin con coordenadas -> las inserta como 'coordenadas lat,lng'.
    - Si lleva a una busqueda/negocio (q=Nombre) -> inserta el NOMBRE del lugar.
    Asi Monserrat recibe datos utiles en vez de una URL opaca. Solo expande dominios
    conocidos de Google (seguro: nunca ejecuta nada, solo extrae texto)."""
    if not texto:
        return texto
    import re as _re
    from urllib.parse import unquote_plus
    for url in _re.findall(r'https?://[^\s]+', texto):
        if not any(d in url.lower() for d in _MAPS_DOMINIOS):
            continue
        coords = _coords_de_texto(url)
        nombre = None
        if not coords:
            try:
                import requests
                r = requests.get(url, allow_redirects=True, timeout=12,
                                 headers={"User-Agent": "Mozilla/5.0"})
                coords = _coords_de_texto(r.url) or _coords_de_texto(r.text[:20000])
                if not coords:
                    mq = _re.search(r'[?&]q=([^&]+)', r.url)
                    if mq:
                        cand = unquote_plus(mq.group(1)).strip()
                        # Evitar que 'q=' sea solo coordenadas ya capturadas u otra URL
                        if cand and not cand.startswith("http"):
                            nombre = cand
            except Exception as e:
                logger.warning(f"No pude expandir maps link {url[:60]}: {e}")
        if coords:
            texto = texto.replace(url, f"(ubicacion compartida, coordenadas {coords})")
            logger.info(f"🔗→📍 Link de mapa expandido a coordenadas {coords}")
        elif nombre:
            texto = texto.replace(url, nombre)
            logger.info(f"🔗→🏷️ Link de mapa expandido a lugar: {nombre}")
    return texto


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
        loc_lat = None
        loc_lng = None

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
                # FIX: Green API envia downloadUrl en multiples lugares
                audio_url = (
                    msg_data.get("downloadUrl", "")
                    or msg_data.get("fileMessageData", {}).get("downloadUrl", "")
                    or msg_data.get("audioMessageData", {}).get("downloadUrl", "")
                )
                texto = _transcribir_audio_whisper(audio_url) if audio_url else None
            elif type_msg == "locationMessage":
                # Cliente/chofer envia ubicacion (mapa) — extraer lat/lng
                loc = msg_data.get("locationMessageData", {})
                lat = loc.get("latitude", "")
                lng = loc.get("longitude", "")
                try:
                    loc_lat = float(lat) if lat not in ("", None) else None
                    loc_lng = float(lng) if lng not in ("", None) else None
                except (TypeError, ValueError):
                    loc_lat = loc_lng = None
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

        if not whatsapp:
            return jsonify({"status": "no_sender"})

        # ───────────────────────────────────────────────────────
        # ROUTER DE CHOFER — ubicacion (ponerse disponible) y
        # respuestas a ofertas (ACEPTO / RECHAZO). Si el remitente es
        # un chofer registrado, NO lo pasamos al agente de clientes.
        # ───────────────────────────────────────────────────────
        try:
            import opc.mvp as _mvp_disp
            _es_chofer = bool(_mvp_disp._buscar_driver_por_tel(whatsapp))
        except Exception as _e:
            logger.warning("Router chofer: %s", _e)
            _mvp_disp = None
            _es_chofer = False

        if _es_chofer and _mvp_disp is not None:
            from opc.whatsapp_green_api import enviar_a_cliente as _wa_send
            # 1) Comparte ubicacion -> queda EN LINEA (disponible)
            if loc_lat is not None and loc_lng is not None:
                r = _mvp_disp.actualizar_ubicacion_chofer(whatsapp, loc_lat, loc_lng)
                rmsg = ("✅ Ubicación recibida. Quedas *EN LÍNEA* y seguirás "
                        "disponible hasta que te desconectes desde la web.\n\n"
                        "💡 Para que siempre sepa dónde estás, comparte tu "
                        "*ubicación en tiempo real* (📎 → Ubicación → "
                        "\"Compartir ubicación en tiempo real\"). Así se "
                        "actualiza sola y te asigno las carreras más cercanas."
                        if r.get("ok") else
                        "No pude registrar tu ubicación. Intenta compartirla de nuevo.")
                try:
                    _wa_send(whatsapp, rmsg)
                except Exception as _se:
                    logger.warning("Envio chofer (loc) fallo: %s", _se)
                return jsonify({"status": "driver_location", "resultado": r})

            # 2) Avisa que terminó la carrera -> reserva completed y vuelve EN LÍNEA
            fin = _mvp_disp.chofer_finaliza_viaje(whatsapp, texto or "")
            if fin.get("es_completar"):
                rmsg = ("✅ *Carrera completada.* Quedas *EN LÍNEA* de nuevo, "
                        "listo para la próxima. ¡Gracias!"
                        if fin.get("ok") else
                        "No pude cerrar la carrera. Intenta de nuevo.")
                try:
                    _wa_send(whatsapp, rmsg)
                except Exception as _se:
                    logger.warning("Envio chofer (fin) fallo: %s", _se)
                return jsonify({"status": "driver_trip_complete", "resultado": fin})

            # 3) Responde a una oferta vigente (ACEPTO / RECHAZO)
            r = _mvp_disp.responder_oferta(whatsapp, texto or "")
            accion = r.get("accion")
            if r.get("ok") and accion == "aceptada":
                rmsg = (f"✅ *Carrera asignada.* Recogida: {r.get('pickup','')} → "
                        f"{r.get('destino','')}. Cobro: RD${r.get('precio',0)} "
                        f"({r.get('payment_method','')}). Dirígete a la recogida.")
            elif r.get("ok") and accion == "rechazada":
                rmsg = "Entendido, paso la carrera a otro chofer. Gracias."
            elif r.get("razon") == "no_oferta_vigente":
                rmsg = ("No tienes una carrera asignada en este momento. "
                        "Comparte tu ubicación para mantenerte disponible.")
            elif r.get("razon") == "oferta_vencida":
                rmsg = "Esa oferta ya venció y pasó a otro chofer. Gracias de todos modos."
            elif r.get("razon") == "respuesta_no_entendida":
                rmsg = "Por favor responde *ACEPTO* o *RECHAZO*."
            else:
                rmsg = ("Comparte tu ubicación para estar disponible, o responde "
                        "*ACEPTO* / *RECHAZO* a una carrera ofrecida.")
            try:
                _wa_send(whatsapp, rmsg)
            except Exception as _se:
                logger.warning("Envio chofer (oferta) fallo: %s", _se)
            return jsonify({"status": "driver_offer_reply", "resultado": r})

        # Cliente mandó nota de voz pero no se pudo transcribir (sin saldo OpenAI,
        # audio inaudible, etc): respuesta formal pidiendo texto. NO invocamos a
        # Monserrat para no quedarnos saludando en bucle sin entender el pedido.
        if cliente_uso_audio and not texto:
            from types import SimpleNamespace
            logger.info(f"🎙️→📝 Audio no transcrito de {whatsapp}: pido solicitud por texto")
            resultado = SimpleNamespace(
                respuesta=("Disculpe, no logré escuchar con claridad su nota de voz. "
                           "¿Sería tan amable de escribirme su solicitud por texto, por favor?"),
                intencion="MVP",
                accion_disparada=None,
                envio_voz=False,
            )
        else:
            if not texto:
                return jsonify({"status": "no_text", "whatsapp": whatsapp})

            # Expandir enlaces de Google Maps a coordenadas o nombre de lugar
            if "http" in texto:
                texto = _expandir_maps_links(texto)

            logger.info(f"📨 Mensaje de {nombre or 'sin nombre'} ({whatsapp}): {texto[:80]}")

            # MVP: pasamos cliente_uso_audio para que Monserrat sepa si responder en voz
            resultado = procesar_mensaje_entrante(
                mensaje=texto,
                whatsapp_cliente=whatsapp,
                nombre_cliente=nombre,
                cliente_uso_audio=locals().get("cliente_uso_audio", False),
            )

        # ENVIO REAL al WhatsApp del cliente via Green API
        # Si el cliente usó audio → respondemos con audio (gTTS)
        # Voz solo si el cliente usó audio Y logramos transcribirlo (texto truthy).
        # Si el audio no se pudo transcribir, respondemos por TEXTO pidiendo que escriba.
        usar_voz = locals().get("cliente_uso_audio", False) and bool(texto)
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
# ENDPOINTS MVP — flujo cerrado de reserva + QR
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/mvp/cotizar", methods=["POST"])
def mvp_cotizar():
    """Body: {origen, destino, pasajeros, hora, km_estimados?}"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        # km_estimados solo como override manual; si no viene, se mide con Google
        km_manual = data.get("km_estimados")
        c = mvp_core.cotizar(
            origen=data["origen"], destino=data["destino"],
            pasajeros=int(data.get("pasajeros", 1)),
            hora=int(data.get("hora", 12)),
            km_estimados=float(km_manual) if km_manual is not None else None,
        )
        return jsonify({
            "precio_rd": c.precio_rd, "vehiculo": c.vehiculo_recomendado,
            "tarifa_lista": c.tarifa_lista, "descuento_pct": c.descuento_pct,
            "segmento": c.segmento,
            "es_nocturno": c.es_nocturno, "requiere_supervisor": c.requiere_supervisor,
            "razon_supervisor": c.razon_supervisor, "moneda": c.moneda,
            "distancia_km": c.km_estimados, "distancia_texto": c.distancia_texto,
            "duracion_texto": c.duracion_texto, "maps_url": c.maps_url,
            "direccion_no_resuelta": c.direccion_no_resuelta,
            "fuente_distancia": c.fuente_distancia,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/v2/mvp/reservar", methods=["POST"])
def mvp_reservar():
    """Crea reserva y devuelve QR del cliente."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        r = mvp_core.crear_reserva(
            customer_name=data["customer_name"],
            customer_phone=data["customer_phone"],
            origin=data["origin"], destination=data["destination"],
            passengers=int(data["passengers"]),
            final_price=int(data["final_price"]),
            payment_method=data.get("payment_method", "cash"),
            vehicle_type=data.get("vehicle_type", "Sedan"),
            service_date=data.get("service_date"),
            service_time=data.get("service_time"),
        )
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/v2/mvp/reserva/asignar", methods=["POST"])
def mvp_asignar():
    """Body: {booking_id, vehicle_type}"""
    data = request.get_json(force=True, silent=True) or {}
    res = mvp_core.asignar_conductor_y_vehiculo(
        booking_id=data["booking_id"],
        vehicle_type=data.get("vehicle_type", "Sedan"),
    )
    return jsonify(res)


@app.route("/api/v2/mvp/reserva/<booking_id>", methods=["GET"])
def mvp_get_reserva(booking_id):
    r = mvp_core.obtener_reserva(booking_id)
    if not r:
        return jsonify({"error": "Reserva no encontrada"}), 404
    return jsonify(r)


@app.route("/api/v2/mvp/qr/cliente/validar", methods=["POST"])
def mvp_qr_cliente_validar():
    """Body: {booking_id, token, driver_id} — conductor escanea QR del cliente."""
    data = request.get_json(force=True, silent=True) or {}
    res = mvp_core.verificar_qr_cliente(
        booking_id=data["booking_id"], token=data["token"],
        driver_id=data["driver_id"],
    )
    return jsonify(res)


@app.route("/vehicle/verify/<vehicle_id>", methods=["GET"])
def mvp_vehicle_verify_pantalla(vehicle_id):
    """Cliente escanea QR fisico del vehiculo. Muestra pantalla verde/roja/amarilla."""
    token = request.args.get("t", "")
    res = mvp_core.verificar_qr_vehiculo(vehicle_id, token)
    return _render_vehicle_verify_page(res)


@app.route("/api/v2/mvp/vehicle/verify", methods=["POST"])
def mvp_vehicle_verify_api():
    """Version JSON del verificador de vehiculo."""
    data = request.get_json(force=True, silent=True) or {}
    res = mvp_core.verificar_qr_vehiculo(
        vehicle_id=data["vehicle_id"], token=data.get("token", ""),
    )
    return jsonify(res)


@app.route("/driver/dashboard", methods=["GET"])
def mvp_driver_dashboard():
    """Panel del conductor — reservas asignadas."""
    driver_id = request.args.get("driver_id", "DRV-001")
    reservas = mvp_core.reservas_conductor(driver_id)
    return _render_driver_dashboard(driver_id, reservas)


@app.route("/qr/cliente/<booking_id>", methods=["GET"])
def mvp_qr_cliente_landing(booking_id):
    """QR del PASAJERO. El conductor lo escanea con su camara: ve todos los
    datos del viaje y puede iniciarlo."""
    token = request.args.get("t", "")
    if not mvp_core.validar_token_cliente(booking_id, token):
        return _render_simple_page("QR invalido", "Este QR no es valido o expiro.", "#dc2626")
    reserva = mvp_core.obtener_reserva(booking_id)
    if not reserva:
        return _render_simple_page("Reserva no encontrada", booking_id, "#dc2626")
    return _render_pasajero_qr_page(reserva, token)


@app.route("/qr/cliente/<booking_id>/img.png", methods=["GET"])
def mvp_qr_cliente_img(booking_id):
    """PNG escaneable del QR del pasajero (para enviarselo por WhatsApp)."""
    token = request.args.get("t", "")
    if not mvp_core.validar_token_cliente(booking_id, token):
        return "QR invalido", 403
    url = f"{mvp_core.PUBLIC_BASE_URL}/qr/cliente/{booking_id}?t={token}"
    png = mvp_core.generar_qr_png(url)
    return png, 200, {"Content-Type": "image/png", "Cache-Control": "no-store"}


@app.route("/qr/cliente/<booking_id>/ver", methods=["GET"])
def mvp_qr_cliente_ver(booking_id):
    """Vista para mostrar/imprimir el QR del pasajero (pruebas y envio)."""
    token = request.args.get("t", "")
    if not mvp_core.validar_token_cliente(booking_id, token):
        return _render_simple_page("QR invalido", "Este QR no es valido.", "#dc2626")
    reserva = mvp_core.obtener_reserva(booking_id) or {}
    img = f"/qr/cliente/{booking_id}/img.png?t={token}"
    return _render_qr_show_page(
        "QR del pasajero",
        f"Reserva {booking_id} · {reserva.get('customer_name','')}",
        "El conductor lo escanea con su camara para ver el viaje e iniciarlo.",
        img,
    )


@app.route("/api/v2/mvp/reserva/<booking_id>/iniciar", methods=["POST"])
def mvp_iniciar_viaje(booking_id):
    """El conductor inicia el viaje tras escanear el QR del pasajero."""
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token") or request.args.get("t", "")
    res = mvp_core.iniciar_viaje(booking_id, token)
    return jsonify(res), (200 if res.get("ok") else 400)


# ═══════════════════════════════════════════════════════════════
# DESPACHO POR CERCANIA — endpoints de control y pruebas
# ═══════════════════════════════════════════════════════════════

@app.route("/api/v2/driver/ubicacion", methods=["POST"])
def api_driver_ubicacion():
    """Registra la ubicacion del chofer (lo pone disponible). {phone, lat, lng}."""
    d = request.get_json(force=True, silent=True) or {}
    phone = d.get("phone") or d.get("driver_phone") or ""
    try:
        lat = float(d.get("lat")); lng = float(d.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "razon": "lat/lng invalidos"}), 400
    res = mvp_core.actualizar_ubicacion_chofer(phone, lat, lng)
    return jsonify(res), (200 if res.get("ok") else 404)


@app.route("/api/v2/driver/gps", methods=["POST"])
def api_driver_gps():
    """Posición GPS del chofer desde el navegador. {driver_id, lat, lng}.
    La página /chofer/gps/<id> la envía cada 3-5 s para seguimiento ultra-fluido."""
    d = request.get_json(force=True, silent=True) or {}
    did = d.get("driver_id") or ""
    try:
        lat = float(d.get("lat")); lng = float(d.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "razon": "lat/lng invalidos"}), 400
    res = mvp_core.actualizar_ubicacion_chofer_por_id(did, lat, lng)
    return jsonify(res), (200 if res.get("ok") else 404)


@app.route("/api/v2/driver/responder", methods=["POST"])
def api_driver_responder():
    """Simula/recibe la respuesta del chofer a una oferta. {phone, texto}."""
    d = request.get_json(force=True, silent=True) or {}
    res = mvp_core.responder_oferta(d.get("phone", ""), d.get("texto", ""))
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/v2/driver/disponibilidad", methods=["POST"])
def api_driver_disponibilidad():
    """Panel web del chofer: {driver_id, disponible:true|false}."""
    d = request.get_json(force=True, silent=True) or {}
    res = mvp_core.cambiar_disponibilidad_chofer(d.get("driver_id", ""), bool(d.get("disponible")))
    return jsonify(res), (200 if res.get("ok") else 404)


@app.route("/api/v2/dispatch/<booking_id>/iniciar", methods=["POST"])
def api_dispatch_iniciar(booking_id):
    """Arranca manualmente el despacho de una reserva."""
    return jsonify(mvp_core.iniciar_despacho(booking_id)), 200


@app.route("/api/v2/dispatch/<booking_id>/estado", methods=["GET"])
def api_dispatch_estado(booking_id):
    """Inspecciona el estado de despacho de una reserva."""
    return jsonify(mvp_core.estado_despacho(booking_id)), 200


@app.route("/api/v2/dispatch/<booking_id>/completar", methods=["POST"])
def api_dispatch_completar(booking_id):
    """Cierra la carrera: reserva -> completed y el chofer vuelve a EN LÍNEA."""
    return jsonify(mvp_core.completar_viaje(booking_id)), 200


@app.route("/api/v2/dispatch/tick", methods=["GET", "POST"])
def api_dispatch_tick():
    """Revisa y reasigna ofertas vencidas (lo hace tambien el scheduler)."""
    return jsonify(mvp_core.revisar_ofertas_vencidas()), 200


@app.route("/api/v2/track/<booking_id>", methods=["GET"])
def api_track(booking_id):
    """Posición del chofer + distancia al pickup para el seguimiento en vivo."""
    return jsonify(mvp_core.estado_seguimiento(booking_id)), 200


@app.route("/seguir/<booking_id>", methods=["GET"])
def seguir_pasajero(booking_id):
    """Página que ve el pasajero: su chofer acercándose, con alerta a 100 m."""
    return _render_tracking_page(booking_id)


@app.route("/api/v2/flota", methods=["GET"])
def api_flota():
    """Estado de toda la flota (para el mapa en vivo de la institución)."""
    return jsonify(mvp_core.estado_flota()), 200


@app.route("/flota", methods=["GET"])
def flota_mapa():
    """Mapa en vivo de la institución: todos los choferes y su estado."""
    return _render_flota_page()


def _render_flota_page() -> str:
    # Página estática; los datos se cargan en vivo desde /api/v2/flota (mismo origen).
    return """<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — Flota en vivo</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<style>
body{margin:0;background:#0f172a;color:#e2e8f0;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
.top{padding:12px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;border-bottom:1px solid #1e293b}
.brand{font-weight:800;letter-spacing:1px;color:#fff;font-size:18px;margin-right:auto}
.chip{background:#1e293b;border-radius:999px;padding:6px 12px;font-size:14px;font-weight:700}
.g{color:#4ade80}.o{color:#fbbf24}.w{color:#94a3b8}
#map{height:60vh;width:100%}
.upd{font-size:12px;color:#64748b;padding:8px 16px}
.list{padding:4px 16px 28px}
.row{background:#1e293b;border-radius:12px;padding:10px 12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:10px}
.nm{font-weight:700;color:#fff}
.meta{font-size:13px;color:#94a3b8}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle}
</style></head><body>
<div class='top'>
  <div class='brand'>EMOVILS · Flota en vivo</div>
  <div class='chip g'>📡 En vivo: <span id='cLive'>0</span></div>
  <div class='chip o'>💤 Sin señal: <span id='cStale'>0</span></div>
  <div class='chip w'>⚪ Sin GPS: <span id='cNo'>0</span></div>
</div>
<div id='map'></div>
<div class='upd' id='upd'>Cargando flota…</div>
<div class='list' id='list'></div>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<script>
var map=L.map('map').setView([18.4861,-69.9312],9);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
var COL={available:'#16a34a',busy:'#f59e0b',offline:'#94a3b8'};
var ET={available:'Disponible',busy:'En viaje',offline:'Desconectado'};
var capa=L.layerGroup().addTo(map); var primero=true;
function haceSec(s){
  if(s==null) return 'sin señal';
  if(s<60) return 'hace '+s+' s';
  if(s<3600) return 'hace '+Math.floor(s/60)+' min';
  if(s<86400) return 'hace '+Math.floor(s/3600)+' h';
  return 'hace '+Math.floor(s/86400)+' d';
}
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
async function cargar(){
  try{
    var r=await fetch('/api/v2/flota'); var j=await r.json();
    if(!j.ok) return;
    document.getElementById('cLive').textContent=j.resumen.en_vivo;
    document.getElementById('cStale').textContent=j.resumen.sin_senal;
    document.getElementById('cNo').textContent=j.resumen.sin_gps;
    capa.clearLayers(); var pts=[]; var html='';
    j.choferes.forEach(function(c){
      var et=ET[c.status]||c.status;
      var col=c.live?(COL[c.status]||COL.offline):'#94a3b8';
      var nombre=esc(c.driver_name||c.driver_id);
      var fresco=c.live?'📡 en vivo':(c.lat!=null?('💤 última '+haceSec(c.secs_ago)):'⚪ sin GPS');
      if(c.lat!=null && c.lng!=null){
        var pop='<b>'+nombre+'</b><br>'+et+' · '+fresco+'<br>'+esc(c.vehiculo||'');
        if(c.viaje){ pop+='<br>🧑 '+esc(c.viaje.cliente||'')+'<br>→ '+esc(c.viaje.destino||''); }
        L.circleMarker([c.lat,c.lng],{radius:c.live?10:8,color:'#0f172a',weight:2,fillColor:col,fillOpacity:c.live?0.95:0.5}).bindPopup(pop).addTo(capa);
        pts.push([c.lat,c.lng]);
      }
      html+='<div class="row"><div><div class="nm"><span class="dot" style="background:'+col+'"></span>'+nombre+'</div><div class="meta">'+et+(c.viaje?(' · → '+esc(c.viaje.destino||'')):'')+'</div></div><div class="meta">'+fresco+'</div></div>';
    });
    document.getElementById('list').innerHTML=html||'<div class="meta">No hay choferes registrados todavía.</div>';
    document.getElementById('upd').textContent='Actualizado '+new Date().toLocaleTimeString()+' · 📡 en vivo = enviando GPS ahora · 💤 = última posición conocida';
    if(primero && pts.length){ map.fitBounds(pts,{padding:[40,40],maxZoom:14}); primero=false; }
  }catch(e){ document.getElementById('upd').textContent='Sin conexión, reintentando…'; }
}
cargar(); setInterval(cargar,15000);
</script>
</body></html>"""


def _render_tracking_page(booking_id: str) -> str:
    from html import escape as _esc
    bid = _esc(booking_id)
    html = """<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1, maximum-scale=1'>
<title>Emovils — Sigue tu viaje</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a}
#top{position:absolute;top:0;left:0;right:0;min-height:88px;background:#0f172a;color:#fff;z-index:1000;padding:12px 16px}
.brand{font-weight:800;letter-spacing:2px;font-size:16px}
#status{font-size:14px;margin-top:4px;color:#cbd5e1;line-height:1.4}
#dist{font-size:24px;font-weight:800;color:#4ade80;margin-top:2px}
#map{position:absolute;top:88px;bottom:0;left:0;right:0;background:#1e293b}
#alert{display:none;position:absolute;top:96px;left:10px;right:10px;z-index:1200;background:#16a34a;color:#fff;padding:16px;border-radius:14px;font-weight:800;text-align:center;box-shadow:0 8px 24px rgba(0,0,0,.5);font-size:16px}
#alert.show{display:block;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.04)}}
#enable{position:absolute;bottom:18px;left:10px;right:10px;z-index:1200;background:#2563eb;color:#fff;border:none;padding:14px;border-radius:12px;font-weight:800;font-size:15px;cursor:pointer}
.pin{font-size:30px;line-height:30px;text-align:center;filter:drop-shadow(0 2px 3px rgba(0,0,0,.5))}
</style></head><body>
<div id='top'>
  <div class='brand'>EMOVILS</div>
  <div id='status'>Conectando…</div>
  <div id='dist'></div>
</div>
<div id='map'></div>
<div id='alert'>🚖 ¡Tu Emovils está a menos de 100 m! Prepárate para salir.</div>
<button id='enable' onclick='enableSound()'>🔔 Toca para activar el aviso con sonido</button>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<script>
const BID='__BID__';
const map=L.map('map',{zoomControl:true}).setView([18.4861,-69.9312],12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
const carIcon=L.divIcon({className:'',html:"<div class='pin'>🚖</div>",iconSize:[34,34],iconAnchor:[17,17]});
const pickIcon=L.divIcon({className:'',html:"<div class='pin'>📍</div>",iconSize:[30,34],iconAnchor:[15,32]});
let carMarker=null,pickMarker=null,alerted=false,audioCtx=null,firstFit=true;

function enableSound(){try{audioCtx=new(window.AudioContext||window.webkitAudioContext)();if(audioCtx.state==='suspended')audioCtx.resume();}catch(e){}document.getElementById('enable').style.display='none';}
function beep(){if(!audioCtx)return;try{const o=audioCtx.createOscillator(),g=audioCtx.createGain();o.connect(g);g.connect(audioCtx.destination);o.type='sine';o.frequency.value=880;g.gain.setValueAtTime(0.001,audioCtx.currentTime);g.gain.exponentialRampToValueAtTime(0.4,audioCtx.currentTime+0.05);g.gain.exponentialRampToValueAtTime(0.001,audioCtx.currentTime+0.6);o.start();o.stop(audioCtx.currentTime+0.6);}catch(e){}}
function alertaProximidad(){const a=document.getElementById('alert');a.classList.add('show');beep();setTimeout(beep,650);setTimeout(beep,1300);if(navigator.vibrate)navigator.vibrate([300,150,300,150,300]);}
function fmt(m){return m>=1000?(m/1000).toFixed(1)+' km':m+' m';}

async function tick(){
  try{
    const r=await fetch('/api/v2/track/'+encodeURIComponent(BID));
    const j=await r.json();
    const st=document.getElementById('status'),ds=document.getElementById('dist');
    if(!j.ok){st.textContent='No encontramos tu viaje.';return;}
    if(j.pickup_lat!=null&&!pickMarker){pickMarker=L.marker([j.pickup_lat,j.pickup_lng],{icon:pickIcon}).addTo(map).bindPopup('Tu recogida');map.setView([j.pickup_lat,j.pickup_lng],15);}
    if(j.booking_status==='completed'){st.textContent='✅ Viaje completado. ¡Gracias por viajar con Emovils!';ds.textContent='';return;}
    if(!j.asignado){st.textContent='Buscando tu chofer más cercano…';ds.textContent='';return;}
    if(j.driver_lat==null){st.textContent=(j.driver_name||'Tu chofer')+' aceptó tu viaje. Esperando su ubicación…';ds.textContent='';return;}
    st.textContent='🚖 '+(j.driver_name||'Tu chofer')+' va en camino'+(j.vehiculo?(' · '+j.vehiculo):'');
    if(!carMarker){carMarker=L.marker([j.driver_lat,j.driver_lng],{icon:carIcon}).addTo(map);}
    else{carMarker.setLatLng([j.driver_lat,j.driver_lng]);}
    if(j.distancia_m!=null){
      ds.textContent='A '+fmt(j.distancia_m)+' de ti'+(j.eta_min!=null?(' · ~'+j.eta_min+' min'):'');
      if(j.distancia_m<=100&&!alerted){alerted=true;alertaProximidad();st.textContent='🚖 ¡Tu Emovils ya casi llega!';}
      if(j.distancia_m>180){alerted=false;document.getElementById('alert').classList.remove('show');}
    }
    if(firstFit&&pickMarker){map.fitBounds(L.latLngBounds([[j.driver_lat,j.driver_lng],[j.pickup_lat,j.pickup_lng]]).pad(0.35));firstFit=false;}
  }catch(e){}
}
tick();setInterval(tick,5000);
</script>
</body></html>"""
    return html.replace("__BID__", bid)


@app.route("/chofer/gps/<driver_id>", methods=["GET"])
def chofer_gps(driver_id):
    """Página GPS del chofer: el navegador envía su posición cada ~4 s para un
    seguimiento ultra-fluido, sin depender de la ubicación en vivo de WhatsApp."""
    return _render_driver_gps_page(driver_id)


def _render_driver_gps_page(driver_id: str) -> str:
    from html import escape as _esc
    did = _esc(driver_id)
    html = """<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1, maximum-scale=1'>
<title>Emovils — GPS Chofer</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#fff}
.wrap{max-width:520px;margin:0 auto;padding:22px 16px 40px}
.brand{font-weight:800;letter-spacing:2px;font-size:18px}
.sub{color:#94a3b8;font-size:13px;margin-top:2px}
.card{background:#1e293b;border-radius:16px;padding:18px;margin-top:16px}
#dot{display:inline-block;width:12px;height:12px;border-radius:50%;background:#64748b;margin-right:8px;vertical-align:middle}
#dot.on{background:#22c55e;animation:p 1.4s infinite}
#dot.warn{background:#f59e0b}
#dot.err{background:#ef4444}
@keyframes p{0%{box-shadow:0 0 0 0 rgba(34,197,94,.6)}70%{box-shadow:0 0 0 12px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
#estado{font-size:20px;font-weight:800;vertical-align:middle}
.rows{margin-top:14px}
.row{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #334155;font-size:14px}
.row:last-child{border-bottom:none}
.row span:first-child{color:#94a3b8}
#btn{width:100%;border:none;border-radius:14px;padding:18px;font-size:18px;font-weight:800;margin-top:18px;cursor:pointer;color:#fff;background:#22c55e}
#btn.stop{background:#dc2626}
#msg{margin-top:14px;font-size:13px;color:#fca5a5;min-height:18px;text-align:center}
.hint{margin-top:16px;color:#64748b;font-size:12px;line-height:1.5;text-align:center}
</style></head><body>
<div class='wrap'>
  <div class='brand'>EMOVILS</div>
  <div class='sub'>Compartir mi ubicación en vivo</div>
  <div class='card'>
    <div><span id='dot'></span><span id='estado'>Desconectado</span></div>
    <div class='rows'>
      <div class='row'><span>Chofer</span><span id='nombre'>__DID__</span></div>
      <div class='row'><span>Vehículo</span><span id='veh'>—</span></div>
      <div class='row'><span>Última posición</span><span id='ultima'>—</span></div>
      <div class='row'><span>Precisión</span><span id='prec'>—</span></div>
      <div class='row'><span>Enviadas</span><span id='cont'>0</span></div>
    </div>
    <button id='btn' onclick='toggle()'>▶ INICIAR GPS</button>
    <div id='msg'></div>
  </div>
  <div class='hint'>Deje esta página abierta durante su jornada y mantenga la pantalla encendida para un seguimiento continuo. Mientras esté EN LÍNEA podrá recibir carreras.</div>
</div>
<script>
const DID='__DID__';
let watchId=null,wake=null,enviadas=0,lastSent=0,activo=false,lastPos=null,lastPosTime=0,hb=null;

fetch('/api/v2/drivers/'+encodeURIComponent(DID)).then(r=>r.json()).then(j=>{
  if(j&&j.ok){
    if(j.driver_name)document.getElementById('nombre').textContent=j.driver_name;
    const v=[j.marca,j.modelo,j.color,j.placa].filter(Boolean).join(' ');
    if(v)document.getElementById('veh').textContent=v;
  }
}).catch(()=>{});

function msg(t){document.getElementById('msg').textContent=t||'';}
function setEstado(txt,cls){document.getElementById('estado').textContent=txt;var d=document.getElementById('dot');d.className='';if(cls)d.classList.add(cls);}
function fmtHora(d){return d.toLocaleTimeString('es-DO',{hour:'2-digit',minute:'2-digit',second:'2-digit'});}

async function send(lat,lng){
  lastSent=Date.now();
  try{
    const r=await fetch('/api/v2/driver/gps',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({driver_id:DID,lat:lat,lng:lng})});
    const j=await r.json();
    if(!j.ok){msg('No se pudo registrar: '+(j.razon||'error'));return;}
    enviadas++;document.getElementById('cont').textContent=enviadas;
    document.getElementById('ultima').textContent=fmtHora(new Date());
    setEstado('EN LÍNEA','on');msg('');
  }catch(e){setEstado('Sin conexión…','warn');msg('Reintentando…');}
}

function onPos(p){
  document.getElementById('prec').textContent=Math.round(p.coords.accuracy)+' m';
  lastPos={lat:p.coords.latitude,lng:p.coords.longitude};lastPosTime=Date.now();
  if(Date.now()-lastSent<3500)return;   // al moverse: ~4 s
  send(lastPos.lat,lastPos.lng);
}
function onErr(e){
  if(e.code===1){setEstado('GPS BLOQUEADO','err');msg('Permiso de ubicación DENEGADO. Toca el candado o la ⓘ en la barra de direcciones → Ubicación → Permitir, y recarga la página.');}
  else if(e.code===2){setEstado('Buscando señal…','warn');msg('No se pudo obtener la ubicación (señal GPS débil).');}
  else if(e.code===3){setEstado('Buscando señal…','warn');msg('La ubicación tardó demasiado. Reintentando…');}
  else{setEstado('Buscando señal…','warn');msg('Error de ubicación.');}
}

async function pedirWake(){try{if('wakeLock' in navigator){wake=await navigator.wakeLock.request('screen');}}catch(e){}}

async function start(){
  if(!('geolocation' in navigator)){setEstado('Sin GPS','err');msg('Este dispositivo no soporta GPS.');return;}
  watchId=navigator.geolocation.watchPosition(onPos,onErr,{enableHighAccuracy:true,maximumAge:2000,timeout:15000});
  await pedirWake();
  activo=true;
  setEstado('Buscando señal…','warn');
  const b=document.getElementById('btn');b.textContent='■ DETENER';b.classList.add('stop');
  msg('Activando tu ubicación…');
  if(hb)clearInterval(hb);
  hb=setInterval(()=>{
    if(!activo)return;
    if(lastPos && (Date.now()-lastPosTime<90000)){send(lastPos.lat,lastPos.lng);}   // latido solo si la posición es reciente
    else if(lastPosTime && (Date.now()-lastPosTime>=90000)){setEstado('Buscando señal…','warn');msg('Sin lecturas recientes del GPS. Mantén esta pantalla abierta y el GPS encendido.');}
  },15000); // evita reenviar posiciones falsas/congeladas
}
function stop(){
  if(watchId!=null){navigator.geolocation.clearWatch(watchId);watchId=null;}
  if(hb){clearInterval(hb);hb=null;}
  if(wake){try{wake.release();}catch(e){}wake=null;}
  activo=false;lastPos=null;lastPosTime=0;
  setEstado('Desconectado','');
  const b=document.getElementById('btn');b.textContent='▶ INICIAR GPS';b.classList.remove('stop');
  msg('');
}
function toggle(){activo?stop():start();}

document.addEventListener('visibilitychange',async()=>{
  if(activo&&wake===null&&document.visibilityState==='visible'){await pedirWake();}
});
</script>
</body></html>"""
    return html.replace("__DID__", did)


@app.route("/driver/<driver_id>/panel", methods=["GET"])
def driver_panel(driver_id):
    """Panel simple del chofer para marcarse Disponible / No disponible."""
    return _render_driver_panel(driver_id)


def _render_driver_panel(driver_id: str) -> str:
    from html import escape as _esc
    did = _esc(driver_id)
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — Panel del chofer</title>
<style>
body{{margin:0;background:#0f172a;color:#e2e8f0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:24px;max-width:480px;margin:0 auto;text-align:center}}
.brand{{font-weight:800;letter-spacing:1px;color:#fff;margin-bottom:4px}}
.sub{{color:#94a3b8;font-size:14px;margin-bottom:20px}}
#estado{{font-size:22px;font-weight:800;margin:18px 0;padding:14px;border-radius:12px;background:#1e293b}}
button{{display:block;width:100%;border:none;padding:18px;border-radius:14px;font-size:18px;font-weight:800;margin:10px 0;color:#fff}}
.on{{background:#16a34a}} .off{{background:#dc2626}}
.note{{color:#94a3b8;font-size:13px;margin-top:18px;line-height:1.5}}
</style></head><body>
<div class='brand'>EMOVILS</div>
<div class='sub'>Panel del chofer · {did}</div>
<div id='estado'>—</div>
<button class='on' onclick="setDisp(true)">Estoy DISPONIBLE</button>
<button class='off' onclick="setDisp(false)">NO disponible</button>
<div class='note'>Recuerda compartir tu ubicación por WhatsApp para recibir carreras cercanas. Este panel solo cambia tu disponibilidad.</div>
<script>
async function setDisp(d){{
  const e=document.getElementById('estado'); e.textContent='Guardando...';
  try{{
    const r=await fetch('/api/v2/driver/disponibilidad',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{driver_id:'{did}',disponible:d}})}});
    const j=await r.json();
    if(j.ok){{ e.textContent = (j.driver_status==='available'?'EN LÍNEA (disponible)':'NO disponible'); e.style.color=(j.driver_status==='available'?'#4ade80':'#f87171'); }}
    else{{ e.textContent = j.razon||'Error'; e.style.color='#f87171'; }}
  }}catch(err){{ e.textContent='Error de conexión'; }}
}}
</script>
</body></html>"""


@app.route("/choferes", methods=["GET"])
def mvp_drivers_admin():
    """Dashboard para registrar y ver los choferes."""
    try:
        choferes = mvp_core.listar_choferes()
    except Exception as e:
        logger.warning("listar_choferes fallo: %s", e)
        choferes = []
    return _render_drivers_admin(choferes)


@app.route("/api/v2/drivers/registrar", methods=["POST"])
def api_drivers_registrar():
    """Alta de un chofer + su vehiculo desde el dashboard."""
    d = request.get_json(silent=True) or {}
    res = mvp_core.registrar_chofer(
        nombre=d.get("nombre", ""),
        telefono=d.get("telefono", ""),
        vehiculo_tipo=d.get("vehiculo_tipo", ""),
        placa=d.get("placa", ""),
        marca=d.get("marca", ""),
        modelo=d.get("modelo", ""),
        color=d.get("color", ""),
        anio=d.get("anio"),
        max_pax=d.get("max_pax"),
        driver_type=d.get("driver_type", "propio"),
    )
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/v2/drivers/<driver_id>", methods=["GET"])
def api_drivers_get(driver_id):
    """Datos completos de un chofer (para llenar el formulario de edición)."""
    res = mvp_core.obtener_chofer(driver_id)
    return jsonify(res), (200 if res.get("ok") else 404)


@app.route("/api/v2/drivers/<driver_id>/actualizar", methods=["POST"])
def api_drivers_actualizar(driver_id):
    """Guarda los cambios de un chofer y/o su vehículo."""
    d = request.get_json(silent=True) or {}
    res = mvp_core.actualizar_chofer(
        driver_id,
        nombre=d.get("nombre"),
        telefono=d.get("telefono"),
        driver_type=d.get("driver_type"),
        driver_status=d.get("driver_status"),
        vehiculo_tipo=d.get("vehiculo_tipo"),
        placa=d.get("placa"),
        marca=d.get("marca"),
        modelo=d.get("modelo"),
        color=d.get("color"),
        anio=d.get("anio"),
        max_pax=d.get("max_pax"),
    )
    return jsonify(res), (200 if res.get("ok") else 400)


# ═══════════════════════════════════════════════════════════════
# CUENTAS DE USUARIO (auth) — empresa / chofer / cliente
# Registro, inicio de sesión y recuperación de contraseña por WhatsApp.
# ═══════════════════════════════════════════════════════════════
@app.route("/api/v2/auth/register", methods=["POST"])
def api_auth_register():
    d = request.get_json(silent=True) or {}
    res = auth.registrar(
        role=d.get("role", ""),
        nombre=d.get("nombre", "") or d.get("name", ""),
        telefono=d.get("telefono", "") or d.get("phone", ""),
        password=d.get("password", ""),
        email=d.get("email", ""),
    )
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/v2/auth/login", methods=["POST"])
def api_auth_login():
    d = request.get_json(silent=True) or {}
    res = auth.login(
        role=d.get("role", ""),
        telefono=d.get("telefono", "") or d.get("phone", ""),
        password=d.get("password", ""),
    )
    return jsonify(res), (200 if res.get("ok") else 401)


@app.route("/api/v2/auth/forgot", methods=["POST"])
def api_auth_forgot():
    d = request.get_json(silent=True) or {}
    res = auth.solicitar_codigo(
        role=d.get("role", ""),
        telefono=d.get("telefono", "") or d.get("phone", ""),
    )
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/v2/auth/reset", methods=["POST"])
def api_auth_reset():
    d = request.get_json(silent=True) or {}
    res = auth.restablecer(
        role=d.get("role", ""),
        telefono=d.get("telefono", "") or d.get("phone", ""),
        codigo=d.get("codigo", "") or d.get("code", ""),
        nueva_password=d.get("password", "") or d.get("nueva_password", ""),
    )
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/v2/auth/me", methods=["GET"])
def api_auth_me():
    authz = request.headers.get("Authorization", "")
    if authz.lower().startswith("bearer "):
        token = authz[7:].strip()
    else:
        token = request.args.get("token", "") or ""
    res = auth.cuenta_desde_token(token)
    return jsonify(res), (200 if res.get("ok") else 401)


def _render_drivers_admin(choferes: list) -> str:
    from html import escape as _esc
    _badges = {
        "available": ("EN LÍNEA", "#16a34a"),
        "busy": ("EN CARRERA", "#d97706"),
        "offline": ("DESCONECTADO", "#64748b"),
        "suspended": ("SUSPENDIDO", "#dc2626"),
    }
    filas = []
    for c in choferes:
        st = (c.get("driver_status") or "offline")
        etiqueta, color = _badges.get(st, (st.upper(), "#64748b"))
        ubic = "📍" if c.get("tiene_ubicacion") else "—"
        did = _esc(c.get("driver_id", ""))
        filas.append(
            "<tr><td><b>" + did + "</b></td>"
            "<td>" + _esc(c.get("driver_name", "")) + "</td>"
            "<td>" + _esc(c.get("driver_phone", "")) + "</td>"
            "<td>" + _esc(c.get("assigned_vehicle_id", "")) + "</td>"
            "<td style='text-align:center'>" + ubic + "</td>"
            "<td><span class='badge' style='background:" + color + "'>" + etiqueta + "</span></td>"
            "<td><button class='btn-edit' onclick=\"editar('" + did + "')\">✏️ Editar</button></td></tr>"
        )
    tabla = ("".join(filas) if filas else
             "<tr><td colspan='7' style='text-align:center;color:#94a3b8;padding:18px'>"
             "Aún no hay choferes registrados.</td></tr>")
    total = len(choferes)
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — Registro de choferes</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#0f172a;color:#e2e8f0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:20px;max-width:760px;margin:0 auto}}
.brand{{font-weight:800;letter-spacing:2px;color:#fff;font-size:22px}}
.sub{{color:#94a3b8;font-size:14px;margin-bottom:18px}}
.card{{background:#1e293b;border-radius:16px;padding:18px;margin-bottom:22px}}
h2{{font-size:16px;margin:0 0 14px}}
label{{display:block;font-size:13px;color:#94a3b8;margin:10px 0 4px}}
input,select{{width:100%;padding:12px;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:15px}}
.row{{display:flex;gap:10px}} .row>div{{flex:1}}
#btn{{width:100%;border:none;padding:16px;border-radius:12px;font-size:16px;font-weight:800;margin-top:16px;color:#fff;background:#16a34a;cursor:pointer}}
#btn:disabled{{opacity:.6}}
#msg{{margin-top:12px;font-size:14px;min-height:18px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 6px;border-bottom:1px solid #334155;text-align:left}}
th{{color:#94a3b8;font-weight:600}}
.badge{{display:inline-block;padding:3px 8px;border-radius:999px;color:#fff;font-size:11px;font-weight:700;white-space:nowrap}}
.hint{{color:#64748b;font-size:12px;margin-top:10px;line-height:1.5}}
.btn-edit{{background:#334155;border:none;color:#e2e8f0;padding:6px 10px;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap}}
.overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:50;padding:18px;overflow:auto}}
.overlay.show{{display:flex;align-items:flex-start;justify-content:center}}
.modal{{background:#1e293b;border-radius:16px;padding:20px;max-width:560px;width:100%;margin:auto}}
.save{{background:#2563eb;color:#fff;border:none;padding:14px;border-radius:12px;font-weight:800;flex:1;cursor:pointer;font-size:15px}}
.save:disabled{{opacity:.6}}
.ghost{{background:transparent;border:1px solid #334155;color:#94a3b8;padding:14px;border-radius:12px;font-weight:700;flex:1;cursor:pointer;font-size:15px}}
</style></head><body>
<div class='brand'>EMOVILS</div>
<div class='sub'>Registro de choferes</div>

<div class='card'>
<h2>➕ Nuevo chofer</h2>
<form onsubmit='return reg(event)'>
<label>Nombre completo *</label>
<input id='nombre' placeholder='Ej. Juan Pérez' autocomplete='off'>
<label>Teléfono WhatsApp *</label>
<input id='telefono' placeholder='Ej. 809 555 1234' inputmode='tel' autocomplete='off'>
<div class='row'>
<div><label>Tipo de chofer</label>
<select id='driver_type'><option value='propio'>Propio</option><option value='afiliado'>Afiliado</option></select></div>
<div><label>Tipo de vehículo</label>
<select id='vehiculo_tipo'><option value='Sedan'>Sedan (4 pax)</option><option value='Van Caravan'>Van Caravan (6 pax)</option><option value='Hyundai H1'>Hyundai H1 (8 pax)</option></select></div>
</div>
<div class='row'>
<div><label>Marca</label><input id='marca' placeholder='Toyota' autocomplete='off'></div>
<div><label>Modelo</label><input id='modelo' placeholder='Corolla' autocomplete='off'></div>
</div>
<div class='row'>
<div><label>Placa</label><input id='placa' placeholder='A123456' autocomplete='off'></div>
<div><label>Color</label><input id='color' placeholder='Negro' autocomplete='off'></div>
<div><label>Año</label><input id='anio' placeholder='2022' inputmode='numeric' autocomplete='off'></div>
</div>
<button id='btn' type='submit'>Registrar chofer</button>
<div id='msg'></div>
</form>
<div class='hint'>El chofer queda <b>DESCONECTADO</b> al registrarse. Pasa a <b>EN LÍNEA</b> automáticamente cuando comparte su <b>ubicación en tiempo real</b> por WhatsApp al número de Monserrat.</div>
</div>

<div class='card'>
<h2>👥 Choferes registrados ({total})</h2>
<table><thead><tr><th>ID</th><th>Nombre</th><th>Teléfono</th><th>Vehículo</th><th>📍</th><th>Estado</th><th></th></tr></thead>
<tbody>{tabla}</tbody></table>
</div>

<div id='modal' class='overlay'>
<div class='modal'>
<h2>✏️ Editar chofer <span id='m_id' style='color:#94a3b8'></span></h2>
<label>Nombre completo</label>
<input id='e_nombre' autocomplete='off'>
<label>Teléfono WhatsApp</label>
<input id='e_telefono' inputmode='tel' autocomplete='off'>
<div class='row'>
<div><label>Tipo de chofer</label>
<select id='e_driver_type'><option value='propio'>Propio</option><option value='afiliado'>Afiliado</option></select></div>
<div><label>Estado</label>
<select id='e_driver_status'><option value='available'>EN LÍNEA</option><option value='busy'>EN CARRERA</option><option value='offline'>DESCONECTADO</option><option value='suspended'>SUSPENDIDO</option></select></div>
</div>
<label>Tipo de vehículo</label>
<select id='e_vehiculo_tipo'><option value='Sedan'>Sedan (4 pax)</option><option value='Van Caravan'>Van Caravan (6 pax)</option><option value='Hyundai H1'>Hyundai H1 (8 pax)</option></select>
<div class='row'>
<div><label>Marca</label><input id='e_marca' autocomplete='off'></div>
<div><label>Modelo</label><input id='e_modelo' autocomplete='off'></div>
</div>
<div class='row'>
<div><label>Placa</label><input id='e_placa' autocomplete='off'></div>
<div><label>Color</label><input id='e_color' autocomplete='off'></div>
<div><label>Año</label><input id='e_anio' inputmode='numeric' autocomplete='off'></div>
</div>
<div id='emsg' style='margin-top:12px;font-size:14px;min-height:18px'></div>
<div class='row' style='margin-top:14px'>
<button class='ghost' onclick='cerrarModal()'>Cancelar</button>
<button id='ebtn' class='save' onclick='guardar()'>Guardar cambios</button>
</div>
</div>
</div>

<script>
async function reg(ev){{
  ev.preventDefault();
  const btn=document.getElementById('btn'), msg=document.getElementById('msg');
  const g=id=>document.getElementById(id).value.trim();
  const body={{nombre:g('nombre'),telefono:g('telefono'),driver_type:g('driver_type'),
    vehiculo_tipo:g('vehiculo_tipo'),marca:g('marca'),modelo:g('modelo'),
    placa:g('placa'),color:g('color'),anio:g('anio')}};
  if(!body.nombre||!body.telefono){{msg.textContent='Nombre y teléfono son obligatorios.';msg.style.color='#f87171';return false;}}
  btn.disabled=true; btn.textContent='Registrando...';
  try{{
    const r=await fetch('/api/v2/drivers/registrar',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
    const j=await r.json();
    if(j.ok){{msg.textContent='✅ '+j.driver_name+' registrado ('+j.driver_id+'). Recargando...';msg.style.color='#4ade80';setTimeout(()=>location.reload(),900);}}
    else{{msg.textContent='⚠️ '+(j.razon||'No se pudo registrar');msg.style.color='#f87171';btn.disabled=false;btn.textContent='Registrar chofer';}}
  }}catch(err){{msg.textContent='Error de conexión';msg.style.color='#f87171';btn.disabled=false;btn.textContent='Registrar chofer';}}
  return false;
}}

let _curId=null;
async function editar(id){{
  try{{
    const r=await fetch('/api/v2/drivers/'+encodeURIComponent(id));
    const j=await r.json();
    if(!j.ok){{alert(j.razon||'No se pudo cargar el chofer');return;}}
    _curId=id;
    document.getElementById('m_id').textContent='· '+id;
    const set=(k,v)=>{{const el=document.getElementById(k); if(el) el.value=(v==null?'':v);}};
    set('e_nombre',j.driver_name); set('e_telefono',j.driver_phone);
    set('e_driver_type',j.driver_type||'propio'); set('e_driver_status',j.driver_status||'offline');
    set('e_vehiculo_tipo',j.vehiculo_tipo||'Sedan');
    set('e_marca',j.marca); set('e_modelo',j.modelo);
    set('e_placa',j.placa); set('e_color',j.color); set('e_anio',j.anio);
    document.getElementById('emsg').textContent='';
    document.getElementById('modal').classList.add('show');
  }}catch(err){{alert('Error de conexión');}}
}}
function cerrarModal(){{document.getElementById('modal').classList.remove('show');_curId=null;}}
async function guardar(){{
  if(!_curId)return;
  const btn=document.getElementById('ebtn'), msg=document.getElementById('emsg');
  const g=id=>document.getElementById(id).value.trim();
  const body={{nombre:g('e_nombre'),telefono:g('e_telefono'),driver_type:g('e_driver_type'),
    driver_status:g('e_driver_status'),vehiculo_tipo:g('e_vehiculo_tipo'),
    marca:g('e_marca'),modelo:g('e_modelo'),placa:g('e_placa'),color:g('e_color'),anio:g('e_anio')}};
  if(!body.nombre||!body.telefono){{msg.textContent='Nombre y teléfono son obligatorios.';msg.style.color='#f87171';return;}}
  btn.disabled=true; btn.textContent='Guardando...';
  try{{
    const r=await fetch('/api/v2/drivers/'+encodeURIComponent(_curId)+'/actualizar',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
    const j=await r.json();
    if(j.ok){{msg.textContent='✅ Guardado. Recargando...';msg.style.color='#4ade80';setTimeout(()=>location.reload(),700);}}
    else{{msg.textContent='⚠️ '+(j.razon||'No se pudo guardar');msg.style.color='#f87171';btn.disabled=false;btn.textContent='Guardar cambios';}}
  }}catch(err){{msg.textContent='Error de conexión';msg.style.color='#f87171';btn.disabled=false;btn.textContent='Guardar cambios';}}
}}
</script>
</body></html>"""


@app.route("/vehicle/<vehicle_id>/qr.png", methods=["GET"])
def mvp_vehicle_qr_img(vehicle_id):
    """PNG del QR fisico del vehiculo (para pegar en el carro)."""
    token = request.args.get("t", "")
    if not mvp_core.validar_token_vehiculo(vehicle_id, token):
        return "QR invalido", 403
    url = f"{mvp_core.PUBLIC_BASE_URL}/vehicle/verify/{vehicle_id}?t={token}"
    png = mvp_core.generar_qr_png(url)
    return png, 200, {"Content-Type": "image/png", "Cache-Control": "no-store"}


@app.route("/vehicle/<vehicle_id>/qr", methods=["GET"])
def mvp_vehicle_qr_ver(vehicle_id):
    """Vista para mostrar/imprimir el QR del vehiculo. Sin token, lo genera."""
    token = request.args.get("t", "")
    if not token:
        token, _u = mvp_core.generar_qr_vehiculo(vehicle_id)
    if not mvp_core.validar_token_vehiculo(vehicle_id, token):
        return _render_simple_page("QR invalido", "Token invalido.", "#dc2626")
    img = f"/vehicle/{vehicle_id}/qr.png?t={token}"
    return _render_qr_show_page(
        "QR del vehiculo",
        f"Vehiculo {vehicle_id}",
        "El pasajero lo escanea para confirmar que es su Emovils asignado.",
        img,
    )


def _render_qr_show_page(titulo: str, subtitulo: str, instruccion: str, img_src: str) -> str:
    from html import escape as _esc
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — {_esc(titulo)}</title>
<style>
body{{margin:0;background:#f8fafc;color:#0f172a;font-family:-apple-system,Segoe UI,Roboto,sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;text-align:center}}
.brand{{font-weight:800;letter-spacing:2px;color:#1E5F8E;margin-bottom:6px}}
h1{{font-size:20px;margin:6px 0}}
p{{color:#475569;max-width:320px}}
.qr{{background:#fff;padding:18px;border-radius:18px;box-shadow:0 8px 30px rgba(0,0,0,.12);margin:16px 0}}
.qr img{{width:260px;height:260px;display:block}}
.sub{{font-size:14px;color:#334155;font-weight:600}}
</style></head><body>
<div class='brand'>EMOVILS</div>
<h1>{_esc(titulo)}</h1>
<div class='qr'><img src='{img_src}' alt='QR'></div>
<div class='sub'>{_esc(subtitulo)}</div>
<p>{_esc(instruccion)}</p>
</body></html>"""


def _render_pasajero_qr_page(reserva: dict, token: str) -> str:
    from html import escape as _esc
    from urllib.parse import quote as _q
    bid = _esc(str(reserva.get("booking_id", "")))
    estado = reserva.get("booking_status", "") or ""
    nombre = _esc(str(reserva.get("customer_name", "")))
    tel = _esc(str(reserva.get("customer_phone", "")))
    origen = _esc(str(reserva.get("origen", "")))
    destino = _esc(str(reserva.get("destino", "")))
    pax = reserva.get("passengers", 0)
    veh = _esc(str(reserva.get("vehicle_type", "")))
    pago = _esc(str(reserva.get("payment_method", "")))
    pago_estado = _esc(str(reserva.get("payment_status", "")))
    cuando = _esc(str(reserva.get("service_time") or reserva.get("travel_date") or ""))
    dist = reserva.get("distancia_km", "")
    dist_txt = f"{dist} km" if dist not in ("", None) else "—"
    maps_url = reserva.get("maps_url", "") or ""
    maps_origen = "https://www.google.com/maps/search/?api=1&query=" + _q(str(reserva.get("origen", "") or ""))
    maps_destino = "https://www.google.com/maps/search/?api=1&query=" + _q(str(reserva.get("destino", "") or ""))
    try:
        precio_fmt = f"{int(reserva.get('final_price', 0)):,}"
    except Exception:
        precio_fmt = str(reserva.get("final_price", 0))

    badges = {
        "confirmed": ("#2563eb", "CONFIRMADA"),
        "in_progress": ("#16a34a", "VIAJE EN CURSO"),
        "completed": ("#475569", "COMPLETADO"),
        "cancelled": ("#dc2626", "CANCELADA"),
        "pending_payment": ("#d97706", "PENDIENTE DE PAGO"),
        "supervisor_review": ("#d97706", "EN REVISION"),
    }
    bcolor, btxt = badges.get(estado, ("#475569", (estado or "—").upper()))
    cobro = f"<div class='cobro'>COBRAR EN EFECTIVO: RD${precio_fmt}</div>" if pago == "cash" else ""
    if estado == "confirmed":
        accion = ("<button id='btnIniciar' class='btn-go' onclick='iniciar()'>Iniciar viaje</button>"
                  "<div id='msg'></div>")
    elif estado == "in_progress":
        accion = "<div class='encurso'>Viaje en curso</div>"
    else:
        accion = ""
    nav = (f"<a class='btn-nav' href='{maps_url}' target='_blank'>Navegar recogida &rarr; destino</a>"
           if maps_url else "")

    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — Hoja de servicio</title>
<style>
body{{margin:0;background:#0f172a;color:#e2e8f0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:16px;max-width:520px;margin:0 auto}}
.head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}
.brand{{font-weight:800;letter-spacing:1px;color:#fff}}
.badge{{padding:5px 10px;border-radius:999px;font-size:12px;font-weight:700;background:{bcolor};color:#fff}}
.code{{color:#94a3b8;font-size:13px;margin-bottom:14px}}
.when{{font-size:18px;font-weight:700;color:#fff;margin:4px 0 16px}}
.card{{background:#1e293b;border-radius:14px;padding:14px 16px;margin-bottom:12px;line-height:1.6}}
.lbl{{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:.5px}}
.val{{font-size:16px;color:#fff;margin-bottom:2px}}
a.maps{{color:#38bdf8;text-decoration:none;font-size:14px}}
a.tel{{color:#38bdf8;text-decoration:none}}
.row{{display:flex;gap:10px}} .row>div{{flex:1}}
.cobro{{background:#166534;color:#fff;padding:12px;border-radius:12px;font-weight:800;text-align:center;margin:10px 0;font-size:17px}}
.btn-go{{display:block;width:100%;background:#16a34a;color:#fff;border:none;padding:16px;border-radius:12px;font-size:18px;font-weight:800;margin-top:8px}}
.btn-nav{{display:block;text-align:center;background:#2563eb;color:#fff;padding:14px;border-radius:12px;text-decoration:none;font-weight:700;margin-top:10px}}
.encurso{{background:#166534;color:#fff;text-align:center;padding:14px;border-radius:12px;font-weight:800;margin-top:8px}}
#msg{{text-align:center;margin-top:10px;font-weight:700}}
</style></head><body>
<div class='head'><div class='brand'>EMOVILS</div><div class='badge'>{btxt}</div></div>
<div class='code'>Hoja de servicio &middot; {bid}</div>
<div class='when'>{cuando}</div>
<div class='card'>
  <div class='lbl'>Pasajero</div>
  <div class='val'>{nombre}</div>
  <div><a class='tel' href='tel:{tel}'>Llamar {tel}</a> &nbsp;|&nbsp; <a class='tel' href='https://wa.me/{tel}' target='_blank'>WhatsApp</a></div>
</div>
<div class='card'>
  <div class='lbl'>Recogida</div>
  <div class='val'>{origen}</div>
  <a class='maps' href='{maps_origen}' target='_blank'>Ver recogida en Maps</a>
  <div style='height:10px'></div>
  <div class='lbl'>Destino</div>
  <div class='val'>{destino}</div>
  <a class='maps' href='{maps_destino}' target='_blank'>Ver destino en Maps</a>
</div>
<div class='card'>
  <div class='row'>
    <div><div class='lbl'>Pasajeros</div><div class='val'>{pax}</div></div>
    <div><div class='lbl'>Vehiculo</div><div class='val'>{veh}</div></div>
    <div><div class='lbl'>Distancia</div><div class='val'>{dist_txt}</div></div>
  </div>
</div>
<div class='card'>
  <div class='lbl'>Precio / pago</div>
  <div class='val'>RD${precio_fmt} &middot; {pago} ({pago_estado})</div>
</div>
{cobro}
{accion}
{nav}
<script>
async function iniciar(){{
  const b=document.getElementById('btnIniciar'); b.disabled=true; b.textContent='Iniciando...';
  try{{
    const r=await fetch('/api/v2/mvp/reserva/{bid}/iniciar',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:'{token}'}})}});
    const j=await r.json();
    const m=document.getElementById('msg');
    if(j.ok){{ m.style.color='#4ade80'; m.textContent='Viaje iniciado'; b.style.display='none'; }}
    else{{ m.style.color='#f87171'; m.textContent=(j.razon||'No se pudo iniciar'); b.disabled=false; b.textContent='Iniciar viaje'; }}
  }}catch(e){{ document.getElementById('msg').textContent='Error de conexion'; b.disabled=false; b.textContent='Iniciar viaje'; }}
}}
</script>
</body></html>"""


def _render_vehicle_verify_page(res: dict) -> str:
    color = res.get("color", "yellow")
    if color == "green":
        bg, emoji, titulo = "#16a34a", "✅", "CONFIRMADO POR EMOVILS"
        body = f"""
            <p>Este es el vehiculo y chofer asignado a su reserva.</p>
            <div class='card'>
                <div><b>Chofer:</b> {res['driver']['name']}</div>
                <div><b>Vehiculo:</b> {res['vehicle']['brand']} {res['vehicle']['model']} {res['vehicle']['color']}</div>
                <div><b>Placa:</b> {res['vehicle']['plate']}</div>
                <div><b>Reserva:</b> {res['booking']['code']}</div>
                <div><b>Origen:</b> {res['booking']['origen']}</div>
                <div><b>Destino:</b> {res['booking']['destino']}</div>
            </div>
            <h2>Puede abordar.</h2>
        """
    elif color == "red":
        bg, emoji, titulo = "#dc2626", "❌", "NO ABORDE"
        body = f"""
            <p>Este vehiculo o chofer no coincide con su reserva Emovils.</p>
            <p>Por su seguridad, no aborde y comuniquese con la central.</p>
            <p>Razon: {res.get('razon','')}</p>
            <a href='https://wa.me/18298610090'>Contactar Emovils Central</a>
        """
    else:
        bg, emoji, titulo = "#d97706", "⚠️", "VALIDACION PENDIENTE"
        body = f"""
            <p>No podemos confirmar automaticamente que este vehiculo corresponde a su reserva.</p>
            <p>No aborde hasta comunicarse con la central de Emovils.</p>
            <p>Razon: {res.get('razon','')}</p>
            <a href='https://wa.me/18298610090'>Contactar Emovils Central</a>
        """
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — Verificacion</title>
<style>
body{{margin:0;background:{bg};color:#fff;font-family:-apple-system,Segoe UI,sans-serif;padding:24px;min-height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center}}
.emoji{{font-size:96px}}
h1{{margin:8px 0 24px;text-align:center}}
.card{{background:rgba(0,0,0,0.2);padding:16px;border-radius:12px;margin:16px 0;font-size:15px;line-height:1.7}}
a{{display:inline-block;margin-top:16px;background:#fff;color:#000;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold}}
h2{{font-size:22px}}
p{{font-size:16px;line-height:1.5}}
</style></head><body>
<div class='emoji'>{emoji}</div>
<h1>{titulo}</h1>
{body}
</body></html>"""


def _render_driver_dashboard(driver_id: str, reservas: list) -> str:
    rows = ""
    for r in reservas:
        verde = "✅" if r.get("vehicle_verification_status") == "green" else "⏳"
        rows += f"""
        <div class='card'>
            <div class='top'>
                <b>{r['booking_id']}</b>
                <span class='estado {r['booking_status']}'>{r['booking_status']}</span>
            </div>
            <div>👤 {r['customer_name']} <a href='tel:{r['customer_phone']}'>{r['customer_phone']}</a></div>
            <div>📍 <b>{r['origen']}</b> → {r['destino']}</div>
            <div>⏰ {r['service_time']} · 👥 {r['passengers']} pax · 🚗 {r['vehicle_type']}{(" · 📏 " + str(r['distancia_km']) + " km") if r.get('distancia_km') else ""}</div>
            <div>💰 RD${r['final_price']:,} · {r['payment_method']} ({r['payment_status']})</div>
            <div>Verificacion vehiculo: {verde}</div>
            {("<div>🗺️ <a href='" + r['maps_url'] + "' target='_blank'>Navegar en Google Maps</a></div>") if r.get('maps_url') else ""}
            <div class='actions'>
                <button onclick="scanQR('{r['booking_id']}')">📷 Escanear QR cliente</button>
            </div>
        </div>
        """
    if not rows:
        rows = "<p>Sin reservas asignadas.</p>"
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — Panel Conductor</title>
<style>
body{{margin:0;background:#0f172a;color:#fff;font-family:-apple-system,Segoe UI,sans-serif;padding:16px}}
h1{{font-size:20px;margin:8px 0 20px}}
.card{{background:#1e293b;padding:16px;border-radius:12px;margin-bottom:14px;line-height:1.7}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.estado{{padding:4px 10px;border-radius:6px;font-size:12px}}
.confirmed{{background:#16a34a}}
.in_progress{{background:#d97706}}
.actions{{margin-top:12px}}
button{{background:#3b82f6;color:#fff;border:0;padding:10px 16px;border-radius:8px;font-size:14px;width:100%;cursor:pointer}}
a{{color:#60a5fa}}
</style></head><body>
<h1>🚗 Panel Conductor — {driver_id}</h1>
{rows}
<script>
function scanQR(bookingId) {{
    const token = prompt('Pega el token del QR del cliente (lo lees del QR):');
    if (!token) return;
    fetch('/api/v2/mvp/qr/cliente/validar', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{booking_id: bookingId, token: token, driver_id: '{driver_id}'}})
    }}).then(r=>r.json()).then(d=>{{
        if (d.ok) {{ alert('✅ Recogida confirmada'); location.reload(); }}
        else alert('❌ ' + (d.razon || 'Error'));
    }});
}}
</script>
</body></html>"""


def _render_simple_page(titulo: str, html: str, color: str) -> str:
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Emovils — {titulo}</title>
<style>
body{{margin:0;background:{color};color:#fff;font-family:-apple-system,Segoe UI,sans-serif;padding:24px;min-height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}}
h1{{margin-bottom:24px}}
.box{{background:rgba(0,0,0,0.2);padding:24px;border-radius:12px;max-width:480px;line-height:1.7}}
</style></head><body>
<h1>{titulo}</h1>
<div class='box'>{html}</div>
</body></html>"""


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5002))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    logger.info(f"Emovils OPC v2 iniciado en puerto {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
