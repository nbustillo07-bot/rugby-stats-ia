import Link from "next/link";
import { notFound } from "next/navigation";
import { analizarDeNuevo, borrarPartido, cancelar } from "@/app/acciones";
import Encabezado from "@/components/Encabezado";
import Estado from "@/components/Estado";
import Refrescar from "@/components/Refrescar";
import TarjetaRevision from "@/components/TarjetaRevision";
import { eventos as leerEventos, jugadores as leerJugadores, partido as leerPartido, sql } from "@/lib/db";
import { exigirSesion } from "@/lib/sesion";
import { aRevisar, idYoutube, mmss, precision, tablaTackles } from "@/lib/tackles";

export const dynamic = "force-dynamic";

export default async function PaginaPartido({
  params, searchParams,
}: { params: Promise<{ id: string }>; searchParams: Promise<{ todas?: string }> }) {
  await exigirSesion();
  const id = Number((await params).id);
  const { todas } = await searchParams;
  const p = await leerPartido(id);
  if (!p) notFound();
  const [plantel, eventos, fotos] = await Promise.all([
    leerJugadores(id),
    leerEventos(id),
    sql<{ numero: number }[]>`SELECT numero FROM fotos WHERE partido_id = ${id}`,
  ]);
  const conFoto = new Set(fotos.map((f) => f.numero));
  const activo = p.estado === "en_cola" || p.estado === "corriendo";
  const tabla = tablaTackles(eventos, plantel);
  const pendientes = eventos.filter(aRevisar);
  const mostrar = todas ? pendientes : pendientes.slice(0, 10);
  const prec = precision(eventos);
  const total = tabla.reduce((s, f) => s + f.tackles + f.errados, 0);
  const yt = idYoutube(p.video_url);

  return (
    <>
      <Encabezado />
      <main>
        <Refrescar activo={activo} />
        <div className="fila entre">
          <div>
            <h1>{p.nombre}</h1>
            <p className="sub">
              {p.equipo_propio} vs {p.equipo_rival}
              {p.desde_s !== null || p.hasta_s !== null
                ? ` · del ${mmss(p.desde_s ?? 0)} al ${p.hasta_s !== null ? mmss(p.hasta_s) : "final"}`
                : " · video entero"}
            </p>
          </div>
          <Estado estado={p.estado} />
        </div>

        <div className="tarjeta">
          {activo ? (
            <>
              <div className="fila entre" style={{ marginBottom: 8 }}>
                <b>{p.estado === "en_cola" ? "Esperando a la compu" : `${Math.round(p.avance * 100)}%`}</b>
                <span className="nota">
                  Gastado US$ {p.gasto_usd.toFixed(2)}
                  {p.estimado_usd !== null ? ` de ~${p.estimado_usd.toFixed(2)} estimados` : ""}
                </span>
              </div>
              <div className="barra-avance"><div style={{ width: `${Math.max(2, p.avance * 100)}%` }} /></div>
              <p className="nota">{p.mensaje}</p>
              <p className="nota">Podés cerrar esto: cuando termine se pone en verde.</p>
            </>
          ) : (
            <p style={{ margin: 0 }}>
              {p.mensaje}
              {p.gasto_usd > 0 && p.estado !== "listo" ? ` · Gastado US$ ${p.gasto_usd.toFixed(2)}` : ""}
            </p>
          )}
          <div className="fila" style={{ marginTop: 12 }}>
            {activo ? (
              <form action={cancelar.bind(null, id)}><button className="btn chico">Cancelar</button></form>
            ) : (
              <form action={analizarDeNuevo.bind(null, id)}>
                <button className="btn chico">{p.estado === "listo" ? "Analizar de nuevo" : "Reintentar"}</button>
              </form>
            )}
            {eventos.length > 0 && (
              <a className="btn chico primario" href={`/api/excel/${id}`}>Bajar Excel</a>
            )}
            <a className="btn chico" href={p.video_url} target="_blank" rel="noreferrer">Ver video</a>
          </div>
        </div>

        {eventos.length > 0 && (
          <>
            <h2>
              Para revisar {pendientes.length > 0 ? `(${pendientes.length})` : ""}
            </h2>
            {pendientes.length === 0 ? (
              <div className="tarjeta vacio">No queda nada por revisar. 👌</div>
            ) : (
              <>
                <p className="sub">Mirá el clip y tocá quién tackleó. Si no fue un tackle de tu equipo, tocá «No fue tackle».</p>
                {mostrar.map((e) => (
                  <TarjetaRevision
                    key={e.id}
                    partidoId={id}
                    evento={e}
                    plantel={plantel}
                    conFoto={[...conFoto]}
                    youtube={yt}
                  />
                ))}
                {!todas && pendientes.length > mostrar.length && (
                  <Link className="btn" href={`/partido/${id}?todas=1`}>
                    Mostrar los {pendientes.length - mostrar.length} que faltan
                  </Link>
                )}
              </>
            )}
            <h2>Tackles por jugador</h2>
            <div className="tarjeta tabla-scroll">
              <table>
                <thead>
                  <tr>
                    <th>N.º</th><th>Jugador</th>
                    <th className="num">Tackles</th><th className="num">Positivos</th>
                    <th className="num">Negativos</th><th className="num">Neutros</th>
                    <th className="num">Errados</th><th className="num">Efectividad</th>
                  </tr>
                </thead>
                <tbody>
                  {tabla.map((f) => (
                    <tr key={f.numero}>
                      <td>{f.numero}</td><td>{f.nombre}</td>
                      <td className="num"><b>{f.tackles}</b></td>
                      <td className="num">{f.positivos}</td><td className="num">{f.negativos}</td>
                      <td className="num">{f.neutros}</td><td className="num">{f.errados}</td>
                      <td className="num">{f.efectividad === null ? "—" : `${Math.round(f.efectividad * 100)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="nota">
                {total} tackles contados. Los que esperan revisión no cuentan todavía.
                {prec.revisados > 0 &&
                  ` · De los revisados, la IA acertó el jugador en ${prec.acertados} de ${prec.revisados} (${Math.round((100 * prec.acertados) / prec.revisados)}%).`}
              </p>
            </div>

          </>
        )}

        <h2>Plantel</h2>
        <div className="tarjeta nota">
          {plantel.map((j) => `${j.numero} ${j.nombre}${j.apodo ? ` (${j.apodo})` : ""}`).join(" · ")}
        </div>

        <form action={borrarPartido.bind(null, id)} style={{ marginTop: 32 }}>
          <button className="btn chico peligro">Borrar este partido</button>
        </form>
      </main>
    </>
  );
}
