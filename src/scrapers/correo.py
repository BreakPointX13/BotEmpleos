"""Alertas por correo (Laborum, LinkedIn, Indeed) — vía IMAP, sin API.

Estos tres portales solo exponen sus ofertas más recientes a través de
alertas de búsquedas guardadas ("avísame por correo"), no de una API o HTML
scrapeable de forma directa: Laborum retiene el listado real detrás de un
mecanismo que requiere navegador, y LinkedIn/Indeed prohíben el scraping
directo (ver Restricciones en `CLAUDE.md`). La alternativa: crear la alerta
en cada portal y leer esos correos en vez de tocar sus sitios.

Requiere `gmail_user`/`gmail_app_password` en `creds` (multiusuario: cada
quien tiene su propia cuenta de Gmail, guardada en su fila de `usuarios`, no
en una variable de entorno global — ver `Scraper.creds` en `base.py`). La
contraseña de aplicación se genera en la cuenta de Google → Seguridad →
Verificación en dos pasos (tiene que estar activada) → Contraseñas de
aplicaciones. No es la contraseña normal de la cuenta.

Sobre el remitente: OJO, filtrar por dominio completo ("linkedin.com") trae
TODO el historial de esa dirección —invitaciones, feed, miles de correos
viejos—, no solo las alertas. Hay que usar la dirección exacta del remitente
de alertas de cada portal, no el dominio genérico:

  - LinkedIn: jobalerts-noreply@linkedin.com (jobs-noreply@ y
    updates-noreply@ son recomendaciones y feed social, no alertas)
  - Indeed:   donotreply@jobalert.indeed.com (resumen "N puestos nuevos") Y
    ADEMÁS donotreply@match.indeed.com (aviso individual de "instant match",
    un puesto por correo, sin resumen) — detectado el 2026-09-04 cuando un
    correo de este segundo remitente no aparecía en ningún lado: llega con
    el mismo HTML (`<a class="strong-text-link">`), así que usa el mismo
    `_parsear_indeed`, solo faltaba registrar la dirección en `PARSERS`. Si
    Indeed vuelve a sumar un tercer remitente de alertas, mismo chequeo:
    HTML real antes de asumir que hace falta un parser nuevo.
  - Laborum:  no_reply@laborum.cl (ojo: esta misma dirección también manda
    confirmaciones de postulación, no solo alertas — el parser tiene que
    poder ignorar esas sin romperse, ver `_parsear_laborum`)

Además de filtrar por remitente exacto, `_buscar_de` acota la búsqueda a los
últimos `DIAS_ATRAS` días (`SINCE` de IMAP) para no volver a traer un
historial completo en cada corrida.

El filtrado nunca toca el resto de la bandeja: se conecta una sola vez por
corrida y le pide al propio servidor IMAP (`SEARCH FROM ... SINCE ...`) antes
de descargar nada. `readonly` además evita marcar nada como leído ni mover
mensajes por error.

`python main.py inspeccionar --portal correo` guarda el asunto y el HTML de
cada correo encontrado en `data/muestra_correo.html` — útil si alguno de los
tres parsers deja de matchear algún día (mismo principio que con
`computrabajo.py`: corregir contra contenido real, no adivinar el formato).
Ojo: ese comando usa `config.example.yaml` sin credenciales, así que para
"correo" no trae nada por sí solo — hay que instanciar `Correo(cfg, creds)`
a mano con las credenciales de un usuario real (ver `_parsear_indeed` para
el contexto de cuándo se hizo así la última vez). Los tres parsers
(`_parsear_laborum`, `_parsear_linkedin`, `_parsear_indeed`) ya están
escritos y verificados contra un correo real cada uno — ver sus docstrings
para el detalle de cada formato.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from ..models import Oferta
from .base import Scraper

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
DIAS_ATRAS = 5  # ventana de búsqueda; suficiente margen si la corrida se salta un día


def _decodificar(valor: str) -> str:
    partes = decode_header(valor or "")
    return "".join(
        t.decode(cod or "utf-8", errors="replace") if isinstance(t, bytes) else t
        for t, cod in partes
    )


def _fecha_correo(msg: Message) -> str | None:
    """ISO 8601 de cuándo llegó el correo. Se usa como reemplazo de
    `publicada` en formatos que no traen la fecha real de la oferta (ver
    `_parsear_linkedin`): no es la fecha en que se publicó el puesto, pero
    evita que la oferta quede enterrada al fondo del orden "más recientes"
    del dashboard por no tener ninguna fecha con la que compararla."""
    try:
        return parsedate_to_datetime(msg.get("Date", "")).isoformat()
    except (TypeError, ValueError):
        return None


def _cuerpo_html(msg: Message) -> str:
    if msg.is_multipart():
        for parte in msg.walk():
            if parte.get_content_type() == "text/html":
                payload = parte.get_payload(decode=True) or b""
                return payload.decode(parte.get_content_charset() or "utf-8", errors="replace")
        return ""
    if msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _parsear_linkedin(msg: Message) -> list[Oferta]:
    """Sorpresa (verificada contra un correo real): el correo de "se ha creado
    tu alerta" no es solo una confirmación vacía, como sí pasa en
    Indeed/Laborum — LinkedIn le mete de una vez las primeras coincidencias
    en el mismo correo.

    LinkedIn usa DOS plantillas distintas para la misma tarjeta, y cambia de
    una a otra sin aviso (se detectó el 2026-09-02: la mitad de las alertas
    de un mismo día ya venían en el formato nuevo). Cada una marca el <a>
    del título con una clase CSS distinta, pero comparten la estructura de
    abajo — por eso ambas se buscan a la vez y el resto del parseo es común:

      - Formato viejo: <a class="text-color-brand">.
      - Formato nuevo ("job-card-flavor", digest rediseñado):
        <a class="font-bold ... text-system-blue-50">. Ojo: alrededor del
        título hay OTROS <a> al mismo puesto (logo de la empresa, wrapper de
        la tarjeta) sin la clase "font-bold" — filtrar solo por
        "/jobs/view/" en el href los incluiría a los tres y triplicaría cada
        oferta.

    En ambos formatos, el id numérico va en la URL (`/jobs/view/{id}`), y el
    <tr> hermano siguiente al del título trae un <p> "Empresa · Ubicación".
    No hay descripción, modalidad ni fecha de publicación real en ningún
    formato — `publicada` usa la fecha del correo como aproximación (ver
    `_fecha_correo`), si no el dashboard las manda todas al fondo del orden
    "más recientes" por no tener fecha con la que compararlas.

    Si en el futuro deja de matchear de nuevo, correr `inspeccionar` y mirar
    el HTML real antes de asumir que sigue siendo alguna de estas dos.
    """
    sopa = BeautifulSoup(_cuerpo_html(msg), "html.parser")
    publicada = _fecha_correo(msg)

    def _es_titulo(tag) -> bool:
        if tag.name != "a" or "/jobs/view/" not in tag.get("href", ""):
            return False
        clases = tag.get("class") or []
        return "text-color-brand" in clases or "font-bold" in clases

    ofertas = []
    for a in sopa.find_all(_es_titulo):
        titulo = a.get_text(strip=True)
        if not titulo:
            continue

        m = re.search(r"/jobs/view/(\d+)", a.get("href", ""))
        url = f"https://www.linkedin.com/jobs/view/{m.group(1)}" if m else a.get("href", "")

        fila_titulo = a.find_parent("tr")
        fila_empresa = fila_titulo.find_next_sibling("tr") if fila_titulo else None
        partes = [p.strip() for p in (fila_empresa.get_text(strip=True) if fila_empresa else "").split("·")]
        empresa = partes[0] if partes and partes[0] else "Sin especificar"
        ubicacion = " · ".join(p for p in partes[1:] if p)

        ofertas.append(Oferta(
            titulo=titulo,
            empresa=empresa,
            url=url,
            fuente="linkedin",
            ubicacion=ubicacion,
            publicada=publicada,
        ))
    return ofertas


def _parsear_indeed(msg: Message) -> list[Oferta]:
    """Verificado contra un correo real (2026-09-02): a diferencia de
    LinkedIn y Laborum, cada alerta de Indeed trae UN solo puesto por
    correo, no un resumen de varios. El de "se ha activado tu alerta" (la
    confirmación al crear la alerta) no trae ningún <a class="strong-text-
    link">, así que simplemente no matchea nada acá — no hace falta
    filtrarlo por asunto como en Laborum.

    El título es un <a class="strong-text-link"> dentro de un <h2> (hay OTRO
    <h2> decorativo antes, "1 puesto disponible de...", sin esa clase — por
    eso se busca por el <a>, no por el <h2>). Los dos primeros <p> que le
    siguen en el documento son empresa y ubicación, en ese orden; más abajo
    puede haber un <p> con el sueldo estimado (con "$"), no siempre presente
    porque Indeed solo lo estima cuando la empresa no lo publica.

    El link va envuelto en el tracking de Indeed (`clk.indeed.com/rc/clk/dl`)
    — igual que Laborum con `delivery.laborum.cl`, es la URL que hay que
    dejar tal cual.
    """
    sopa = BeautifulSoup(_cuerpo_html(msg), "html.parser")
    publicada = _fecha_correo(msg)

    ofertas = []
    for a in sopa.find_all("a", class_="strong-text-link"):
        titulo = a.get_text(strip=True)
        h2 = a.find_parent("h2")
        if not titulo or not h2:
            continue

        siguientes = h2.find_all_next("p")
        empresa = siguientes[0].get_text(strip=True) if len(siguientes) > 0 else "Sin especificar"
        ubicacion = siguientes[1].get_text(strip=True) if len(siguientes) > 1 else ""
        salario = next(
            (p.get_text(strip=True) for p in siguientes[:5] if "$" in p.get_text()), ""
        )

        ofertas.append(Oferta(
            titulo=titulo,
            empresa=empresa,
            url=a.get("href", ""),
            fuente="indeed",
            ubicacion=ubicacion,
            salario=salario,
            publicada=publicada,
        ))
    return ofertas


# Laborum manda desde la misma dirección tanto el resumen de alertas como
# confirmaciones de postulación ("ya recibieron tu postulación") — y estas
# últimas también incluyen una sección de "ofertas relacionadas" con <h3>
# reales, pero con una estructura de tarjeta distinta (sin los <p> de
# empresa/ubicación al lado). Un asunto que no menciona "aviso" es la señal
# más confiable —verificada contra un correo real de cada tipo— de que no es
# un resumen y hay que descartarlo antes de intentar parsear nada.
_ASUNTOS_NO_DIGEST = ("postulacion", "postulación", "postulaste")


def _parsear_laborum(msg: Message) -> list[Oferta]:
    """Cada oferta del resumen es un <h3> envuelto en un <a> de tracking
    (`delivery.laborum.cl`), seguido de una serie de <p> hermanos con
    empresa, ubicación, jornada y modalidad, en ese orden. No hay
    descripción: el resumen por correo no la incluye, igual que en el
    scraper HTML (ver docstring de `computrabajo.py`).
    """
    asunto = _decodificar(msg.get("Subject", "")).lower()
    if any(patron in asunto for patron in _ASUNTOS_NO_DIGEST):
        return []

    sopa = BeautifulSoup(_cuerpo_html(msg), "html.parser")
    # El resumen tampoco trae fecha de publicación; misma aproximación que
    # en `_parsear_linkedin` para que no quede al fondo del dashboard.
    publicada = _fecha_correo(msg)

    ofertas = []
    for h3 in sopa.find_all("h3"):
        enlace = h3.find_parent("a")
        if not enlace or not h3.parent or not h3.parent.parent:
            continue

        campos = [
            p.get_text(strip=True)
            for p in h3.parent.parent.find_next_siblings("p")
        ]
        # "Apto discapacidad" es un badge fijo, no un dato posicional; se
        # descarta para no correr el resto de los campos de lugar.
        campos = [c for c in campos if c and c != "Apto discapacidad"]
        if not campos:
            # Sin ningún <p> hermano: es una tarjeta de otra plantilla (p. ej.
            # "ofertas relacionadas"), no una del resumen real. Mejor
            # perderla que guardarla con todo en blanco.
            continue

        ofertas.append(Oferta(
            titulo=h3.get_text(strip=True),
            empresa=campos[0],
            url=enlace.get("href", ""),
            fuente="laborum",
            ubicacion=campos[1] if len(campos) > 1 else "",
            modalidad=campos[3] if len(campos) > 3 else "",
            publicada=publicada,
            tags=[c for c in campos[2:4] if c],  # jornada + modalidad, visibles para scoring.py
        ))
    return ofertas


# remitente exacto -> función que convierte ese correo en ofertas
PARSERS = {
    "jobalerts-noreply@linkedin.com": _parsear_linkedin,
    "donotreply@jobalert.indeed.com": _parsear_indeed,
    "donotreply@match.indeed.com": _parsear_indeed,
    "no_reply@laborum.cl": _parsear_laborum,
}


class Correo(Scraper):
    nombre = "correo"

    def buscar_todo(self, consultas: list[str]) -> list[Oferta]:
        # No hay "consulta" acá: cada alerta ya filtró por palabra clave del
        # lado del portal. Se lee todo lo nuevo en una sola pasada, no una
        # vez por consulta como el resto de los scrapers.
        try:
            return self.buscar("")
        except Exception:
            log.exception("correo: error irrecuperable leyendo la bandeja")
            return []

    def buscar(self, consulta: str) -> list[Oferta]:
        usuario, clave = self._credenciales()
        if not usuario:
            return []

        ofertas: list[Oferta] = []
        with imaplib.IMAP4_SSL(IMAP_HOST) as conexion:
            conexion.login(usuario, clave)
            conexion.select("INBOX", readonly=True)
            for remitente, parser in PARSERS.items():
                for msg in self._buscar_de(conexion, remitente):
                    ofertas.extend(parser(msg))
        return ofertas

    def _credenciales(self) -> tuple[str, str]:
        # Multiusuario: cada quien tiene su propia cuenta de Gmail, así que
        # ya no puede salir de una variable de entorno global del proceso.
        usuario = self.creds.get("gmail_user")
        clave = self.creds.get("gmail_app_password")
        if not usuario or not clave:
            log.warning("correo: sin configurar (falta gmail_user o gmail_app_password)")
            return "", ""
        return usuario, clave

    @staticmethod
    def _buscar_de(conexion: imaplib.IMAP4_SSL, remitente: str) -> list[Message]:
        desde = (datetime.now(timezone.utc) - timedelta(days=DIAS_ATRAS)).strftime("%d-%b-%Y")
        _, datos = conexion.search(None, f'(FROM "{remitente}" SINCE "{desde}")')
        mensajes = []
        for id_msg in datos[0].split():
            _, datos_msg = conexion.fetch(id_msg, "(RFC822)")
            if not datos_msg or not datos_msg[0]:
                continue
            mensajes.append(email.message_from_bytes(datos_msg[0][1]))
        return mensajes

    def guardar_muestra(self, consulta: str, destino: str) -> int:
        """Guarda asunto + HTML de cada correo encontrado, para escribir los
        parsers contra contenido real en vez de adivinar el formato."""
        usuario, clave = self._credenciales()
        if not usuario:
            return 0

        bloques: list[str] = []
        with imaplib.IMAP4_SSL(IMAP_HOST) as conexion:
            conexion.login(usuario, clave)
            conexion.select("INBOX", readonly=True)
            for remitente in PARSERS:
                for msg in self._buscar_de(conexion, remitente):
                    asunto = _decodificar(msg.get("Subject", ""))
                    de = _decodificar(msg.get("From", ""))
                    bloques.append(
                        f"<!-- ===== {remitente} | De: {de} | Asunto: {asunto} ===== -->\n"
                        + _cuerpo_html(msg)
                    )

        with open(destino, "w", encoding="utf-8") as f:
            f.write("\n\n".join(bloques))
        return len(bloques)
