"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

async function achicar(archivo: File, lado = 1200): Promise<Blob> {
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
    return await new Promise<Blob>((ok) => c.toBlob((b) => ok(b!), "image/jpeg", 0.85));
  } finally {
    URL.revokeObjectURL(url);
  }
}

export default function SubirFotos({ personaId }: { personaId: number }) {
  const entrada = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const [texto, setTexto] = useState("");

  async function subir(archivos: FileList | null) {
    if (!archivos?.length) return;
    const lista = Array.from(archivos).slice(0, 40);
    let hechas = 0;
    try {
      // De a 4 por pedido, para no pasar el límite de tamaño.
      for (let i = 0; i < lista.length; i += 4) {
        setTexto(`Subiendo ${Math.min(i + 4, lista.length)} de ${lista.length}…`);
        const datos = new FormData();
        datos.append("persona_id", String(personaId));
        for (const f of lista.slice(i, i + 4)) datos.append("foto", await achicar(f), "foto.jpg");
        const r = await fetch("/api/album/subir", { method: "POST", body: datos });
        const res = await r.json();
        if (!r.ok) throw new Error(res.error || "No se pudo subir.");
        hechas += res.subidas;
      }
      setTexto(`Listo: ${hechas} foto(s) nuevas.`);
      router.refresh();
    } catch (e) {
      setTexto(e instanceof Error ? e.message : String(e));
    } finally {
      if (entrada.current) entrada.current.value = "";
    }
  }

  return (
    <div className="fila">
      <input ref={entrada} type="file" accept="image/*" multiple hidden onChange={(e) => subir(e.target.files)} />
      <button type="button" className="btn primario" onClick={() => entrada.current?.click()}>
        📷 Subir fotos
      </button>
      <span className="ayuda">{texto || "Podés elegir varias. Sirven aunque no sean perfectas: de cara, de cuerpo, jugando."}</span>
    </div>
  );
}
