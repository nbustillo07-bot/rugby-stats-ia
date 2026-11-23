"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { prepararBase, sql } from "@/lib/db";
import { exigirSesion } from "@/lib/sesion";
import type { EstadoForm } from "./acciones";

export async function crearClub(_: EstadoForm, datos: FormData): Promise<EstadoForm> {
  await exigirSesion();
  await prepararBase();
  const nombre = String(datos.get("nombre") || "").trim();
  if (!nombre) return { error: "Falta el nombre del club." };
  const equipos = String(datos.get("equipos") || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
  if (!equipos.length) return { error: "Poné por lo menos un equipo (por ejemplo: Primera)." };
  const existe = await sql`SELECT 1 FROM clubes WHERE lower(nombre) = lower(${nombre})`;
  if (existe.length) return { error: "Ya hay un club con ese nombre." };
  const id = await sql.begin(async (tx) => {
    const [c] = await tx<{ id: number }[]>`
      INSERT INTO clubes (nombre, kit, legible)
      VALUES (${nombre}, ${String(datos.get("kit") || "").trim()}, ${String(datos.get("legible") || "media")})
      RETURNING id`;
    for (const [i, e] of equipos.entries()) {
      await tx`INSERT INTO equipos (club_id, nombre, orden) VALUES (${c.id}, ${e}, ${i}) ON CONFLICT DO NOTHING`;
    }
    return c.id;
  });
  redirect(`/club/${id}`);
}

export async function editarClub(clubId: number, datos: FormData): Promise<void> {
  await exigirSesion();
  await sql`UPDATE clubes SET kit = ${String(datos.get("kit") || "").trim()},
            legible = ${String(datos.get("legible") || "media")} WHERE id = ${clubId}`;
  const nuevo = String(datos.get("nuevo_equipo") || "").trim();
  if (nuevo) {
    await sql`INSERT INTO equipos (club_id, nombre, orden)
              VALUES (${clubId}, ${nuevo}, (SELECT coalesce(max(orden), 0) + 1 FROM equipos WHERE club_id = ${clubId}))
              ON CONFLICT DO NOTHING`;
  }
  revalidatePath(`/club/${clubId}`);
}

export async function agregarJugador(clubId: number, datos: FormData): Promise<void> {
  await exigirSesion();
  const nombre = String(datos.get("nombre") || "").trim();
  if (!nombre) return;
  const numero = Number(datos.get("numero")) || null;
  const equipo = Number(datos.get("equipo_id")) || null;
  const [p] = await sql<{ id: number }[]>`
    INSERT INTO personas (club_id, equipo_id, nombre, apodo, numero_habitual)
    VALUES (${clubId}, ${equipo}, ${nombre}, ${String(datos.get("apodo") || "").trim()}, ${numero})
    RETURNING id`;
  redirect(`/jugador/${p.id}`);
}

export async function editarJugador(personaId: number, datos: FormData): Promise<void> {
  await exigirSesion();
  const nombre = String(datos.get("nombre") || "").trim();
  if (!nombre) return;
  await sql`UPDATE personas SET nombre = ${nombre}, apodo = ${String(datos.get("apodo") || "").trim()},
            numero_habitual = ${Number(datos.get("numero")) || null},
            equipo_id = ${Number(datos.get("equipo_id")) || null}
            WHERE id = ${personaId}`;
  revalidatePath(`/jugador/${personaId}`);
}

export async function borrarJugador(personaId: number): Promise<void> {
  await exigirSesion();
  const [p] = await sql<{ club_id: number }[]>`DELETE FROM personas WHERE id = ${personaId} RETURNING club_id`;
  redirect(p ? `/club/${p.club_id}` : "/club");
}

export async function borrarFoto(personaId: number, fotoId: number): Promise<void> {
  await exigirSesion();
  await sql`DELETE FROM fotos_persona WHERE id = ${fotoId} AND persona_id = ${personaId}`;
  revalidatePath(`/jugador/${personaId}`);
}
