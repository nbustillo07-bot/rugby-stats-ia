import Link from "next/link";
import { notFound } from "next/navigation";
import { agregarJugador, editarClub } from "@/app/acciones_club";
import Encabezado from "@/components/Encabezado";
import { prepararBase, sql } from "@/lib/db";
import { personasDeClub, type Club, type Equipo } from "@/lib/club";
import { exigirSesion } from "@/lib/sesion";

export const dynamic = "force-dynamic";

export default async function PaginaClub({ params }: { params: Promise<{ id: string }> }) {
  await exigirSesion();
  await prepararBase();
  const id = Number((await params).id);
  const [club] = await sql<Club[]>`SELECT id, nombre, kit, legible FROM clubes WHERE id = ${id}`;
  if (!club) notFound();
  const equipos = await sql<Equipo[]>`SELECT * FROM equipos WHERE club_id = ${id} ORDER BY orden, id`;
  const personas = await personasDeClub(id);
  const partidos = await sql<{ equipo_id: number; n: number }[]>`
    SELECT equipo_id, count(*)::int AS n FROM partidos WHERE equipo_id IS NOT NULL GROUP BY equipo_id`;
  const grupos = [
    ...equipos.map((e) => ({ id: e.id as number | null, nombre: e.nombre, gente: personas.filter((p) => p.equipo_id === e.id) })),
    { id: null, nombre: "Sin equipo asignado", gente: personas.filter((p) => p.equipo_id === null) },
  ].filter((g) => g.id !== null || g.gente.length);

  return (
    <>
      <Encabezado />
      <main>
        <p className="nota"><Link href="/club">← Club</Link></p>
        <h1>{club.nombre}</h1>
        <p className="sub">{personas.length} jugadores · {personas.reduce((s, p) => s + p.fotos, 0)} fotos en los álbumes</p>

        {grupos.map((g) => (
          <section key={g.nombre}>
            <h2>
              {g.nombre}{" "}
              <span className="nota">
                {g.gente.length} jugadores
                {g.id !== null ? ` · ${partidos.find((x) => x.equipo_id === g.id)?.n ?? 0} partidos` : ""}
              </span>
            </h2>
            {g.gente.length === 0 ? (
              <div className="tarjeta nota">
                Todavía no hay jugadores. Se agregan solos cuando cargás un partido de este equipo, o abajo a mano.
              </div>
            ) : (
              <div className="grilla-jugadores">
                {g.gente.map((p) => (
                  <Link key={p.id} href={`/jugador/${p.id}`} className="ficha-jugador">
                    {p.foto_id
                      // eslint-disable-next-line @next/next/no-img-element
                      ? <img src={`/api/album/foto/${p.foto_id}`} alt={p.nombre} loading="lazy" />
                      : <span className="sinfoto">{p.numero_habitual ?? "?"}</span>}
                    <b>{p.numero_habitual ? `${p.numero_habitual} · ` : ""}{p.apodo || p.nombre}</b>
                    <small>{p.fotos} foto{p.fotos === 1 ? "" : "s"}</small>
                  </Link>
                ))}
              </div>
            )}
          </section>
        ))}

        <h2>Agregar un jugador a mano</h2>
        <form action={agregarJugador.bind(null, id)} className="tarjeta">
          <div className="grilla2">
            <div><label htmlFor="nombre">Nombre y apellido</label><input id="nombre" name="nombre" required /></div>
            <div><label htmlFor="apodo">Apodo</label><input id="apodo" name="apodo" /></div>
            <div><label htmlFor="numero">Número habitual</label><input id="numero" name="numero" type="number" min={1} max={99} /></div>
            <div>
              <label htmlFor="equipo_id">Equipo</label>
              <select id="equipo_id" name="equipo_id" defaultValue={equipos[0]?.id}>
                {equipos.map((e) => <option key={e.id} value={e.id}>{e.nombre}</option>)}
              </select>
            </div>
          </div>
          <button className="btn primario" style={{ marginTop: 12 }}>Agregar</button>
        </form>

        <h2>Datos del club</h2>
        <form action={editarClub.bind(null, id)} className="tarjeta">
          <label htmlFor="kit">Cómo es la camiseta</label>
          <input id="kit" name="kit" defaultValue={club.kit} />
          <label htmlFor="legible">¿Se lee el número en la espalda?</label>
          <select id="legible" name="legible" defaultValue={club.legible}>
            <option value="alta">Sí, bien</option>
            <option value="media">Más o menos</option>
            <option value="baja">Casi nunca</option>
          </select>
          <label htmlFor="nuevo_equipo">Sumar otro equipo</label>
          <input id="nuevo_equipo" name="nuevo_equipo" placeholder="M19" />
          <button className="btn" style={{ marginTop: 12 }}>Guardar</button>
        </form>
      </main>
    </>
  );
}
