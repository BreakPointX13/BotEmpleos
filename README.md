# Buscador de ofertas

Recolecta ofertas de trabajo de varios portales, las puntúa según el perfil de
cada usuario, elimina duplicados y avisa por Telegram. Trae además un
dashboard con login real para revisar y marcar ofertas con el teclado.

**Multiusuario**: nació como proyecto personal para un perfil de desarrollador
junior (full stack y móvil) en Chile, y ahora soporta varias personas —cada
una con su propia búsqueda (portales, palabras clave, pesos de puntaje), su
propio Telegram y, si quiere, su propia cuenta de Gmail para alertas por
correo. Cada quien configura lo suyo desde `/configuracion` en el navegador,
sin tocar YAML ni terminal.

---

## Partir

```bash
git clone <tu-repo> && cd buscador-empleos
python -m venv venv && source venv/bin/activate    # en Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Completa FLASK_SECRET_KEY (cualquier string largo al azar, sirve para
# firmar las sesiones del dashboard) y, si ya tienes datos de una instalación
# de una sola persona, TELEGRAM_TOKEN/TELEGRAM_CHAT_ID/GMAIL_* para migrarlos.

# Cuenta nueva desde cero (la primera, la tuya):
python main.py crear-usuario --login tu-usuario --password tu-clave

# Para invitar a alguien más después, no hace falta SSH de nuevo — genera un
# link de un solo uso y que la persona cree su propia cuenta:
python main.py generar-invitacion --url-base https://tu-dominio-o-ip

# O, si vienes de una versión previa de una sola persona con config.yaml y
# datos ya acumulados:
python main.py migrar-a-multiusuario --login tu-usuario --password tu-clave

python main.py buscar --sin-avisos
python main.py servir     # http://127.0.0.1:5000 → /login
```

Ya no existe "el" `config.yaml` de uso diario: cada usuario tiene su propio
perfil de búsqueda guardado en la base de datos, editable desde
`/configuracion` en el dashboard. `config.example.yaml` queda como plantilla
de referencia (la config inicial de una cuenta nueva, y la que usa
`inspeccionar` para probar scrapers).

## Comandos

| Comando | Qué hace |
|---|---|
| `python main.py buscar` | Recolecta, puntúa, guarda y avisa por Telegram — para **todos** los usuarios activos |
| `python main.py buscar --sin-avisos` | Igual, pero sin notificar. Úsalo mientras calibras |
| `python main.py servir` | Levanta el dashboard en localhost:5000 |
| `python main.py resumen [--usuario LOGIN]` | Cuántas ofertas hay en cada estado, de uno o de todos |
| `python main.py recalcular-scores [--usuario LOGIN]` | Repuntúa lo ya guardado con el perfil actual — corre esto después de cambiar filtros/pesos en `/configuracion`, si quieres que también se actualice lo que ya tenías |
| `python main.py crear-usuario --login X --password Y` | Da de alta una cuenta nueva con la config de ejemplo (por SSH) |
| `python main.py generar-invitacion --url-base https://tu-dominio` | Link de un solo uso para que alguien cree su propia cuenta sin SSH |
| `python main.py migrar-a-multiusuario --login X --password Y` | Migra una instalación vieja de una sola persona (una vez) |
| `python main.py inspeccionar --portal computrabajo` | Guarda HTML de muestra para arreglar selectores |
| `python -m pytest tests/ -v` | Corre los tests |

## Estado de cada portal

