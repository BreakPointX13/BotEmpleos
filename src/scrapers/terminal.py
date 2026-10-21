"""Terminal.io — vía el JSON que trae embebido su propia página SSR.

Es una app Next.js con server-side rendering: la página
`/engineers/job-openings` ya trae el listado completo dentro de un
`<script id="__NEXT_DATA__">` (`props.pageProps.allJobsData.job`), sin
necesidad de tocar su API GraphQL (`api.terminal.io/graphql`) ni de pedir
cada oferta por separado. No soporta búsqueda por texto —siempre devuelve el
mismo listado—, así que se cachea una vez por corrida y se filtra acá, igual
que en `remoteok.py` y `weworkremotely.py`.

Terminal contrata para empresas de EE.UU./Canadá pero cada oferta declara en
qué países puede vivir el postulante (`job_acceptable_locations`). Se
descartan de entrada las que no aceptan Chile: no tiene sentido avisar de un
puesto al que no puedes postular.

`min_years_of_experience` viene como un enum limpio (ZERO_TWO, TWO_FIVE,
FIVE_TEN, TEN_PLUS) en vez de tener que adivinarlo del texto. Cuando es
ZERO_TWO se agrega el tag "entry level" —una de las señales junior que ya
reconoce `scoring.py`— porque casi ningún título acá dice literalmente
"junior".
"""

from __future__ import annotations

import html
import json
import logging
import re

from ..models import Oferta, normalizar_texto
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://www.terminal.io"
RUTA_LISTADO = f"{BASE}/engineers/job-openings"

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


def _slug(texto: str) -> str:
    return re.sub(r"\s+", "-", normalizar_texto(texto)).strip("-")


class Terminal(Scraper):
    nombre = "terminal"
    pausa_segundos = 2.5

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
            skills = [s["skill"]["name"] for s in self._skills(item)]
            texto = _normalizar_para_match(
                " ".join([
                    item.get("title", ""),
                    item.get("role_type", ""),
                    " ".join(skills),
                    item.get("about", ""),
                    item.get("what_youll_do", ""),
                    item.get("what_youll_bring", ""),
                ])
            )
            if all(patron.search(texto) for patron in patrones):
                ofertas.append(self._a_oferta(item, skills))
        return ofertas

    def _obtener_crudo(self) -> list[dict]:
        if self._crudo is not None:
            return self._crudo

        r = self._get(RUTA_LISTADO)
        if r is None:
            self._crudo = []
            return self._crudo

        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            r.text, re.S,
        )
        if not m:
            log.warning("terminal: no se encontró __NEXT_DATA__ en la página; ¿cambió el sitio?")
            self._crudo = []
            return self._crudo

        try:
            datos = json.loads(m.group(1))
            empleos = datos["props"]["pageProps"]["allJobsData"]["job"]
        except (ValueError, KeyError, TypeError):
            log.error("terminal: la forma del JSON embebido cambió, revisar el scraper")
            self._crudo = []
            return self._crudo

        self._crudo = [e for e in empleos if self._acepta_chile(e)]
        return self._crudo

    @staticmethod
    def _acepta_chile(item: dict) -> bool:
        paises = [
            (loc.get("location") or {}).get("value")
            for loc in (item.get("job_acceptable_locations") or [])
        ]
        return "Chile" in paises

    @staticmethod
    def _skills(item: dict) -> list[dict]:
        vistos, resultado = set(), []
        for campo in ("job_tech_stack", "job_required_skills"):
            for entrada in item.get(campo) or []:
                nombre = ((entrada.get("skill") or {}).get("name") or "").strip()
                if nombre and nombre.lower() not in vistos:
                    vistos.add(nombre.lower())
                    resultado.append(entrada)
        return resultado

    def _a_oferta(self, item: dict, skills: list[str]) -> Oferta:
        titulo = (item.get("title") or "").strip()
        empresa = ((item.get("organization") or {}).get("name") or "").strip() or "Sin especificar"

        id_oferta = item.get("id")
        url = (
            f"{RUTA_LISTADO}/{(item.get('role_type') or '').lower()}"
            f"/{_slug(empresa)}/{id_oferta}/{_slug(titulo)}"
            if id_oferta else RUTA_LISTADO
        )

        paises = [
            (loc.get("location") or {}).get("value")
            for loc in (item.get("job_acceptable_locations") or [])
        ]

        salario = ""
        if item.get("min_salary") and item.get("max_salary"):
            salario = f"USD {item['min_salary']}–{item['max_salary']}"

        tags = list(skills)
        if item.get("min_years_of_experience") == "ZERO_TWO":
            tags.append("entry level")

        descripcion = " ".join(filter(None, [
            item.get("about", ""), item.get("what_youll_do", ""), item.get("what_youll_bring", ""),
        ]))

        return Oferta(
            titulo=titulo,
            empresa=empresa,
            url=url,
            fuente=self.nombre,
            ubicacion=", ".join(p for p in paises if p) or "Remoto",
            modalidad="remoto",
            descripcion=_limpiar_html(descripcion)[:4000],
            salario=salario,
            publicada=item.get("created_at"),
            tags=tags,
        )
