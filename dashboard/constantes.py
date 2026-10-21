"""Constantes compartidas entre las plantillas del dashboard (listado y
formulario de configuración)."""

from __future__ import annotations

ETIQUETAS = {
    "nueva": "Sin revisar",
    "interesada": "Me interesa",
    "postulada": "Postulada",
    "descartada": "Descartada",
}

NOMBRES_PORTAL = {
    "getonbrd": "Get on Board",
    "remoteok": "Remote OK",
    "weworkremotely": "We Work Remotely",
    "trabajando": "Trabajando.cl",
    "terminal": "Terminal.io",
    "computrabajo": "Computrabajo",
    "laborum": "Laborum",
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    # "correo" no es un `fuente` real de ninguna oferta (las de correo.py
    # quedan como "laborum"/"linkedin"/"indeed"): esta entrada solo etiqueta
    # el checkbox de portales_activos en /configuracion.
    "correo": "Correo (LinkedIn/Indeed)",
}

# Las dos formas en que una oferta termina en 0 sin que el usuario la haya
# tocado: excluida por un término de `filtros.excluir` (mismo cargo, otro
# nivel) o sin ninguna coincidencia de rol/stack (otra carrera). Ver
# `Repo.listar(categoria=...)` y `Repo.conteo_ocultas`.
CATEGORIAS_OCULTAS = {
    "excluida": "Cargo relacionado, no buscado",
    "sin_relacion": "Sin relación con tu perfil",
}

# Nombres de los 7 pesos de scoring, en el orden en que se muestran en el
# formulario. Fijos porque `scoring.py` los referencia literalmente por
# nombre — no tiene sentido un editor de pesos con claves arbitrarias.
PESOS_SCORING = (
    "junior_en_titulo", "junior_en_texto", "por_tecnologia",
    "tope_stack", "rol_coincide", "remoto", "hibrido",
)
