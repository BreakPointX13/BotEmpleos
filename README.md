# BotEmpleos

Sistema multiusuario que recolecta ofertas de trabajo desde distintos portales
de empleo, las puntúa según el perfil de cada persona, elimina duplicados y
notifica las más relevantes por Telegram. Incluye un dashboard web con
autenticación para revisar y clasificar las ofertas.

Comenzó como una herramienta personal para automatizar mi propia búsqueda de
empleo como desarrollador junior en Chile. Hoy lo usan varias personas con
carreras distintas, cada una con su propio perfil de búsqueda, que configura
desde el navegador.

## Funcionalidades

- **Recolección desde nueve fuentes**: APIs públicas, feeds RSS, scraping de
  HTML y lectura de alertas de empleo por correo (IMAP).
- **Puntaje configurable por usuario**: cada oferta recibe un puntaje según
  la seniority, las tecnologías, el rol y la modalidad, y queda registrado el
  motivo de cada punto.
- **Exclusiones duras**: las ofertas que contienen términos excluidos (por
  ejemplo, "senior" o "tech lead" en un perfil junior) se descartan antes de
  llegar al usuario.
- **Deduplicación entre portales**: la misma vacante publicada en varios
  sitios con títulos distintos se consolida en un único registro.
- **Notificaciones por Telegram**: cada usuario recibe sus avisos en su
  propio bot.
- **Dashboard web**: login por sesión, listado ordenado por puntaje o fecha y
  triaje completo con atajos de teclado (interesa / postulé / descartar).
- **Configuración desde el navegador**: cada usuario edita sus portales,
  consultas, filtros y pesos en un formulario, sin tocar archivos ni la
  terminal.
- **Alta de cuentas por invitación**: links de un solo uso con expiración,
  sin registro público abierto.

## Stack tecnológico

| Área | Tecnología |
|---|---|
| Lenguaje | Python 3 |
| Web | Flask, Jinja2, Werkzeug (hash de contraseñas) |
| Base de datos | SQLite (SQL directo, sin ORM) |
| Recolección | requests, BeautifulSoup4, xml.etree (RSS), imaplib y email (IMAP) |
| Configuración | PyYAML (perfil de referencia), JSON (perfil por usuario) |
| Notificaciones | API de bots de Telegram |
| Frontend | HTML y CSS propios, JavaScript sin frameworks |
| Tests | pytest |

El proyecto usa solo cinco dependencias externas a propósito: cada librería
nueva tenía que justificarse frente a lo que ya ofrece la biblioteca estándar.

## Arquitectura

```
scrapers  ->  scoring  ->  deduplicación  ->  SQLite  ->  Telegram / dashboard
```

Todos los scrapers devuelven una lista del mismo modelo (`Oferta`). Ese
modelo es el contrato entre capas: el puntaje, la deduplicación, la
persistencia y las notificaciones no saben de qué portal vino cada dato, y
agregar un portal nuevo no exige cambios en el resto del sistema.

```
main.py                  CLI: búsqueda, servidor, gestión de usuarios, mantenimiento
src/
  models.py              modelo Oferta y huella de deduplicación
  scoring.py             puntaje y exclusiones a partir del perfil del usuario
  db.py                  capa de datos (usuarios, ofertas, invitaciones)
  pipeline.py            ejecución por usuario activo
  scrapers/              un módulo por fuente, sobre una clase base común
  notify/telegram.py     envío de avisos
dashboard/
  app.py                 rutas web
  auth.py                autenticación por sesión
  templates/             vistas
tests/                   tests del núcleo, sin acceso a red
```

## Fuentes de datos

| Fuente | Método |
|---|---|
| Get on Board | API pública JSON |
| Remote OK | API pública JSON |
| We Work Remotely | RSS |
| Terminal | JSON embebido en la página |
| Trabajando.cl | API JSON del sitio |
| Computrabajo | Scraping de HTML |
| Laborum, LinkedIn, Indeed | Alertas de empleo por correo, leídas por IMAP |

LinkedIn e Indeed prohíben el scraping en sus términos de servicio. En lugar
de scrapearlos, el sistema lee las alertas de empleo que esos mismos sitios
envían por correo al usuario, que es el canal que ellos ofrecen para este
propósito.

## Decisiones técnicas destacadas

**Aislamiento de datos entre usuarios.** La clave primaria de las ofertas es
compuesta (`usuario_id`, `huella`), de modo que una misma vacante puede ser
relevante para dos personas con estados de seguimiento independientes. La
capa de datos exige el `usuario_id` en cada operación y lanza un error si
falta: es preferible un fallo visible a una fuga silenciosa de datos entre
cuentas. Como SQLite no permite modificar una clave primaria con
`ALTER TABLE`, el paso de una instalación de un solo usuario a multiusuario
se resolvió con un comando de migración dedicado.

