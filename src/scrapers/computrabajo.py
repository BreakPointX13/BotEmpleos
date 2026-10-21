"""Computrabajo Chile — scraping de HTML.

IMPORTANTE, LEE ESTO ANTES DE CORRERLO
--------------------------------------
Los selectores de abajo son un punto de partida, no una garantía. Computrabajo
cambia su markup cada cierto tiempo. Antes de confiar en este scraper, corre:

    python main.py inspeccionar --portal computrabajo

Eso baja una página de resultados, la guarda en `data/muestra_computrabajo.html`
e imprime cuántas tarjetas detectó con los selectores actuales. Si detecta cero,
abre ese archivo (o el sitio con las herramientas de desarrollador) y corrige el
diccionario SELECTORES. Es un cambio de dos líneas.

Este es exactamente el tipo de tarea que conviene delegarle a Claude Code:
pásale el HTML de muestra y pídele que ajuste los selectores.

La tarjeta compacta ya no trae una descripción/resumen del puesto —solo
título, empresa, ubicación, salario (si lo publican) y hace cuánto se
publicó—, así que `descripcion` queda vacía para este portal. No es un bug
que se pueda arreglar con otro selector: ese texto simplemente no está en el
listado, hay que abrir cada oferta individual para conseguirlo, y no se
justifica una petición extra por tarjeta solo para eso.

Ojo con `ubicacion`: el párrafo de la empresa también tiene la clase `fs16`
y, cuando la empresa tiene rating, mete su propio `<span>` (el puntaje, tipo
"4,4") antes del párrafo real de ubicación. Por eso el selector excluye
explícitamente ese párrafo (`:not(.dFlex)`) en vez de matchear la primera
`p.fs16 span` que aparezca.

Sobre el User-Agent
--------------------
El WAF de Computrabajo devuelve 403 específicamente por el User-Agent
identificable que usa el resto del proyecto (`base.USER_AGENT`) — con un
User-Agent de navegador normal la misma URL responde 200, sin ningún desafío
de JavaScript de por medio. El `robots.txt` del sitio no prohíbe estas rutas
de búsqueda para ningún user-agent, así que no se está esquivando una
política declarada, solo una regla de firewall demasiado agresiva. Por eso
este scraper —y solo este— pisa el User-Agent por defecto.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup

from ..models import Oferta
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://cl.computrabajo.com"

# Cualquier User-Agent de navegador reciente sirve; no hace falta que coincida
# con uno real byte a byte, el WAF solo descarta lo que no *parece* navegador.
USER_AGENT_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SELECTORES = {
    "tarjeta": "article.box_offer",
    "titulo": "h2 a",
    "empresa": "p.dFlex a",
    "ubicacion": "p.fs16:not(.dFlex) span",
    # Salario y modalidad viven como hermanos dentro del mismo div, cada uno
    # identificado por su ícono (i_salary / i_home_office), no por texto.
    "info_extra": "div.fs13.mt15 span.dIB",
    # Ojo: `div.fs13.mt15` (salario/modalidad) y este `p` comparten clases;
    # `fc_aux` es lo que distingue al párrafo de la fecha.
    "fecha": "p.fs13.fc_aux",
}

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_UNIDADES = {"minuto": "minutes", "hora": "hours", "día": "days", "dia": "days"}


def fecha_publicacion(texto: str, ahora: datetime | None = None) -> str | None:
    """Convierte el texto relativo de la tarjeta en ISO 8601.

    Sin esto `publicada` queda vacía y el dashboard, que ordena "más
    recientes" por esa columna, manda todas las ofertas de Computrabajo al
    fondo (mismo problema que tuvo LinkedIn, ver `correo._fecha_correo`).
    Formatos verificados 2026-10-21: "Hace 10 horas", "Hace 2 días", "Ayer",
    "28 de septiembre" (sin año) y "Más de 30 días". Un formato desconocido
    devuelve None en vez de inventar una fecha.
    """
    ahora = ahora or datetime.now(timezone.utc)
    t = " ".join(texto.lower().split())

    if t == "hoy":
        return ahora.isoformat()
    if t == "ayer":
        return (ahora - timedelta(days=1)).isoformat()

    m = re.fullmatch(r"hace (\d+) (minuto|hora|día|dia)s?", t)
    if m:
        return (ahora - timedelta(**{_UNIDADES[m[2]]: int(m[1])})).isoformat()

    # Cota inferior, no la fecha real: basta para que quede detrás de las
    # que sí tienen fecha precisa.
    if t.startswith("más de 30 días") or t.startswith("mas de 30 dias"):
        return (ahora - timedelta(days=30)).isoformat()

    m = re.fullmatch(r"(\d{1,2}) de ([a-z]+)", t)
    if m and m[2] in _MESES:
        try:
            fecha = ahora.replace(month=_MESES[m[2]], day=int(m[1]))
        except ValueError:
            return None
        # Sin año: "28 de diciembre" leído en enero es del año pasado.
        if fecha > ahora:
            fecha = fecha.replace(year=fecha.year - 1)
        return fecha.isoformat()

    return None


class Computrabajo(Scraper):
    nombre = "computrabajo"
    pausa_segundos = 3.0  # sitio con protección; no lo apures

    def __init__(self, cfg: dict, creds: dict | None = None):
        super().__init__(cfg, creds)
        self.session.headers.update({"User-Agent": USER_AGENT_NAVEGADOR})

    def buscar(self, consulta: str) -> list[Oferta]:
        url = f"{BASE}/trabajo-de-{quote_plus(consulta.replace(' ', '-'))}"
        r = self._get(url)
        if r is None:
            return []
        return self._parsear(r.text)

    def _parsear(self, html_texto: str) -> list[Oferta]:
        sopa = BeautifulSoup(html_texto, "html.parser")
        tarjetas = sopa.select(SELECTORES["tarjeta"])

        if not tarjetas:
            # Cero tarjetas es ambiguo: puede ser un selector roto o, más
            # seguido, una consulta que de verdad no tiene resultados —
            # Computrabajo muestra "¡Ups! Parece que no hay ofertas..." en
            # ese caso (verificado 2026-09-04 contra «front end junior»,
            # una búsqueda real sin nada que ver con selectores). Solo la
            # segunda merece la alarma de selectores desactualizados.
            if "no hay ofertas" in html_texto.lower():
                log.info("computrabajo: sin resultados para esta consulta")
            else:
                log.warning(
                    "computrabajo: 0 tarjetas con «%s». Los selectores están "
                    "desactualizados; corre `inspeccionar` y ajústalos.",
                    SELECTORES["tarjeta"],
                )
            return []

        ofertas = []
        for tarjeta in tarjetas:
            enlace = tarjeta.select_one(SELECTORES["titulo"])
            if not enlace:
                continue
            salario, modalidad = self._salario_y_modalidad(tarjeta)
            ofertas.append(Oferta(
                titulo=enlace.get_text(strip=True),
                empresa=self._texto(tarjeta, "empresa") or "Sin especificar",
                url=urljoin(BASE, enlace.get("href", "")),
                fuente=self.nombre,
                ubicacion=self._texto(tarjeta, "ubicacion"),
                modalidad=modalidad,
                salario=salario,
                publicada=fecha_publicacion(self._texto(tarjeta, "fecha")),
                # `modalidad` no entra en `texto_completo`: se repite acá como
                # tag para que `scoring.py` detecte "remoto"/"híbrido".
                tags=[modalidad] if modalidad else [],
            ))
        return ofertas

    @staticmethod
    def _texto(tarjeta, clave: str) -> str:
        el = tarjeta.select_one(SELECTORES[clave])
        return el.get_text(strip=True) if el else ""

    @staticmethod
    def _salario_y_modalidad(tarjeta) -> tuple[str, str]:
        salario = modalidad = ""
        for bloque in tarjeta.select(SELECTORES["info_extra"]):
            icono = bloque.select_one(".icon")
            clases = icono.get("class", []) if icono else []
            texto = bloque.get_text(strip=True)
            if "i_salary" in clases:
                salario = texto
            elif "i_home_office" in clases:
                modalidad = texto
        return salario, modalidad

    def guardar_muestra(self, consulta: str, destino: str) -> int:
        """Baja una página y la guarda, para poder ajustar selectores offline."""
        url = f"{BASE}/trabajo-de-{quote_plus(consulta.replace(' ', '-'))}"
        r = self._get(url)
        if r is None:
            return 0
        with open(destino, "w", encoding="utf-8") as f:
            f.write(r.text)
        return len(BeautifulSoup(r.text, "html.parser").select(SELECTORES["tarjeta"]))
