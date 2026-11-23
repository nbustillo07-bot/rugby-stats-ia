import { createHash } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const COOKIE = "rugby_sesion";

export function firma(clave: string): string {
  return createHash("sha256").update(`rugby-stats|${clave}`).digest("hex");
}

export async function sesionValida(): Promise<boolean> {
  const clave = process.env.APP_PASSWORD;
  if (!clave) return true; // sin contraseña configurada (solo para probar en local)
  const c = (await cookies()).get(COOKIE)?.value;
  return c === firma(clave);
}

/** Para las páginas: si no hay sesión, al login. */
export async function exigirSesion(): Promise<void> {
  if (!(await sesionValida())) redirect("/login");
}

export async function abrirSesion(clave: string): Promise<boolean> {
  const real = process.env.APP_PASSWORD;
  if (!real || clave !== real) return false;
  (await cookies()).set(COOKIE, firma(real), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 180,
    path: "/",
  });
  return true;
}
