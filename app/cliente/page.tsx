"use client";

import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import styles from "./cliente.module.css";

interface Movimiento {
  id: string;
  comercio: string;
  fecha: string;
  montoBruto: number;
  montoDescuento: number;
  montoCobrado: number;
}
interface Perfil {
  clienteId: string;
  nombre: string | null;
  plan: string;
  qrToken: string | null;
  qrVersion: number;
  ahorroAcumulado: number;
  historial: Movimiento[];
}

const pesos = (centavos: number) =>
  (centavos / 100).toLocaleString("es-MX", { style: "currency", currency: "MXN" });

const fecha = (iso: string) =>
  new Date(iso).toLocaleDateString("es-MX", { day: "2-digit", month: "short" });

export default function ClientePage() {
  const [clienteId, setClienteId] = useState("");
  const [data, setData] = useState<Perfil | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  async function cargar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCargando(true);
    try {
      const q = new URLSearchParams({ clienteId: clienteId.trim() });
      const r = await fetch(`/api/v1/clientes/me?${q}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ?? "No se pudo cargar el perfil");
      setData(d);
    } catch (err) {
      setError((err as Error).message);
      setData(null);
    } finally {
      setCargando(false);
    }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.inner}>
        <form className={styles.form} onSubmit={cargar}>
          <input
            className={styles.input}
            value={clienteId}
            onChange={(e) => setClienteId(e.target.value)}
            placeholder="ID de cliente"
            autoComplete="off"
          />
          <button className={styles.btn} disabled={cargando || !clienteId}>
            {cargando ? "…" : "Ver"}
          </button>
        </form>

        {error && <div className={styles.error}>{error}</div>}

        {data && (
          <>
            <div className={styles.card}>
              <div className={styles.cardTop}>
                <span className={styles.brand}>Vínculo</span>
                <span className={styles.plan}>{data.plan}</span>
              </div>
              <div className={styles.nombre}>{data.nombre ?? "Cliente"}</div>
              <div className={styles.qrBox}>
                {data.qrToken ? (
                  <QRCodeSVG value={data.qrToken} size={200} level="M" />
                ) : (
                  <span style={{ color: "#111" }}>Sin QR emitido</span>
                )}
              </div>
              <p className={styles.qrHint}>Muestra este código en la caja del comercio</p>
            </div>

            <div className={styles.ahorro}>
              <div className={styles.ahorroLabel}>Ahorro acumulado</div>
              <div className={styles.ahorroValue}>{pesos(data.ahorroAcumulado)}</div>
            </div>

            <div className={styles.hist}>
              <p className={styles.histTitle}>Historial</p>
              {data.historial.length === 0 ? (
                <p className={styles.empty}>Aún no tienes consumos registrados.</p>
              ) : (
                data.historial.map((m) => (
                  <div className={styles.row} key={m.id}>
                    <div>
                      <div className={styles.rowMain}>{m.comercio}</div>
                      <div className={styles.rowSub}>{fecha(m.fecha)}</div>
                    </div>
                    <div className={styles.rowRight}>
                      <div className={styles.rowCobrado}>{pesos(m.montoCobrado)}</div>
                      <div className={styles.rowAhorro}>
                        ahorraste {pesos(m.montoDescuento)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
