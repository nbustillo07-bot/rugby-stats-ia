"""Los prompts y los esquemas de respuesta.

Dos criterios que atraviesan todo el archivo:

1. **Nada acá nombra un club.** Los equipos, las camisetas y los planteles
   entran como parámetros. El mismo prompt sirve para cualquier partido.

2. **Al modelo se le piden observaciones, no juicios.** No se le pregunta si
   un tackle fue "positivo": se le pregunta si el portador avanzó, se quedó o
   retrocedió. El criterio de positivo/negativo lo aplica el código desde
   ``config.yaml``. Así, si mañana se cambia el criterio, no hay que volver a
   analizar el video: se recalcula sobre lo ya medido.

También se le pide siempre que pueda decir "no sé": un evento sin jugador
vuelve a la cola, y eso es mejor que un número inventado.
"""

from __future__ import annotations

from ..modelos import ACCIONES, RASGOS, RESULTADOS, Equipo, Ficha, Jugador

# ---------------------------------------------------------------------------
# Piezas comunes
# ---------------------------------------------------------------------------

ENCABEZADO = """Sos un analista de rugby. Mirás video de un partido y devolvés \
datos duros, no relato. Nunca describís la jugada en prosa: solo completás los \
campos que se te piden.

Reglas que no se negocian:
- Si no podés ver algo con seguridad, lo dejás en null y bajás la confianza. \
Un dato inventado arruina la planilla; un "no sé" se revisa después.
- Nunca inventás un número de camiseta que no esté en el plantel que te paso.
- La confianza va de 0 a 1 y es honesta: 0.9 es "lo vi claro", 0.5 es "me \
parece", 0.2 es "casi no se ve".
- Respondés únicamente con el JSON pedido, sin texto alrededor."""


def describir_equipos(propio: Equipo, rival: Equipo) -> str:
    """Cómo distinguir un equipo del otro en pantalla."""
    lineas = ["Equipos en cancha:"]
    for etiqueta, eq in (("propio", propio), ("rival", rival)):
        detalle = f'- "{etiqueta}" = {eq.nombre}'
        if eq.kit:
            detalle += f". Camiseta: {eq.kit}"
        legible = {
            "alta": "el número de la espalda se lee bien",
            "media": "el número de la espalda se lee a veces",
            "baja": "el número de la espalda casi no se lee",
        }.get(eq.numero_legible, "")
        if legible:
            detalle += f" ({legible})"
        lineas.append(detalle)
    lineas.append(
        'En el campo "equipo" de cada evento escribí exactamente "propio" o '
        '"rival", nunca el nombre del club.'
    )
    return "\n".join(lineas)


def describir_plantel(jugadores: list[Jugador], titulo: str = "Plantel") -> str:
    if not jugadores:
        return f"{titulo}: no me lo pasaron. Dejá todos los números en null."
    titulares = [j for j in jugadores if j.titular]
    suplentes = [j for j in jugadores if not j.titular]
    partes = [f"{titulo} (son los únicos números válidos):"]
    if titulares:
        partes.append("Titulares:")
        partes += [f"  {_fila_jugador(j)}" for j in titulares]
    if suplentes:
        partes.append("Suplentes (entran durante el partido):")
        partes += [f"  {_fila_jugador(j)}" for j in suplentes]
    return "\n".join(partes)


def _fila_jugador(j: Jugador) -> str:
    extra = [p for p in (j.puesto, f'apodo "{j.apodo}"' if j.apodo else "") if p]
    return f"{j.numero}. {j.nombre}" + (f" — {', '.join(extra)}" if extra else "")


def describir_fichas(fichas: dict[int, Ficha], jugadores: list[Jugador]) -> str:
    """Las fichas vigentes, que es lo que permite reconocer sin ver el número."""
    if not fichas:
        return (
            "Todavía no hay fichas de apariencia. Identificá solo por el número "
            "de la espalda; si no se lee, dejá el número en null."
        )
    por_numero = {j.numero: j for j in jugadores}
    partes = [
        "Fichas de apariencia (así se ve cada jugador en ESTE partido). "
        "Usalas cuando no se lea el número:"
    ]
    for numero in sorted(fichas):
        f = fichas[numero]
        j = por_numero.get(numero)
        nombre = j.nombre if j else "?"
        confirmada = "confirmada" if f.confirmada else f"confianza {f.confianza:.2f}"
        partes.append(f"  {numero}. {nombre} — {f.descripcion()} [{confirmada}]")
    partes.append(
        "Descarte: en cancha hay 15 por equipo. Cada jugador que identificás "
        "con seguridad achica las opciones para los demás. Si dos fichas "
        "podrían ser el mismo jugador, elegí null y bajá la confianza."
    )
    return "\n".join(partes)


