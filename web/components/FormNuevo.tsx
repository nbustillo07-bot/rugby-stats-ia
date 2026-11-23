"use client";

import { useActionState, useRef } from "react";
import { useRouter } from "next/navigation";
import { crearPartido, type EstadoForm } from "@/app/acciones";
import SubirFormacion from "./SubirFormacion";

const EJEMPLO = `1 Juan Pérez
2 Pedro Gómez (Pepe)
3 ...
...
15 ...
16 ... (suplentes del 16 en adelante)`;

export default function FormNuevo({
  anteriores, base, equipos,
}: {
  anteriores: { id: number; nombre: string }[];
  base: Record<string, string>;
  equipos: { id: number; etiqueta: string }[];
}) {
  const [estado, accion, enviando] = useActionState<EstadoForm, FormData>(crearPartido, {});
  const router = useRouter();
  const plantel = useRef<HTMLTextAreaElement>(null);
  const equipo = useRef<HTMLInputElement>(null);
  const v: Record<string, string> = { ...base, ...(estado.valores || {}) };
  return (
    // La clave hace que, después de un error, el formulario se rearme con lo
    // que se había escrito (incluidos los desplegables).
    <form action={accion} key={estado.valores ? JSON.stringify(estado.valores) : "nuevo"}>
      <div className="tarjeta">
        <label htmlFor="equipo_id">¿De qué equipo es el partido?</label>
        <select
          id="equipo_id"
          name="equipo_id"
          defaultValue={v.equipo_id || ""}
          onChange={(e) => router.push(e.target.value ? `/nuevo?equipo=${e.target.value}` : "/nuevo")}
        >
          <option value="">— ninguno (cargo todo a mano) —</option>
          {equipos.map((e) => <option key={e.id} value={e.id}>{e.etiqueta}</option>)}
        </select>
        <div className="ayuda">
          {equipos.length
            ? "Al elegirlo se completa el club, la camiseta y el último plantel, y las fotos de cada jugador quedan en su álbum."
            : <>Todavía no hay clubes: creá el tuyo en <a href="/club">Club</a> y cada jugador va a tener su álbum de fotos.</>}
        </div>
      </div>

      {anteriores.length > 0 && !v.equipo_id && (
        <div className="tarjeta">
          <label htmlFor="copiar">Copiar equipo y plantel de un partido anterior</label>
          <select id="copiar" defaultValue="" onChange={(e) => e.target.value && router.push(`/nuevo?copiar=${e.target.value}`)}>
            <option value="">— no, lo cargo ahora —</option>
            {anteriores.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
          </select>
        </div>
      )}

      <div className="tarjeta">
        <label htmlFor="video_url">Link del video (YouTube)</label>
        <input id="video_url" name="video_url" defaultValue={v.video_url} placeholder="https://www.youtube.com/watch?v=..." required />
        <div className="grilla2">
          <div>
            <label htmlFor="desde">Analizar desde (opcional)</label>
            <input id="desde" name="desde" defaultValue={v.desde} placeholder="45:10" />
          </div>
          <div>
            <label htmlFor="hasta">Hasta (opcional)</label>
            <input id="hasta" name="hasta" defaultValue={v.hasta} placeholder="51:30" />
          </div>
        </div>
        <div className="ayuda">
          Para probar conviene un tramo de 5 a 10 minutos (~US$ 1). El partido entero sale ~US$ 5,80 (estimado).
        </div>
        <label htmlFor="nombre">Nombre del partido (opcional)</label>
        <input id="nombre" name="nombre" defaultValue={v.nombre} placeholder="Fecha 8 vs San Isidro" />
      </div>

      <div className="tarjeta">
        <div className="grilla2">
          <div>
            <label htmlFor="equipo_propio">Tu equipo</label>
            <input ref={equipo} id="equipo_propio" name="equipo_propio" defaultValue={v.equipo_propio} required />
            <label htmlFor="kit_propio">Cómo es la camiseta</label>
            <input id="kit_propio" name="kit_propio" defaultValue={v.kit_propio}
              placeholder="rayas verticales bordó y blancas, número blanco" />
            <label htmlFor="legible_propio">¿Se lee el número en la espalda?</label>
            <select id="legible_propio" name="legible_propio" defaultValue={v.legible_propio || "media"}>
              <option value="alta">Sí, bien</option>
              <option value="media">Más o menos</option>
              <option value="baja">Casi nunca (camiseta a rayas, número del mismo tono)</option>
            </select>
          </div>
          <div>
            <label htmlFor="equipo_rival">Rival</label>
            <input id="equipo_rival" name="equipo_rival" defaultValue={v.equipo_rival} required />
            <label htmlFor="kit_rival">Cómo es su camiseta</label>
            <input id="kit_rival" name="kit_rival" defaultValue={v.kit_rival} placeholder="lisa azul, número blanco" />
            <input type="hidden" name="legible_rival" value="media" />
          </div>
        </div>
      </div>

      <div className="tarjeta">
        <label htmlFor="plantel">Plantel de tu equipo: un jugador por línea, número primero</label>
        <SubirFormacion plantel={plantel} equipo={equipo} />
        <textarea ref={plantel} id="plantel" name="plantel" defaultValue={v.plantel} placeholder={EJEMPLO} required />
        <div className="ayuda">El apodo entre paréntesis ayuda (el relator los usa). Del 16 en adelante cuentan como suplentes.</div>
      </div>

      {estado.error && <div className="error-form">{estado.error}</div>}
      <button className="btn primario grande" disabled={enviando}>
        {enviando ? "Guardando…" : "Analizar"}
      </button>
    </form>
  );
}
