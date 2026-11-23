"use client";

import { useActionState } from "react";
import { entrar, type EstadoForm } from "../acciones";

export default function Login() {
  const [estado, accion, enviando] = useActionState<EstadoForm, FormData>(entrar, {});
  return (
    <main style={{ maxWidth: 380, paddingTop: 80 }}>
      <h1>🏉 Rugby Stats</h1>
      <p className="sub">Poné la contraseña de la app.</p>
      <form action={accion} className="tarjeta">
        <label htmlFor="clave">Contraseña</label>
        <input id="clave" name="clave" type="password" autoFocus required />
        {estado.error && <div className="error-form">{estado.error}</div>}
        <div style={{ marginTop: 14 }}>
          <button className="btn primario" disabled={enviando}>Entrar</button>
        </div>
      </form>
    </main>
  );
}
