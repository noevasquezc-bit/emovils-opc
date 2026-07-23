export default function Home() {
  return (
    <main style={{ padding: "2.5rem", maxWidth: 640 }}>
      <h1>Vínculo</h1>
      <p>Plataforma de afiliación comercio-cliente.</p>
      <p style={{ color: "#666" }}>
        Arquitectura base — Next.js 15, Prisma y NextAuth. Ver{" "}
        <code>SPEC.md</code> para el modelo funcional completo.
      </p>
      <p>
        <a href="/caja" style={{ color: "#4f46e5", fontWeight: 600 }}>
          → Abrir pantalla de caja
        </a>
      </p>
      <p>
        <a href="/comercio" style={{ color: "#4f46e5", fontWeight: 600 }}>
          → Dashboard de comercio
        </a>
      </p>
    </main>
  );
}
