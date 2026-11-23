import { sql } from "./db";

export type Club = { id: number; nombre: string; kit: string; legible: string };
export type Equipo = { id: number; club_id: number; nombre: string; orden: number };
export type Persona = {
  id: number; club_id: number; equipo_id: number | null; nombre: string; apodo: string;
  numero_habitual: number | null; fotos: number; foto_id: number | null;
};

export function normalizar(s: string): string {
  return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
}

export async function clubes(): Promise<(Club & { equipos: Equipo[]; jugadores: number })[]> {
  const cs = await sql<Club[]>`SELECT id, nombre, kit, legible FROM clubes ORDER BY nombre`;
  const eqs = await sql<Equipo[]>`SELECT * FROM equipos ORDER BY club_id, orden, id`;
  const ns = await sql<{ club_id: number; n: number }[]>`SELECT club_id, count(*)::int AS n FROM personas GROUP BY club_id`;
  return cs.map((c) => ({
    ...c,
    equipos: eqs.filter((e) => e.club_id === c.id),
    jugadores: ns.find((x) => x.club_id === c.id)?.n ?? 0,
  }));
}

export async function personasDeClub(clubId: number): Promise<Persona[]> {
  return sql<Persona[]>`
    SELECT p.id, p.club_id, p.equipo_id, p.nombre, p.apodo, p.numero_habitual,
      (SELECT count(*)::int FROM fotos_persona f WHERE f.persona_id = p.id) AS fotos,
      (SELECT f.id FROM fotos_persona f WHERE f.persona_id = p.id
        ORDER BY (f.origen = 'subida') DESC, f.creado DESC LIMIT 1) AS foto_id
    FROM personas p WHERE p.club_id = ${clubId}
    ORDER BY p.numero_habitual NULLS LAST, p.nombre`;
}

/** Equipos de todos los clubes, para elegir en "Nuevo partido". */
export async function equiposParaElegir(): Promise<{ id: number; etiqueta: string }[]> {
  return sql<{ id: number; etiqueta: string }[]>`
    SELECT e.id, c.nombre || ' · ' || e.nombre AS etiqueta
    FROM equipos e JOIN clubes c ON c.id = e.club_id ORDER BY c.nombre, e.orden, e.id`;
}

/** Lo que se precarga al elegir un equipo: club, camiseta y el último plantel. */
export async function precargaDeEquipo(equipoId: number): Promise<Record<string, string> | null> {
  const [e] = await sql<{ club: string; kit: string; legible: string; club_id: number }[]>`
    SELECT c.nombre AS club, c.kit, c.legible, c.id AS club_id
    FROM equipos e JOIN clubes c ON c.id = e.club_id WHERE e.id = ${equipoId}`;
  if (!e) return null;
  const ultimo = await sql<{ numero: number; nombre: string; apodo: string }[]>`
    SELECT j.numero, j.nombre, j.apodo FROM jugadores j
    WHERE j.partido_id = (SELECT id FROM partidos WHERE equipo_id = ${equipoId} ORDER BY creado DESC LIMIT 1)
    ORDER BY j.numero`;
  const lista = ultimo.length
    ? ultimo
    : await sql<{ numero: number; nombre: string; apodo: string }[]>`
        SELECT numero_habitual AS numero, nombre, apodo FROM personas
        WHERE equipo_id = ${equipoId} AND numero_habitual IS NOT NULL ORDER BY numero_habitual`;
  return {
    equipo_id: String(equipoId),
    equipo_propio: e.club,
    kit_propio: e.kit,
    legible_propio: e.legible,
    plantel: lista.map((j) => `${j.numero} ${j.nombre}${j.apodo ? ` (${j.apodo})` : ""}`).join("\n"),
  };
}