def _vocabulario_acciones() -> str:
    lineas = ["Acciones válidas y sus resultados posibles:"]
    for accion in ACCIONES:
        lineas.append(f"  {accion}: {', '.join(RESULTADOS[accion])}")
    lineas.append(
        "No uses ninguna acción ni resultado que no esté en esta lista. Si lo "
        "que viste no entra en ninguna, no lo devuelvas."
    )
    return "\n".join(lineas)


def _tiempos(inicio_s: float, fin_s: float) -> str:
    return (
        f"El fragmento que estás viendo va del segundo {inicio_s:.0f} al "
        f"{fin_s:.0f} del video. Todos los tiempos que devuelvas son en "
        "segundos del video completo, no del fragmento."
    )


# ---------------------------------------------------------------------------
# Paso 1 — pasada gruesa: juego, tiempo muerto y marcador
# ---------------------------------------------------------------------------

ESQUEMA_GRUESA = {
    "type": "object",
    "properties": {
        "tramos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio_s": {"type": "number"},
                    "fin_s": {"type": "number"},
                    "tipo": {"type": "string", "enum": ["juego", "muerto"]},
                    "reloj": {"type": "string"},
                    "marcador_local": {"type": "integer", "nullable": True},
                    "marcador_visitante": {"type": "integer", "nullable": True},
                    "confianza": {"type": "number"},
                },
                "required": ["inicio_s", "fin_s", "tipo", "confianza"],
            },
        }
    },
    "required": ["tramos"],
}


def prompt_gruesa(propio: Equipo, rival: Equipo, inicio_s: float, fin_s: float) -> str:
    return f"""{ENCABEZADO}

{describir_equipos(propio, rival)}

{_tiempos(inicio_s, fin_s)}

TAREA: separar el video en tramos de JUEGO y de TIEMPO MUERTO, y leer el \
marcador en pantalla.

Es tiempo muerto: el juego detenido, repeticiones, la cámara en el público o \
en el banco, la charla del árbitro, la formación de un scrum o un line antes \
de que la pelota entre, la publicidad, el entretiempo.
Es juego: desde que la pelota está en movimiento y disputada hasta que el \
árbitro para o la pelota sale.

Tramos cortos de duda, marcalos como "muerto" con confianza baja: perder un \
poco de juego es más barato que analizar en detalle el público.

En "reloj" poné el reloj del partido tal como se ve en el cartel de pantalla \
(formato mm:ss), si se ve. Si no hay cartel, dejalo vacío.
En "marcador_local" y "marcador_visitante" poné el tanteador tal como está en \
el cartel, en el orden en que aparece. Si no se ve, null.

Los tramos tienen que ser contiguos y cubrir todo el fragmento, sin huecos ni \
solapes."""


# ---------------------------------------------------------------------------
# Paso 2 — fichas de jugadores
# ---------------------------------------------------------------------------

ESQUEMA_FICHAS = {
    "type": "object",
    "properties": {
        "lecturas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "numero": {"type": "integer"},
                    "t_segundos": {"type": "number"},
                    "confianza": {"type": "number"},
                    "rasgos": {
                        "type": "object",
                        "properties": {r: {"type": "string"} for r in RASGOS},
                    },
                    "nota": {"type": "string"},
                    # Dónde está el jugador en el cuadro de t_segundos, en
                    # proporciones de 0 a 1: [x izquierda, y arriba, x derecha,
                    # y abajo]. Con esto se recorta la ficha visual.
                    "caja": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["numero", "t_segundos", "confianza", "rasgos"],
            },
        }
    },
    "required": ["lecturas"],
}