**Perfil como datos, no como código.** El perfil de cada usuario (portales,
consultas, filtros y pesos) se guarda como JSON en la base de datos, con el
mismo formato que el archivo de configuración original. Así, el puntaje y
los scrapers no tuvieron que cambiar su lógica para soportar perfiles
distintos: solo cambió el origen de la configuración.

**Deduplicación por normalización.** Cada oferta tiene una huella calculada
sobre la empresa y el título normalizado, en tres pasos cuyo orden importa:
eliminar frases completas, unificar sinónimos y quitar palabras genéricas.
Por ejemplo, "full time" es ruido, pero "full stack" es información, así que
las frases se procesan primero. Con esto, "Desarrollador Full Stack Junior"
y "Junior Full-Stack Dev" de la misma empresa se consolidan en un solo
registro.

**Coincidencia por palabra completa.** Los términos de los filtros se buscan
como palabras completas y no como subcadenas. Sin esto, excluir "sr" (senior)
descartaría cualquier oferta que contenga "desarrollo". Hay un test
específico para este caso.

**Tolerancia a fallos.** Si un portal falla o cambia su formato, la corrida
continúa con el resto: cada consulta captura sus propios errores. Los
portales sin búsqueda por texto se descargan una sola vez por corrida y se
filtran localmente, en vez de consultarlos una vez por cada término de
búsqueda.

**Scraping responsable.** El sistema hace pausas de 2,5 segundos entre
requests al mismo portal, reintenta con espera, identifica su User-Agent y
revisa el `robots.txt` de cada sitio antes de incorporarlo.

**Verificación contra datos reales.** Los parsers de cada fuente se
validaron contra respuestas y correos reales, no solo contra la
documentación. En el caso de la API de Get on Board, la documentación no
coincidía con la respuesta real en varios campos, y LinkedIn alterna entre
dos plantillas de correo distintas para la misma alerta; ambos casos se
detectaron y corrigieron de esta forma.

## Puntaje

Cada oferta parte en cero. Si contiene un término excluido, se descarta de
inmediato. Si no, suma puntos según señales con pesos configurables por
usuario. Estos son los valores por defecto para un perfil junior:

| Señal | Puntos |
|---|---|
| "junior" en el título | 40 |
| "junior" solo en la descripción | 20 |
| Cada tecnología del stack buscado | 6 (máximo 30) |
| Rol coincidente | 15 |
| Remoto / híbrido | 10 / 5 |

## Instalación

Requiere Python 3.10 o superior.

```bash
git clone https://github.com/BreakPointX13/BotEmpleos.git
cd BotEmpleos
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # completar FLASK_SECRET_KEY
python main.py crear-usuario --login usuario --password clave
python main.py buscar --sin-avisos
python main.py servir             # http://127.0.0.1:5000
```

Las credenciales de Telegram y Gmail de cada usuario se configuran desde la
sección `/configuracion` del dashboard.

## Comandos

| Comando | Descripción |
|---|---|
| `python main.py buscar [--sin-avisos]` | Recolecta, puntúa y guarda ofertas para todos los usuarios activos |
| `python main.py servir` | Inicia el dashboard web |
| `python main.py crear-usuario --login X --password Y` | Crea una cuenta |
| `python main.py generar-invitacion --url-base URL` | Genera un link de registro de un solo uso |
| `python main.py resumen [--usuario X]` | Muestra el conteo de ofertas por estado |
| `python main.py recalcular-scores [--usuario X]` | Recalcula los puntajes después de cambiar un perfil |
| `python main.py inspeccionar --portal computrabajo` | Guarda HTML de muestra para diagnosticar selectores |
| `python -m pytest tests/ -v` | Ejecuta los tests |

Para una ejecución periódica basta con una sola entrada de `cron`, que procesa
a todos los usuarios:

```
0 9,15,21 * * * cd /ruta/al/proyecto && ./venv/bin/python main.py buscar
```

## Tests

La suite cubre el núcleo del sistema sin acceder a la red: el puntaje y las
exclusiones (incluida la coincidencia por palabra completa), la
deduplicación y el aislamiento de datos entre usuarios.

```bash
python -m pytest tests/ -v
```

## Próximos pasos

- Registro de postulaciones con fecha y seguimiento de cada proceso.
- Mejorar la deduplicación cuando una misma empresa aparece con nombres
  distintos en cada portal (por ejemplo, "Acme SpA" y "Acme").

## Autor

Daniel Leal — [github.com/BreakPointX13](https://github.com/BreakPointX13)
