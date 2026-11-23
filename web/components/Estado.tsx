const NOMBRES: Record<string, string> = {
  nuevo: "Nuevo",
  en_cola: "Esperando a la compu",
  corriendo: "Analizando",
  listo: "Listo",
  error: "Error",
  cancelado: "Cancelado",
};

export default function Estado({ estado }: { estado: string }) {
  return (
    <span className={`chip ${estado}`}>
      <span className="punto" /> {NOMBRES[estado] ?? estado}
    </span>
  );
}
