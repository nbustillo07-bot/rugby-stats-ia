"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { avisarCompu, prepararBase, sql } from "@/lib/db";
import { abrirSesion, exigirSesion } from "@/lib/sesion";
import { aSegundos, leerPlantel } from "@/lib/tackles";
import { normalizar } from "@/lib/club";

export type EstadoForm = { error?: string; valores?: Record<string, string> };

export async function entrar(_: EstadoForm, datos: FormData): Promise<EstadoForm> {
  const ok = await abrirSesion(String(datos.get("clave") || ""));
  if (!ok) return { error: "Contraseña incorrecta." };
  redirect("/");
}

export async function crearPartido(_: EstadoForm, datos: FormData): Promise<EstadoForm> {
  await exigirSesion();
  await prepararBase();
  const r = await validarYCrear(datos);
  if (typeof r === "string") {
    // React vacía el formulario después de mandarlo: se devuelven los valores
    // para que no haya que escribir todo de nuevo.
    const valores: Record<string, string> = {};
    for (const [k, v] of datos.entries()) if (typeof v === "string") valores[k] = v;
    return { error: r, valores };
  }
  await avisarCompu();
  redirect(`/partido/${r}`);
}

async function validarYCrear(datos: FormData): Promise<string | number> {
  const t = (k: string) => String(datos.get(k) || "").trim();
  const video = t("video_url");
  if (!/^https?:\/\//.test(video)) return "Pegá el link del video (YouTube).";
  if (!t("equipo_propio") || !t("equipo_rival")) return "Faltan los nombres de los equipos.";
  const { jugadores, errores } = leerPlantel(t("plantel"));
  if (errores.length) return errores.join(" · ");
  if (jugadores.length < 15) {
    return `Cargaste ${jugadores.length} jugadores: hacen falta por lo menos los 15 titulares.`;
  }
  const desde = aSegundos(t("desde"));
  const hasta = aSegundos(t("hasta"));
  if (t("desde") && desde === null) return "No entendí el «desde». Usá mm:ss o h:mm:ss.";
  if (t("hasta") && hasta === null) return "No entendí el «hasta». Usá mm:ss o h:mm:ss.";
  if (desde !== null && hasta !== null && hasta <= desde) return "El «hasta» tiene que ser después del «desde».";

  const equipoId = Number(t("equipo_id")) || null;
  const [eq] = equipoId
    ? await sql<{ club_id: number; division: string }[]>`SELECT club_id, nombre AS division FROM equipos WHERE id = ${equipoId}`
    : [];
  const nombre = t("nombre") || `${t("equipo_propio")}${eq ? ` (${eq.division})` : ""} vs ${t("equipo_rival")}`;
  const id = await sql.begin(async (tx) => {
    const [p] = await tx<{ id: number }[]>`
      INSERT INTO partidos (nombre, equipo_propio, kit_propio, legible_propio,
        equipo_rival, kit_rival, legible_rival, video_url, desde_s, hasta_s, estado, mensaje, equipo_id)
      VALUES (${nombre}, ${t("equipo_propio")}, ${t("kit_propio")}, ${t("legible_propio") || "media"},
        ${t("equipo_rival")}, ${t("kit_rival")}, ${t("legible_rival") || "media"},
        ${video}, ${desde}, ${hasta}, 'en_cola', 'Esperando a la compu…', ${eq ? equipoId : null})
      RETURNING id`;
    // Con equipo elegido, cada jugador se une a su ficha del club (o se crea):
    // así sus fotos de este partido van a su álbum.
    const personas: { id: number; nombre: string; apodo: string }[] = eq
      ? [...(await tx<{ id: number; nombre: string; apodo: string }[]>`
          SELECT id, nombre, apodo FROM personas WHERE club_id = ${eq.club_id}`)]
      : [];
    for (const j of jugadores) {
      let personaId: number | null = null;
      if (eq) {
        const n = normalizar(j.nombre);
        const a = normalizar(j.apodo);
        const encontrada =
          personas.find((x) => normalizar(x.nombre) === n) ??
          (a ? personas.find((x) => normalizar(x.apodo) === a) : undefined);
        if (encontrada) {
          personaId = encontrada.id;
          await tx`UPDATE personas SET numero_habitual = ${j.numero},
                   apodo = CASE WHEN apodo = '' THEN ${j.apodo} ELSE apodo END
                   WHERE id = ${personaId}`;
        } else {
          const [nueva] = await tx<{ id: number }[]>`
            INSERT INTO personas (club_id, equipo_id, nombre, apodo, numero_habitual)
            VALUES (${eq.club_id}, ${equipoId}, ${j.nombre}, ${j.apodo}, ${j.numero}) RETURNING id`;
          personaId = nueva.id;
          personas.push({ id: nueva.id, nombre: j.nombre, apodo: j.apodo });
        }
      }
      await tx`INSERT INTO jugadores (partido_id, numero, nombre, apodo, titular, persona_id)
               VALUES (${p.id}, ${j.numero}, ${j.nombre}, ${j.apodo}, ${j.titular}, ${personaId})`;
    }
    await tx`INSERT INTO trabajos (partido_id) VALUES (${p.id})`;
    return p.id;
  });
  return id;
}

export async function analizarDeNuevo(id: number): Promise<void> {
  await exigirSesion();
  const activos = await sql`SELECT 1 FROM trabajos WHERE partido_id = ${id} AND estado IN ('en_cola','corriendo')`;
  if (!activos.length) {
    await sql`INSERT INTO trabajos (partido_id) VALUES (${id})`;
    await sql`UPDATE partidos SET estado = 'en_cola', avance = 0, mensaje = 'Esperando a la compu…',
              actualizado = now() WHERE id = ${id}`;
    await avisarCompu();
  }
  revalidatePath(`/partido/${id}`);
}

export async function cancelar(id: number): Promise<void> {
  await exigirSesion();
  await sql`UPDATE trabajos SET estado = 'cancelado', terminado = now()
            WHERE partido_id = ${id} AND estado IN ('en_cola','corriendo')`;
  await sql`UPDATE partidos SET estado = 'cancelado', mensaje = 'Cancelado.', actualizado = now()
            WHERE id = ${id} AND estado IN ('en_cola','corriendo')`;
  revalidatePath(`/partido/${id}`);
}

export async function borrarPartido(id: number): Promise<void> {
  await exigirSesion();
  await sql`DELETE FROM partidos WHERE id = ${id}`;
  redirect("/");
}

/** Elegir quién tackleó (desde los candidatos o la lista). */
export async function elegirJugador(partidoId: number, eventoId: number, numero: number): Promise<void> {
  await exigirSesion();
  await sql`
    UPDATE eventos SET jugador_numero = ${numero},
      revisado = (accion = 'Tackle errado' OR resultado IN ('positivo','negativo','neutro')),
      pendiente = NOT (accion = 'Tackle errado' OR resultado IN ('positivo','negativo','neutro')),
      confianza = 1
    WHERE id = ${eventoId} AND partido_id = ${partidoId}`;
  revalidatePath(`/partido/${partidoId}`);
}

/** Cambiar el resultado: positivo / negativo / neutro, o que fue un tackle errado. */
export async function elegirResultado(partidoId: number, eventoId: number, resultado: string): Promise<void> {
  await exigirSesion();
  const errado = resultado === "errado";
  if (!errado && !["positivo", "negativo", "neutro"].includes(resultado)) return;
  await sql`
    UPDATE eventos SET
      accion = ${errado ? "Tackle errado" : "Tackle"},
      resultado = ${resultado},
      revisado = (jugador_numero IS NOT NULL),
      pendiente = (jugador_numero IS NULL)
    WHERE id = ${eventoId} AND partido_id = ${partidoId}`;
  revalidatePath(`/partido/${partidoId}`);
}

export async function confirmar(partidoId: number, eventoId: number): Promise<void> {
  await exigirSesion();
  await sql`UPDATE eventos SET revisado = true, pendiente = false, confianza = 1
            WHERE id = ${eventoId} AND partido_id = ${partidoId}
              AND jugador_numero IS NOT NULL
              AND (accion = 'Tackle errado' OR resultado IN ('positivo','negativo','neutro'))`;
  revalidatePath(`/partido/${partidoId}`);
}

export async function descartar(partidoId: number, eventoId: number): Promise<void> {
  await exigirSesion();
  await sql`UPDATE eventos SET descartado = true, revisado = true
            WHERE id = ${eventoId} AND partido_id = ${partidoId}`;
  revalidatePath(`/partido/${partidoId}`);
}

export async function deshacer(partidoId: number, eventoId: number): Promise<void> {
  await exigirSesion();
  await sql`UPDATE eventos SET descartado = false, revisado = false
            WHERE id = ${eventoId} AND partido_id = ${partidoId}`;
  revalidatePath(`/partido/${partidoId}`);
}
