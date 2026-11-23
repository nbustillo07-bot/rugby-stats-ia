import Encabezado from "@/components/Encabezado";
import FormNuevo from "@/components/FormNuevo";
import { jugadores, prepararBase, sql } from "@/lib/db";
import { equiposParaElegir, precargaDeEquipo } from "@/lib/club";
import { exigirSesion } from "@/lib/sesion";
import { mmss } from "@/lib/tackles";

export const dynamic = "force-dynamic";

export default async function Nuevo({ searchParams }: { searchParams: Promise<{ copiar?: string; equipo?: string }> }) {
  await exigirSesion();
  await prepararBase();
  const { copiar, equipo } = await searchParams;
  const equipos = await equiposParaElegir();
  const anteriores = await sql<{ id: number; nombre: string }[]>`
    SELECT id, nombre FROM partidos ORDER BY creado DESC LIMIT 30`;
  let base: Record<string, string> = {};
  if (copiar) {
    const [p] = await sql<Record<string, string>[]>`SELECT * FROM partidos WHERE id = ${Number(copiar)}`;
    if (p) {
      const js = await jugadores(Number(copiar));
      base = {
        equipo_propio: p.equipo_propio, kit_propio: p.kit_propio, legible_propio: p.legible_propio,
        kit_rival: "", plantel: js.map((j) => `${j.numero} ${j.nombre}${j.apodo ? ` (${j.apodo})` : ""}`).join("\n"),
      };
    }
  }
  if (equipo) base = (await precargaDeEquipo(Number(equipo))) ?? base;
  return (
    <>
      <Encabezado />
      <main>
        <h1>Nuevo partido</h1>
        <p className="sub">Se miden los tackles de tu equipo. El rival no hace falta cargarlo.</p>
        <FormNuevo anteriores={anteriores} base={base} equipos={equipos} />
        <p className="nota">Formato del tramo: {mmss(2710)} o 1:05:30. Vacío = el video entero.</p>
      </main>
    </>
  );
}