def prompt_fichas(
    equipo: Equipo, jugadores: list[Jugador], inicio_s: float, fin_s: float,
    faltantes: list[int] | None = None,
) -> str:
    objetivo = ""
    if faltantes:
        objetivo = (
            "\nPriorizá estos números, que son los que todavía no tengo fichados: "
            f"{', '.join(str(n) for n in sorted(faltantes))}.\n"
        )
    return f"""{ENCABEZADO}

Equipo a fichar: {equipo.nombre}. Camiseta: {equipo.kit or "sin descripción"}.
Mirá SOLO a los jugadores de este equipo; ignorá a los del otro.

{describir_plantel(jugadores)}
{objetivo}
{_tiempos(inicio_s, fin_s)}

TAREA: encontrar cuadros donde se le lea el NÚMERO EN LA ESPALDA a un jugador \
de este equipo y, desde ese cuadro, anotar cómo se ve.

Dónde mirar primero: las pausas. El line, el scrum armándose, la formación de \
un penal, la salida, el festejo de un try, el jugador caminando de espaldas a \
la cámara. En esos momentos el número se lee; en pleno juego, casi nunca.

Para cada lectura devolvé los rasgos que le sirven a otra persona para \
reconocerlo cuando NO se le vea el número:
- casco: "no", o el color ("sí, oscuro", "sí, blanco")
- vincha: "no", o el color
- vendas: dónde y de qué color ("blancas en los muslos", "violeta en el muslo \
izquierdo")
- color_botines: lo más preciso que puedas ("blancos", "amarillos", "naranjas")
- pelo: largo y color ("oscuro corto", "claro largo", "rapado")
- contextura: "flaco", "mediano", "robusto"
- medias: si tienen algo distinto (bajas, de otro color, rotas)
- munequeras (muñequeras): "no", o el color
- protector_bucal: solo si es de un color llamativo
- otro: cualquier cosa que lo distinga (barba, tatuaje visible, camiseta por \
fuera del short)

Si un rasgo no se ve, dejalo en "" (vacío). No lo inventes: una ficha con tres \
rasgos ciertos sirve más que una con diez inventados.

Solo devolvé una lectura si **leíste el número**. Si viste a un jugador con \
rasgos muy marcados pero no le viste el número, no lo devuelvas: para eso está \
el paso siguiente.

Podés devolver varias lecturas del mismo número en momentos distintos: sirve, \
porque los jugadores se embarran y se cambian las vendas.

En "caja" indicá dónde está ese jugador en el cuadro de "t_segundos", como \
[x izquierda, y arriba, x derecha, y abajo] en proporciones de 0 a 1 del \
ancho y del alto del cuadro (0,0 es la esquina de arriba a la izquierda). \
La caja tiene que encerrar al jugador entero, de la cabeza a los botines. Si \
no estás seguro de cuál es en el cuadro, no pongas caja."""


# ---------------------------------------------------------------------------
# Paso 3 — eventos a velocidad media
# ---------------------------------------------------------------------------

ESQUEMA_EVENTOS = {
    "type": "object",
    "properties": {
        "eventos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t_segundos": {"type": "number"},
                    "accion": {"type": "string", "enum": list(ACCIONES)},
                    "resultado": {"type": "string"},
                    "equipo": {"type": "string", "enum": ["propio", "rival"]},
                    "numero": {"type": "integer", "nullable": True},
                    "numeros_secundarios": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "metros": {"type": "number", "nullable": True},
                    "confianza": {"type": "number"},
                    "revisar_contacto": {"type": "boolean"},
                    "nota": {"type": "string"},
                },
                "required": ["t_segundos", "accion", "equipo", "confianza"],
            },
        }
    },
    "required": ["eventos"],
}


