import { sql } from "@/lib/db";
import { sesionValida } from "@/lib/sesion";

export async function GET(_: Request, { params }: { params: Promise<{ partido: string; numero: string }> }) {
  if (!(await sesionValida())) return new Response("No autorizado", { status: 401 });
  const { partido, numero } = await params;
  const f = await sql<{ imagen: Buffer }[]>`
    SELECT imagen FROM fotos WHERE partido_id = ${Number(partido)} AND numero = ${Number(numero)}`;
  if (!f[0]) return new Response("Sin foto", { status: 404 });
  return new Response(new Uint8Array(f[0].imagen), {
    headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, no-cache" },
  });
}
