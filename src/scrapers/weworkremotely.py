"""We Work Remotely — vía su RSS público de programación, sin autenticación.

Se usa el feed agregado de la categoría (Full-Stack + Back-End + Front-End
Programming, ~25 ofertas más recientes) en vez de categorías sueltas: no hay
una categoría separada de "mobile" vigente y este feed ya las junta todas. No
soporta búsqueda por texto, así que —igual que en `remoteok.py`— la respuesta
se cachea una vez por corrida y el filtrado por consulta se hace acá.

El título viene como "Empresa: Puesto"; si no trae ":" se usa el título
completo y se deja la empresa sin especificar.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..models import Oferta, normalizar_texto
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://weworkremotely.com/categories/remote-programming-jobs.rss"

# Mismo problema que en remoteok.py: los textos en inglés suelen escribir
# estos términos pegados ("fullstack") mientras que config.yaml los trae
# separados ("full stack").
_SINONIMOS_EN = {
    "full stack": "fullstack",
    "front end": "frontend",
    "back end": "backend",
    "movil": "mobile",
}


def _normalizar_para_match(texto: str) -> str:
    texto = normalizar_texto(texto)
    for frase, unificado in _SINONIMOS_EN.items():
        texto = texto.replace(frase, unificado)
    return texto


def _limpiar_html(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", html.unescape(texto)).strip()


def _fecha_iso(pub_date: str | None) -> str | None:
    if not pub_date:
        return None
    try:
        return parsedate_to_datetime(pub_date).isoformat()
    except (TypeError, ValueError):
        return None


class WeWorkRemotely(Scraper):
    nombre = "weworkremotely"
    pausa_segundos = 2.0

    def __init__(self, cfg: dict, creds: dict | None = None):
        super().__init__(cfg, creds)
        self._crudo: list[dict] | None = None

    def buscar(self, consulta: str) -> list[Oferta]:
        crudo = self._obtener_crudo()
        if not crudo:
            return []

        palabras = [p for p in _normalizar_para_match(consulta).split() if len(p) >= 3]
        if not palabras:
            return []
        patrones = [re.compile(r"\b" + re.escape(p) + r"\b") for p in palabras]

        ofertas = []
        for item in crudo:
            texto = _normalizar_para_match(
                " ".join([item["titulo"], item["categoria"], item["descripcion"]])
            )
            if all(patron.search(texto) for patron in patrones):
                ofertas.append(self._a_oferta(item))
        return ofertas

    def _obtener_crudo(self) -> list[dict]:
        if self._crudo is not None:
            return self._crudo

        r = self._get(BASE)
        if r is None:
            self._crudo = []
            return self._crudo

        try:
            raiz = ET.fromstring(r.content)
        except ET.ParseError:
            log.error("weworkremotely: el RSS no es XML válido")
            self._crudo = []
            return self._crudo

        items = []
        for item in raiz.findall("./channel/item"):
            titulo_crudo = (item.findtext("title") or "").strip()
            empresa, _, puesto = titulo_crudo.partition(": ")
            if not puesto:
                empresa, puesto = "", titulo_crudo

            items.append({
                "titulo": puesto or titulo_crudo,
                "empresa": empresa or "Sin especificar",
                "url": (item.findtext("link") or "").strip(),
                "region": (item.findtext("region") or "").strip(),
                "categoria": (item.findtext("category") or "").strip(),
                "descripcion": _limpiar_html(item.findtext("description") or ""),
                "publicada": _fecha_iso(item.findtext("pubDate")),
            })
        self._crudo = items
        return self._crudo

    def _a_oferta(self, item: dict) -> Oferta:
        return Oferta(
            titulo=item["titulo"],
            empresa=item["empresa"],
            url=item["url"],
            fuente=self.nombre,
            ubicacion=item["region"],
            modalidad="remoto",
            descripcion=item["descripcion"][:4000],
            publicada=item["publicada"],
            tags=[item["categoria"]] if item["categoria"] else [],
        )
