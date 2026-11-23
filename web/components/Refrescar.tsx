"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Mientras el análisis corre, vuelve a pedir la página cada tantos segundos. */
export default function Refrescar({ activo, cada = 5000 }: { activo: boolean; cada?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!activo) return;
    const t = setInterval(() => router.refresh(), cada);
    return () => clearInterval(t);
  }, [activo, cada, router]);
  return null;
}
