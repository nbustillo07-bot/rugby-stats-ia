import { sql } from "@/lib/db";
import { sesionValida } from "@/lib/sesion";

export const runtime = "nodejs";

export async function POST(req: Request) {
  if (!(await sesionValida())) return Response.json({ error: "No autorizado" }, { status: 401 });
  const datos = await req.formData();
  const persona = Number(datos.get("persona_id"));
  const [p] = await sql`SELECT 1 FROM personas WHERE id = ${persona}`;
  if (!p) return Response.json({ error: "Ese jugador no existe." }, { status: 404 });
  const fotos = datos.getAll("foto").filter((f): f is File => f instanceof File && f.size > 0);
  let n = 0;
  for (const f of fotos.slice(0, 20)) {
    if (f.size > 4_000_000) continue;
    const bytes = Buffer.from(await f.arrayBuffer());
    await sql`INSERT INTO fotos_persona (persona_id, imagen, origen) VALUES (${persona}, ${bytes}, 'subida')`;
    n += 1;
  }
  return Response.json({ subidas: n });
}
