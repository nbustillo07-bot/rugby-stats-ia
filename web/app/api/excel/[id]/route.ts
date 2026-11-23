import ExcelJS from "exceljs";
import { eventos as leerEventos, jugadores as leerJugadores, partido as leerPartido } from "@/lib/db";
import { sesionValida } from "@/lib/sesion";
import { aRevisar, cuenta, mmss } from "@/lib/tackles";

export const runtime = "nodejs";

export async function GET(_: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!(await sesionValida())) return new Response("No autorizado", { status: 401 });
  const id = Number((await params).id);
  const p = await leerPartido(id);
  if (!p) return new Response("No existe", { status: 404 });
  const [plantel, eventos] = await Promise.all([leerJugadores(id), leerEventos(id)]);
  const tackles = eventos.filter((e) => (e.accion === "Tackle" || e.accion === "Tackle errado") && !e.descartado);

  const libro = new ExcelJS.Workbook();
  libro.creator = "rugby-stats-ia";
  const negrita = { bold: true };
  const cabecera = (hoja: ExcelJS.Worksheet) => {
    hoja.getRow(1).font = negrita;
    hoja.getRow(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFE3F4EA" } };
    hoja.views = [{ state: "frozen", ySplit: 1 }];
  };

  // -- Eventos -------------------------------------------------------------
  // Jugadores primero (es lo que se mira); sus fórmulas cuentan desde Eventos.
  const ju = libro.addWorksheet("Jugadores");
  const ev = libro.addWorksheet("Eventos");
  ev.columns = [
    { header: "N.º", key: "n", width: 6 },
    { header: "Jugador", key: "jug", width: 24 },
    { header: "Momento del video", key: "t", width: 12 },
    { header: "Reloj", key: "reloj", width: 8 },
    { header: "Acción", key: "accion", width: 14 },
    { header: "Resultado", key: "res", width: 11 },
    { header: "Cuenta", key: "cuenta", width: 8 },
    { header: "Revisado", key: "rev", width: 9 },
    { header: "Confianza", key: "conf", width: 10 },
    { header: "Nota", key: "nota", width: 60 },
  ];
  const nombre = new Map(plantel.map((j) => [j.numero, j.nombre]));
  for (const e of tackles) {
    const fila = ev.addRow({
      n: e.jugador_numero ?? "",
      jug: e.jugador_numero !== null ? nombre.get(e.jugador_numero) ?? "" : "",
      t: mmss(e.t_segundos),
      reloj: e.reloj,
      accion: e.accion,
      res: e.accion === "Tackle errado" ? "errado" : e.resultado,
      cuenta: cuenta(e) ? "Sí" : "No",
      rev: e.revisado ? "Sí" : "No",
      conf: Math.round(e.confianza * 100) / 100,
      nota: e.nota,
    });
    if (!cuenta(e)) fila.font = { color: { argb: "FFC53030" } };
  }
  cabecera(ev);

  // -- Jugadores (con fórmulas) ---------------------------------------------
  ju.columns = [
    { header: "N.º", width: 6 }, { header: "Jugador", width: 28 },
    { header: "Tackles", width: 9 }, { header: "Positivos", width: 10 },
    { header: "Negativos", width: 10 }, { header: "Neutros", width: 9 },
    { header: "Errados", width: 9 }, { header: "Efectividad", width: 11 },
    { header: "Sin revisar", width: 11 },
  ];
  const cuenta1 = (fila: number, accion: string, resultado?: string) =>
    `COUNTIFS(Eventos!$A:$A,$A${fila},Eventos!$E:$E,"${accion}",Eventos!$G:$G,"Sí"` +
    (resultado ? `,Eventos!$F:$F,"${resultado}")` : ")");
  plantel.forEach((j, i) => {
    const f = i + 2;
    ju.addRow([
      j.numero,
      j.apodo ? `${j.nombre} (${j.apodo})` : j.nombre,
      { formula: cuenta1(f, "Tackle") },
      { formula: cuenta1(f, "Tackle", "positivo") },
      { formula: cuenta1(f, "Tackle", "negativo") },
      { formula: cuenta1(f, "Tackle", "neutro") },
      { formula: cuenta1(f, "Tackle errado") },
      { formula: `IFERROR(C${f}/(C${f}+G${f}),"")` },
      { formula: `COUNTIFS(Eventos!$A:$A,$A${f},Eventos!$H:$H,"No")` },
    ]);
    ju.getCell(`H${f}`).numFmt = "0%";
  });
  const ultima = plantel.length + 1;
  const total = ju.addRow([
    "", "TOTAL",
    ...["C", "D", "E", "F", "G"].map((c) => ({ formula: `SUM(${c}2:${c}${ultima})` })),
    { formula: `IFERROR(C${ultima + 1}/(C${ultima + 1}+G${ultima + 1}),"")` },
    { formula: `SUM(I2:I${ultima})` },
  ]);
  total.font = negrita;
  ju.getCell(`H${ultima + 1}`).numFmt = "0%";
  cabecera(ju);

  // -- Notas ------------------------------------------------------------------
  const no = libro.addWorksheet("Notas");
  no.columns = [{ width: 28 }, { width: 90 }];
  const pend = tackles.filter(aRevisar).length;
  [
    ["Partido", p.nombre],
    ["Video", p.video_url],
    ["Tramo", p.desde_s !== null || p.hasta_s !== null ? `${mmss(p.desde_s ?? 0)} a ${p.hasta_s !== null ? mmss(p.hasta_s) : "final"}` : "video entero"],
    ["Qué se midió", "SOLO TACKLES del equipo propio."],
    ["Tackle positivo / negativo", "Positivo si el portador no avanza después del contacto; negativo si avanza; neutro si quedó dentro de la tolerancia."],
    ["Sin clasificar no vale", "Un tackle sin jugador o sin resultado NO se cuenta (columna Cuenta = No, en rojo). Se resuelve en la app, en «Para revisar»."],
    ["Quedan por revisar", String(pend)],
    ["Costo del análisis", `US$ ${p.gasto_usd.toFixed(2)}`],
  ].forEach(([a, b]) => { const r = no.addRow([a, b]); r.getCell(1).font = negrita; });

  const datos = await libro.xlsx.writeBuffer();
  const archivo = `${p.nombre.replace(/[^\w\- áéíóúñÁÉÍÓÚÑ]/g, "").trim() || "partido"} - tackles.xlsx`;
  return new Response(new Uint8Array(datos as ArrayBuffer), {
    headers: {
      "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(archivo)}`,
    },
  });
}
