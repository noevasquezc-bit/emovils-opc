"""
Emovils — Paginas HTML para el sistema QR
Optimizadas para movil. Sin dependencias externas.
"""

BASE_STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f0f4f8; min-height: 100vh; }
  .header { background: #1a1a2e; color: white; padding: 16px 20px;
            display: flex; align-items: center; gap: 12px; }
  .logo { font-size: 22px; font-weight: 800; letter-spacing: -0.5px; }
  .logo span { color: #4ade80; }
  .tagline { font-size: 11px; color: #94a3b8; }
  .card { background: white; border-radius: 16px; padding: 20px;
          margin: 16px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
  .label { font-size: 11px; color: #64748b; text-transform: uppercase;
           letter-spacing: 0.5px; margin-bottom: 4px; }
  .value { font-size: 15px; color: #1e293b; font-weight: 500; }
  .row { display: flex; gap: 16px; margin-bottom: 14px; }
  .col { flex: 1; }
  .divider { height: 1px; background: #f1f5f9; margin: 16px 0; }
  .btn { display: block; width: 100%; padding: 14px; border-radius: 12px;
         font-size: 15px; font-weight: 600; border: none; cursor: pointer;
         text-align: center; text-decoration: none; margin-top: 8px; }
  .btn-green { background: #22c55e; color: white; }
  .btn-gray { background: #e2e8f0; color: #475569; }
  .qr-wrap { text-align: center; padding: 16px 0; }
  .qr-wrap img { width: 200px; height: 200px; border: 8px solid #f8fafc;
                 border-radius: 12px; }
  .qr-label { font-size: 12px; color: #94a3b8; margin-top: 8px; }
  .status-badge { display: inline-block; padding: 4px 12px; border-radius: 20px;
                  font-size: 12px; font-weight: 600; }
  .badge-green { background: #dcfce7; color: #16a34a; }
  .badge-yellow { background: #fef9c3; color: #ca8a04; }
  .badge-blue { background: #dbeafe; color: #1d4ed8; }
</style>
"""


def pagina_reserva_cliente(reserva: dict, base_url: str, qr_b64: str = None) -> str:
    """
    Pagina que recibe el cliente por WhatsApp.
    Muestra sus datos de reserva y su QR de embarque.
    El conductor escanea este QR al recoger al cliente.
    """
    bid = reserva["booking_id"]
    estado = reserva.get("estado", "confirmada")
    conductor = reserva.get("conductor_nombre") or ""
    placa = reserva.get("vehiculo_placa") or ""
    color = reserva.get("vehiculo_color") or ""
    modelo = reserva.get("vehiculo_modelo") or ""
    tiene_conductor = bool(conductor)

    badge_map = {
        "confirmada": ("badge-blue", "Confirmada"),
        "conductor_asignado": ("badge-green", "Conductor Asignado"),
        "en_camino": ("badge-green", "En Camino"),
        "completada": ("badge-green", "Completada"),
    }
    badge_cls, badge_txt = badge_map.get(estado, ("badge-yellow", estado.title()))

    qr_html = ""
    if qr_b64:
        qr_html = f"""
        <div class="qr-wrap">
          <img src="data:image/png;base64,{qr_b64}" alt="Tu QR de embarque">
          <p class="qr-label">Muestra este QR al conductor al abordar</p>
        </div>"""
    else:
        token = reserva.get("token_cliente", "")
        verify_url = f"{base_url}/driver/scan/{bid}?t={token}"
        qr_html = f"""
        <div class="qr-wrap" style="padding:20px;background:#f8fafc;border-radius:12px;">
          <div style="font-size:48px">🎫</div>
          <p style="margin-top:8px;font-weight:600;color:#1e293b">QR de Embarque</p>
          <p class="qr-label" style="margin-top:4px">ID: {bid}</p>
        </div>"""

    conductor_html = ""
    if tiene_conductor:
        conductor_html = f"""
        <div class="divider"></div>
        <p class="label">Tu Conductor</p>
        <div class="row" style="margin-top:8px;align-items:center">
          <div style="width:48px;height:48px;background:#1a1a2e;border-radius:50%;
                      display:flex;align-items:center;justify-content:center;
                      font-size:20px;flex-shrink:0">🧑‍✈️</div>
          <div>
            <div class="value">{conductor}</div>
            <div class="label" style="margin:2px 0 0">{color} {modelo} • {placa}</div>
          </div>
        </div>
        <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;
                    padding:12px;margin-top:12px;font-size:13px;color:#166534;">
          ✅ Al llegar el vehículo, escanee el QR en la puerta para verificar.
          Si aparece pantalla verde = es su conductor. Si sale rojo = NO aborde.
        </div>"""
    else:
        conductor_html = f"""
        <div class="divider"></div>
        <div style="background:#fef3c7;border-radius:10px;padding:12px;
                    font-size:13px;color:#92400e;">
          ⏳ Estamos asignando su conductor. Le notificaremos por WhatsApp
          en cuanto esté confirmado.
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head><title>Tu Reserva — Emovils</title>{BASE_STYLE}</head>
<body>
<div class="header">
  <div>
    <div class="logo">Emo<span>vils</span></div>
    <div class="tagline">Traslados Ejecutivos</div>
  </div>
  <div style="margin-left:auto">
    <span class="status-badge {badge_cls}">{badge_txt}</span>
  </div>
</div>

<div class="card">
  <p style="font-size:13px;color:#64748b;margin-bottom:12px">Reserva #{bid}</p>
  <div class="row">
    <div class="col"><p class="label">Fecha</p><p class="value">{reserva.get('fecha','')}</p></div>
    <div class="col"><p class="label">Hora</p><p class="value">{reserva.get('hora','')}</p></div>
  </div>
  <div class="row">
    <div class="col"><p class="label">Origen</p><p class="value">{reserva.get('origen','')}</p></div>
    <div class="col"><p class="label">Destino</p><p class="value">{reserva.get('destino','')}</p></div>
  </div>
  <div class="row">
    <div class="col"><p class="label">Pasajeros</p><p class="value">{reserva.get('pasajeros','')}</p></div>
    <div class="col"><p class="label">Precio</p><p class="value">RD${reserva.get('precio_rd',0):,}</p></div>
  </div>
  {conductor_html}
</div>

<div class="card">
  <p class="label" style="margin-bottom:12px">Tu QR de Embarque</p>
  {qr_html}
  <p style="font-size:12px;color:#94a3b8;text-align:center;margin-top:8px">
    El conductor escaneará este código para confirmar tu identidad
  </p>
</div>

<div style="padding:0 16px 32px">
  <a href="tel:{reserva.get('conductor_telefono','')}" class="btn btn-gray"
     {'style="display:none"' if not tiene_conductor else ''}>
    📞 Llamar al conductor
  </a>
</div>
</body></html>"""


def pagina_verificacion_vehiculo(resultado: str, reserva: dict = None,
                                  vehiculo: dict = None) -> str:
    """
    Pagina que ve el cliente al escanear el QR de la puerta del vehiculo.
    resultado: 'verde' | 'rojo' | 'amarillo'
    """
    if resultado == "verde":
        bg = "#16a34a"
        icono = "✅"
        titulo = "¡Vehículo Correcto!"
        subtitulo = "Este es su conductor asignado"
        msg_color = "#f0fdf4"
        border_color = "#86efac"
        msg_text_color = "#166534"
        instruccion = "Puede abordar con seguridad."
    elif resultado == "rojo":
        bg = "#dc2626"
        icono = "❌"
        titulo = "Vehículo Incorrecto"
        subtitulo = "Este NO es su conductor asignado"
        msg_color = "#fef2f2"
        border_color = "#fca5a5"
        msg_text_color = "#991b1b"
        instruccion = "NO aborde. Contacte a Emovils inmediatamente."
    else:  # amarillo
        bg = "#ca8a04"
        icono = "⚠️"
        titulo = "Verificación Pendiente"
        subtitulo = "Contacte a la central antes de abordar"
        msg_color = "#fefce8"
        border_color = "#fde047"
        msg_text_color = "#854d0e"
        instruccion = "Llame a la central para confirmar su conductor."

    conductor_html = ""
    if resultado == "verde" and reserva:
        conductor_html = f"""
        <div class="card" style="margin-top:16px">
          <p class="label" style="margin-bottom:12px">Información de su Conductor</p>
          <div class="row" style="align-items:center">
            <div style="width:56px;height:56px;background:#1a1a2e;border-radius:50%;
                        display:flex;align-items:center;justify-content:center;
                        font-size:24px;flex-shrink:0">🧑‍✈️</div>
            <div>
              <div class="value" style="font-size:17px">{reserva.get('conductor_nombre','')}</div>
              <div class="label" style="margin-top:2px">
                {reserva.get('vehiculo_color','')} {reserva.get('vehiculo_modelo','')}
              </div>
              <div style="font-size:14px;font-weight:700;color:#1a1a2e;margin-top:2px">
                Placa: {reserva.get('vehiculo_placa','')}
              </div>
            </div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head><title>Verificación de Vehículo — Emovils</title>{BASE_STYLE}</head>
<body>
<div class="header">
  <div>
    <div class="logo">Emo<span>vils</span></div>
    <div class="tagline">Verificación de Vehículo</div>
  </div>
</div>

<div style="background:{bg};padding:40px 20px;text-align:center;color:white">
  <div style="font-size:80px;margin-bottom:16px">{icono}</div>
  <h1 style="font-size:24px;font-weight:800;margin-bottom:8px">{titulo}</h1>
  <p style="font-size:15px;opacity:0.9">{subtitulo}</p>
</div>

<div style="margin:16px">
  <div style="background:{msg_color};border:1px solid {border_color};border-radius:12px;
              padding:16px;color:{msg_text_color};font-size:14px;font-weight:500">
    {instruccion}
  </div>
</div>

{conductor_html}

<div style="padding:0 16px 32px;margin-top:8px">
  <a href="tel:+18099999999" class="btn btn-gray">📞 Llamar a Emovils Central</a>
</div>
</body></html>"""


def pagina_conductor_scan(reserva: dict, resultado: str) -> str:
    """
    Pagina que ve el conductor cuando escanea el QR del cliente.
    resultado: 'ok' | 'ya_usado' | 'no_encontrado'
    """
    if resultado == "ok":
        bg = "#16a34a"
        icono = "✅"
        titulo = "Pasajero Verificado"
        subtitulo = "Confirme el abordaje"
        btn_html = f"""
        <form method="POST" action="/driver/confirmar-abordaje" style="padding:16px">
          <input type="hidden" name="booking_id" value="{reserva.get('booking_id','') if reserva else ''}">
          <button type="submit" class="btn btn-green">Confirmar Abordaje</button>
        </form>"""
    elif resultado == "ya_usado":
        bg = "#ca8a04"
        icono = "⚠️"
        titulo = "QR Ya Utilizado"
        subtitulo = "Este código ya fue escaneado"
        btn_html = ""
    else:
        bg = "#dc2626"
        icono = "❌"
        titulo = "QR Inválido"
        subtitulo = "Reserva no encontrada"
        btn_html = ""

    pasajero_html = ""
    if reserva and resultado == "ok":
        pasajero_html = f"""
        <div class="card">
          <p class="label" style="margin-bottom:12px">Datos del Pasajero</p>
          <div class="row">
            <div class="col"><p class="label">Nombre</p>
              <p class="value">{reserva.get('cliente_nombre','')}</p></div>
            <div class="col"><p class="label">Pasajeros</p>
              <p class="value">{reserva.get('pasajeros','')}</p></div>
          </div>
          <div class="row">
            <div class="col"><p class="label">Origen</p>
              <p class="value">{reserva.get('origen','')}</p></div>
            <div class="col"><p class="label">Destino</p>
              <p class="value">{reserva.get('destino','')}</p></div>
          </div>
          <div class="row">
            <div class="col"><p class="label">Reserva</p>
              <p class="value">{reserva.get('booking_id','')}</p></div>
            <div class="col"><p class="label">Precio</p>
              <p class="value">RD${reserva.get('precio_rd',0):,}</p></div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head><title>Verificación Conductor — Emovils</title>{BASE_STYLE}</head>
<body>
<div class="header">
  <div>
    <div class="logo">Emo<span>vils</span></div>
    <div class="tagline">Panel del Conductor</div>
  </div>
</div>

<div style="background:{bg};padding:32px 20px;text-align:center;color:white">
  <div style="font-size:72px;margin-bottom:12px">{icono}</div>
  <h1 style="font-size:22px;font-weight:800;margin-bottom:6px">{titulo}</h1>
  <p style="font-size:14px;opacity:0.9">{subtitulo}</p>
</div>

{pasajero_html}
{btn_html}
</body></html>"""
