"use client";

import { useActionState } from "react";
import { crearClub } from "@/app/acciones_club";
import type { EstadoForm } from "@/app/acciones";

export default function FormClub() {
  const [estado, accion, enviando] = useActionState<EstadoForm, FormData>(crearClub, {});
  return (
    <form action={accion} className="tarjeta">
      <h2 style={{ marginTop: 0 }}>Agregar un club</h2>
      <label htmlFor="nombre">Nombre del club</label>
      <input id="nombre" name="nombre" placeholder="Los Molinos" required />
      <label htmlFor="kit">Cómo es la camiseta</label>
      <input id="kit" name="kit" placeholder="rayas verticales bordó y blancas, número blanco" />
      <label htmlFor="legible">¿Se lee el número en la espalda?</label>
      <select id="legible" name="legible" defaultValue="media">
        <option value="alta">Sí, bien</option>
        <option value="media">Más o menos</option>
        <option value="baja">Casi nunca</option>
      </select>
      <label htmlFor="equipos">Equipos del club (uno por línea)</label>
      <textarea id="equipos" name="equipos" style={{ minHeight: 140 }}
        defaultValue={"Primera\nIntermedia\nPre-A\nPre-B\nPre-C"} />
      {estado.error && <div className="error-form">{estado.error}</div>}
      <button className="btn primario" disabled={enviando} style={{ marginTop: 12 }}>Crear club</button>
    </form>
  );
}
