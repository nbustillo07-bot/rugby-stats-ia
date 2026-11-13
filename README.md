# rugby-stats-ia

Stats de rugby por jugador, sacadas del video del partido con la API de Gemini.

Cargás el plantel (número de camiseta y nombre), le pasás el link del video, el
sistema propone los eventos con quién los hizo, vos confirmás o corregís, y sale
un Excel con una fila por jugador.

**Sirve para cualquier equipo.** Los clubes, las camisetas y los planteles se
cargan por partido: no hay nada de ningún club escrito en el código. El video
puede ser de YouTube, un archivo en tu compu o cualquier otra URL.

> **Lo que todavía no está medido.** El sistema está armado y probado de punta
> a punta, pero **nunca se corrió contra Gemini con un partido real**: falta
> una clave de API para eso. Hasta que no corras un tramo y lo revises fila por
> fila, la precisión es desconocida. El [primer hito](#el-primer-hito) está más
> abajo.

---

## Índice

- [Qué hace y qué no](#qué-hace-y-qué-no)
- [Instalación](#instalación)
- [Cómo se usa](#cómo-se-usa)
- [El primer hito](#el-primer-hito)
- [Cómo funciona por dentro](#cómo-funciona-por-dentro)
- [Qué sale en el Excel](#qué-sale-en-el-excel)
- [Configuración](#configuración)
- [Límites y costos de la API](#límites-y-costos-de-la-api)
- [Privacidad](#privacidad)
- [Desde la terminal](#desde-la-terminal)
- [Problemas conocidos](#problemas-conocidos)
- [Tests](#tests)
- [Estructura del repo](#estructura-del-repo)

---

## Qué hace y qué no

**Hace:** cuenta acciones por jugador — tackles (positivos, negativos, neutros,
errados), carries, quiebres, pases, offloads, patadas, rucks limpiados, pelotas
perdidas y recuperadas, penales cometidos, tries, conversiones, penales a los
palos, drops y lines.

**No hace:** contar una narración de la jugada. La salida son datos duros en una
tabla, no un relato.

**Dos reglas que atraviesan todo el proyecto:**

1. **Sin clasificar no vale.** Un evento sin jugador o sin resultado válido no
   se cuenta. El sistema vuelve atrás, lo mira con más cuadros por segundo, y si
   igual no sale, lo manda a la cola de revisión marcado como pendiente. Nunca
   se exporta un "sin identificar" en silencio.
2. **La precisión final la pone quien revisa.** El objetivo es 95%; un 70% no
   sirve. Por eso la revisión humana no es un accesorio: es el paso que
   convierte propuestas en datos. Y hay una pantalla que mide cuánto acierta el
   modelo, porque sin eso no se sabe si el sistema sirve.

---

## Instalación

### En Windows, con doble clic

En la carpeta hay cinco archivos numerados. Se usan en orden, con doble clic:

| Archivo | Qué hace |
|---|---|
| `1_instalar.bat` | Crea el entorno, instala las librerías y corre las pruebas. Si falta Python, lo instala y pide volver a abrirlo |
| `2_poner_clave.bat` | Abre Google AI Studio y el archivo `.env` para pegar la clave |
| `3_verificar.bat` | Chequea que la clave ande (y, si se le pasa un link, que pueda mirar video) |
| `4_abrir.bat` | Abre la aplicación en el navegador |
| `5_subir_a_github.bat` | Sube el repo a GitHub (hay que crear antes el repo vacío en github.com/new) |

Si Windows muestra el cartel azul "Windows protegió su PC": **Más información →
Ejecutar de todas formas**.

### A mano, en cualquier sistema

Hace falta **Python 3.11 o más nuevo**. Para ver si lo tenés, abrí una terminal
y escribí:

```bash
python --version
```

Si dice 3.11 o más, estás. Si no, bajalo de [python.org](https://www.python.org/downloads/)
y tildá "Add Python to PATH" durante la instalación.

### 1. Bajar el repo

```bash
git clone https://github.com/<tu-usuario>/rugby-stats-ia.git
cd rugby-stats-ia
```

### 2. Crear un entorno aislado

Esto es para que las librerías de este proyecto no se mezclen con las de otros.

```bash
python -m venv .venv
```

Y activarlo. **En Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

Si PowerShell se queja de permisos, corré una vez:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**En Mac o Linux:**

```bash
source .venv/bin/activate
```

Cuando está activado, el prompt de la terminal arranca con `(.venv)`.

### 3. Instalar las librerías

```bash
pip install -r requirements.txt
```

### 4. La clave de la API

La clave de Gemini es gratis. Se saca en
[Google AI Studio](https://aistudio.google.com/apikey): entrás con tu cuenta de
Google, "Create API key", y la copiás.

Después, copiá el archivo de ejemplo y pegá la clave adentro:

**Windows:**
```powershell
copy .env.example .env
notepad .env
```

**Mac o Linux:**
```bash
cp .env.example .env
nano .env
```

Tiene que quedar así, sin comillas ni espacios:

```
GEMINI_API_KEY=AIzaSy...
```

### 5. Verificar que anda

```bash
python scripts/verificar_api.py
```

Si contesta `✓ Contestó el modelo gemini-3.8-flash`, está listo. Para probar
también la parte de video, que es la que importa:

```bash
python scripts/verificar_api.py --video "https://www.youtube.com/watch?v=XXXX"
```

### 6. (Opcional) ffmpeg y yt-dlp

`yt-dlp` se instala con el `pip install` de arriba. Sirve para bajar videos y
para averiguar cuánto duran.

`ffmpeg` **no** se instala con pip. Sirve para cortar los clips de la cola de
revisión cuando el video es un archivo local. Con videos de YouTube no hace
falta: se embeben directo. Para instalarlo:

| Sistema | Comando |
|---|---|
| Windows | `winget install ffmpeg` |
| Mac | `brew install ffmpeg` |
| Linux | `sudo apt install ffmpeg` |

### 7. Levantar la interfaz

```bash
streamlit run app.py
```

Se abre en el navegador, en `http://localhost:8501`.

---

## Cómo se usa

Las pantallas están en el orden en que se usan.

### 1 · Partido

Los dos equipos y el video. **Describir bien la camiseta importa**: es lo que le
permite al modelo distinguir un equipo del otro. Algo como
`"rayas bordó y blancas verticales, número blanco"` sirve mucho más que
`"bordó"`.

El campo **¿se lee el número en la espalda?** decide bastante:

- **alta** — camiseta de color liso con número contrastado. El modelo lo lee y
  las fichas son un extra.
- **baja** — camiseta a rayas, o número del mismo tono que la tela. Acá las
  fichas de apariencia dejan de ser opcionales: son la única forma de saber
  quién es quién.

También se elige **el tramo a analizar**. Para la primera corrida, poné cinco o
diez minutos: alcanza para medir la precisión y no te comés la cuota del día.

### 2 · Plantel

Número de camiseta y nombre, a mano en la tabla o importando un CSV. **Es el
paso que fija todo lo demás**: esos números son los únicos válidos en todo el
sistema. Si el modelo devuelve un 47 y el 47 no está en el plantel, el evento se
marca pendiente en lugar de contarse.

Dos cosas que mejoran mucho el resultado:

- **Cargar los 15 titulares completos.** El sistema usa el descarte: son 15 en
  cancha, y cada jugador que identifica con seguridad achica las opciones para
  los demás. Con el plantel incompleto, identifica bastante peor.
- **Cargar los apodos.** El relator usa más los apodos que los apellidos, y el
  relato se cruza con el video.

El CSV mínimo es así (el orden de las columnas no importa, y los nombres de
columna aceptan variantes: `numero`, `nro`, `camiseta`, `#`…):

```csv
numero,nombre,apodo,puesto,titular
9,Nombre Apellido,Apodo,medio scrum,si
10,Otro Nombre,,apertura,si
17,Un Suplente,,hooker,no
```

Si falta la columna `titular`, se deduce por el número: del 1 al 15 titulares,
del 16 en adelante suplentes.

### 3 · Fichas

El sistema recorre el video buscando cuadros donde se **lea el número en la
espalda**, y desde ahí anota cómo se ve el jugador: casco, vincha, vendas, color
de botines, pelo, contextura, medias, muñequeras.

Esto existe porque **en el contacto el número casi nunca se ve**. Lo que se ve
es que el que entró al tackle tenía vincha blanca y botines rojos.

Mira primero los tiempos muertos, que es donde están las pausas en las que el
número se lee: el line, el scrum armándose, la formación de un penal, la salida,
el festejo de un try. (Si corrés la pasada gruesa antes, apunta directo a esas
pausas y sale más rápido y más barato.)

**Conviene revisar las fichas antes de analizar**: una ficha mal confirmada
arrastra el error a todos los eventos de ese jugador. Si una ficha no se
corresponde con el jugador, borrala: es mejor no tener ficha que tener una
equivocada.

### 4 · Análisis

Se puede correr todo de una o paso por paso. **Cada paso queda guardado**, así
que si se agota la cuota del día a mitad de un partido, al volver arranca donde
quedó y no vuelve a pagar lo ya hecho.

### 5 · Revisión

La cola de eventos, **los más dudosos primero**: primero los pendientes, después
por confianza creciente. Así las primeras tarjetas son las que más corrigen.

Cada tarjeta trae el clip, la propuesta (acción, jugador, resultado) y tres
botones:

- **Confirmar** — la propuesta estaba bien. Esto es lo que cuenta como acierto
  del modelo.
- **Guardar corrección** — cambiás lo que esté mal. Baja la precisión medida,
  que es justamente lo que queremos saber.
- **No existió** — el evento no pasó. Se registra y se saca de las cuentas.

También se pueden cargar eventos a mano, para los que el sistema no vio.

### 6 · Precisión

De las propuestas que revisaste, qué porcentaje confirmaste **sin cambiarle
nada**, por tipo de evento. Cuando un tipo pasa el 95%, se puede dejar de
revisarlo.

La pantalla además dice **qué campo falla** en cada tipo, que es más útil que el
porcentaje: si lo que falla es *quién* hizo el tackle, se arregla subiendo los
cuadros por segundo del re-examen o confirmando mejor las fichas; si falla *el
resultado*, suele ser la tolerancia de avance en `config.yaml`.

Con menos de 10 eventos revisados de un tipo, el porcentaje es ruido y la
pantalla lo dice en lugar de mostrar un número lindo.

### 7 · Exportar

El Excel. Ver [qué sale](#qué-sale-en-el-excel).

---

## El primer hito

Esto todavía no se probó con Gemini. El dato que falta es **cuánto acierta en un
tramo real**, y se consigue así:

1. Conseguí la clave y corré `python scripts/verificar_api.py --video "<URL>"`.
2. Creá el partido con un tramo corto: cinco o diez minutos de juego.
3. Cargá el plantel completo, con apodos.
4. Corré **Fichas** y confirmalas una por una.
5. Corré el **Análisis** completo.
6. Andá a **Revisión** y confirmá o corregí **fila por fila**, sin saltear.
   Este paso es el que genera el dato.
7. Mirá **Precisión**.

Ese número —por tipo de evento, no en general— es lo que dice si el sistema
sirve. Hasta tenerlo, cualquier afirmación sobre la precisión es una suposición.

Lo que ya se sabe de las pruebas anteriores, hechas a mano y no con Gemini:

| Se probó | Dio |
|---|---|
| Detección y seguimiento automático estándar (YOLO + ByteTrack), 52 s | El seguimiento de un jugador dura 1,6 s de mediana. Con lectura de número: 43% del tiempo identificado para la camiseta lisa, **0% para la camiseta a rayas** |
| Identificar por rasgos de partido, con ~7 jugadores marcados a mano | 11 de 13 bien; los 2 errores eran jugadores que no estaban en la lista, y tenían confianza baja |
| Lo mismo con ~15 jugadores marcados | **17 de 17 bien**, en cuadros de juego distintos |
| Etiquetado de eventos, primer clip | Mal: atribuyó tackles a los jugadores equivocados y marcó un knock-on como tackle |
| Etiquetado de eventos, 6 minutos a 2 cuadros/s | 21 eventos, sin corregir fila por fila → **sin precisión medida** |

La conclusión que guió el diseño: **identificar por apariencia funciona si el
plantel está completo y se permite decir "no sé"**, y el punto débil es *quién
hizo el tackle*, porque a 2 cuadros por segundo se ve el contacto pero no quién
entró. De ahí viene el re-examen a 10 cuadros por segundo.

---

## Cómo funciona por dentro

### Las tres velocidades

Mandarle el partido entero al modelo a muchos cuadros por segundo es carísimo y
además innecesario: la mayor parte del video no es juego. Así que se mira en
tres pasadas, cada una a la velocidad que necesita:

| Pasada | Cuadros por segundo | Para qué |
|---|---|---|
| **Gruesa** | 1 cada 2 segundos (`fps=0.5`) | Separar juego de tiempo muerto, leer el reloj y el marcador |
| **Juego** | 2 por segundo | Seguir la secuencia y encontrar los contactos |
| **Contacto** | 10 por segundo, 3 segundos | Quién tackleó, si el portador avanzó, si la pelota fue adelante |

La pasada gruesa es la que hace barato todo el resto: en el tramo que se probó,
menos de un tercio del video era juego real, así que las pasadas finas corren
sobre mucho menos material. La de contacto va en resolución alta (≈300 tokens
por segundo de video) pero sobre pocos segundos; las otras dos en baja (≈100).

### Al modelo se le piden observaciones, no juicios

Esto es una decisión de diseño que vale la pena entender.

Al modelo **no** se le pregunta si un tackle fue "positivo". Se le pregunta qué
pasó con el portador después del contacto: `avanza`, `se_detiene`, `retrocede` o
`no_se_ve`. El criterio de positivo/negativo lo aplica el código, desde
`config.yaml`.

Dos ventajas concretas:

- Si mañana cambiás el criterio, **se recalcula sobre lo ya medido** sin gastar
  un solo pedido de API.
- El modelo no tiene que interpretar bien una regla: solo tiene que ver bien.

El criterio por defecto es la convención del rugby: **positivo si el portador no
avanza** (se queda donde lo agarraron o lo hacen retroceder), **negativo si
avanza**. Se invierte en `config.yaml` con
`reglas.tackle_positivo: portador_avanza`.

Además, si el modelo logró estimar los metros de avance, esa medición manda
sobre la etiqueta: un "avanza" de 20 cm está dentro de la tolerancia y sale
**neutro**, no negativo.

### Dos confianzas separadas

En el re-examen del contacto, el modelo devuelve dos confianzas distintas:
`confianza` (qué tan seguro está de lo que pasó) y `confianza_identificacion`
(qué tan seguro está de los números).

Pueden ser muy distintas, y eso es exactamente lo que pasa en la práctica: se ve
clarísimo que hubo un tackle que frenó al portador, y no se tiene idea de quién
lo hizo. La confianza del evento es la menor de las dos, así que un tackle bien
visto pero mal identificado va a la cola en lugar de colarse a la planilla.

### Señales además del video

- **El relato.** Los subtítulos automáticos nombran jugadores pero erran los
  apellidos poco comunes ("Shofre" por "Joffre"). Se le pasa la lista de
  apellidos y apodos del plantel como vocabulario. El relato ayuda a saber
  quién, pero **si el video y el relato se contradicen, gana el video**: el
  relator se equivoca y llega tarde.
- **El marcador en pantalla.** Da el reloj del partido y los puntos sin mirar la
  jugada. De ahí sale el desfase entre el reloj del cartel y el tiempo del
  video, así todos los eventos quedan con el reloj del partido.
- **El árbitro.** Su seña es más confiable que inferir la infracción.
- **Las formaciones fijas.** En scrum, line y salida se sabe quién está dónde
  por puesto: sirve para re-anclar identidades.

### Cambios de jugadores

Se detectan por tres lados: aparece un número del 16 al 23 que antes no estaba,
lo dice el relato, o se ve en la línea de touch. También se pueden cargar a mano.
Al entrar un suplente, se le arma su ficha.

### Todo es reanudable

Cada pedido a la API se guarda en disco por su hash (`data/cache/`), y cada paso
marca su progreso en la base (`data/partidos.db`). Si el proceso se corta —se
agotó la cuota, se cerró la terminal, se cayó internet— al volver a correrlo
sigue desde donde quedó sin volver a pagar lo ya hecho.

---

## Qué sale en el Excel

Tres hojas.

**Hoja "Jugadores"** — una fila por jugador, con **fórmulas** que cuentan desde
la hoja Eventos. Esto importa: si corregís o borrás una fila de Eventos, las
stats por jugador **se recalculan solas**. No son números pegados.

Columnas: N.º · Jugador · Equipo · Tackles · Tackles positivos · Tackles
negativos · Tackles neutros · Tackles errados · Carries · Metros ganados (est.)
· Quiebres · Pases · Offloads · Patadas · Rucks limpiados · Pelotas perdidas ·
Pelotas recuperadas · Penales cometidos · Tries · Conversiones · Penales a los
palos · Drops · Lines tirados · Lines tirados ganados · Lines tomados · Puntos ·
Minutos jugados · Sin revisar. Con fila de total al final.

**Hoja "Eventos"** — una fila por acción: N.º · Reloj · Equipo · Jugador ·
Acción · Resultado · Metros · Confianza · Fuente · Revisado · Pendiente · Nota.
Las filas pendientes van en rojo, y las fórmulas las excluyen.

**Hoja "Notas"** — qué está **medido**, qué es **estimado** y qué es
**calculado**; los criterios usados (incluido el de tackle positivo, con el
valor que tengas configurado); de dónde salió cada dato; el estado de la
revisión; y la precisión medida por tipo de evento. Los puntos por try,
conversión, penal y drop son **celdas editables**: cambiás el número y la
columna Puntos se recalcula.

Lo que está **estimado** y conviene no leer como medición: los **metros
ganados**. La cámara panea y hace zoom, así que la estimación sale de las líneas
de la cancha y es gruesa. Tomalos como orden de magnitud.

Los **minutos jugados** son **calculados**: salen de los minutos de entrada y
salida, no de medir presencia en cancha.

---

## Configuración

Todo está en `config.yaml`, comentado línea por línea. Lo que más conviene tocar:

| Parámetro | Qué hace |
|---|---|
| `modelo.nombre` | Qué modelo usar. Por defecto `gemini-3.8-flash` |
| `modelo.alternativas` | Si el principal da error de cuota o no existe, se prueban estos en orden |
| `velocidades.*.fps` | Los cuadros por segundo de cada pasada. Subir el de `contacto` es lo primero que conviene probar si falla la identificación |
| `reglas.tackle_positivo` | `portador_detenido` (por defecto) o `portador_avanza` |
| `reglas.tolerancia_avance_cm` | Cuánto avance se tolera antes de considerar que avanzó |
| `reglas.umbral_confianza_revision` | Debajo de esto, el evento va obligatoriamente a la cola |
| `reglas.reintentos_sin_clasificar` | Cuántas veces se vuelve a mirar un evento incompleto antes de mandarlo a la cola |
| `puntos.*` | Puntos por try, conversión, penal y drop |
| `api.rpm_inicial` | Con cuántos pedidos por minuto arranca la cola |
| `video.descargar_si_dura_mas_de_s` | A partir de qué largo conviene bajar el video en lugar de pasar la URL |
| `salida.jugadores_de` | `propio` o `ambos` |

---

## Límites y costos de la API

Verificado al 13/11/2026 contra la documentación de Google. **Conviene
re-chequearlo**: esto cambia seguido.

- `gemini-3.8-flash` tiene free tier y es el modelo de los ejemplos oficiales de
  video. También tienen free tier `gemini-3.6-flash`, `gemini-3.5-flash-lite` y
  `gemini-3.1-flash-lite`.
- Gemini acepta **URLs de YouTube** directamente, pero solo videos **públicos**:
  ni privados ni ocultos.
- Free tier: hasta **8 horas de video de YouTube por día**.
- Se puede recortar (`start_offset`, `end_offset`) y fijar los cuadros por
  segundo (`fps`, de 0 a 24) por pedido. Eso es lo que permite el método de tres
  velocidades.
- Consumo aproximado: **100 tokens por segundo** de video en resolución baja,
  **300** en alta.
- Los límites por minuto y por día dependen del modelo y de la cuenta, y se ven
  en AI Studio. **No están hardcodeados acá**: la cola arranca con un ritmo
  razonable y lo baja sola ante un 429.

### El problema del audio en URLs de YouTube

Esto no es obvio y cuesta plata: **al pasar una URL de YouTube con recorte, la
API recorta los cuadros pero manda el audio del video entero en cada pedido.**
En un partido de 80 minutos, con decenas de ventanas, eso multiplica los tokens.

El sistema lo maneja solo: si el video dura más de lo que fija
`video.descargar_si_dura_mas_de_s` (20 minutos por defecto) y hay `yt-dlp`
instalado, lo baja una vez y lo sube por la Files API, donde el recorte ahorra
de verdad.

`scripts/verificar_api.py --video ...` reporta los tokens por segundo de video
reales: si te da mucho más de 100 en resolución baja, estás pagando el audio.

---

## Privacidad

En el free tier, **Google puede usar el contenido que le mandás para mejorar sus
productos**. Si el video del partido ya es público en YouTube, no cambia nada.
Si es material interno del club, tenelo en cuenta antes de subirlo.

La clave va en `.env`, que está en `.gitignore` y no se sube al repo. La base de
datos, los videos bajados, la caché y los Excel generados tampoco se suben.

---

## Desde la terminal

Para dejar un partido largo corriendo, o para automatizar.

```bash
# Crear un partido y analizarlo
python scripts/analizar.py nuevo \
    --propio "Mi Club" --kit-propio "rayas bordó y blancas" --legible-propio baja \
    --rival "Club Rival" --kit-rival "bordó liso" --legible-rival alta \
    --video "https://www.youtube.com/watch?v=XXXX" \
    --plantel mi_plantel.csv \
    --desde 22:48 --hasta 28:40 \
    --correr

# Ver los partidos cargados
python scripts/analizar.py lista

# Ver en qué está uno
python scripts/analizar.py estado --partido 1

# Correr solo algunos pasos
python scripts/analizar.py correr --partido 1 --pasos eventos,contactos

# Exportar
python scripts/analizar.py exportar --partido 1
```

### Modo simulado

Con `--simulado` (o el interruptor en la interfaz) todo el flujo corre con datos
**inventados**, sin tocar la API ni gastar cuota. Sirve para ver que la
instalación, el flujo y el Excel funcionan antes de tener la clave.

Los datos que salen de ahí **no miden nada**, y el Excel sale con un aviso en
rojo adelante para que no se confunda con uno real.

```bash
python scripts/analizar.py correr --partido 1 --simulado
```

---

## Problemas conocidos

**"No sé cuánto dura el video."** Instalá `yt-dlp` (para URLs) o `ffmpeg` (para
archivos), o poné el tramo a analizar a mano en la pantalla Partido.

**El modelo no identifica a casi nadie.** Lo más probable es que falten fichas,
o que el plantel esté incompleto. El descarte (son 15 en cancha) es gran parte
de lo que hace funcionar la identificación. Si las fichas están y sigue
fallando, subí `velocidades.contacto.fps` en `config.yaml`.

**Errores 429 todo el tiempo.** Es la cuota. La cola baja el ritmo sola, pero si
pasa seguido, bajá `api.rpm_inicial` o analizá tramos más cortos. El límite
diario se ve en AI Studio.

**El modelo inventa eventos.** Se ve en la pantalla de Precisión como
"descartados". Subí `reglas.umbral_confianza_revision` para que entren menos
propuestas flojas.

**No se ven los clips en la revisión.** Con YouTube se embeben solos. Con un
archivo local hace falta `ffmpeg`.

**Los metros ganados dan cualquier cosa.** Son estimados y la cámara panea y
hace zoom. Si no te sirven, ignorá la columna: está marcada como estimada en la
hoja de notas justamente por esto.

---

## Tests

```bash
python -m pytest tests/ -q
```

Corren **sin clave de API**: hay un cliente simulado que devuelve respuestas con
la forma correcta, así que se puede probar la base de datos, las reglas, el
pipeline completo, el Excel y todas las pantallas de Streamlit sin gastar cuota.

Lo que **no** cubren: que Gemini acierte. Eso solo se mide revisando fila por
fila (ver [el primer hito](#el-primer-hito)).

---

## Estructura del repo

```
rugby-stats-ia/
├── app.py                    pantalla de inicio (streamlit run app.py)
├── config.yaml               toda la configuración, comentada
├── pages/                    las 7 pantallas, en orden de uso
├── rugby_stats/
│   ├── modelos.py            los objetos del dominio y el vocabulario cerrado
│   ├── config.py             carga de config.yaml y de la clave
│   ├── db.py                 SQLite: todo queda guardado para poder retomar
│   ├── plantel.py            carga del plantel a mano o por CSV
│   ├── stats.py              agregación de eventos a stats por jugador
│   ├── excel.py              el Excel con fórmulas
│   ├── revision.py           la cola de revisión humana
│   ├── precision.py          la medición de precisión
│   ├── ui.py                 ayudantes de las pantallas
│   ├── gemini/
│   │   ├── cliente.py        cola, reintentos, caché, y el modo simulado
│   │   ├── prompts.py        los prompts y los esquemas de respuesta
│   │   └── video.py          YouTube, archivos y URLs; recortes y clips
│   └── analisis/
│       ├── reglas.py         las reglas del sistema, separadas del modelo
│       ├── pasadas.py        una función por paso del análisis
│       └── pipeline.py       el orquestador reanudable
├── scripts/
│   ├── verificar_api.py      chequear la clave y el modelo antes de gastar
│   └── analizar.py           la CLI
├── data/ejemplos/            un CSV de plantel de ejemplo
└── tests/                    228 tests, todos sin clave de API
```

---

## Licencia

MIT. Ver [LICENSE](LICENSE).