| Portal | Método | Estado |
|---|---|---|
| **Get on Board** | API pública JSON | Listo, verificado contra una respuesta real. Es tu fuente principal |
| **Remote OK** | API pública JSON | Listo. Sin filtro por consulta en el origen: el scraper filtra localmente por palabra clave (AND, sin `tags`, ver docstring) |
| **We Work Remotely** | RSS público | Listo. Mismo filtrado local que Remote OK, sobre el feed agregado de programación (~25 ofertas recientes, sesgado a senior) |
| **Trabajando.cl** | API interna (no documentada) | Listo. Búsqueda real por palabra clave. Ojo: endpoint encontrado a mano inspeccionando bundles de Nuxt, no hay documentación pública — ver docstring del scraper si deja de funcionar |
| **Terminal.io** | JSON embebido (SSR) | Listo. Solo ofertas que aceptan postulantes desde Chile. Contrata para empresas de EE.UU./Canadá, sesgado a senior — pocas coincidencias esperadas para un perfil junior |
| **Computrabajo** | Scraping HTML | Listo. El bloqueo era por User-Agent (WAF), no por selectores desactualizados — igual se corrigieron dos selectores rotos de paso (ubicación y salario/modalidad). Ver docstring del scraper |
| **Correo (Laborum)** | Alerta por email, vía IMAP | Listo, verificado contra un correo real. Requiere `GMAIL_USER`/`GMAIL_APP_PASSWORD` en `.env` y una alerta creada en Laborum |
| **Correo (LinkedIn)** | Alerta por email, vía IMAP | Listo, verificado contra un correo real. Sorpresa: el correo de "se creó tu alerta" ya trae las primeras ofertas adentro, no es solo una confirmación |
| **Correo (Indeed)** | Alerta por email, vía IMAP | Listo, verificado contra un correo real. A diferencia de Laborum/LinkedIn manda un puesto por correo, no un resumen — cada match genera su propio mensaje |

**Get on Board** cubre bastante bien el mercado tech junior en Chile y es la
fuente principal. Su API está en beta, así que si algún día deja de devolver
resultados, revisa `https://api-doc.getonbrd.com` y ajusta
`src/scrapers/getonbrd.py`. El mapeo de campos ya está verificado contra una
respuesta real (empresa, ubicación, modalidad y tags estaban rotos hasta
hace poco — ver el docstring del archivo para el detalle).

**Computrabajo** ya está activo. Su WAF devolvía 403 por el User-Agent
identificable del proyecto —no por selectores desactualizados—, así que este
scraper usa un User-Agent de navegador normal en vez del genérico (ver el
docstring de `src/scrapers/computrabajo.py` para el porqué). El markup igual
puede cambiar con el tiempo; si un día deja de traer resultados:

```bash
python main.py inspeccionar --portal computrabajo
```

Eso te dice cuántas tarjetas detecta con los selectores actuales. Si detecta
cero, abre el HTML que guardó y corrige el diccionario `SELECTORES` al inicio de
`src/scrapers/computrabajo.py`. Es una tarea ideal para pasarle a Claude Code
junto con el archivo de muestra.

**LinkedIn no se scrapea directo y no conviene agregarlo.** Sus términos de
servicio prohíben el scraping y tiene detección agresiva que termina en
bloqueo de cuenta o IP. Lo mismo pasa con Indeed. La alternativa que sí está
implementada: crear alertas de empleo nativas en LinkedIn/Indeed y leer esos
correos por IMAP (`src/scrapers/correo.py`) en vez de tocar sus sitios — ver
la fila "Correo" en la tabla de arriba.

## Cómo funciona el puntaje

Cada oferta parte en cero y suma. Si aparece un término de `filtros.excluir`
(senior, tech lead, 5 años), se descarta de inmediato sin analizar el resto.

| Señal | Puntos |
|---|---|
| "junior" en el título | 40 |
| "junior" solo en la descripción | 20 |
| Cada tecnología de tu stack | 6, con tope de 30 |
| El rol coincide con lo que buscas | 15 |
| Remoto / híbrido | 10 / 5 |

Cada punto queda registrado en la columna `motivos`, que el dashboard muestra
bajo cada oferta. Si el ranking no refleja tus prioridades, mira los motivos y
ajusta los pesos desde `/configuracion`.

Los términos se buscan como palabra completa, no como subcadena. Suena a
detalle, pero sin eso `sr` haría match dentro de "desarrollo" y descartarías
media base de datos sin notarlo. Hay un test que cubre justamente ese caso.

## Deduplicación

