"""Envío de avisos por Telegram.

Setup (5 minutos), uno por usuario — cada quien tiene su propio bot:
  1. Habla con @BotFather en Telegram, manda /newbot y guarda el token.
  2. Escríbele cualquier cosa a tu bot recién creado.
  3. Abre https://api.telegram.org/bot<TU_TOKEN>/getUpdates y copia el
     "chat":{"id": ...} que aparece. Ese es tu chat_id.
  4. Carga ambos valores en tu perfil desde `/configuracion` en el dashboard.

Multiusuario: `token`/`chat_id` llegan como parámetros explícitos en vez de
leerse de una variable de entorno global, porque cada usuario tiene los
suyos propios (guardados en su fila de `usuarios`, ver `src/db.py`).
"""

from __future__ import annotations

import html
import logging
import time

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_CARACTERES = 4096


def _escapar(texto: str) -> str:
    return html.escape(texto or "", quote=False)


def _escapar_atributo(texto: str) -> str:
    """Para texto que va dentro de un atributo HTML (ej. href="...").

    A diferencia de `_escapar`, esta sí debe escapar comillas: una comilla
    sin escapar en la URL corta el atributo antes de tiempo y Telegram
    rechaza el mensaje completo con 400 Bad Request, no solo esa oferta.
    """
    return html.escape(texto or "", quote=True)


def formatear(fila) -> str:
    """Arma el mensaje de una oferta. Usa HTML, que es más tolerante que Markdown."""
    barra = "▰" * (fila["score"] // 20) + "▱" * (5 - fila["score"] // 20)
    partes = [
        f"<b>{_escapar(fila['titulo'])}</b>",
        f"{_escapar(fila['empresa'])}",
        f"{barra}  {fila['score']}/100 · {fila['fuente']}",
    ]
    detalle = " · ".join(filter(None, [fila["ubicacion"], fila["modalidad"], fila["salario"]]))
    if detalle:
        partes.append(_escapar(detalle))
    partes.append(f'<a href="{_escapar_atributo(fila["url"])}">Ver oferta</a>')
    return "\n".join(partes)


def enviar(mensaje: str, token: str | None, chat_id: str | None) -> bool:
    if not token or not chat_id:
        log.warning("Telegram sin configurar (falta token o chat_id)")
        return False

    try:
        r = requests.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": mensaje[:MAX_CARACTERES],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code == 429:
            espera = r.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(espera + 1)
            return enviar(mensaje, token, chat_id)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Telegram: no se pudo enviar (%s)", e)
        return False


def notificar_ofertas(filas, token: str | None, chat_id: str | None, tope: int = 10) -> list[str]:
    """Manda las mejores ofertas. Devuelve las huellas efectivamente enviadas."""
    if not filas:
        return []

    enviadas: list[str] = []
    seleccion = filas[:tope]

    encabezado = f"<b>{len(filas)} ofertas nuevas</b>"
    if len(filas) > tope:
        encabezado += f" · mostrando las {tope} mejores"
    enviar(encabezado, token, chat_id)

    for fila in seleccion:
        if enviar(formatear(fila), token, chat_id):
            enviadas.append(fila["huella"])
        time.sleep(0.5)  # el límite de Telegram es ~30 mensajes/segundo, vamos sobrados

    # Las que no se mostraron igual quedan marcadas: están en el dashboard.
    enviadas.extend(f["huella"] for f in filas[tope:])
    return enviadas
