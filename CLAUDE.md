# Contexto del proyecto

Recolector de ofertas de trabajo, **multiusuario**: cada persona tiene su
propio perfil de búsqueda (portales, palabras clave, pesos de scoring), su
propio Telegram y, opcionalmente, su propia cuenta de Gmail para alertas por
correo. Nació como proyecto personal de un desarrollador junior buscando
trabajo, y se extendió para que 2-3 personas cercanas —con carreras
distintas— lo usaran cada una con su propia configuración, editable desde un
formulario web en el dashboard (no por SSH ni YAML).

Puntúa cada oferta contra el perfil de su dueño, elimina duplicados dentro de
los datos de ese usuario, guarda en SQLite y avisa por Telegram. Incluye un
dashboard en Flask (con login real) para triaje con teclado.

El perfil original y el criterio de diseño por defecto siguen siendo
"desarrollador junior en Chile" — el filtro de seniority es el ejemplo canon,
no un detalle: si una oferta senior pasa el filtro de alguien que busca
junior, eso es un bug. Cada usuario puede tener un perfil completamente
distinto vía `/configuracion`.

## Arquitectura

Flujo: `scrapers → scoring → dedup (por usuario) → SQLite → Telegram / dashboard`

```
main.py                  CLI (buscar / servir / inspeccionar / resumen /
                          crear-usuario / migrar-a-multiusuario)
config.example.yaml      shape de referencia del `cfg` de cada usuario (portales,
                          consultas, filtros, pesos) — ya no hay "el" config.yaml
src/models.py            dataclass Oferta + propiedad `huella` (dedup)
src/scoring.py           puntaje y exclusiones duras (recibe cfg, sin keywords hardcodeados)
src/db.py                SQLite: tabla `usuarios` + `ofertas` (PK compuesta usuario_id+huella)
src/pipeline.py          ejecutar_todos() corre ejecutar() una vez por usuario activo
src/scrapers/base.py     Scraper(cfg, creds) — sesión HTTP, pausa entre requests, reintentos
src/scrapers/*.py        uno por portal
src/notify/telegram.py   envío de avisos, token/chat_id explícitos (no os.getenv)
dashboard/auth.py        login por sesión (werkzeug + flask.session), sin roles
dashboard/app.py         rutas: /login, /, /estado/<huella>, /configuracion
dashboard/templates/     index (listado), login, configuracion (formulario del perfil)
tests/test_nucleo.py     16 tests, sin red
```

`Oferta` es el contrato entre capas: todo scraper devuelve `list[Oferta]` y nada
aguas abajo sabe de qué portal vino el dato.

## Multiusuario

Decisión central: cada usuario guarda su `cfg` completo (mismo shape que
`config.example.yaml`) como JSON en `usuarios.config_json`. Esto es lo que
permite que `scoring.py`, `models.py` y cada scraper **no tengan que cambiar
de lógica** para soportar carreras/perfiles distintos — siguen recibiendo el
mismo dict de siempre, solo cambia de dónde sale (JSON en la fila del
usuario, no `yaml.safe_load` de un archivo fijo).

- `Repo(ruta, usuario_id=...)`: todo método sobre `ofertas` exige
  `usuario_id` (`ValueError` si falta) y filtra `WHERE usuario_id = ?`. Sin
  esto se arriesga a mezclar o filtrar datos entre usuarios por un bug
  silencioso — mejor que falle ruidoso.
- La PK de `ofertas` es compuesta `(usuario_id, huella)`, no solo `huella`:
  el mismo puesto real puede ser relevante para dos usuarios con estado de
  triaje (interesada/postulada/descartada) totalmente independiente.
  SQLite no permite cambiar la PK con `ALTER TABLE` — por eso existe
  `migrar-a-multiusuario` en vez de una migración incremental simple.
- Credenciales (Telegram, Gmail) viven en columnas de `usuarios`, no en
  variables de entorno globales — cada scraper/notificador las recibe
  explícitas (`Scraper.creds`, parámetros de `telegram.enviar`), nunca via
  `os.getenv`. Guardarlas en la misma SQLite es la misma sensibilidad que ya
  tenía el `.env` de antes (2-3 personas de confianza, sin cifrado extra:
  sería una dependencia nueva sin razón fuerte para este tamaño).
- Login sin roles ni recuperación de contraseña: son cuentas de confianza.
  Dos formas de crearlas, sin registro público abierto:
  - `python main.py crear-usuario --login --password` (por SSH, el dueño
    del servidor elige usuario/clave).
  - `python main.py generar-invitacion` genera un link de un solo uso
    (tabla `invitaciones`, expira en `--horas`, default 48h) — la persona
    invitada entra a `/invitar/<token>`, elige su propio usuario/clave, y
    queda logueada de una en `/configuracion`. El link deja de servir en
    cuanto se usa una vez (`Repo.marcar_invitacion_usada`), sin necesidad de
    que el dueño toque la terminal por cada persona nueva.
