"""Trabajando.cl — vía su API interna, sin autenticación.

El sitio es una SPA (Nuxt/Vue): el HTML inicial solo trae un esqueleto de
carga, las ofertas llegan después por un fetch del propio frontend a
`/api/searchjob`. Se encontró el endpoint y el parámetro de búsqueda
inspeccionando los bundles de Nuxt (`_nuxt/*.js`), no por documentación
pública — si un día deja de traer resultados, hay que repetir ese proceso:
bajar la página de búsqueda, listar los `<link rel="modulepreload">`, y
grepear los chunks por "texto/cargo/palabraClave" u otro parámetro nuevo.

La API no expone la descripción completa de la oferta, solo un resumen
truncado (ver `descripcionOferta`), y no trae la URL de detalle armada: se
reconstruye a mano con `idOferta` + un slug del cargo. El slug es cosmético
—el servidor solo lee el id numérico al inicio de la ruta— así que no hace
falta que coincida byte a byte con el que genera el sitio.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

from ..models import Oferta, normalizar_texto
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://www.trabajando.cl"
RUTA_BUSQUEDA = "/api/searchjob"


def _slug(texto: str) -> str:
    return re.sub(r"\s+", "-", normalizar_texto(texto)).strip("-")


def _fecha_iso(fecha_publicacion: str | None) -> str | None:
    if not fecha_publicacion:
        return None
    try:
        return datetime.strptime(fecha_publicacion, "%Y-%m-%d %H:%M").isoformat()
    except ValueError:
        return None


class Trabajando(Scraper):
    nombre = "trabajando"
    pausa_segundos = 2.0

    def buscar(self, consulta: str) -> list[Oferta]:
        r = self._get(
            f"{BASE}{RUTA_BUSQUEDA}",
            params={"cargo": consulta, "pagina": 1},
            headers={"Accept": "application/json"},
        )
        if r is None:
            return []

        try:
            payload = r.json()
        except ValueError:
            log.error("trabajando: la respuesta no es JSON válido")
            return []

        return [self._a_oferta(item) for item in payload.get("ofertas", [])]

    def _a_oferta(self, item: dict) -> Oferta:
        # La API devuelve algunos campos de texto con entidades HTML sin
        # decodificar (p. ej. "React &#43; TypeScript").
        titulo = html.unescape(item.get("nombreCargo", ""))

        id_oferta = item.get("idOferta")
        slug = _slug(titulo)
        url = f"{BASE}/trabajo/{id_oferta}-{slug}" if id_oferta else BASE

        jornada = item.get("nombreJornada") or ""

        return Oferta(
            titulo=titulo,
            empresa=html.unescape(item.get("nombreEmpresa") or "") or "Sin especificar",
            url=url,
            fuente=self.nombre,
            ubicacion=html.unescape(item.get("ubicacion") or ""),
            modalidad=jornada,
            # `descripcionOferta` viene truncada por la propia API; no hay
            # forma de pedir el texto completo sin una petición extra por
            # oferta, y no se justifica el costo para un resumen de listado.
            descripcion=html.unescape(item.get("descripcionOferta") or ""),
            publicada=_fecha_iso(item.get("fechaPublicacion")),
            # Se guarda la jornada como tag para que `scoring.py` detecte
            # "teletrabajo"/"híbrido" en el texto buscable: el campo
            # `modalidad` no forma parte de `texto_completo`.
            tags=[jornada] if jornada else [],
        )
