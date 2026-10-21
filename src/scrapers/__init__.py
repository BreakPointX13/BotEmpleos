"""Registro de portales.

Para agregar un portal nuevo: crea el módulo con una subclase de `Scraper`,
impórtala acá y agrégala al diccionario. Nada más del sistema necesita cambiar.
"""

from .base import Scraper
from .computrabajo import Computrabajo
from .correo import Correo
from .getonbrd import GetOnBrd
from .remoteok import RemoteOK
from .terminal import Terminal
from .trabajando import Trabajando
from .weworkremotely import WeWorkRemotely

PORTALES: dict[str, type[Scraper]] = {
    "getonbrd": GetOnBrd,
    "computrabajo": Computrabajo,
    "remoteok": RemoteOK,
    "trabajando": Trabajando,
    "terminal": Terminal,
    "weworkremotely": WeWorkRemotely,
    "correo": Correo,
}

__all__ = [
    "PORTALES", "Scraper", "GetOnBrd", "Computrabajo", "RemoteOK", "Trabajando",
    "Terminal", "WeWorkRemotely", "Correo",
]
