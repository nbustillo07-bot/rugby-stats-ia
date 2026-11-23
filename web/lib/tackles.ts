import type { Evento, Jugador } from "./db";

export const RESULTADOS_TACKLE = ["positivo", "negativo", "neutro"] as const;

/** Un tackle cuenta si tiene jugador, resultado válido, y no está pendiente ni descartado. */
export function cuenta(e: Evento): boolean {
  if (e.descartado || e.pendiente || e.jugador_numero === null) return false;
  if (e.accion === "Tackle") return (RESULTADOS_TACKLE as readonly string[]).includes(e.resultado);
  if (e.accion === "Tackle errado") return true;
  return false;
}

export function aRevisar(e: Evento): boolean {
  if (e.revisado || e.descartado) return false;
  if (e.accion !== "Tackle" && e.accion !== "Tackle errado") return false;
  return e.pendiente || e.jugador_numero === null || e.confianza < 0.85;
}

export type FilaTackles = {
  numero: number;
  nombre: string;
  tackles: number;
  positivos: number;
  negativos: number;
  neutros: number;
  errados: number;
  efectividad: number | null;
};

export function tablaTackles(eventos: Evento[], plantel: Jugador[]): FilaTackles[] {
  const filas = new Map<number, FilaTackles>();
  for (const j of plantel) {
    filas.set(j.numero, {
      numero: j.numero,
      nombre: j.apodo ? `${j.nombre} (${j.apodo})` : j.nombre,
      tackles: 0, positivos: 0, negativos: 0, neutros: 0, errados: 0, efectividad: null,
    });
  }
  for (const e of eventos) {
    if (!cuenta(e)) continue;
    const f = filas.get(e.jugador_numero!);
    if (!f) continue;
    if (e.accion === "Tackle errado") f.errados += 1;
    else {
      f.tackles += 1;
      if (e.resultado === "positivo") f.positivos += 1;
      if (e.resultado === "negativo") f.negativos += 1;
      if (e.resultado === "neutro") f.neutros += 1;
    }
  }
  for (const f of filas.values()) {
    const intentos = f.tackles + f.errados;
    f.efectividad = intentos ? f.tackles / intentos : null;
  }
  return [...filas.values()].sort((a, b) => a.numero - b.numero);
}

/** De los revisados, en cuántos el jugador que propuso el modelo era el correcto. */
export function precision(eventos: Evento[]): { revisados: number; acertados: number } {
  const r = eventos.filter(
    (e) => e.revisado && !e.descartado && e.jugador_modelo !== null &&
      (e.accion === "Tackle" || e.accion === "Tackle errado"),
  );
  return { revisados: r.length, acertados: r.filter((e) => e.jugador_modelo === e.jugador_numero).length };
}

export function mmss(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "";
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** "45:10" o "1:02:30" o "2710" → segundos. Vacío → null. */
export function aSegundos(texto: string): number | null {
  const t = (texto || "").trim();
  if (!t) return null;
  if (/^\d+(\.\d+)?$/.test(t)) return Number(t);
  const partes = t.split(":").map((x) => Number(x));
  if (partes.some((x) => Number.isNaN(x))) return null;
  return partes.reduce((acc, x) => acc * 60 + x, 0);
}

/** Una línea por jugador: "4 Pablo Curuchet (Curu)", "4, Pablo Curuchet, Curu", "4 - Pablo". */
export function leerPlantel(texto: string): { jugadores: Jugador[]; errores: string[] } {
  const jugadores: Jugador[] = [];
  const errores: string[] = [];
  const vistos = new Set<number>();
  for (const cruda of texto.split(/\r?\n/)) {
    const linea = cruda.trim();
    if (!linea) continue;
    const m = linea.match(/^(\d{1,2})\s*[-.,;:)\t ]\s*(.+)$/);
    if (!m) {
      errores.push(`No entendí: «${linea}» (tiene que empezar con el número)`);
      continue;
    }
    const numero = Number(m[1]);
    let resto = m[2].trim();
    let apodo = "";
    const par = resto.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
    if (par) {
      resto = par[1].trim();
      apodo = par[2].trim();
    } else if (resto.includes(",")) {
      const [n, a] = resto.split(",", 2);
      resto = n.trim();
      apodo = (a || "").trim();
    }
    if (!resto) {
      errores.push(`Falta el nombre del ${numero}`);
      continue;
    }
    if (vistos.has(numero)) {
      errores.push(`El ${numero} está dos veces`);
      continue;
    }
    vistos.add(numero);
    jugadores.push({ numero, nombre: resto, apodo, titular: numero <= 15 });
  }
  return { jugadores: jugadores.sort((a, b) => a.numero - b.numero), errores };
}

export function idYoutube(url: string): string | null {
  const m = url.match(/(?:youtu\.be\/|v=|\/embed\/|\/shorts\/|\/live\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}
