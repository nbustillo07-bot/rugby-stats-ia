"use client";

import { useRef, useState } from "react";

type Jugador = { numero: number; nombre: string; apodo: string };

/** Achica la foto en el teléfono antes de mandarla (más rápido y entra en el límite). */
async function achicar(archivo: File, lado = 1600): Promise<Blob> {
  const url = URL.createObjectURL(archivo);
  try {
    const img = await new Promise<HTMLImageElement>((ok, mal) => {
      const i = new Image();
      i.onload = () => ok(i);
      i.onerror = mal;
      i.src = url;
    });
    const escala = Math.min(1, lado / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * escala);
    c.height = Math.round(img.height * escala);
    c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
    return await new Promise<Blob>((ok) => c.toBlob((b) => ok(b!), "image/jpeg", 0.9));
  } finally {
    URL.revokeObjectURL(url);
  }
}

export default function SubirFormacion({
  plantel, equipo,
}: { plantel: React.RefObject<HTMLTextAreaElement | null>; equipo: React.RefObject<HTMLInputElement | null> }) {
  const archivo = useRef<HTMLInputElement>(null);
  const [estado, setEstado] = useState<{ tipo: "" | "leyendo" | "ok" | "error"; texto: string }>({ tipo: "", texto: "" });

  async function leer(archivos: FileList | null) {
    if (!archivos || !archivos.length) return;
    setEstado({ tipo: "leyendo", texto: "Leyendo la foto… (unos segundos)" });
    try {
      const datos = new FormData();
      for (const f of Array.from(archivos).slice(0, 4)) datos.append("foto", await achicar(f), "formacion.jpg");
      const r = await fetch("/api/formacion", { method: "POST", body: datos });
      const res = await r.json();
      if (!r.ok) throw new Error(res.error || "No pude leer la foto.");
      const jugadores: Jugador[] = res.jugadores;
      if (!jugadores.length) throw new Error(res.aviso || "No encontré jugadores en la foto.");
      if (plantel.current) {
        plantel.current.value = jugadores
          .map((j) => `${j.numero} ${j.nombre}${j.apodo ? ` (${j.apodo})` : ""}`)
          .join("\n");
      }
      if (res.equipo && equipo.current && !equipo.current.value) equipo.current.value = res.equipo;
      const titulares = jugadores.filter((j) => j.numero <= 15).length;
      const faltan = Array.from({ length: 15 }, (_, i) => i + 1).filter((n) => !jugadores.some((j) => j.numero === n));
      setEstado({
        tipo: faltan.length || res.aviso ? "error" : "ok",
        texto:
          `Leí ${jugadores.length} jugadores (${titulares} titulares). Revisá que estén bien.` +
          (faltan.length ? ` Faltan los números: ${faltan.join(", ")}.` : "") +
          (res.aviso ? ` ${res.aviso}` : ""),
      });
    } catch (e) {
      setEstado({ tipo: "error", texto: e instanceof Error ? e.message : String(e) });
    } finally {
      if (archivo.current) archivo.current.value = "";
    }
  }

  return (
    <div style={{ marginBottom: 10 }}>
      <input
        ref={archivo}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => leer(e.target.files)}
      />
      <button
        type="button"
        className="btn"
        disabled={estado.tipo === "leyendo"}
        onClick={() => archivo.current?.click()}
      >
        📷 Subir foto de la formación
      </button>
      <span className="ayuda" style={{ marginLeft: 10 }}>
        La de Instagram con titulares y suplentes. Si son dos fotos, elegí las dos juntas.
      </span>
      {estado.texto && (
        <div
          className={estado.tipo === "error" ? "error-form" : "ayuda"}
          style={estado.tipo === "ok" ? { color: "var(--verde)", fontWeight: 600 } : undefined}
        >
          {estado.texto}
        </div>
      )}
    </div>
  );
}