- `/configuracion` es la única forma de que un usuario cambie su perfil —
  nunca edita YAML. El helper `_cfg_desde_formulario` en `dashboard/app.py`
  arma el `cfg` desde el POST; un valor numérico que no parsea conserva el
  anterior en vez de tumbar el guardado. Los campos secretos (token de
  Telegram, app password de Gmail) nunca se re-muestran en el HTML —vacío
  en el POST significa "no cambiar", no "borrar".
- Editar el perfil en `/configuracion` **no repuntúa lo ya guardado**:
  `Repo.guardar` no reprocesa una huella que ya existe, así que las ofertas
  de antes se quedan con el score viejo hasta correr
  `python main.py recalcular-scores` (`src/pipeline.py:recalcular_scores`).
  No toca `estado`: el triaje manual (interesada/postulada/descartada)
  siempre manda sobre el recálculo.

## Convenciones

- Código, comentarios y nombres de variables **en español**. Mantenerlo.
- Los comentarios explican *por qué*, no *qué*. No agregar comentarios obvios.
- Un portal caído nunca debe tumbar la corrida: `Scraper.buscar_todo` captura
  excepciones por consulta y sigue.
- Sin dependencias nuevas salvo que haya una razón fuerte. `.env` se lee a mano
  en `main.py` justamente para evitar `python-dotenv`.
- Todo término de filtro se busca como **palabra completa** (`_aparece` en
  `scoring.py`). Sin eso, `sr` hace match dentro de "desarrollo" y descarta media
  base de datos. Hay un test que cubre ese caso; no lo rompas.
- Portales sin búsqueda por texto en origen (`remoteok.py`, `weworkremotely.py`,
  `terminal.py`) traen todo el listado una vez por corrida (cacheado en la
  instancia del scraper, no por consulta) y filtran localmente con match de
  palabra completa en modo AND, tras normalizar sinónimos inglés/español
  (`full stack`→`fullstack`, etc.). Es intencional: evita pegarle N veces a la
  misma API por las N consultas de `config.yaml`.

## Zonas delicadas

**`models.py`, propiedad `huella`.** Es la parte más frágil y la que más costó.
Normaliza en tres pasos y el orden importa: (1) eliminar frases completas, (2)
unificar sinónimos, (3) sacar palabras genéricas. "full time" es ruido pero
"full stack" no, y por eso las frases van primero. Si tocas `_RUIDO`, corre los
tests de dedup. Limitación conocida: falla cuando la empresa se escribe distinto
en cada portal ("Acme SpA" vs "Acme").

**`scrapers/computrabajo.py`, diccionario `SELECTORES`.** Verificado y activo,
pero el markup de Computrabajo cambia cada cierto tiempo. Si vuelve a devolver
cero tarjetas, correr `python main.py inspeccionar --portal computrabajo`,
abrir el HTML guardado en `data/` y corregir los selectores. No inventar
selectores sin mirar el HTML real. Ojo con `ubicacion`: el párrafo de la
empresa también tiene la clase `fs16`, así que el selector debe excluirlo
explícitamente o vuelve a matchear el rating de la empresa en vez de la
ciudad (ver docstring del archivo).

Este scraper además pisa el `User-Agent` por defecto de `base.py` con uno de
navegador normal: el WAF de Computrabajo bloqueaba específicamente el
User-Agent identificable del proyecto (403), no por un desafío de JS ni por
`robots.txt` —esa ruta está permitida para cualquier user-agent—, solo una
regla de firewall demasiado agresiva. Es la única excepción a "User-Agent
identificable" en el proyecto; si otro portal empieza a devolver 403 sin
razón aparente, probar lo mismo antes de asumir que hace falta un navegador
headless.

**`scrapers/getonbrd.py`.** API pública en beta, formato JSON:API
(`data[].attributes`). Documentación en https://api-doc.getonbrd.com. El
mapeo de campos se escribió una vez desde la documentación y quedó mal en
cuatro de cinco campos no triviales —la doc no coincide con la respuesta
real—; ya está verificado y corregido (ver el docstring del archivo para el
detalle de cada uno). Si vuelve a romperse, imprimir el JSON crudo de un
`item` antes de asumir la forma de la respuesta; no confiar en la
documentación por sí sola. Nota no obvia: la deduplicación por `huella` usa
`empresa`, así que un campo de empresa roto no solo se ve mal — hace que
ofertas de compañías distintas con títulos parecidos colapsen en una sola
fila y desaparezcan.

**`scrapers/correo.py`.** Lee alertas de Laborum/LinkedIn/Indeed por IMAP en
vez de scrapear (ver Restricciones). Tres cosas no obvias, las tres
verificadas contra correos reales, no adivinadas:

1. Filtrar por dominio completo (`FROM linkedin.com`) trae TODO el historial
   de esa dirección —miles de correos, no solo alertas—. Hay que usar la
   dirección exacta del remitente de alertas (`jobalerts-noreply@linkedin.com`,
   no `jobs-noreply@` ni `updates-noreply@`, que son de otra cosa).
