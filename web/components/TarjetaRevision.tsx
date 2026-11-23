"use client";

import { useTransition } from "react";
import { confirmar, descartar, elegirJugador, elegirResultado } from "@/app/acciones";
import type { Evento, Jugador } from "@/lib/db";

function mmss(s: number) {
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export default function TarjetaRevision({
  partidoId, evento: e, plantel, conFoto, youtube,
}: {
  partidoId: number; evento: Evento; plantel: Jugador[]; conFoto: number[]; youtube: string | null;
}) {
  const [ocupado, empezar] = useTransition();
  const porNumero = new Map(plantel.map((j) => [j.numero, j]));
  const candidatos = (e.candidatos || []).filter((c) => porNumero.has(c.numero)).slice(0, 3);
  if (!candidatos.length && e.jugador_numero !== null) {
    candidatos.push({ numero: e.jugador_numero, probabilidad: e.confianza });
  }
  const resultado = e.accion === "Tackle errado" ? "errado" : e.resultado;
  const desde = Math.max(0, Math.floor(e.t_segundos - 3));
  const hasta = Math.ceil(e.t_segundos + 6);
  const motivo = e.jugador_numero === null
    ? "No se sabe quién fue"
    : e.pendiente ? "Hay que confirmar" : "La IA no está segura";

  const hacer = (f: () => Promise<void>) => empezar(() => { void f(); });

  return (
    <div className="tarjeta" style={{ opacity: ocupado ? 0.5 : 1 }}>
      <div className="fila entre" style={{ marginBottom: 10 }}>
        <b>Tackle en el {mmss(e.t_segundos)}{e.reloj ? ` · reloj ${e.reloj}` : ""}</b>
        <span className="chip error">{motivo}</span>
      </div>
      <div className="revision">
        <div>
          {youtube ? (
            <div className="video">
              <iframe
                loading="lazy"
                src={`https://www.youtube-nocookie.com/embed/${youtube}?start=${desde}&end=${hasta}&rel=0&modestbranding=1`}
                title={`Tackle ${mmss(e.t_segundos)}`}
                allow="autoplay; encrypted-media; picture-in-picture"
                allowFullScreen
              />
            </div>
          ) : (
            <p className="nota">Segundo {mmss(e.t_segundos)} del video.</p>
          )}
          {e.nota && <p className="nota">{e.nota}</p>}
        </div>
        <div>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>¿Quién tackleó?</div>
          <div className="candidatos">
            {candidatos.map((c) => {
              const j = porNumero.get(c.numero)!;
              return (
                <button
                  key={c.numero}
                  className={`candidato ${c.numero === e.jugador_numero ? "elegido" : ""}`}
                  disabled={ocupado}
                  title={c.por_que || ""}
                  onClick={() => hacer(() => elegirJugador(partidoId, e.id, c.numero))}
                >
                  {conFoto.includes(c.numero)
                    // eslint-disable-next-line @next/next/no-img-element
                    ? <img src={`/api/foto/${partidoId}/${c.numero}`} alt={j.nombre} />
                    : <span className="sinfoto">{c.numero}</span>}
                  <b>{c.numero} · {j.apodo || j.nombre.split(" ").slice(-1)[0]}</b>
                  <small>{Math.round(c.probabilidad * 100)}%</small>
                </button>
              );
            })}
          </div>
          <label htmlFor={`otro-${e.id}`}>Otro jugador</label>
          <select
            id={`otro-${e.id}`}
            defaultValue=""
            disabled={ocupado}
            onChange={(ev) => ev.target.value && hacer(() => elegirJugador(partidoId, e.id, Number(ev.target.value)))}
          >
            <option value="">— elegir —</option>
            {plantel.map((j) => (
              <option key={j.numero} value={j.numero}>{j.numero} · {j.nombre}{j.apodo ? ` (${j.apodo})` : ""}</option>
            ))}
          </select>

          <div style={{ fontWeight: 600, margin: "14px 0 6px" }}>Resultado</div>
          <div className="resultados">
            {[["positivo", "Positivo"], ["neutro", "Neutro"], ["negativo", "Negativo"], ["errado", "Errado"]].map(([v, t]) => (
              <button
                key={v}
                className={`btn chico ${resultado === v ? "activo" : ""}`}
                disabled={ocupado}
                onClick={() => hacer(() => elegirResultado(partidoId, e.id, v))}
              >{t}</button>
            ))}
          </div>

          <div className="fila" style={{ marginTop: 14 }}>
            {e.jugador_numero !== null && resultado && (
              <button className="btn chico primario" disabled={ocupado}
                onClick={() => hacer(() => confirmar(partidoId, e.id))}>
                Está bien así
              </button>
            )}
            <button className="btn chico peligro" disabled={ocupado}
              onClick={() => hacer(() => descartar(partidoId, e.id))}>
              No fue tackle
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
