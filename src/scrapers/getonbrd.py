"""Get on Board — vía API pública (sin autenticación).

Es la fuente más limpia que vas a tener: JSON estructurado, sin HTML frágil y
sin riesgo de que te bloqueen. Debería ser tu fuente principal.

Ojo: la API está en beta y cambia. Si un día deja de devolver resultados,
revisa https://api-doc.getonbrd.com y ajusta RUTA_BUSQUEDA o el mapeo de
`_a_oferta`. La estructura es JSON:API, o sea data[].attributes.

El mapeo de campos se escribió una vez desde la documentación y estaba mal en
cuatro de cinco campos no triviales al compararlo contra una respuesta real
(quedó verificado y corregido acá):

  - `empresa` viene en `attributes.company.data.attributes.name`, NO en un
    `relationships.company` a nivel raíz (eso es JSON:API "de libro", pero
    no es lo que devuelve esta API). El código viejo siempre caía al
    fallback "Sin especificar".
  - `ubicacion` viene en `attributes.countries` (lista, plural), no en
    `country`/`city` (esas claves no existen). Antes quedaba siempre vacía.
  - La modalidad remota real está en `attributes.remote_modality`
    ("fully_remote" / "remote_local" / "hybrid" / "no_remote"), no en
    `attributes.modality` —ese campo es una referencia `{data: {id, type}}`
    sin resolver, así que terminaba guardándose el `repr` del dict.
  - Los tags solo traen nombre si se pide `expand[]=tags` además de
    `expand[]=company`; sin eso, `attributes.tags` es `{data: [{id, type}]}`
    sin nombres, y como es un dict, iterarlo de largo daba `["data"]`.
  - `published_at` es un timestamp Unix (int), no ISO 8601 como espera
    `Oferta.publicada` — se convierte acá.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

from ..models import Oferta
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://www.getonbrd.com/api/v0"
RUTA_BUSQUEDA = "/search/jobs"

_MODALIDAD = {
    "fully_remote": "remoto",
    "remote_local": "remoto",
    "hybrid": "híbrido",
    "no_remote": "presencial",
}


def _limpiar_html(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", html.unescape(texto)).strip()


def _fecha_iso(timestamp_unix) -> str | None:
    if not timestamp_unix:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp_unix), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


class GetOnBrd(Scraper):
    nombre = "getonbrd"
    pausa_segundos = 1.0  # es una API, se puede ser un poco menos tímido

    def buscar(self, consulta: str) -> list[Oferta]:
        r = self._get(
            f"{BASE}{RUTA_BUSQUEDA}",
            params={
                "query": consulta,
                "per_page": 50,
                "page": 1,
                "expand[]": ["company", "tags"],
            },
        )
        if r is None:
            return []

        try:
            payload = r.json()
        except ValueError:
            log.error("getonbrd: la respuesta no es JSON válido")
            return []

        return [
            oferta
            for item in payload.get("data", [])
            if (oferta := self._a_oferta(item)) is not None
        ]

    def _a_oferta(self, item: dict) -> Oferta | None:
        attrs = item.get("attributes") or {}
        titulo = attrs.get("title")
        if not titulo:
            return None

        empresa_data = ((attrs.get("company") or {}).get("data") or {})
        empresa = (empresa_data.get("attributes") or {}).get("name") or "Sin especificar"

        modalidad = _MODALIDAD.get(attrs.get("remote_modality"), "")

        tags_data = ((attrs.get("tags") or {}).get("data") or [])
        tags = [
            nombre
            for t in tags_data
            if (nombre := (t.get("attributes") or {}).get("name"))
        ]

        salario = ""
        if attrs.get("min_salary") and attrs.get("max_salary"):
            salario = f"USD {attrs['min_salary']}–{attrs['max_salary']}"

        url = (item.get("links") or {}).get("public_url") or (
            f"https://www.getonbrd.com/jobs/{item.get('id', '')}"
        )

        return Oferta(
            titulo=titulo,
            empresa=empresa,
            url=url,
            fuente=self.nombre,
            ubicacion=", ".join(attrs.get("countries") or []),
            modalidad=modalidad,
            descripcion=_limpiar_html(
                (attrs.get("description") or "") + " " + (attrs.get("desirable") or "")
            )[:4000],
            salario=salario,
            publicada=_fecha_iso(attrs.get("published_at")),
            # `modalidad` no entra en `texto_completo`: se repite acá como
            # tag para que `scoring.py` detecte "remoto"/"híbrido".
            tags=tags + ([modalidad] if modalidad else []),
        )
