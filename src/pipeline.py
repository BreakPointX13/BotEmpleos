"""Orquestador: recolectar → puntuar → guardar → notificar.

Multiusuario: `ejecutar_todos` corre esta cadena una vez por cada usuario
registrado, con su propio `cfg` (portales/consultas/filtros/scoring) y sus
propias credenciales (Telegram, Gmail). Un usuario mal configurado (Telegram
roto, config corrupta) no debe tumbar la corrida de los demás — mismo
principio que ya existe acá abajo por portal en `recolectar`.
"""

from __future__ import annotations

import json
import logging

from .db import Repo
from .models import Oferta
from .notify import telegram
from .scoring import DESCARTADA, puntuar
from .scrapers import PORTALES

log = logging.getLogger(__name__)

# "correo" no es un portal que se consulte activamente: revisa si llegó una
# alerta nueva desde la última corrida, y la mayoría de los días eso da 0 de
# forma legítima (nadie publicó nada que calce). Alertar por eso sería puro
# ruido, así que se excluye del chequeo de "0 ofertas = posible falla".
_PORTALES_SIN_CHEQUEO_DE_VACIO = {"correo"}

_CLAVES_CREDS = ("telegram_token", "telegram_chat_id", "gmail_user", "gmail_app_password")


def recolectar(
    cfg: dict, creds: dict, solo_portales: set[str] | None = None,
) -> tuple[list[Oferta], dict[str, dict]]:
    """Devuelve las ofertas junto con el estado de cada portal (cuántas trajo,
    o el error si se cayó), para poder avisar de fallas en `ejecutar`.

    `solo_portales` restringe la corrida a un subconjunto (ej. {"correo"},
    para el cron aparte de cada 5 minutos que solo revisa la bandeja sin
    tocar el resto de los portales — ver `main.py --solo-portal`).
    """
    ofertas: list[Oferta] = []
    estado_portales: dict[str, dict] = {}

    activos = cfg["portales_activos"]
    if solo_portales is not None:
        activos = [p for p in activos if p in solo_portales]

    for nombre in activos:
        clase = PORTALES.get(nombre)
        if clase is None:
            log.warning("Portal desconocido en la config: %s", nombre)
            continue
        log.info("Consultando %s…", nombre)
        try:
            encontradas = clase(cfg, creds).buscar_todo(cfg["consultas"])
            ofertas.extend(encontradas)
            estado_portales[nombre] = {"cantidad": len(encontradas), "error": None}
        except Exception as e:
            # Defensa extra: cada scraper ya protege sus consultas una por
            # una (ver `Scraper.buscar_todo`), pero esto cubre lo que se
            # escape de ahí (p. ej. un error en __init__) para que un portal
            # roto no tumbe la recolección de los demás.
            log.exception("%s: error irrecuperable, se salta este portal", nombre)
            estado_portales[nombre] = {"cantidad": 0, "error": str(e) or type(e).__name__}

    return ofertas, estado_portales


def _alertas_portales(estado_portales: dict[str, dict]) -> list[str]:
    alertas = []
    for nombre, info in estado_portales.items():
        if info["error"]:
            alertas.append(f"❌ <b>{nombre}</b>: error — {info['error']}")
        elif info["cantidad"] == 0 and nombre not in _PORTALES_SIN_CHEQUEO_DE_VACIO:
            alertas.append(f"⚠️ <b>{nombre}</b>: 0 ofertas en toda la corrida")
    return alertas


def ejecutar(
    cfg: dict, ruta_db: str, usuario_id: int, creds: dict,
    notificar: bool = True, solo_portales: set[str] | None = None,
) -> dict:
    ofertas, estado_portales = recolectar(cfg, creds, solo_portales)
    resumen = {"recolectadas": len(ofertas), "nuevas": 0, "descartadas": 0, "notificadas": 0}

    with Repo(ruta_db, usuario_id=usuario_id) as repo:
        for oferta in ofertas:
            puntuar(oferta, cfg)
            if oferta.score == DESCARTADA:
                resumen["descartadas"] += 1
                oferta.score = 0  # se guarda igual, para poder auditar el filtro
            if repo.guardar(oferta):
                resumen["nuevas"] += 1

        if notificar:
            token, chat_id = creds.get("telegram_token"), creds.get("telegram_chat_id")

            alertas = _alertas_portales(estado_portales)
            if alertas:
                telegram.enviar("🔧 <b>Aviso del bot</b>\n" + "\n".join(alertas), token, chat_id)

            pendientes = repo.pendientes_de_notificar(cfg["score_minimo_aviso"])
            huellas = telegram.notificar_ofertas(
                pendientes, token, chat_id, cfg["max_avisos_por_corrida"]
            )
            repo.marcar_notificadas(huellas)
            resumen["notificadas"] = len(huellas)

    return resumen


def recalcular_scores(ruta_db: str, login: str | None = None) -> dict[str, dict]:
    """Vuelve a puntuar todo lo ya guardado con el `cfg` actual de cada
    usuario. Hace falta porque `Repo.guardar` no reprocesa nada si la huella
    ya existe: si cambias tus filtros/pesos en `/configuracion`, las ofertas
    de antes se quedan con el score viejo hasta correr esto. No toca
    `estado` — el triaje manual del usuario manda.
    """
    with Repo(ruta_db) as repo:
        usuarios = repo.listar_usuarios(solo_activos=False)
    if login is not None:
        usuarios = [u for u in usuarios if u["login"] == login]

    resultados: dict[str, dict] = {}
    for fila in usuarios:
        cfg = json.loads(fila["config_json"])
        revisadas = cambiadas = 0
        with Repo(ruta_db, usuario_id=fila["id"]) as repo:
            for f in repo.todas_para_recalculo():
                revisadas += 1
                oferta = Oferta(
                    titulo=f["titulo"], empresa=f["empresa"], url=f["url"],
                    fuente=f["fuente"], descripcion=f["descripcion"] or "",
                )
                puntuar(oferta, cfg)
                nuevo_score = 0 if oferta.score == DESCARTADA else oferta.score
                motivos_json = json.dumps(oferta.motivos, ensure_ascii=False)
                if nuevo_score != f["score"] or motivos_json != (f["motivos"] or "[]"):
                    repo.actualizar_score(f["huella"], nuevo_score, oferta.motivos)
                    cambiadas += 1
        resultados[fila["login"]] = {"revisadas": revisadas, "cambiadas": cambiadas}

    return resultados


def ejecutar_todos(
    ruta_db: str, notificar: bool = True, solo_portales: set[str] | None = None,
) -> dict[str, dict]:
    """Corre `ejecutar` para cada usuario activo. Un usuario que falle queda
    registrado en su propio resultado, sin afectar a los demás."""
    with Repo(ruta_db) as repo:
        usuarios = repo.listar_usuarios(solo_activos=True)

    resultados: dict[str, dict] = {}
    for fila in usuarios:
        try:
            cfg = json.loads(fila["config_json"])
            creds = {clave: fila[clave] for clave in _CLAVES_CREDS}
            resultados[fila["login"]] = ejecutar(
                cfg, ruta_db, fila["id"], creds, notificar, solo_portales
            )
        except Exception:
            log.exception("usuario %s: la corrida completa falló, se salta", fila["login"])
            resultados[fila["login"]] = {"error": "ver logs del servidor"}

    return resultados
