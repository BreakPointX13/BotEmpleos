"""Modelo común al que todos los scrapers normalizan sus resultados."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

# Frases que se eliminan enteras. Van primero porque contienen palabras que sí
# importan por separado: "full time" es ruido, pero "full stack" no.
_FRASES_RUIDO = (
    "full time", "part time", "tiempo completo", "media jornada",
    "jornada completa", "trabajo remoto", "100 remoto",
)

# Variantes del mismo concepto que cada portal escribe distinto.
_SINONIMOS = {
    "fullstack": "full stack",
    "full-stack": "full stack",
    "frontend": "front end",
    "backend": "back end",
    "moviles": "movil",
    "mobile": "movil",
    "android": "movil android",
    "web": "web",
}

# Palabras genéricas que no distinguen un puesto de otro. Casi toda oferta de
# desarrollo dice "desarrollador" o "developer", así que no aportan identidad.
_RUIDO = {
    "junior", "jr", "senior", "sr", "semi", "ssr", "trainee", "practicante",
    "desarrollador", "desarrolladora", "developer", "dev", "programador",
    "programadora", "ingeniero", "ingeniera", "engineer", "analista",
    "de", "del", "la", "el", "en", "para", "y", "o", "con",
    "remoto", "remota", "remote", "hibrido", "presencial", "teletrabajo",
    "santiago", "chile", "urgente", "nuevo", "buscamos",
}


def normalizar_texto(valor: str) -> str:
    """Minúsculas, sin tildes, sin puntuación, espacios colapsados."""
    if not valor:
        return ""
    valor = unicodedata.normalize("NFKD", valor)
    valor = "".join(c for c in valor if not unicodedata.combining(c))
    valor = valor.lower()
    valor = re.sub(r"[^a-z0-9áéíóúñ\s]", " ", valor)
    return re.sub(r"\s+", " ", valor).strip()


@dataclass
class Oferta:
    """Una oferta de trabajo, ya normalizada e independiente del portal."""

    titulo: str
    empresa: str
    url: str
    fuente: str                      # "getonbrd", "computrabajo", ...
    ubicacion: str = ""
    modalidad: str = ""              # remoto / híbrido / presencial
    descripcion: str = ""
    salario: str = ""
    publicada: str | None = None     # ISO 8601 si el portal la entrega
    tags: list[str] = field(default_factory=list)

    # Se llenan más adelante en el pipeline
    score: int = 0
    motivos: list[str] = field(default_factory=list)
    estado: str = "nueva"            # nueva / interesada / postulada / descartada
    vista_primera_vez: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def huella(self) -> str:
        """Identidad estable de la oferta, para detectar duplicados entre portales.

        Dos avisos del mismo puesto en Computrabajo y Get on Board suelen tener
        títulos distintos ("Dev Full Stack Jr" vs "Junior Full-Stack Developer"),
        así que la huella se calcula sobre el título despojado de ruido.
        """
        titulo = normalizar_texto(self.titulo)

        for frase in _FRASES_RUIDO:
            titulo = titulo.replace(frase, " ")

        palabras = [_SINONIMOS.get(p, p) for p in titulo.split()]
        nucleo = sorted({p for p in " ".join(palabras).split() if p not in _RUIDO})

        # Si al depurar no queda nada distintivo ("Desarrollador Junior" a secas),
        # es más seguro volver al título completo que agrupar ofertas ajenas.
        titulo_nucleo = " ".join(nucleo) if nucleo else titulo.strip()

        base = f"{normalizar_texto(self.empresa)}|{titulo_nucleo}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    @property
    def texto_completo(self) -> str:
        """Todo el texto buscable de la oferta, para aplicarle los filtros."""
        return normalizar_texto(
            " ".join([self.titulo, self.descripcion, " ".join(self.tags)])
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["huella"] = self.huella
        return d
