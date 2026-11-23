import Link from "next/link";
import { notFound } from "next/navigation";
import { borrarFoto, borrarJugador, editarJugador } from "@/app/acciones_club";
import Encabezado from "@/components/Encabezado";
import SubirFotos from "@/components/SubirFotos";
import { prepararBase, sql } from "@/lib/db";
import type { Equipo } from "@/lib/club";
import { exigirSesion } from "@/lib/sesion";

export const dynamic = "force-dynamic";

type Fila = { id: number; club_id: number; club: string; equipo_id: number | null; nombre: string; apodo: string; numero_habitual: number | null };

export default async function PaginaJugador({ params }: { params: Promise<{ id: string }> }) {
  await exigirSesion();
  await prepararBase();
  const id = Number((await params).id);
  const [p] = await sql<Fila[]>`
    SELECT p.id, p.club_id, c.nombre AS club, p.equipo_id, p.nombre, p.apodo, p.numero_habitual
    FROM personas p JOIN clubes c ON c.id = p.club_id WHERE p.id = ${id}`;
  if (!p) notFound();
  const [equipos, fotos, partidos] = await Promise.all([
    sql<Equipo[]>`SELECT * FROM equipos WHERE club_id = ${p.club_id} ORDER BY orden, id`,
    sql<{ id: number; origen: string; partido: string | null; creado: Date }[]>`
      SELECT f.id, f.origen, pa.nombre AS partido, f.creado FROM fotos_persona f
      LEFT JOIN partidos pa ON pa.id = f.partido_id
      WHERE f.persona_id = ${id} ORDER BY (f.origen = 'subida') DESC, f.creado DESC`,
    sql<{ id: number; nombre: string; numero: number; tackles: number }[]>`
      SELECT pa.id, pa.nombre, j.numero,
        (SELECT count(*)::int FROM eventos e WHERE e.partido_id = pa.id AND e.jugador_numero = j.numero
          AND e.accion = 'Tackle' AND NOT e.pendiente AND NOT e.descartado) AS tackles
      FROM jugadores j JOIN partidos pa ON pa.id = j.partido_id
      WHERE j.persona_id = ${id} ORDER BY pa.creado DESC`,
  ]);

  return (
    <>
      <Encabezado />
      <main>
        <p className="nota"><Link href={`/club/${p.club_id}`}>← {p.club}</Link></p>
        <h1>{p.numero_habitual ? `${p.numero_habitual} · ` : ""}{p.nombre}{p.apodo ? ` (${p.apodo})` : ""}</h1>

        <h2>Álbum ({fotos.length})</h2>
        <div className="tarjeta">
          <SubirFotos personaId={id} />
          {fotos.length === 0 ? (
            <p className="nota">Todavía no hay fotos. Se suman solas con cada partido analizado.</p>
          ) : (
            <div className="album">
              {fotos.map((f) => (
                <figure key={f.id}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={`/api/album/foto/${f.id}`} alt="" loading="lazy" />
                  <figcaption>
                    {f.origen === "subida" ? "Subida" : f.partido ?? "Partido"}
                    <form action={borrarFoto.bind(null, id, f.id)}>
                      <button className="btn chico peligro" title="Si no es él, borrala">Borrar</button>
                    </form>
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
          <p className="nota">
            Si alguna foto no es de él (pasa si en un partido se lo confundió), borrala: es mejor no tener foto que tener una equivocada.
          </p>
        </div>

        {partidos.length > 0 && (
          <>
            <h2>Partidos</h2>
            <div className="tarjeta">
              <table>
                <thead><tr><th>Partido</th><th className="num">N.º</th><th className="num">Tackles</th></tr></thead>
                <tbody>
                  {partidos.map((x) => (
                    <tr key={x.id}>
                      <td><Link href={`/partido/${x.id}`}>{x.nombre}</Link></td>
                      <td className="num">{x.numero}</td>
                      <td className="num">{x.tackles}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <h2>Datos</h2>
        <form action={editarJugador.bind(null, id)} className="tarjeta">
          <div className="grilla2">
            <div><label htmlFor="nombre">Nombre y apellido</label><input id="nombre" name="nombre" defaultValue={p.nombre} required /></div>
            <div><label htmlFor="apodo">Apodo</label><input id="apodo" name="apodo" defaultValue={p.apodo} /></div>
            <div><label htmlFor="numero">Número habitual</label><input id="numero" name="numero" type="number" min={1} max={99} defaultValue={p.numero_habitual ?? ""} /></div>
            <div>
              <label htmlFor="equipo_id">Equipo</label>
              <select id="equipo_id" name="equipo_id" defaultValue={p.equipo_id ?? ""}>
                <option value="">— ninguno —</option>
                {equipos.map((e) => <option key={e.id} value={e.id}>{e.nombre}</option>)}
              </select>
            </div>
          </div>
          <button className="btn" style={{ marginTop: 12 }}>Guardar</button>
        </form>
        <form action={borrarJugador.bind(null, id)} style={{ marginTop: 24 }}>
          <button className="btn chico peligro">Borrar este jugador</button>
        </form>
      </main>
    </>
  );
}
