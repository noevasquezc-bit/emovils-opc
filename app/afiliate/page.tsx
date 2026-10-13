"use client";

import { useEffect, useState } from "react";
import styles from "./afiliate.module.css";

interface Plan {
  plan: string;
  cuotaFija: number;
  tasaComisionBps: number;
  maxSucursales: number | null;
}
interface Exito {
  merchantId: string;
  razonSocial: string;
  plan: string;
  adminEmail: string;
  sucursalId: string | null;
}

const pesos = (c: number) =>
  (c / 100).toLocaleString("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 });

export default function AfiliatePage() {
  const [planes, setPlanes] = useState<Plan[]>([]);
  const [form, setForm] = useState({
    razonSocial: "",
    rfc: "",
    email: "",
    password: "",
    telefono: "",
    nombreContacto: "",
    plan: "starter",
    sucursalNombre: "",
    sucursalPin: "",
  });
  const [exito, setExito] = useState<Exito | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  useEffect(() => {
    fetch("/api/v1/planes?countryId=MX")
      .then((r) => r.json())
      .then((d) => setPlanes(d.planes ?? []))
      .catch(() => {});
  }, []);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCargando(true);
    try {
      const r = await fetch("/api/v1/afiliacion/comercio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          razonSocial: form.razonSocial,
          rfc: form.rfc || undefined,
          countryId: "MX",
          email: form.email,
          password: form.password,
          telefono: form.telefono || undefined,
          nombreContacto: form.nombreContacto || undefined,
          plan: form.plan,
          sucursal:
            form.sucursalNombre || form.sucursalPin
              ? { nombre: form.sucursalNombre, pin: form.sucursalPin }
              : undefined,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ?? "No se pudo completar la afiliación");
      setExito(d);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCargando(false);
    }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.hero}>
        <div className={styles.brand}>ETOURS.DO</div>
        <h1 className={styles.h1}>Afilia tu negocio y llena tu local</h1>
        <p className={styles.lead}>
          Ofrece descuentos a miles de viajeros y clientes ETOURS. Tú cobras directo;
          nosotros te traemos el tráfico. Pagas solo una comisión mensual.
        </p>
      </div>

      <div className={styles.inner}>
        {exito ? (
          <div className={styles.card}>
            <div className={styles.ok}>
              <div className={styles.okCheck}>🎉</div>
              <div className={styles.okTitle}>¡{exito.razonSocial} ya es afiliado!</div>
              <p style={{ color: "#64748b", margin: 0 }}>
                Tu cuenta de administrador quedó lista.
              </p>
              <div className={styles.okData}>
                <div>Plan: <strong style={{ textTransform: "capitalize" }}>{exito.plan}</strong></div>
                <div>Acceso admin: <code>{exito.adminEmail}</code></div>
                <div>ID de comercio: <code>{exito.merchantId}</code></div>
                {exito.sucursalId && <div>Sucursal creada: <code>{exito.sucursalId}</code></div>}
              </div>
              <div className={styles.links}>
                <a className={styles.linkBtn} href="/comercio">Ver mi dashboard</a>
                <a className={`${styles.linkBtn} ${styles.ghost}`} href="/caja">Abrir mi caja</a>
              </div>
            </div>
          </div>
        ) : (
          <>
            {planes.length > 0 && (
              <div className={styles.planes}>
                {planes
                  .filter((p) => p.plan !== "scale")
                  .map((p) => (
                    <div key={p.plan} className={styles.plan}>
                      <div className={styles.planName}>{p.plan}</div>
                      <div className={styles.planPrice}>{pesos(p.cuotaFija)}<span style={{ fontSize: "0.8rem", fontWeight: 400 }}>/mes</span></div>
                      <div className={styles.planMeta}>
                        {p.tasaComisionBps / 100}% comisión ·{" "}
                        {p.maxSucursales ?? "∞"} sucursal{p.maxSucursales === 1 ? "" : "es"}
                      </div>
                    </div>
                  ))}
              </div>
            )}

            <form className={styles.card} onSubmit={enviar}>
              <p className={styles.cardTitle}>Datos de tu negocio</p>

              <label className={styles.label}>Razón social / nombre del negocio *</label>
              <input className={styles.input} value={form.razonSocial} onChange={set("razonSocial")} placeholder="Taquería La Esquina" />

              <div className={styles.rowTwo}>
                <div>
                  <label className={styles.label}>RFC (opcional)</label>
                  <input className={styles.input} value={form.rfc} onChange={set("rfc")} placeholder="XAXX010101000" />
                </div>
                <div>
                  <label className={styles.label}>Plan</label>
                  <select className={styles.select} value={form.plan} onChange={set("plan")}>
                    <option value="starter">Starter</option>
                    <option value="growth">Growth</option>
                  </select>
                </div>
              </div>

              <label className={styles.label}>Nombre de contacto</label>
              <input className={styles.input} value={form.nombreContacto} onChange={set("nombreContacto")} placeholder="Tu nombre" />

              <div className={styles.rowTwo}>
                <div>
                  <label className={styles.label}>Email (acceso admin) *</label>
                  <input className={styles.input} type="email" value={form.email} onChange={set("email")} placeholder="negocio@correo.com" />
                </div>
                <div>
                  <label className={styles.label}>Teléfono</label>
                  <input className={styles.input} value={form.telefono} onChange={set("telefono")} placeholder="+52…" />
                </div>
              </div>

              <label className={styles.label}>Contraseña * (mín. 8)</label>
              <input className={styles.input} type="password" value={form.password} onChange={set("password")} placeholder="••••••••" />

              <p className={styles.cardTitle} style={{ marginTop: "1.5rem" }}>Tu primera sucursal (opcional)</p>
              <div className={styles.rowTwo}>
                <div>
                  <label className={styles.label}>Nombre de sucursal</label>
                  <input className={styles.input} value={form.sucursalNombre} onChange={set("sucursalNombre")} placeholder="Matriz / Centro" />
                </div>
                <div>
                  <label className={styles.label}>PIN de caja</label>
                  <input className={styles.input} value={form.sucursalPin} onChange={set("sucursalPin")} placeholder="4–6 dígitos" inputMode="numeric" />
                </div>
              </div>
              <p className={styles.hint}>El PIN lo usa la cajera para entrar a la pantalla de caja.</p>

              <button className={styles.btn} disabled={cargando}>
                {cargando ? "Afiliando…" : "Afiliarme a ETOURS"}
              </button>
              {error && <div className={styles.error}>{error}</div>}
            </form>
          </>
        )}
      </div>
    </div>
  );
}
