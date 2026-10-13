"use client";

import { useState } from "react";
import styles from "./comercio.module.css";

interface Comision {
  tasaComisionBps: number;
  comision: number;
  iva: number;
  total: number;
}
interface Dashboard {
  comercio: { id: string; razonSocial: string; plan: string };
  periodo: string;
  moneda: string;
  negociado: boolean;
  volumen: number;
  transacciones: number;
  ahorroClientes: number;
  comisionEstimada: Comision | null;
  sucursales: { sucursal: string; volumen: number; transacciones: number }[];
}

const pesos = (centavos: number) =>
  (centavos / 100).toLocaleString("es-MX", { style: "currency", currency: "MXN" });

function periodoActual(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export default function ComercioPage() {
  const [merchantId, setMerchantId] = useState("");
  const [periodo, setPeriodo] = useState(periodoActual());
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  async function cargar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCargando(true);
    try {
      const q = new URLSearchParams({ merchantId: merchantId.trim(), periodo });
      const r = await fetch(`/api/v1/comercio/dashboard?${q}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ?? "No se pudo cargar el dashboard");
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
        <h1 className={styles.h1}>Vínculo · Comercio</h1>
        <p className={styles.sub}>Volumen, transacciones y comisión estimada del periodo.</p>

        <form className={styles.form} onSubmit={cargar}>
          <input
            className={styles.input}
            value={merchantId}
            onChange={(e) => setMerchantId(e.target.value)}
            placeholder="ID del comercio"
            autoComplete="off"
          />
          <input
            className={`${styles.input} ${styles.period}`}
            value={periodo}
            onChange={(e) => setPeriodo(e.target.value)}
            placeholder="YYYY-MM"
          />
          <button className={styles.btn} disabled={cargando || !merchantId}>
            {cargando ? "Cargando…" : "Ver"}
          </button>
        </form>

        {error && <div className={styles.error}>{error}</div>}

        {data && (
          <>
            <p className={styles.sub}>
              <strong>{data.comercio.razonSocial}</strong> · plan {data.comercio.plan}
              {data.negociado && <span className={styles.badge}>% negociado</span>} ·{" "}
              periodo {data.periodo}
            </p>

            <div className={styles.grid}>
              <div className={styles.stat}>
                <div className={styles.statLabel}>Volumen del mes</div>
                <div className={styles.statValue}>{pesos(data.volumen)}</div>
                <div className={styles.statHint}>{data.transacciones} transacciones</div>
              </div>
              <div className={styles.stat}>
                <div className={styles.statLabel}>Comisión estimada</div>
                <div className={`${styles.statValue} ${styles.accent}`}>
                  {data.comisionEstimada ? pesos(data.comisionEstimada.total) : "—"}
                </div>
                <div className={styles.statHint}>
                  {data.comisionEstimada
                    ? `${data.comisionEstimada.tasaComisionBps / 100}% + IVA · comisión ${pesos(
                        data.comisionEstimada.comision
                      )}`
                    : "sin configuración de plan"}
                </div>
              </div>
              <div className={styles.stat}>
                <div className={styles.statLabel}>Ahorro a clientes</div>
                <div className={`${styles.statValue} ${styles.green}`}>
                  {pesos(data.ahorroClientes)}
                </div>
                <div className={styles.statHint}>descuentos aplicados</div>
              </div>
            </div>

            <div className={styles.card}>
              <p className={styles.cardTitle}>Por sucursal</p>
              {data.sucursales.length === 0 ? (
                <p className={styles.empty}>Sin transacciones en el periodo.</p>
              ) : (
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Sucursal</th>
                      <th style={{ textAlign: "right" }}>Transacciones</th>
                      <th style={{ textAlign: "right" }}>Volumen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sucursales.map((s, i) => (
                      <tr key={i}>
                        <td>{s.sucursal}</td>
                        <td style={{ textAlign: "right" }}>{s.transacciones}</td>
                        <td style={{ textAlign: "right" }}>{pesos(s.volumen)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
