import Link from "next/link";
import { compu } from "@/lib/db";

export default async function Encabezado() {
  const c = await compu();
  const texto = c.conectada
    ? `Compu conectada${c.estado ? ` · ${c.estado}` : ""}`
    : c.hace_s === null
      ? "La compu todavía no se conectó nunca"
      : `Compu desconectada (hace ${haceTanto(c.hace_s)})`;
  return (
    <header className="barra">
      <nav className="menu">
        <Link href="/" className="marca">🏉 Rugby Stats</Link>
        <Link href="/">Partidos</Link>
        <Link href="/club">Club</Link>
      </nav>
      <span className={`compu ${c.conectada ? "si" : "no"}`} title="La compu es la que hace el análisis">
        <span className="punto" /> {texto}
      </span>
    </header>
  );
}

function haceTanto(s: number): string {
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} días`;
}
