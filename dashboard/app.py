"""Dashboard multiusuario para triaje de ofertas.

Cada quien entra con su propio usuario/clave (ver `dashboard/auth.py`) y solo
ve sus propias ofertas — el aislamiento entre usuarios lo garantiza el
esquema de la base (PK compuesta `(usuario_id, huella)` en `ofertas`, ver
`src/db.py`), no lógica extra acá.

Está pensado para una sesión corta al día: llegas, ves lo nuevo ordenado por
puntaje o fecha, y marcas cada oferta con el teclado sin tocar el mouse.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.auth import requiere_login, usuario_actual_id  # noqa: E402
from dashboard.constantes import CATEGORIAS_OCULTAS, ETIQUETAS, NOMBRES_PORTAL, PESOS_SCORING  # noqa: E402
from src.db import ESTADOS_VALIDOS, Repo  # noqa: E402
from src.scrapers import PORTALES  # noqa: E402

CONFIG_EJEMPLO = Path(__file__).parent.parent / "config.example.yaml"


def _hace(publicada: str | None) -> str | None:
    """Convierte el ISO 8601 de `publicada` en un texto relativo tipo "hace
    3 días". Puede volver None: hay ofertas guardadas antes de que
    Computrabajo y Laborum rellenaran `publicada`."""
    if not publicada:
        return None
    try:
        dt = datetime.fromisoformat(publicada)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    segundos = (datetime.now(timezone.utc) - dt).total_seconds()
    if segundos < 0:
        return "recién"
    if segundos < 3600:
        return "hace poco"
    if segundos < 86400:
        return f"hace {int(segundos // 3600)} h"
    dias = int(segundos // 86400)
    if dias == 1:
        return "ayer"
    if dias < 30:
        return f"hace {dias} días"
    meses = dias // 30
    return f"hace {meses} mes{'es' if meses > 1 else ''}"


def _lista_desde_textarea(texto: str) -> list[str]:
    return [linea.strip() for linea in (texto or "").splitlines() if linea.strip()]


def _cfg_desde_formulario(form, cfg_actual: dict) -> dict:
    """Arma un dict con el mismo shape que config.yaml a partir del POST del
    formulario. Cada número que no parsea bien conserva su valor anterior en
    vez de tumbar el guardado por un typo."""
    def _entero(campo: str, valor_actual):
        try:
            return int(form.get(campo, valor_actual))
        except (TypeError, ValueError):
            return valor_actual

    scoring_actual = cfg_actual.get("scoring", {})
    return {
        "portales_activos": [p for p in form.getlist("portales_activos") if p in PORTALES],
        "consultas": _lista_desde_textarea(form.get("consultas", "")),
        "filtros": {
            "excluir": _lista_desde_textarea(form.get("filtros_excluir", "")),
            "junior": _lista_desde_textarea(form.get("filtros_junior", "")),
            "roles": _lista_desde_textarea(form.get("filtros_roles", "")),
            "stack": _lista_desde_textarea(form.get("filtros_stack", "")),
        },
        "scoring": {
            clave: _entero(f"scoring_{clave}", scoring_actual.get(clave, 0))
            for clave in PESOS_SCORING
        },
        "score_minimo_aviso": _entero(
            "score_minimo_aviso", cfg_actual.get("score_minimo_aviso", 45)
        ),
        "max_avisos_por_corrida": _entero(
            "max_avisos_por_corrida", cfg_actual.get("max_avisos_por_corrida", 10)
        ),
    }


def crear_app(ruta_db: str) -> Flask:
    app = Flask(__name__)
    app.config["RUTA_DB"] = ruta_db
    # Sin esto `flask.session` no puede firmar la cookie — falla explícito en
    # vez de generar una clave al vuelo, que invalidaría todas las sesiones
    # activas cada vez que se reinicia el proceso.
    app.secret_key = os.environ["FLASK_SECRET_KEY"]

    def _filas(
        estado: str | None, orden: str, ocultar_descartadas: bool,
        categoria: str | None = None, fuente: str | None = None,
    ):
        with Repo(app.config["RUTA_DB"], usuario_id=usuario_actual_id()) as repo:
            filas = repo.listar(
                estado=estado, limite=300, orden=orden,
                ocultar_descartadas=ocultar_descartadas,
                categoria=categoria, fuente=fuente,
            )
            conteo = repo.conteo_por_estado()
            conteo_ocultas = repo.conteo_ocultas()
            fuentes_usadas = repo.fuentes_usadas()
        ofertas = []
        for f in filas:
            d = dict(f)
            d["motivos"] = json.loads(d["motivos"] or "[]")
            d["tags"] = json.loads(d["tags"] or "[]")
            d["segmentos"] = min(5, d["score"] // 20)
            # Umbrales alineados con score_minimo_aviso (45): "alto" es lo que
            # de verdad te avisaría por Telegram; "medio" queda cerca pero no
            # llega; "bajo" incluye también las descartadas (score 0).
            d["nivel"] = "alto" if d["score"] >= 70 else "medio" if d["score"] >= 45 else "bajo"
            d["publicada_hace"] = _hace(d["publicada"])
            ofertas.append(d)
        return ofertas, conteo, conteo_ocultas, fuentes_usadas

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            with Repo(app.config["RUTA_DB"]) as repo:
                fila = repo.obtener_usuario_por_login(request.form.get("login", ""))
            clave = request.form.get("password", "")
            if fila and fila["activo"] and check_password_hash(fila["clave_hash"], clave):
                session.clear()
                session["usuario_id"] = fila["id"]
                return redirect(request.args.get("siguiente") or url_for("inicio"))
            # Mensaje genérico: no decir cuál de los dos (usuario/clave) falló.
            error = "Usuario o clave incorrectos."
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/invitar/<token>", methods=["GET", "POST"])
    def invitar(token):
        with Repo(app.config["RUTA_DB"]) as repo:
            if not repo.invitacion_valida(token):
                return render_template("invitar.html", valido=False)

            error = None
            if request.method == "POST":
                login_nuevo = request.form.get("login", "").strip()
                clave = request.form.get("password", "")
                if not login_nuevo or not clave:
                    error = "Completa usuario y contraseña."
                elif repo.obtener_usuario_por_login(login_nuevo) is not None:
                    error = "Ese usuario ya existe, elige otro."
                else:
                    cfg_inicial = yaml.safe_load(CONFIG_EJEMPLO.read_text(encoding="utf-8"))
                    uid = repo.crear_usuario(login_nuevo, generate_password_hash(clave), cfg_inicial)
                    repo.marcar_invitacion_usada(token)
                    session.clear()
                    session["usuario_id"] = uid
                    return redirect(url_for("configuracion"))

        return render_template("invitar.html", valido=True, error=error)

    @app.route("/")
    @requiere_login
    def inicio():
        estado = request.args.get("estado") or None
        if estado not in ESTADOS_VALIDOS:
            estado = None
        categoria = request.args.get("categoria") or None
        if categoria not in CATEGORIAS_OCULTAS:
            categoria = None
        if categoria:
            estado = None  # son dos formas de filtrar mutuamente excluyentes
        fuente = request.args.get("fuente") or None
        orden = request.args.get("orden") or "reciente"
        if orden not in ("reciente", "puntaje"):
            orden = "reciente"
        # Por defecto las descartadas se ocultan (info "precisa y útil", sin
        # ruido); ?desc=mostrar las vuelve a traer.
        mostrar_descartadas = request.args.get("desc") == "mostrar"

        with Repo(app.config["RUTA_DB"], usuario_id=usuario_actual_id()) as repo:
            if fuente not in repo.fuentes_usadas():
                fuente = None

        ofertas, conteo, conteo_ocultas, fuentes_usadas = _filas(
            estado, orden, not mostrar_descartadas, categoria, fuente
        )
        return render_template(
            "index.html",
            ofertas=ofertas,
            conteo=conteo,
            conteo_ocultas=conteo_ocultas,
            total=sum(conteo.values()),
            estado_activo=estado,
            categoria_activa=categoria,
            fuente_activa=fuente,
            fuentes_usadas=fuentes_usadas,
            orden_activo=orden,
            mostrar_descartadas=mostrar_descartadas,
            etiquetas=ETIQUETAS,
            categorias_ocultas=CATEGORIAS_OCULTAS,
            nombres_portal=NOMBRES_PORTAL,
        )

    @app.route("/estado/<huella>", methods=["POST"])
    @requiere_login
    def cambiar(huella: str):
        estado = (request.get_json(silent=True) or {}).get("estado")
        if estado not in ESTADOS_VALIDOS:
            return jsonify(error="Estado no válido"), 400
        # Sin chequeo extra de pertenencia: la PK es (usuario_id, huella), así
        # que si la huella no es del usuario logueado, el UPDATE simplemente
        # no afecta ninguna fila.
        with Repo(app.config["RUTA_DB"], usuario_id=usuario_actual_id()) as repo:
            repo.cambiar_estado(huella, estado)
        return jsonify(ok=True, estado=estado)

    @app.route("/configuracion", methods=["GET", "POST"])
    @requiere_login
    def configuracion():
        uid = usuario_actual_id()
        mensaje = None
        with Repo(app.config["RUTA_DB"]) as repo:
            fila = repo.obtener_usuario(uid)
            if request.method == "POST":
                cfg_actual = json.loads(fila["config_json"])
                nuevo_cfg = _cfg_desde_formulario(request.form, cfg_actual)
                repo.actualizar_config(uid, json.dumps(nuevo_cfg, ensure_ascii=False))
                # Campo secreto vacío en el POST = "no lo toques" (nunca se
                # re-muestra el valor real en el HTML, ver plantilla).
                repo.actualizar_credenciales(
                    uid,
                    telegram_token=request.form.get("telegram_token") or fila["telegram_token"],
                    telegram_chat_id=request.form.get("telegram_chat_id") or fila["telegram_chat_id"],
                    gmail_user=request.form.get("gmail_user") or fila["gmail_user"],
                    gmail_app_password=request.form.get("gmail_app_password") or fila["gmail_app_password"],
                )
                mensaje = "Guardado."
                fila = repo.obtener_usuario(uid)
            cfg = json.loads(fila["config_json"])

        return render_template(
            "configuracion.html",
            cfg=cfg,
            fila=fila,
            portales=PORTALES,
            nombres_portal=NOMBRES_PORTAL,
            pesos_scoring=PESOS_SCORING,
            mensaje=mensaje,
        )

    return app
