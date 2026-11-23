-- Base de datos compartida entre la app web (Vercel) y el trabajador (la compu).
-- Se puede correr las veces que haga falta: solo crea lo que falta.

CREATE TABLE IF NOT EXISTS partidos (
  id              SERIAL PRIMARY KEY,
  nombre          TEXT NOT NULL,
  creado          TIMESTAMPTZ NOT NULL DEFAULT now(),
  equipo_propio   TEXT NOT NULL,
  kit_propio      TEXT NOT NULL DEFAULT '',
  legible_propio  TEXT NOT NULL DEFAULT 'media',
  equipo_rival    TEXT NOT NULL,
  kit_rival       TEXT NOT NULL DEFAULT '',
  legible_rival   TEXT NOT NULL DEFAULT 'media',
  video_url       TEXT NOT NULL,
  desde_s         REAL,
  hasta_s         REAL,
  -- nuevo | en_cola | corriendo | listo | error | cancelado
  estado          TEXT NOT NULL DEFAULT 'nuevo',
  avance          REAL NOT NULL DEFAULT 0,
  mensaje         TEXT NOT NULL DEFAULT '',
  gasto_usd       REAL NOT NULL DEFAULT 0,
  estimado_usd    REAL,
  actualizado     TIMESTAMPTZ NOT NULL DEFAULT now(),
  local_id        INTEGER
);

CREATE TABLE IF NOT EXISTS jugadores (
  id          SERIAL PRIMARY KEY,
  partido_id  INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
  numero      INTEGER NOT NULL,
  nombre      TEXT NOT NULL,
  apodo       TEXT NOT NULL DEFAULT '',
  titular     BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS jugadores_partido ON jugadores(partido_id);

CREATE TABLE IF NOT EXISTS trabajos (
  id          SERIAL PRIMARY KEY,
  partido_id  INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
  tipo        TEXT NOT NULL DEFAULT 'analizar',
  -- en_cola | corriendo | listo | error | cancelado
  estado      TEXT NOT NULL DEFAULT 'en_cola',
  creado      TIMESTAMPTZ NOT NULL DEFAULT now(),
  empezado    TIMESTAMPTZ,
  terminado   TIMESTAMPTZ,
  mensaje     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS trabajos_estado ON trabajos(estado);

CREATE TABLE IF NOT EXISTS eventos (
  id              SERIAL PRIMARY KEY,
  partido_id      INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
  t_segundos      REAL NOT NULL,
  reloj           TEXT NOT NULL DEFAULT '',
  accion          TEXT NOT NULL,
  resultado       TEXT NOT NULL DEFAULT '',
  jugador_numero  INTEGER,
  -- Lo que propuso el modelo, para medir la precisión después de revisar.
  jugador_modelo  INTEGER,
  resultado_modelo TEXT NOT NULL DEFAULT '',
  confianza       REAL NOT NULL DEFAULT 0,
  pendiente       BOOLEAN NOT NULL DEFAULT FALSE,
  revisado        BOOLEAN NOT NULL DEFAULT FALSE,
  descartado      BOOLEAN NOT NULL DEFAULT FALSE,
  nota            TEXT NOT NULL DEFAULT '',
  candidatos      JSONB NOT NULL DEFAULT '[]'::jsonb,
  secundarios     JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS eventos_partido ON eventos(partido_id);

CREATE TABLE IF NOT EXISTS fotos (
  partido_id  INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
  numero      INTEGER NOT NULL,
  imagen      BYTEA NOT NULL,
  PRIMARY KEY (partido_id, numero)
);

CREATE TABLE IF NOT EXISTS compu (
  id              INTEGER PRIMARY KEY DEFAULT 1,
  ultimo_latido   TIMESTAMPTZ,
  estado          TEXT NOT NULL DEFAULT '',
  version         TEXT NOT NULL DEFAULT ''
);
