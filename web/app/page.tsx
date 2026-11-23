import Link from "next/link";
import Encabezado from "@/components/Encabezado";
import Estado from "@/components/Estado";
import Refrescar from "@/components/Refrescar";
import { prepararBase, sql, type Partido } from "@/lib/db";
import { exigirSesion } from "@/lib/sesion";

export const dynamic = "force-dynamic";

export default async function Inicio() {
  await exigirSesion();
  await prepararBase();
  const partidos = await sql<(Partido & { tackles: number; revisar: number })[]>`
    SELECT p.*,
      (SELECT count(*)::int FROM eventos e WHERE e.partido_id = p.id
        AND e.accion IN ('Tackle','Tackle errado') AND NOT e.descartado) AS tackles,
      (SELECT count(*)::int FROM eventos e WHERE e.partido_id = p.id
        AND e.accion IN ('Tackle','Tackle errado') AND NOT e.descartado AND NOT e.revisado
        AND (e.pendiente OR e.jugador_numero IS NULL OR e.confianza < 0.85)) AS revisar
    FROM partidos p ORDER BY p.creado DESC`;
  const activos = partidos.some((p) => p.estado === "en_cola" || p.estado === "corriendo");

  return (
    <>
      <Encabezado />
      <main>
        <Refrescar activo={activos} cada={8000} />
        <div className="fila entre">
          <div>
            <h1>Partidos</h1>
            <p className="sub">Pegás el link, cargás el plantel y apretás Analizar. Cuando está listo se pone en verde.</p>
          </div>
          <Link href="/nuevo" className="btn primario">+ Nuevo partido</Link>
        </div>

        {partidos.length === 0 ? (
          <div className="tarjeta vacio">Todavía no hay partidos. Tocá <b>Nuevo partido</b>.</div>
        ) : (
          <div className="lista-partidos">
            {partidos.map((p) => (
              <Link key={p.id} href={`/partido/${p.id}`}>
                <div className="tarjeta">
                  <div className="fila entre">
                    <b>{p.nombre}</b>
                    <Estado estado={p.estado} />
                  </div>
                  <div className="nota" style={{ marginTop: 6 }}>
                    {p.estado === "corriendo"
                      ? `${Math.round(p.avance * 100)}% · ${p.mensaje}`
                      : p.estado === "listo"
                        ? `${p.tackles} tackles${p.revisar ? ` · ${p.revisar} para revisar` : " · todo revisado"} · US$ ${p.gasto_usd.toFixed(2)}`
                        : p.mensaje}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>
    </>
  );
}
