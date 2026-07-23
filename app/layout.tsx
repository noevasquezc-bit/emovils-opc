import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vínculo",
  description: "Plataforma de afiliación comercio-cliente",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
