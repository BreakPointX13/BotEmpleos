"""Puntuación de ofertas.

La lógica es deliberadamente simple y explicable: cada oferta acumula puntos por
señales positivas y se descarta de plano si aparece una señal de exclusión. Cada
punto queda registrado en `motivos`, así que siempre puedes ver por qué una
oferta llegó a 78 y otra a 31, y ajustar `config.yaml` en consecuencia.
"""

from __future__ import annotations

import re

from .models import Oferta, normalizar_texto

DESCARTADA = -1  # score sentinela para ofertas excluidas


def _aparece(termino: str, texto: str) -> bool:
    """Busca el término como palabra completa, no como subcadena.

    Importa: sin esto, "sr" hace match dentro de "desarrollo" y descartarías
    media base de datos sin darte cuenta.
    """
    patron = r"\b" + re.escape(normalizar_texto(termino)) + r"\b"
    return re.search(patron, texto) is not None


def puntuar(oferta: Oferta, cfg: dict) -> Oferta:
    texto = oferta.texto_completo
    titulo = normalizar_texto(oferta.titulo)
    reglas = cfg["scoring"]
    score = 0
    motivos: list[str] = []

    # 1. Exclusiones duras. Si aparecen, la oferta no sigue.
    for termino in cfg["filtros"]["excluir"]:
        if _aparece(termino, texto):
            oferta.score = DESCARTADA
            oferta.estado = "descartada"
            oferta.motivos = [f"excluida por «{termino}»"]
            return oferta

    # 2. Coincidencias de stack, con techo para que no gane una oferta solo por
    #    listar treinta tecnologías.
    encontradas = [t for t in cfg["filtros"]["stack"] if _aparece(t, texto)]
    if encontradas:
        puntos = min(len(encontradas) * reglas["por_tecnologia"], reglas["tope_stack"])
        score += puntos
        motivos.append("stack: " + ", ".join(encontradas[:6]))

    # 3. Roles que te interesan explícitamente, buscados en el título.
    roles = [r for r in cfg["filtros"]["roles"] if _aparece(r, titulo)]
    if roles:
        score += reglas["rol_coincide"]
        motivos.append(f"rol: {roles[0]}")

    # 4. Señal de seniority junior. Es fuerte, pero por sí sola no dice nada
    #    de la carrera: "ejecutivo comercial junior" matchea igual que
    #    "desarrollador junior". Por eso solo suma si la oferta ya mostró
    #    relación con tu perfil (rol o stack) — si no, se anota pero no puntúa.
    relevante = bool(roles) or bool(encontradas)
    junior_en_titulo = any(_aparece(t, titulo) for t in cfg["filtros"]["junior"])
    junior_en_texto = any(_aparece(t, texto) for t in cfg["filtros"]["junior"])
    if relevante and junior_en_titulo:
        score += reglas["junior_en_titulo"]
        motivos.append("junior en el título")
    elif relevante and junior_en_texto:
        score += reglas["junior_en_texto"]
        motivos.append("junior en la descripción")
    elif junior_en_titulo or junior_en_texto:
        motivos.append("junior, pero sin relación con tu rol/stack (no sumó puntos)")

    # 5. Modalidad.
    if any(_aparece(t, texto) for t in ("remoto", "remote", "teletrabajo")):
        score += reglas["remoto"]
        motivos.append("remoto")
    elif any(_aparece(t, texto) for t in ("hibrido", "hybrid")):
        score += reglas["hibrido"]
        motivos.append("híbrido")

    oferta.score = max(0, min(100, score))
    oferta.motivos = motivos
    return oferta