def prompt_eventos(
    propio: Equipo,
    rival: Equipo,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    fichas_propias: dict[int, Ficha],
    inicio_s: float,
    fin_s: float,
    relato: str = "",
    solo_propio: bool = False,
) -> str:
    bloque_foco = ""
    if solo_propio:
        bloque_foco = f"""
FOCO: solo interesa {propio.nombre} (el equipo "propio"). Del rival devolvé \
ÚNICAMENTE sus carries, sin número (dejalo en null): hacen falta porque cada \
carry rival frenado es un tackle del equipo propio que hay que atribuir. \
Ninguna otra acción del rival: ni pases, ni patadas, ni lines, ni tackles.
"""
    bloque_relato = ""
    if relato.strip():
        bloque_relato = f"""
RELATO de este tramo (transcripción automática, tiene errores en los nombres):
{relato.strip()}

El relato ayuda a saber QUIÉN, pero no es la verdad: el relator se equivoca y \
llega tarde. Si el video y el relato se contradicen, gana el video y lo \
anotás en "nota". Si el relato nombra a alguien que el video no te deja \
confirmar, usalo con confianza más baja y poné "relato" en la nota.
"""

    return f"""{ENCABEZADO}

{describir_equipos(propio, rival)}

{describir_plantel(jugadores_propios, f"Plantel de {propio.nombre} (propio)")}

{describir_plantel(jugadores_rivales, f"Plantel de {rival.nombre} (rival)")}

{describir_fichas(fichas_propias, jugadores_propios)}
{bloque_foco}{bloque_relato}
{_tiempos(inicio_s, fin_s)}

{_vocabulario_acciones()}

TAREA: listar las acciones que pasan en este tramo, una por una, con quién las \
hizo.

Para cada evento:
- "t_segundos": el momento del contacto o de la acción, lo más preciso que \
puedas.
- "numero": el número del jugador que HACE la acción. En un tackle es el que \
tacklea, no el portador. Si no sabés quién es, poné null: NO adivines.
- "numeros_secundarios": los demás involucrados (el segundo tackleador, el \
que recibe el pase). Vacío si no hay.
- "metros": solo en Carry, y solo si podés estimarlos con las líneas de la \
cancha. Es una estimación; si no hay referencias, null.
- "revisar_contacto": true cuando hubo un contacto y no estás seguro de quién \
entró. A esta velocidad el contacto se ve pero no siempre se ve quién llegó: \
marcándolo en true, después se vuelve a mirar ese instante con muchos más \
cuadros por segundo. Marcalo generoso: es mucho mejor revisar de más que \
atribuir mal un tackle.

No devuelvas eventos del tiempo muerto ni de las repeticiones. Si un mismo \
contacto se ve dos veces porque hubo repetición, devolvelo una sola vez, en el \
momento de la jugada real.

Si en una jugada no podés separar quién hizo qué, devolvé el evento con el \
número en null y confianza baja antes que atribuirlo al jugador equivocado."""


# ---------------------------------------------------------------------------
# Paso 4 — el contacto, a velocidad alta
# ---------------------------------------------------------------------------

ESQUEMA_CONTACTO = {
    "type": "object",
    "properties": {
        "portador": {"type": "integer", "nullable": True},
        "portador_equipo": {"type": "string", "enum": ["propio", "rival"]},
        "tackleadores": {"type": "array", "items": {"type": "integer"}},
        "tackleadores_equipo": {"type": "string", "enum": ["propio", "rival"]},
        # Observación, no juicio: el criterio lo aplica el código.
        "avance_portador": {
            "type": "string",
            "enum": ["avanza", "se_detiene", "retrocede", "no_se_ve"],
        },
        "metros_avance": {"type": "number", "nullable": True},
        "tackle_completado": {"type": "boolean"},
        "pelota_adelante": {"type": "boolean"},
        "hubo_infraccion": {"type": "string"},
        "confianza": {"type": "number"},
        "confianza_identificacion": {"type": "number"},
        "nota": {"type": "string"},
    },
    "required": ["avance_portador", "tackleadores", "confianza"],
}


