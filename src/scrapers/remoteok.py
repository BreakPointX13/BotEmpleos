"""Remote OK — vía API pública, sin autenticación.

La API (https://remoteok.com/api) no acepta parámetros de búsqueda: siempre
devuelve el mismo listado con las ~100 ofertas más recientes de todas las
categorías. Por eso la respuesta se cachea una vez por corrida (una instancia
nueva del scraper vive lo que dura `buscar_todo`) y el filtrado por consulta
se hace acá, comparando palabra por palabra contra título, tags y descripción.

Ojo con las consultas en español ("desarrollador movil"): las ofertas están en
inglés, así que solo matchean los términos que son iguales o similares en
ambos idiomas ("junior", "full stack", "react native", "flutter"). Es
esperado, no un bug: esas consultas en español simplemente no aportan acá.

La API devuelve el texto con doble codificación UTF-8 ("5 aÃ±os" en vez de
"5 años"): no es un problema de `requests` —`r.encoding` ya viene en utf-8 y
`r.json()` da lo mismo que `r.content.decode("utf-8")`—, los bytes mal
codificados vienen así desde el origen. Verificado el 2026-09-27, es el único
portal del proyecto al que le pasa. Importa más de lo que parece: `scoring.py`
busca palabra completa sobre texto normalizado, y "5 aÃ±os" normaliza a
"5 aa os", así que el término de exclusión "5 años" NO matchea y una oferta
que pide 5+ años de experiencia pasa el filtro de seniority como si fuera
junior. Por eso `_reparar_mojibake` corre sobre el crudo, antes de puntuar.

Términos de la API: piden mencionar "Remote OK" como fuente si se republican
resultados. No aplica a este uso (aviso personal por Telegram), pero queda
anotado por si el dashboard alguna vez se hace público.
"""

from __future__ import annotations

import logging
import re

from ..models import Oferta, normalizar_texto
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://remoteok.com/api"

# Los tags/descripciones en inglés suelen escribir estos términos pegados
# ("frontend", "fullstack") en vez de separados como en config.yaml ("front
# end", "full stack"). Sin esto, buscar palabra por palabra nunca matchea.
_SINONIMOS_EN = {
    "full stack": "fullstack",
    "front end": "frontend",
    "back end": "backend",
    "movil": "mobile",
}


# Campos de texto libre del JSON; los demás son ids, fechas y URLs ASCII.
_CAMPOS_TEXTO = ("position", "company", "description", "location")


def _reparar_mojibake(texto: str) -> str:
    """Deshace el doble encoding UTF-8 de la API (ver docstring del módulo).

    Un texto ya correcto no se toca: o su acento no sobrevive a latin-1, o los
    bytes resultantes no son UTF-8 válido. En ambos casos salta la excepción y
    se devuelve tal cual, así que es seguro aplicarlo a todo sin distinguir.
    """
    try:
        return texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto


def _normalizar_para_match(texto: str) -> str:
    texto = normalizar_texto(texto)
    for frase, unificado in _SINONIMOS_EN.items():
        texto = texto.replace(frase, unificado)
    return texto


class RemoteOK(Scraper):
    nombre = "remoteok"
    pausa_segundos = 2.0

    def __init__(self, cfg: dict, creds: dict | None = None):
        super().__init__(cfg, creds)
        self._crudo: list[dict] | None = None

    def buscar(self, consulta: str) -> list[Oferta]:
        crudo = self._obtener_crudo()
        if not crudo:
            return []

        # Palabras de 3+ letras: las más cortas ("de", "en") generan ruido y
        # de todos modos no distinguen nada en inglés. Se exige que TODAS
        # aparezcan (no cualquiera) para no matchear, por ejemplo, "full
        # stack junior" contra una oferta que solo menciona "junior" al pasar.
        palabras = [p for p in _normalizar_para_match(consulta).split() if len(p) >= 3]
        if not palabras:
            return []
        patrones = [re.compile(r"\b" + re.escape(p) + r"\b") for p in palabras]

        ofertas = []
        for item in crudo:
            # Ojo: no se busca en `tags`. Algunas ofertas traen ahí una lista
            # de categorías genéricas del sitio ("flutter", "sales", "hr", …)
            # sin relación real con el puesto, probablemente para aparecer en
            # más páginas de categoría. El título y la descripción sí son
            # específicos del puesto.
            texto = _normalizar_para_match(
                " ".join([item.get("position", ""), item.get("description", "")])
            )
            if all(patron.search(texto) for patron in patrones):
                ofertas.append(self._a_oferta(item))
        return ofertas

    def _obtener_crudo(self) -> list[dict]:
        if self._crudo is not None:
            return self._crudo

        r = self._get(BASE)
        if r is None:
            # Distinto de "la API respondió pero nada matcheó": eso es normal
            # acá (ofertas en inglés, consultas en español) y el aviso de "0
            # ofertas" de `pipeline._alertas_portales` no los diferencia. Un
            # ERROR en el log es lo que separa la falla real del cero legítimo.
            log.error("remoteok: la API no respondió tras los reintentos")
            self._crudo = []
            return self._crudo

        try:
            payload = r.json()
        except ValueError:
            log.error("remoteok: la respuesta no es JSON válido")
            self._crudo = []
            return self._crudo

        # El primer elemento del array es un aviso legal de la API, no una
        # oferta: se descarta filtrando por la presencia de "id".
        self._crudo = [item for item in payload if isinstance(item, dict) and item.get("id")]
        for item in self._crudo:
            for campo in _CAMPOS_TEXTO:
                if isinstance(item.get(campo), str):
                    item[campo] = _reparar_mojibake(item[campo])
            if isinstance(item.get("tags"), list):
                item["tags"] = [
                    _reparar_mojibake(t) for t in item["tags"] if isinstance(t, str)
                ]
        return self._crudo

    def _a_oferta(self, item: dict) -> Oferta:
        salario = ""
        if item.get("salary_min") and item.get("salary_max"):
            salario = f"USD {item['salary_min']}–{item['salary_max']}"

        return Oferta(
            titulo=item.get("position", ""),
            empresa=item.get("company") or "Sin especificar",
            url=item.get("url") or item.get("apply_url") or "",
            fuente=self.nombre,
            ubicacion=item.get("location") or "Remoto",
            modalidad="remoto",
            descripcion=re.sub(r"<[^>]+>", " ", item.get("description") or "")[:4000],
            salario=salario,
            publicada=item.get("date"),
            tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
        )