2. `_buscar_de` acota además por `SINCE` (`DIAS_ATRAS`, hoy 5 días). Sin esto,
   cada corrida repite el problema anterior aunque el remitente sea exacto.
3. `no_reply@laborum.cl` manda tanto el resumen de alertas como
   confirmaciones de postulación, y estas últimas también traen `<h3>` que
   parecen ofertas pero con otra estructura de tarjeta. `_parsear_laborum`
   descarta por asunto (`_ASUNTOS_NO_DIGEST`) y por tarjetas sin ningún campo
   extraído, en vez de asumir que todo `<h3>` es una oferta real.
4. Contraintuitivo: en LinkedIn, el correo de "se ha creado tu alerta" NO es
   una confirmación vacía —trae las primeras coincidencias reales en el
   mismo correo (tarjetas `<a class="text-color-brand">`)—. Se asumió lo
   contrario al principio (como en Indeed/Laborum) y estaba mal; no repetir
   ese supuesto con Indeed sin verificarlo primero.
5. La tarjeta de LinkedIn no trae fecha de publicación real, y sin `publicada`
   el dashboard (que ordena "más recientes" por esa columna) manda la oferta
   al fondo de la lista aunque el parser haya funcionado perfecto —se ve
   como "no llegó nada" sin serlo. `_parsear_linkedin` usa la fecha del
   correo (`_fecha_correo`) como aproximación. Lección general: un scraper
   nuevo que no rellena `publicada` es funcionalmente invisible bajo el
   orden por defecto del dashboard, aunque los datos estén bien guardados.
6. LinkedIn usa DOS plantillas de tarjeta para la misma alerta y cambia de
   una a otra sin aviso —detectado el 2026-09-02, cuando la mitad de las
   alertas de un mismo día ya venían en un rediseño ("job-card-flavor") que
   `_parsear_linkedin` no reconocía: el `<a>` del título dejó de tener
   `class="text-color-brand"` y pasó a `class="font-bold ...
   text-system-blue-50"`, así que ese selector simplemente no matcheaba
   nada, sin ningún error ni warning que lo delatara —el usuario reportó
   "no me llegan alertas" antes de que se notara. `_parsear_linkedin` ahora
   busca las dos clases a la vez. Lección general: un selector que deja de
   matchear una plantilla rediseñada no lanza excepción, solo devuelve
   listas vacías silenciosamente —si un usuario reporta "dejaron de llegar
   avisos" sin que haya errores en el log, sospechar de un cambio de
   plantilla antes que de un fallo de conexión o de credenciales.

`_parsear_indeed` ya está escrito y verificado contra un correo real
(2026-09-02). A diferencia de LinkedIn y Laborum, Indeed manda UN solo
puesto por correo, no un resumen — cada alerta que matchea genera su propio
mensaje. El de "se ha activado tu alerta" (la confirmación al crearla) no
trae ningún puesto adentro, y el parser lo detecta solo (no encuentra el
`<a class="strong-text-link">` del título) sin necesitar un filtro de
asunto como en Laborum. Ver el docstring de `_parsear_indeed` para el
detalle del formato.

## Restricciones

- **No scrapear LinkedIn ni Indeed directamente.** Sus términos prohíben el
  scraping y tienen detección agresiva. Es una decisión tomada, no un
  pendiente. La vía que sí está permitida y en uso: alertas de búsqueda por
  correo, leídas por IMAP (`scrapers/correo.py`).
- Respetar las pausas entre requests (`pausa_segundos`). No bajarlas para ir más
  rápido.
- Antes de agregar un portal, revisar su `robots.txt`.

## Comandos

```bash
python main.py buscar --sin-avisos                  # probar sin notificar (todos los usuarios)
python main.py servir                               # dashboard en :5000
python main.py inspeccionar --portal computrabajo   # HTML de muestra (usa config.example.yaml)
python main.py crear-usuario --login X --password Y # dar de alta una cuenta (por SSH)
python main.py generar-invitacion --url-base https://tu-dominio   # link de un solo uso
python main.py resumen [--usuario X]                # conteo por estado, uno o todos
python main.py recalcular-scores [--usuario X]      # repuntúa lo guardado tras editar el perfil
python -m pytest tests/ -v                          # tests
```

## Agregar un portal

1. `src/scrapers/miportal.py` con subclase de `Scraper` que implemente
   `buscar(consulta) -> list[Oferta]`.
2. Registrarla en `PORTALES` (`src/scrapers/__init__.py`).
3. Agregarla a `config.example.yaml` (queda disponible como checkbox en
   `/configuracion` para que cada usuario la active si quiere — ya no hay
   "el" `config.yaml` que editar).

Nada más cambia. Preferir siempre API o RSS sobre HTML.

## Pendientes, en orden de prioridad

1. Registro de postulaciones con fecha y seguimiento de estado.

Descartado (decisión tomada, no un olvido): scoring con la API de Claude
comparando cada oferta contra el CV. Tiene costo recurrente aparte del
servidor y el usuario prefirió no sumarlo.
