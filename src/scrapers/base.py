"""Base común a todos los scrapers.

Cada portal se implementa como una subclase que sabe una sola cosa: cómo pedirle
ofertas a ese sitio y devolverlas como objetos `Oferta`. Todo lo demás —
reintentos, pausas entre requests, logging — vive acá.
"""

from __future__ import annotations

import abc
import logging
import time

import requests

from ..models import Oferta

log = logging.getLogger(__name__)

# Un User-Agent identificable es de buena educación y evita que te bloqueen por
# parecer tráfico anónimo. Cambia el contacto por el tuyo.
USER_AGENT = (
    "buscador-empleos-personal/1.0 "
    "(uso personal; contacto: tu-email@ejemplo.com)"
)


class Scraper(abc.ABC):
    nombre: str = "base"
    pausa_segundos: float = 2.5  # entre requests al mismo portal

    def __init__(self, cfg: dict, creds: dict | None = None):
        self.cfg = cfg
        # Credenciales propias del usuario (Telegram, Gmail) — hoy solo las
        # usa `correo.py`. Aditivo: `Scraper(cfg)` sigue funcionando igual
        # que antes de multiusuario (`creds` queda como dict vacío).
        self.creds = creds or {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "es-CL,es;q=0.9",
        })
        self._ultimo_request = 0.0

    def _get(self, url: str, **kwargs) -> requests.Response | None:
        """GET con pausa obligatoria y un reintento. Nunca lanza excepción."""
        espera = self.pausa_segundos - (time.monotonic() - self._ultimo_request)
        if espera > 0:
            time.sleep(espera)

        for intento in (1, 2):
            try:
                r = self.session.get(url, timeout=20, **kwargs)
                self._ultimo_request = time.monotonic()
                if r.status_code == 429:
                    log.warning("%s: rate limit, esperando 30s", self.nombre)
                    time.sleep(30)
                    continue
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                log.warning("%s: fallo intento %d en %s (%s)", self.nombre, intento, url, e)
                time.sleep(3)
        return None

    @abc.abstractmethod
    def buscar(self, consulta: str) -> list[Oferta]:
        """Devuelve las ofertas del portal para una consulta de texto."""

    def buscar_todo(self, consultas: list[str]) -> list[Oferta]:
        resultados: list[Oferta] = []
        for consulta in consultas:
            try:
                encontradas = self.buscar(consulta)
                log.info("%s: %d ofertas para «%s»", self.nombre, len(encontradas), consulta)
                resultados.extend(encontradas)
            except Exception:
                # Un portal caído no debe tumbar la corrida completa.
                log.exception("%s: error irrecuperable con «%s»", self.nombre, consulta)
        return resultados
