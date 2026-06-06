"""
Emovils — Dashboard del Dueño
Panel de control en tiempo real para gestionar reservas, conductores y vehiculos.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emovils — Panel de Control</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f172a; color: #e2e8f0; min-height: 100vh; }

/* HEADER */
.header { background: #1e293b; border-bottom: 1px solid #334155;
          padding: 14px 24px; display: flex; align-items: center;
          justify-content: space-between; position: sticky; top: 0; z-index: 100; }
.logo { font-size: 20px; font-weight: 800; }
.logo span { color: #4ade80; }
.header-right { display: flex; align-items: center; gap: 16px; }
.live-dot { width: 8px; height: 8px; background: #4ade80; border-radius: 50%;
            animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
.live-text { font-size: 12px; color: #4ade80; }
.refresh-btn { background: #334155; border: none; color: #94a3b8; padding: 6px 14px;
               border-radius: 8px; cursor: pointer; font-size: 13px; }
.refresh-btn:hover { background: #475569; }

/* METRICS */
.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
           padding: 20px 24px; }
@media(max-width:768px){ .metrics { grid-template-columns: repeat(2,1fr); } }
.metric-card { background: #1e293b; border-radius: 12px; padding: 16px;
               border: 1px solid #334155; }
.metric-label { font-size: 11px; color: #64748b; text-transform: uppercase;
                letter-spacing: 0.5px; margin-bottom: 8px; }
.metric-value { font-size: 28px; font-weight: 700; }
.metric-sub { font-size: 12px; color: #64748b; margin-top: 4px; }
.green { color: #4ade80; } .yellow { color: #fbbf24; }
.blue { color: #60a5fa; } .red { color: #f87171; }

/* TABS */
.tabs { display: flex; gap: 4px; padding: 0 24px 16px; border-bottom: 1px solid #334155; }
.tab { padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px;
       color: #64748b; border: none; background: transparent; }
.tab.active { background: #334155; color: #e2e8f0; font-weight: 600; }
.tab:hover { background: #1e293b; }

/* CONTENT */
.content { padding: 20px 24px; }

/* TABLE */
.table-wrap { background: #1e293b; border-radius: 12px; border: 1px solid #334155;
              overflow: hidden; }
.table-header { display: grid; grid-template-columns: 140px 1fr 1fr 100px 100px 120px 140px;
                padding: 12px 16px; background: #0f172a; font-size: 11px;
                color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
.table-row { display: grid; grid-template-columns: 140px 1fr 1fr 100px 100px 120px 140px;
             padding: 14px 16px; border-top: 1px solid #334155; align-items: center;
             font-size: 13px; transition: background 0.15s; }
.table-row:hover { background: #334155; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 20px;
         font-size: 11px; font-weight: 600; white-space: nowrap; }
.badge-green { background: #14532d; color: #4ade80; }
.badge-yellow { background: #451a03; color: #fbbf24; }
.badge-blue { background: #1e3a5f; color: #60a5fa; }
.badge-red { background: #450a0a; color: #f87171; }
.badge-gray { background: #1e293b; color: #64748b; }
.btn-sm { padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;
          cursor: pointer; border: none; }
.btn-primary { background: #3b82f6; color: white; }
.btn-primary:hover { background: #2563eb; }
.btn-green { background: #16a34a; color: white; }
.empty { padding: 48px; text-align: center; color: #475569; }
.empty-icon { font-size: 40px; margin-bottom: 12px; }

/* MODAL */
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
                 z-index: 200; align-items: center; justify-content: center; }
.modal-overlay.open { display: flex; }
.modal { background: #1e293b; border-radius: 16px; padding: 24px; width: 90%;
         max-width: 480px; border: 1px solid #334155; }
.modal h3 { font-size: 16px; font-weight: 700; margin-bottom: 16px; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: 12px; color: #94a3b8;
                    margin-bottom: 6px; }
.form-group input, .form-group select {
  width: 100%; background: #0f172a; border: 1px solid #334155;
  border-radius: 8px; padding: 10px 12px; color: #e2e8f0; font-size: 14px; }
.form-group input:focus, .form-group select:focus {
  outline: none; border-color: #3b82f6; }
.modal-actions { display: flex; gap: 10px; margin-top: 20px; justify-content: flex-end; }
.btn-cancel { background: #334155; color: #94a3b8; padding: 8px 18px;
              border-radius: 8px; border: none; cursor: pointer; font-size: 14px; }

/* NUEVA RESERVA FORM */
.form-card { background: #1e293b; border-radius: 12px; padding: 20px;
             border: 1px solid #334155; max-width: 640px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media(max-width:600px){ .form-grid { grid-template-columns: 1fr; } }

/* VEHICULOS */
.vehicle-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px,1fr));
                gap: 16px; }
.vehicle-card { background: #1e293b; border-radius: 12px; padding: 16px;
                border: 1px solid #334155; }
.vehicle-plate { font-size: 20px; font-weight: 800; letter-spacing: 2px;
                 color: #fbbf24; margin-bottom: 8px; }
.qr-mini { font-size: 11px; color: #64748b; word-break: break-all; }

/* TOAST */
.toast { position: fixed; bottom: 24px; right: 24px; background: #16a34a;
         color: white; padding: 12px 20px; border-radius: 10px; font-size: 14px;
         font-weight: 600; z-index: 300; display: none; }
.toast.show { display: block; animation: slideIn 0.3s ease; }
@keyframes slideIn { from{transform:translateY(20px);opacity:0} to{transform:translateY(0);opacity:1} }
</style>
</head>
<body>

<div class="header">
  <div class="logo">Emo<span>vils</span> <span style="font-size:13px;color:#64748b;font-weight:400">Panel de Control</span></div>
  <div class="header-right">
    <div class="live-dot"></div>
    <span class="live-text">EN VIVO</span>
    <button class="refresh-btn" onclick="cargarDatos()">↻ Actualizar</button>
  </div>
</div>

<!-- METRICS -->
<div class="metrics" id="metrics">
  <div class="metric-card">
    <div class="metric-label">Reservas Hoy</div>
    <div class="metric-value green" id="m-hoy">—</div>
    <div class="metric-sub">Total del día</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">En Progreso</div>
    <div class="metric-value yellow" id="m-activas">—</div>
    <div class="metric-sub">Servicios activos</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Sin Conductor</div>
    <div class="metric-value red" id="m-sin-conductor">—</div>
    <div class="metric-sub">Requieren asignación</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Completadas</div>
    <div class="metric-value blue" id="m-completadas">—</div>
    <div class="metric-sub">Servicios finalizados</div>
  </div>
</div>

<!-- TABS -->
<div class="tabs">
  <button class="tab active" onclick="cambiarTab('reservas', this)">📋 Reservas</button>
  <button class="tab" onclick="cambiarTab('nueva', this)">➕ Nueva Reserva</button>
  <button class="tab" onclick="cambiarTab('vehiculos', this)">🚗 Vehículos</button>
</div>

<div class="content">

<!-- TAB: RESERVAS -->
<div id="tab-reservas">
  <div class="table-wrap">
    <div class="table-header">
      <div>Reserva</div>
      <div>Cliente</div>
      <div>Ruta</div>
      <div>Hora</div>
      <div>Precio</div>
      <div>Estado</div>
      <div>Acciones</div>
    </div>
    <div id="tabla-reservas">
      <div class="empty"><div class="empty-icon">📋</div><p>Cargando reservas...</p></div>
    </div>
  </div>
</div>

<!-- TAB: NUEVA RESERVA -->
<div id="tab-nueva" style="display:none">
  <div class="form-card">
    <h3 style="margin-bottom:16px;font-size:16px">Nueva Reserva Manual</h3>
    <div class="form-grid">
      <div class="form-group">
        <label>Nombre del cliente</label>
        <input type="text" id="f-nombre" placeholder="Nombre completo">
      </div>
      <div class="form-group">
        <label>Teléfono / WhatsApp</label>
        <input type="text" id="f-telefono" placeholder="+1 809...">
      </div>
      <div class="form-group">
        <label>Origen (recogida)</label>
        <input type="text" id="f-origen" placeholder="Dirección o sector">
      </div>
      <div class="form-group">
        <label>Destino</label>
        <input type="text" id="f-destino" placeholder="Destino final">
      </div>
      <div class="form-group">
        <label>Fecha</label>
        <input type="date" id="f-fecha">
      </div>
      <div class="form-group">
        <label>Hora</label>
        <input type="time" id="f-hora">
      </div>
      <div class="form-group">
        <label>Pasajeros</label>
        <select id="f-pasajeros">
          <option>1</option><option>2</option><option>3</option><option>4</option>
          <option>5</option><option>6</option><option>7</option>
        </select>
      </div>
      <div class="form-group">
        <label>Precio (RD$)</label>
        <input type="number" id="f-precio" placeholder="1500">
      </div>
      <div class="form-group">
        <label>Vehículo</label>
        <select id="f-vehiculo"><option value="sedan">Sedan</option><option value="van">Van</option></select>
      </div>
      <div class="form-group">
        <label>Forma de pago</label>
        <select id="f-pago">
          <option value="efectivo">Efectivo</option>
          <option value="tarjeta">Tarjeta</option>
          <option value="en linea">En línea</option>
        </select>
      </div>
    </div>
    <button class="btn-sm btn-primary" style="width:100%;padding:12px;font-size:15px;margin-top:8px"
            onclick="crearReserva()">Crear Reserva y Enviar QR al Cliente</button>
  </div>
</div>

<!-- TAB: VEHICULOS -->
<div id="tab-vehiculos" style="display:none">
  <div style="margin-bottom:16px">
    <div class="form-card" style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:12px;align-items:end;max-width:100%">
      <div class="form-group" style="margin:0">
        <label>Placa</label>
        <input type="text" id="v-placa" placeholder="A123456">
      </div>
      <div class="form-group" style="margin:0">
        <label>Modelo</label>
        <input type="text" id="v-modelo" placeholder="Toyota Camry">
      </div>
      <div class="form-group" style="margin:0">
        <label>Color</label>
        <input type="text" id="v-color" placeholder="Negro">
      </div>
      <button class="btn-sm btn-primary" style="padding:10px 16px;white-space:nowrap"
              onclick="registrarVehiculo()">+ Agregar</button>
    </div>
  </div>
  <div class="vehicle-grid" id="lista-vehiculos">
    <div class="empty"><div class="empty-icon">🚗</div><p>Sin vehículos registrados</p></div>
  </div>
</div>

</div><!-- end content -->

<!-- MODAL ASIGNAR CONDUCTOR -->
<div class="modal-overlay" id="modal-asignar">
  <div class="modal">
    <h3>Asignar Conductor</h3>
    <input type="hidden" id="modal-booking-id">
    <div class="form-group">
      <label>Nombre del conductor</label>
      <input type="text" id="c-nombre" placeholder="Nombre completo">
    </div>
    <div class="form-group">
      <label>Teléfono del conductor</label>
      <input type="text" id="c-telefono" placeholder="+1 829...">
    </div>
    <div class="form-group">
      <label>ID Vehículo</label>
      <input type="text" id="c-vehiculo-id" placeholder="VH-XXXXXX">
    </div>
    <div class="form-group">
      <label>Placa</label>
      <input type="text" id="c-placa" placeholder="A123456">
    </div>
    <div class="form-group">
      <label>Color y Modelo</label>
      <input type="text" id="c-vehiculo-desc" placeholder="Negro Toyota Camry">
    </div>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="cerrarModal()">Cancelar</button>
      <button class="btn-sm btn-green" style="padding:8px 20px;font-size:14px" onclick="confirmarAsignacion()">Asignar</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const API = '';  // mismo origen

let reservasData = [];
let vehiculosData = [];

const ESTADOS = {
  'confirmada':         ['badge-blue',   'Confirmada'],
  'conductor_asignado': ['badge-green',  'Conductor ✓'],
  'en_camino':          ['badge-yellow', 'En Camino'],
  'completada':         ['badge-green',  'Completada'],
  'cancelada':          ['badge-red',    'Cancelada'],
};

function badge(estado) {
  const [cls, txt] = ESTADOS[estado] || ['badge-gray', estado];
  return `<span class="badge ${cls}">${txt}</span>`;
}

function toast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = ok ? '#16a34a' : '#dc2626';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

async function cargarDatos() {
  try {
    const r = await fetch('/api/dashboard');
    const d = await r.json();
    reservasData = d.reservas || [];
    vehiculosData = d.vehiculos || [];
    renderMetrics(d);
    renderReservas();
    renderVehiculos();
  } catch(e) {
    console.error(e);
  }
}

function renderMetrics(d) {
  const hoy = new Date().toISOString().split('T')[0];
  const hoyCount = reservasData.filter(r => r.creada_en && r.creada_en.startsWith(hoy)).length;
  const activas = reservasData.filter(r => ['confirmada','conductor_asignado','en_camino'].includes(r.estado)).length;
  const sinConductor = reservasData.filter(r => r.estado === 'confirmada' && !r.conductor_nombre).length;
  const completadas = reservasData.filter(r => r.estado === 'completada').length;
  document.getElementById('m-hoy').textContent = hoyCount;
  document.getElementById('m-activas').textContent = activas;
  document.getElementById('m-sin-conductor').textContent = sinConductor;
  document.getElementById('m-completadas').textContent = completadas;
}

function renderReservas() {
  const el = document.getElementById('tabla-reservas');
  if (!reservasData.length) {
    el.innerHTML = '<div class="empty"><div class="empty-icon">📋</div><p>No hay reservas aún</p></div>';
    return;
  }
  const sorted = [...reservasData].sort((a,b) => b.creada_en > a.creada_en ? 1 : -1);
  el.innerHTML = sorted.map(r => {
    const sinCond = !r.conductor_nombre;
    const btn = sinCond
      ? `<button class="btn-sm btn-primary" onclick="abrirModal('${r.booking_id}')">Asignar</button>`
      : `<span style="font-size:12px;color:#4ade80">✓ ${r.conductor_nombre}</span>`;
    return `<div class="table-row">
      <div style="font-size:11px;color:#94a3b8;font-weight:600">${r.booking_id}</div>
      <div>
        <div style="font-weight:600">${r.cliente_nombre || '—'}</div>
        <div style="font-size:11px;color:#64748b">${r.cliente_telefono || ''}</div>
      </div>
      <div>
        <div style="font-size:12px">${r.origen || '—'}</div>
        <div style="font-size:11px;color:#64748b">→ ${r.destino || ''}</div>
      </div>
      <div>
        <div>${r.hora || '—'}</div>
        <div style="font-size:11px;color:#64748b">${r.fecha || ''}</div>
      </div>
      <div style="font-weight:700;color:#4ade80">RD$${(r.precio_rd||0).toLocaleString()}</div>
      <div>${badge(r.estado)}</div>
      <div>${btn}</div>
    </div>`;
  }).join('');
}

function renderVehiculos() {
  const el = document.getElementById('lista-vehiculos');
  if (!vehiculosData.length) {
    el.innerHTML = '<div class="empty"><div class="empty-icon">🚗</div><p>Sin vehículos registrados</p></div>';
    return;
  }
  el.innerHTML = vehiculosData.map(v => `
    <div class="vehicle-card">
      <div class="vehicle-plate">${v.placa}</div>
      <div style="font-size:13px;margin-bottom:4px">${v.color || ''} ${v.modelo || ''}</div>
      <div style="font-size:11px;color:#64748b;margin-bottom:8px">${v.vehicle_id}</div>
      <div style="font-size:11px;color:#4ade80">${v.activo ? '● Activo' : '○ Inactivo'}</div>
      <div class="qr-mini" style="margin-top:8px">
        QR: ${window.location.origin}/v/${v.vehicle_id}
      </div>
    </div>
  `).join('');
}

function cambiarTab(tab, el) {
  ['reservas','nueva','vehiculos'].forEach(t => {
    document.getElementById('tab-'+t).style.display = t === tab ? 'block' : 'none';
  });
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  if (tab === 'vehiculos') cargarDatos();
}

function abrirModal(bookingId) {
  document.getElementById('modal-booking-id').value = bookingId;
  ['c-nombre','c-telefono','c-vehiculo-id','c-placa','c-vehiculo-desc'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('modal-asignar').classList.add('open');
}

function cerrarModal() {
  document.getElementById('modal-asignar').classList.remove('open');
}

async function confirmarAsignacion() {
  const bookingId = document.getElementById('modal-booking-id').value;
  const desc = document.getElementById('c-vehiculo-desc').value.split(' ');
  const body = {
    conductor: {
      id: 'DR-' + Date.now(),
      nombre: document.getElementById('c-nombre').value,
      telefono: document.getElementById('c-telefono').value,
    },
    vehiculo: {
      id: document.getElementById('c-vehiculo-id').value || 'VH-' + Date.now(),
      placa: document.getElementById('c-placa').value,
      color: desc[0] || '',
      modelo: desc.slice(1).join(' ') || '',
    }
  };
  try {
    const r = await fetch(`/reserva/${bookingId}/asignar-conductor`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (d.ok) {
      toast('✓ Conductor asignado. Cliente notificado por WhatsApp.');
      cerrarModal(); cargarDatos();
    } else { toast('Error: ' + JSON.stringify(d), false); }
  } catch(e) { toast('Error de conexión', false); }
}

async function crearReserva() {
  const datos = {
    nombre: document.getElementById('f-nombre').value,
    telefono: document.getElementById('f-telefono').value,
    whatsapp: document.getElementById('f-telefono').value,
    origen: document.getElementById('f-origen').value,
    destino: document.getElementById('f-destino').value,
    fecha: document.getElementById('f-fecha').value,
    hora: document.getElementById('f-hora').value,
    pasajeros: parseInt(document.getElementById('f-pasajeros').value),
    precio: parseFloat(document.getElementById('f-precio').value),
    vehiculo: document.getElementById('f-vehiculo').value,
    forma_pago: document.getElementById('f-pago').value,
  };
  if (!datos.nombre || !datos.telefono || !datos.origen || !datos.destino) {
    toast('Completa todos los campos', false); return;
  }
  try {
    const r = await fetch('/reserva/crear', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(datos)
    });
    const d = await r.json();
    if (d.booking_id) {
      toast('✓ Reserva creada: ' + d.booking_id + ' — QR enviado al cliente');
      setTimeout(() => cambiarTab('reservas', document.querySelectorAll('.tab')[0]), 1500);
      cargarDatos();
    } else { toast('Error: ' + JSON.stringify(d), false); }
  } catch(e) { toast('Error de conexión', false); }
}

async function registrarVehiculo() {
  const datos = {
    placa: document.getElementById('v-placa').value,
    modelo: document.getElementById('v-modelo').value,
    color: document.getElementById('v-color').value,
  };
  if (!datos.placa) { toast('La placa es requerida', false); return; }
  try {
    const r = await fetch('/vehiculo/registrar', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(datos)
    });
    const d = await r.json();
    if (d.vehicle_id) {
      toast('✓ Vehículo ' + datos.placa + ' registrado. QR generado.');
      ['v-placa','v-modelo','v-color'].forEach(id => document.getElementById(id).value = '');
      cargarDatos();
    } else { toast('Error', false); }
  } catch(e) { toast('Error de conexión', false); }
}

// Auto-refresh cada 30 segundos
cargarDatos();
setInterval(cargarDatos, 30000);
</script>
</body>
</html>"""
