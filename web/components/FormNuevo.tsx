"use client";

import { useActionState } from "react";
import { useRouter } from "next/navigation";
import { crearPartido, type EstadoForm } from "@/app/acciones";

const EJEMPLO = `1 Juan Pérez
2 Pedro Gómez (Pepe)
3 ...
...
15 ...
16 ... (suplentes del 16 en adelante)`;

export default function FormNuevo({
  anteriores, base,
}: { anteriores: { id: number; nombre: string }[]; base: Record<string, string> }) {
  const [estado, accion, enviando] = useActionState<EstadoForm, FormData>(crearPartido, {});
  const router = useRouter();
  const v: Record<string, string> = { ...base, ...(estado.valores || {}) };
  return (
    <form action={accion}>
      {anteriores.length > 0 && (
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
          Para probar conviene un tramo de 5 a 10 minutos (~US$ 1). El partido entero sale ~US$ 6,70 (estimado).
        </div>
        <label htmlFor="nombre">Nombre del partido (opcional)</label>
        <input id="nombre" name="nombre" defaultValue={v.nombre} placeholder="Fecha 8 vs San Isidro" />
      </div>

      <div className="tarjeta">
        <div className="grilla2">
          <div>
            <label htmlFor="equipo_propio">Tu equipo</label>
            <input id="equipo_propio" name="equipo_propio" defaultValue={v.equipo_propio} required />
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
        <textarea id="plantel" name="plantel" defaultValue={v.plantel} placeholder={EJEMPLO} required />
        <div className="ayuda">El apodo entre paréntesis ayuda (el relator los usa). Del 16 en adelante cuentan como suplentes.</div>
      </div>

      {estado.error && <div className="error-form">{estado.error}</div>}
      <button className="btn primario grande" disabled={enviando}>
        {enviando ? "Guardando…" : "Analizar"}
      </button>
    </form>
  );
}
