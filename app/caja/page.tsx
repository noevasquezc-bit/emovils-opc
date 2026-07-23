"use client";

import { useState } from "react";
import styles from "./caja.module.css";

// ── Tipos de las respuestas de la API ──
interface Validacion {
  clienteId: string;
  nombre: string;
  plan: string;
  tasaDescuentoBps: number;
}
interface Resultado {
  montoBruto: number;
  montoDescuento: number;
  montoCobrado: number;
  moneda: string;
}

const pesos = (centavos: number) =>
  (centavos / 100).toLocaleString("es-MX", { style: "currency", currency: "MXN" });

export default function CajaPage() {
  // Sesión de caja
  const [token, setToken] = useState<string | null>(null);
  const [sucursalNombre, setSucursalNombre] = useState("");
  const [sucursalId, setSucursalId] = useState("");
  const [pin, setPin] = useState("");

  // Venta
  const [qr, setQr] = useState("");
  const [cliente, setCliente] = useState<Validacion | null>(null);
  const [montoPesos, setMontoPesos] = useState("");
  const [resultado, setResultado] = useState<Resultado | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  // Preview del descuento (cálculo en centavos, como el backend)
  const brutoCentavos = Math.round((parseFloat(montoPesos) || 0) * 100);
  const descPreview = cliente
    ? Math.floor((brutoCentavos * cliente.tasaDescuentoBps) / 10000)
    : 0;
  const cobrarPreview = brutoCentavos - descPreview;

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCargando(true);
    try {
      const r = await fetch("/api/v1/sucursal/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sucursalId: sucursalId.trim(), pin: pin.trim() }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error ?? "No se pudo iniciar sesión");
      setToken(data.token);
      setSucursalNombre(data.nombre);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCargando(false);
    }
  }

  async function validarQr() {
    setError(null);
    setCliente(null);
    setResultado(null);
    setCargando(true);
    try {
      const r = await fetch("/api/v1/qr/validar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ qrToken: qr.trim() }),
      });
      const data = await r.json();
      if (!r.ok || !data.valido) {
        throw new Error(mensajeQr(data.error));
      }
      setCliente(data);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCargando(false);
    }
  }

  async function registrar() {
    if (!cliente || !token || brutoCentavos <= 0) return;
    setError(null);
    setCargando(true);
    try {
      const r = await fetch("/api/v1/transacciones", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          qrToken: qr.trim(),
          montoBruto: brutoCentavos,
          idempotencyKey: crypto.randomUUID(),
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error ?? "No se pudo registrar la venta");
      setResultado(data);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCargando(false);
    }
  }

  function nuevaVenta() {
    setQr("");
    setCliente(null);
    setMontoPesos("");
    setResultado(null);
    setError(null);
  }

  function salir() {
    setToken(null);
    setPin("");
    nuevaVenta();
  }

  // ── Pantalla de login ──
  if (!token) {
    return (
      <div className={styles.wrap}>
        <form className={styles.card} onSubmit={login}>
          <p className={styles.brand}>Vínculo · Caja</p>
          <p className={styles.sub}>Inicia sesión con el PIN de tu sucursal</p>

          <label className={styles.label}>ID de sucursal</label>
          <input
            className={styles.input}
            value={sucursalId}
            onChange={(e) => setSucursalId(e.target.value)}
            placeholder="Identificador de la sucursal"
            autoComplete="off"
          />

          <label className={styles.label}>PIN</label>
          <input
            className={styles.input}
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            placeholder="••••"
            inputMode="numeric"
            type="password"
          />

          <button className={styles.btn} disabled={cargando || !sucursalId || !pin}>
            {cargando ? "Entrando…" : "Entrar"}
          </button>
          {error && <div className={styles.error}>{error}</div>}
        </form>
      </div>
    );
  }

  // ── Pantalla de venta ──
  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.topbar}>
          <span className={styles.suc}>📍 {sucursalNombre}</span>
          <button className={styles.logout} onClick={salir}>
            Salir
          </button>
        </div>

        {resultado ? (
          // Confirmación
          <div className={styles.ok}>
            <div className={styles.okCheck}>✅</div>
            <p className={styles.sub} style={{ margin: 0 }}>Cobrar al cliente</p>
            <div className={styles.okMonto}>{pesos(resultado.montoCobrado)}</div>
            <p className={styles.okAhorro}>
              Ahorro aplicado: {pesos(resultado.montoDescuento)}
            </p>
            <button className={styles.btn} onClick={nuevaVenta}>
              Nueva venta
            </button>
          </div>
        ) : (
          <>
            <p className={styles.brand}>Nueva venta</p>
            <p className={styles.sub}>Escanea o pega el QR del cliente</p>

            <label className={styles.label}>QR del cliente</label>
            <input
              className={styles.input}
              value={qr}
              onChange={(e) => setQr(e.target.value)}
              placeholder="VINCULO|v1|MX|…"
              autoComplete="off"
            />
            <button
              className={`${styles.btn} ${styles.btnGhost}`}
              onClick={validarQr}
              disabled={cargando || !qr.trim()}
            >
              {cargando && !cliente ? "Validando…" : "Validar QR"}
            </button>

            {cliente && (
              <>
                <div className={styles.cliente}>
                  <span className={styles.clienteNombre}>{cliente.nombre}</span>
                  <span className={styles.badge}>
                    {cliente.plan} · {cliente.tasaDescuentoBps / 100}%
                  </span>
                </div>

                <label className={styles.label}>Monto de la cuenta (MXN)</label>
                <input
                  className={styles.input}
                  value={montoPesos}
                  onChange={(e) => setMontoPesos(e.target.value)}
                  placeholder="0.00"
                  inputMode="decimal"
                />

                {brutoCentavos > 0 && (
                  <div className={styles.rows}>
                    <div className={styles.row}>
                      <span className="k">Subtotal</span>
                      <span className="v">{pesos(brutoCentavos)}</span>
                    </div>
                    <div className={styles.row}>
                      <span className="k">Descuento</span>
                      <span className={`v ${styles.descuento}`}>
                        −{pesos(descPreview)}
                      </span>
                    </div>
                    <div className={styles.row}>
                      <span className="k">A cobrar</span>
                      <span className={`v ${styles.total}`}>{pesos(cobrarPreview)}</span>
                    </div>
                  </div>
                )}

                <button
                  className={styles.btn}
                  onClick={registrar}
                  disabled={cargando || brutoCentavos <= 0}
                >
                  {cargando ? "Registrando…" : "Aplicar descuento y registrar"}
                </button>
              </>
            )}
          </>
        )}

        {error && <div className={styles.error}>{error}</div>}
      </div>
    </div>
  );
}

function mensajeQr(error?: string): string {
  switch (error) {
    case "qr_caducado":
      return "El QR está caducado. Pide al cliente regenerarlo desde su app.";
    case "cliente_suspendido":
      return "La cuenta del cliente está suspendida.";
    case "cliente_no_encontrado":
      return "Cliente no encontrado.";
    case "firma_invalida":
    case "formato":
    case "prefijo_o_version":
      return "QR inválido o dañado.";
    case "expirado":
      return "El QR expiró.";
    default:
      return "No se pudo validar el QR.";
  }
}
