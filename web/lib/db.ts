import postgres from "postgres";
import { ESQUEMA } from "./esquema";

declare global {
  // eslint-disable-next-line no-var
  var __sql: ReturnType<typeof postgres> | undefined;
  // eslint-disable-next-line no-var
  var __esquemaListo: Promise<void> | undefined;
}

function conectar() {
  // Sin DATABASE_URL (por ejemplo al compilar) no se conecta: el cliente de
  // postgres recién se conecta con la primera consulta, y ahí falla claro.
  const url = process.env.DATABASE_URL || "postgres://falta-DATABASE_URL@localhost:1/falta";
  const local = /localhost|127\.0\.0\.1/.test(url);
  return postgres(url, {
    ssl: local ? false : "require",
    max: 3,
    idle_timeout: 20,
    connect_timeout: 15,
    prepare: false,
  });
}

export const sql = globalThis.__sql ?? (globalThis.__sql = conectar());

/** Crea las tablas que falten, una sola vez por servidor. */
export function prepararBase(): Promise<void> {
  if (!globalThis.__esquemaListo) {
    // Con un candado: si la app y la compu arrancan a la vez, no chocan al
    // crear las tablas.
    globalThis.__esquemaListo = sql.begin(async (tx) => {
      await tx`SELECT pg_advisory_xact_lock(727274)`;
      await tx.unsafe(ESQUEMA);
    }).then(() => undefined).catch((e) => {
      globalThis.__esquemaListo = undefined;
      throw e;
    });
  }
  return globalThis.__esquemaListo;
}

export type Partido = {
  id: number;
  nombre: string;
  creado: Date;
  equipo_propio: string;
  kit_propio: string;
  legible_propio: string;
  equipo_rival: string;
  kit_rival: string;
  legible_rival: string;
  video_url: string;
  desde_s: number | null;
  hasta_s: number | null;
  estado: string;
  avance: number;
  mensaje: string;
  gasto_usd: number;
  estimado_usd: number | null;
  actualizado: Date;
};

export type Jugador = { numero: number; nombre: string; apodo: string; titular: boolean };

export type Candidato = { numero: number; probabilidad: number; por_que?: string };

export type Evento = {
  id: number;
  t_segundos: number;
  reloj: string;
  accion: string;
  resultado: string;
  jugador_numero: number | null;
  jugador_modelo: number | null;
  confianza: number;
  pendiente: boolean;
  revisado: boolean;
  descartado: boolean;
  nota: string;
  candidatos: Candidato[];
};

export async function partido(id: number): Promise<Partido | null> {
  await prepararBase();
  const filas = await sql<Partido[]>`SELECT * FROM partidos WHERE id = ${id}`;
  return filas[0] ?? null;
}

export async function jugadores(id: number): Promise<Jugador[]> {
  return sql<Jugador[]>`
    SELECT numero, nombre, apodo, titular FROM jugadores
    WHERE partido_id = ${id} ORDER BY numero`;
}

export async function eventos(id: number): Promise<Evento[]> {
  return sql<Evento[]>`
    SELECT id, t_segundos, reloj, accion, resultado, jugador_numero, jugador_modelo,
           confianza, pendiente, revisado, descartado, nota, candidatos
    FROM eventos WHERE partido_id = ${id} ORDER BY t_segundos`;
}

export async function compu(): Promise<{ conectada: boolean; estado: string; hace_s: number | null }> {
  await prepararBase();
  const f = await sql<{ estado: string; hace_s: number | null }[]>`
    SELECT estado, EXTRACT(EPOCH FROM (now() - ultimo_latido))::float AS hace_s FROM compu WHERE id = 1`;
  if (!f[0] || f[0].hace_s === null) return { conectada: false, estado: "", hace_s: null };
  // Sin trabajo, la compu se reporta cada 15 minutos (para que la base pueda
  // dormirse), así que "conectada" es haberse reportado hace menos de 20.
  return { conectada: f[0].hace_s < 20 * 60, estado: f[0].estado, hace_s: f[0].hace_s };
}

/** Despierta a la compu al instante: le manda un aviso por su canal de ntfy. */
export async function avisarCompu(): Promise<void> {
  try {
    const [c] = await sql<{ aviso: string }[]>`SELECT aviso FROM compu WHERE id = 1`;
    if (!c?.aviso) return;
    const base = process.env.NTFY_BASE || "https://ntfy.sh";
    await fetch(`${base}/${c.aviso}`, {
      method: "POST",
      body: "trabajo",
      signal: AbortSignal.timeout(4000),
    });
  } catch {
    // Si no llega, la compu igual lo encuentra en su próxima vuelta.
  }
}