La misma vacante aparece en varios portales con títulos distintos. Cada oferta
tiene una huella calculada sobre el título despojado de palabras genéricas, de
modo que "Desarrollador Full Stack Junior" y "Junior Full-Stack Dev" de la misma
empresa colapsan en un solo registro. La fuente adicional queda anotada en
`fuentes_extra`.

No es perfecto: falla cuando la empresa se escribe distinto en cada portal
("Acme SpA" vs "Acme"). Si te molesta, ahí está el siguiente lugar donde iterar.

## Dashboard

Pensado para una pasada corta al día. Navegación con teclado, sin mouse:

- `j` / `k` — moverse entre ofertas
- `i` — marcar que te interesa
- `p` — marcar que postulaste
- `x` — descartar
- `enter` — abrir la oferta en otra pestaña

## Telegram

Cada usuario tiene su propio bot — no es una variable de entorno compartida:

1. Habla con `@BotFather`, manda `/newbot`, guarda el token.
2. Escríbele algo a tu bot recién creado.
3. Abre `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y copia el `chat.id`.
4. Carga ambos valores en `/configuracion`, en el dashboard.

## Automatizar

Un solo proceso Flask + un solo `cron` alcanzan para todos los usuarios —
`main.py buscar` internamente corre la recolección de cada cuenta activa por
separado, así que no hace falta un cron por persona:

```
0 9,15,21 * * * cd /ruta/al/proyecto && ./venv/bin/python main.py buscar >> /var/log/bot-empleos.log 2>&1
```

Para dejarlo corriendo 24/7 sin depender de tu computador, un VPS chico
(1 vCPU / 1 GB RAM sobra) con el dashboard como servicio `systemd`
(`python main.py servir --host 0.0.0.0 --puerto 5000`) y el cron de arriba en
el crontab de root es suficiente. Ojo con exponer el dashboard a internet:
usa un `FLASK_SECRET_KEY` real y, si el tráfico te preocupa (redes públicas),
considera ponerle HTTPS con un dominio.

## Buenas prácticas de scraping

El proyecto ya trae pausa de 2,5 a 3 segundos entre requests, un User-Agent
identificable y reintentos con espera. Cambia el correo de contacto en
`src/scrapers/base.py` por el tuyo. Con este volumen ningún portal se molesta,
pero revisa el `robots.txt` de cualquier sitio nuevo antes de agregarlo.

## Agregar un portal

1. Crea `src/scrapers/miportal.py` con una subclase de `Scraper` que implemente
   `buscar(consulta) -> list[Oferta]`.
2. Regístrala en el diccionario `PORTALES` de `src/scrapers/__init__.py`.
3. Agrégala a `config.example.yaml` — queda disponible como checkbox en
   `/configuracion` para que cada usuario la active si le sirve.

Nada más del sistema necesita cambiar. El scoring, la deduplicación, la base y
las notificaciones ya funcionan sobre el modelo común, por usuario.

## Estructura

```
main.py                     CLI (incluye alta de cuentas y migración)
config.example.yaml         plantilla del perfil de búsqueda de un usuario
src/
  models.py                 Oferta + huella para dedup
  db.py                     SQLite: usuarios + ofertas (PK usuario_id+huella)
  scoring.py                puntaje y exclusiones, genérico por cfg
  pipeline.py               ejecutar_todos(): corre cada usuario por separado
  scrapers/                 uno por portal, reciben (cfg, creds)
  notify/telegram.py        avisos, token/chat_id explícitos por usuario
dashboard/
  auth.py                   login por sesión
  app.py                    rutas: /login, /, /estado/<huella>, /configuracion
  templates/                index, login, configuracion
tests/                      16 tests del núcleo (incluye aislamiento multiusuario)
```

## Próximos pasos sugeridos

1. **Registrar postulaciones** con fecha y estado, para hacer seguimiento.
2. Terminar `_parsear_indeed` en `src/scrapers/correo.py` cuando llegue el
   primer correo real con ofertas (hoy solo llega la confirmación de alerta).