def prompt_contacto(
    propio: Equipo,
    rival: Equipo,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    fichas_propias: dict[int, Ficha],
    inicio_s: float,
    fin_s: float,
    pista: str = "",
    solo_propio: bool = False,
) -> str:
    bloque_pista = f"\nLo que se detectó a menor velocidad: {pista}\n" if pista else ""
    if solo_propio:
        bloque_pista += f"""
FOCO: solo hace falta identificar a los jugadores de {propio.nombre} (el \
equipo "propio"). Del rival alcanza con decir que es rival: dejá su número en \
null y no gastes atención en reconocerlo. Toda tu atención va a cuál o cuáles \
jugadores propios participan del contacto.
"""
    return f"""{ENCABEZADO}

{describir_equipos(propio, rival)}

{describir_plantel(jugadores_propios, f"Plantel de {propio.nombre} (propio)")}

{describir_plantel(jugadores_rivales, f"Plantel de {rival.nombre} (rival)")}

{describir_fichas(fichas_propias, jugadores_propios)}
{bloque_pista}
{_tiempos(inicio_s, fin_s)}

Estás viendo unos pocos segundos a muchos cuadros por segundo. Este es el \
momento del contacto.

TAREA: resolver el contacto, cuadro por cuadro.

1. "portador": el número del que lleva la pelota al entrar al contacto, y en \
"portador_equipo" de qué equipo es.
2. "tackleadores": los números de los que lo tacklean, el primero que llega \
primero. Si entran dos, devolvé los dos. Si no podés identificar a ninguno, \
devolvé la lista vacía: NO adivines.
3. "avance_portador": qué pasa con el portador DESPUÉS del contacto, \
comparando dónde estaba al primer contacto y dónde queda la pelota al caer:
   - "avanza": gana terreno después del primer contacto
   - "se_detiene": cae prácticamente donde lo agarraron
   - "retrocede": lo empujan hacia atrás
   - "no_se_ve": la cámara no te deja ver el final
   Esto es una observación de lo que pasó, no una evaluación: no digas si el \
tackle fue bueno o malo.
4. "metros_avance": cuántos metros avanzó o retrocedió (negativo si retrocede), \
estimado con las líneas de la cancha. Si no hay referencias, null.
5. "tackle_completado": true si el portador termina en el piso.
6. "pelota_adelante": true si la pelota va hacia adelante con las manos o el \
brazo (knock-on o pase forward).
7. "hubo_infraccion": si el árbitro sanciona algo y se ve su seña, cuál. Vacío \
si no hay.
8. "confianza": qué tan seguro estás de lo que pasó.
9. "confianza_identificacion": aparte, qué tan seguro estás de los NÚMEROS. \
Pueden ser distintas: podés ver clarísimo que hubo un tackle que frenó al \
portador (confianza 0.95) y no tener idea de quién lo hizo \
(confianza_identificacion 0.2).

El número de la espalda casi nunca se ve en el contacto. Reconocé por los \
rasgos de las fichas: casco, vincha, vendas, color de botines, contextura. \
Si dos jugadores de la ficha podrían ser el que viste, dejalo afuera."""


# ---------------------------------------------------------------------------
# Paso 5 — relato
# ---------------------------------------------------------------------------

ESQUEMA_RELATO = {
    "type": "object",
    "properties": {
        "fragmentos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio_s": {"type": "number"},
                    "fin_s": {"type": "number"},
                    "texto": {"type": "string"},
                    "jugadores_nombrados": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["inicio_s", "fin_s", "texto"],
            },
        }
    },
    "required": ["fragmentos"],
}


def prompt_relato(
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    inicio_s: float,
    fin_s: float,
) -> str:
    vocabulario: list[str] = []
    for j in jugadores_propios + jugadores_rivales:
        formas = ", ".join(j.nombres_para_relato)
        vocabulario.append(f"  {j.numero}. {formas}")
    lista = "\n".join(vocabulario) if vocabulario else "  (no me pasaron planteles)"

    return f"""{ENCABEZADO}

{_tiempos(inicio_s, fin_s)}

TAREA: transcribir lo que dice el relator y marcar a qué jugadores nombra.

Nombres y apodos que puede decir (es tu vocabulario: si escuchás algo parecido \
a uno de estos, es ese):
{lista}

La transcripción automática se equivoca con los apellidos, sobre todo con los \
que no son comunes: escucha "Shofre" por "Joffre", "Mawic" por "Massuitz". \
Cuando lo que suena se parece a un nombre de la lista, usá el de la lista.

En "jugadores_nombrados" poné los números de los jugadores que el relator \
nombra en ese fragmento. Si nombra a alguien que no está en la lista, no lo \
pongas.

Partí la transcripción en fragmentos de 10 a 30 segundos. En "texto" va lo que \
se dice, sin arreglarlo ni resumirlo, salvo los nombres.

Tené en cuenta que el relator llega tarde: nombra al jugador uno o dos \
segundos después de la acción, y a veces se corrige."""


