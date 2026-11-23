// Copia esquema.sql (la fuente, que también usa el trabajador de la compu) a
// lib/esquema.ts, para que viaje dentro de la app en Vercel.
import { readFileSync, writeFileSync } from "node:fs";
const sql = readFileSync(new URL("../esquema.sql", import.meta.url), "utf8");
writeFileSync(
  new URL("../lib/esquema.ts", import.meta.url),
  "// Generado por scripts/esquema.mjs desde esquema.sql. No editar a mano.\n" +
    `export const ESQUEMA = ${JSON.stringify(sql)};\n`,
);
