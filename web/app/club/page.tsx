import Link from "next/link";
import Encabezado from "@/components/Encabezado";
import FormClub from "@/components/FormClub";
import { prepararBase } from "@/lib/db";
import { clubes } from "@/lib/club";
import { exigirSesion } from "@/lib/sesion";

export const dynamic = "force-dynamic";

export default async function Clubes() {
  await exigirSesion();
  await prepararBase();
  const lista = await clubes();
  return (
    <>
      <Encabezado />
      <main>
        <h1>Club</h1>
        <p className="sub">
          Cada jugador tiene su álbum de fotos: se llena solo con cada partido analizado y con las que subas.
          En el próximo análisis, esas fotos ayudan a reconocerlo desde el principio.
        </p>
        {lista.map((c) => (
          <Link key={c.id} href={`/club/${c.id}`} style={{ textDecoration: "none" }}>
            <div className="tarjeta">
              <div className="fila entre"><b>{c.nombre}</b><span className="nota">{c.jugadores} jugadores</span></div>
              <div className="nota" style={{ marginTop: 6 }}>{c.equipos.map((e) => e.nombre).join(" · ")}</div>
            </div>
          </Link>
        ))}
        <FormClub />
      </main>
    </>
  );
}
