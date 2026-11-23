import { sesionValida } from "@/lib/sesion";

export const runtime = "nodejs";
export const maxDuration = 60;

const MODELO = process.env.MODELO_FORMACION || "claude-sonnet-5-5";

const ESQUEMA = {
  type: "object",
  properties: {
    equipo: { type: ["string", "null"] },
    jugadores: {
      type: "array",
      items: {
        type: "object",
        properties: {
          numero: { type: "integer" },
          nombre: { type: "string" },
          apodo: { type: ["string", "null"] },
        },
        required: ["numero", "nombre", "apodo"],
        additionalProperties: false,
      },
    },
    aviso: { type: ["string", "null"] },
  },
  required: ["equipo", "jugadores", "aviso"],
  additionalProperties: false,
};

const PROMPT = `Esta imagen es la formación de un equipo de rugby, como las que publican los clubes en Instagram (titulares y suplentes con su número de camiseta).

Sacá TODOS los jugadores que aparecen, cada uno con:
- "numero": el número de camiseta (titulares del 1 al 15, suplentes del 16 en adelante);
- "nombre": nombre y apellido tal como aparecen (si solo está el apellido, solo el apellido);
- "apodo": el apodo si aparece (entre comillas o paréntesis), si no null.

En "equipo" el nombre del club si aparece. Si un número o un nombre no se lee, no lo inventes: dejalo afuera y contalo en "aviso". Si la imagen no es una formación de rugby, devolvé la lista vacía y explicalo en "aviso".`;

type Jugador = { numero: number; nombre: string; apodo: string | null };

function extraerJson(texto: string): unknown {
  const i = texto.indexOf("{");
  const j = texto.lastIndexOf("}");
  if (i < 0 || j < i) return null;
  try {
    return JSON.parse(texto.slice(i, j + 1));
  } catch {
    return null;
  }
}

async function pedir(imagenes: { tipo: string; datos: string }[], conFormato: boolean) {
  const cuerpo: Record<string, unknown> = {
    model: MODELO,
    max_tokens: 4000,
    messages: [
      {
        role: "user",
        content: [
          ...imagenes.map((im) => ({
            type: "image",
            source: { type: "base64", media_type: im.tipo, data: im.datos },
          })),
          {
            type: "text",
            text: conFormato ? PROMPT : `${PROMPT}\n\nRespondé solo con un JSON: {"equipo": ..., "jugadores": [{"numero", "nombre", "apodo"}], "aviso": ...}`,
          },
        ],
      },
    ],
  };
  if (conFormato) cuerpo.output_config = { effort: "low", format: { type: "json_schema", schema: ESQUEMA } };
  return fetch(`${process.env.ANTHROPIC_BASE_URL || "https://api.anthropic.com"}/v1/messages`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": process.env.ANTHROPIC_API_KEY || "",
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify(cuerpo),
  });
}

export async function POST(req: Request) {
  if (!(await sesionValida())) return Response.json({ error: "No autorizado" }, { status: 401 });
  if (!process.env.ANTHROPIC_API_KEY) {
    return Response.json({
      error: "Para leer fotos falta la clave de Claude en Vercel: agregá la variable ANTHROPIC_API_KEY en el proyecto y volvé a publicar.",
    }, { status: 400 });
  }
  const datos = await req.formData();
  const archivos = datos.getAll("foto").filter((f): f is File => f instanceof File && f.size > 0);
  if (!archivos.length) return Response.json({ error: "No llegó ninguna foto." }, { status: 400 });
  const imagenes = await Promise.all(
    archivos.slice(0, 4).map(async (f) => ({
      tipo: ["image/jpeg", "image/png", "image/webp", "image/gif"].includes(f.type) ? f.type : "image/jpeg",
      datos: Buffer.from(await f.arrayBuffer()).toString("base64"),
    })),
  );

  let r = await pedir(imagenes, true);
  if (r.status === 400) {
    const txt = await r.text();
    if (/output_config|json_schema|format|effort/i.test(txt)) r = await pedir(imagenes, false);
    else return Response.json({ error: `Claude no aceptó la foto: ${txt.slice(0, 300)}` }, { status: 400 });
  }
  if (r.status === 401 || r.status === 403) {
    return Response.json({ error: "La clave de Claude en Vercel no anda (ANTHROPIC_API_KEY)." }, { status: 400 });
  }
  if (!r.ok) {
    const txt = await r.text();
    const sinSaldo = /credit balance/i.test(txt);
    return Response.json({
      error: sinSaldo ? "La cuenta de Claude no tiene saldo." : `Claude no pudo leer la foto (${r.status}). Probá de nuevo.`,
    }, { status: 502 });
  }
  const msg = await r.json();
  const texto = (msg.content || []).filter((b: { type: string }) => b.type === "text").map((b: { text: string }) => b.text).join("");
  const res = extraerJson(texto) as { equipo?: string | null; jugadores?: Jugador[]; aviso?: string | null } | null;
  if (!res || !Array.isArray(res.jugadores)) {
    return Response.json({ error: "No pude entender lo que leyó Claude. Probá con otra foto." }, { status: 502 });
  }
  const vistos = new Set<number>();
  const jugadores = res.jugadores
    .filter((j) => Number.isInteger(j.numero) && j.numero > 0 && j.numero < 100 && j.nombre?.trim())
    .filter((j) => (vistos.has(j.numero) ? false : (vistos.add(j.numero), true)))
    .sort((a, b) => a.numero - b.numero)
    .map((j) => ({ numero: j.numero, nombre: j.nombre.trim(), apodo: (j.apodo || "").trim() }));
  return Response.json({ equipo: res.equipo || "", jugadores, aviso: res.aviso || "" });
}
