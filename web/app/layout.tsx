import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rugby Stats",
  description: "Tackles por jugador, sacados del video del partido.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#1f8a4c" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
