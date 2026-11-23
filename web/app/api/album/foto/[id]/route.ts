import { sql } from "@/lib/db";
import { sesionValida } from "@/lib/sesion";

export async function GET(_: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await sesionValida())) return new Response("No autorizado", { status: 401 });
  const id = Number((await params).id);
  const [f] = await sql<{ imagen: Buffer }[]>`SELECT imagen FROM fotos_persona WHERE id = ${id}`;
  if (!f) return new Response("Sin foto", { status: 404 });
  return new Response(new Uint8Array(f.imagen), {
    headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, max-age=86400, immutable" },
  });
}