def prompt_relato_corregir(
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    fragmentos: list[dict],
) -> str:
    """Para cuando el relato viene de subtítulos automáticos (Claude).

    No hay audio que escuchar: hay un texto con errores en los nombres, y lo
    que se pide es arreglarlos con el plantel y marcar a quién se nombra.
    """
    vocabulario: list[str] = []
    for j in jugadores_propios + jugadores_rivales:
        vocabulario.append(f"  {j.numero}. {', '.join(j.nombres_para_relato)}")
    lista = "\n".join(vocabulario) if vocabulario else "  (no me pasaron planteles)"
    texto = "\n".join(
        f"[{f['inicio_s']:.1f}-{f['fin_s']:.1f}] {f['texto']}" for f in fragmentos
    )
    return f"""{ENCABEZADO}

Esto es la transcripción automática del relato de un partido de rugby, con \
el segundo del video de cada fragmento entre corchetes. La hizo una máquina \
y se equivoca sobre todo con los apellidos poco comunes: escribe "Shofre" \
por "Joffre", "Mawic" por "Massuitz".

Nombres y apodos que puede haber dicho el relator (es tu vocabulario: si algo \
suena parecido a uno de estos, es ese):
{lista}

TRANSCRIPCIÓN:
{texto}

TAREA: devolvé los mismos fragmentos, con los mismos tiempos, corrigiendo \
SOLO los nombres de jugadores. No resumas, no completes, no arregles nada más.
En "jugadores_nombrados" poné los números de los jugadores de la lista que se \
nombran en cada fragmento. Si nombra a alguien que no está en la lista, no lo \
pongas. Si dudás entre dos (hay apellidos repetidos), no pongas ninguno."""


# ---------------------------------------------------------------------------
# Paso 6 — cambios
# ---------------------------------------------------------------------------

ESQUEMA_CAMBIOS = {
    "type": "object",
    "properties": {
        "cambios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t_segundos": {"type": "number"},
                    "equipo": {"type": "string", "enum": ["propio", "rival"]},
                    "entra": {"type": "integer", "nullable": True},
                    "sale": {"type": "integer", "nullable": True},
                    "como_lo_supe": {
                        "type": "string",
                        "enum": ["numero_en_cancha", "relato", "linea_de_touch", "cartel"],
                    },
                    "confianza": {"type": "number"},
                    "nota": {"type": "string"},
                },
                "required": ["t_segundos", "equipo", "confianza", "como_lo_supe"],
            },
        }
    },
    "required": ["cambios"],
}


def prompt_cambios(
    propio: Equipo,
    rival: Equipo,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    inicio_s: float,
    fin_s: float,
    relato: str = "",
) -> str:
    bloque_relato = f"\nRELATO de este tramo:\n{relato.strip()}\n" if relato.strip() else ""
    return f"""{ENCABEZADO}

{describir_equipos(propio, rival)}

{describir_plantel(jugadores_propios, f"Plantel de {propio.nombre} (propio)")}

{describir_plantel(jugadores_rivales, f"Plantel de {rival.nombre} (rival)")}
{bloque_relato}
{_tiempos(inicio_s, fin_s)}

TAREA: detectar cambios de jugadores.

Tres formas de darte cuenta, y conviene cruzarlas:
1. "numero_en_cancha": aparece en juego un número de suplente (16 al 23) que \
antes no estaba.
2. "relato": el relator lo dice ("entra por...", "se va...").
3. "linea_de_touch": se ve al jugador esperando en el lateral con el cartel, o \
el saludo entre el que entra y el que sale.

Si sabés quién entra pero no quién sale, poné "sale" en null, y al revés. \
Media certeza sirve: la persona completa el resto a mano.

Un jugador que sale lesionado y vuelve (por ejemplo después de una herida \
sangrante) no es un cambio: anotalo en "nota" y poné confianza baja."""
