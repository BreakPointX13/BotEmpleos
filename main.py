#!/usr/bin/env python3
"""Buscador de empleos — punto de entrada.

    python main.py buscar                 # recolecta, puntúa y avisa por Telegram (todos los usuarios)
    python main.py buscar --sin-avisos    # igual pero sin notificar (útil al probar)
    python main.py servir                 # abre el dashboard en localhost:5000
    python main.py inspeccionar --portal computrabajo   # guarda HTML de muestra
    python main.py resumen [--usuario LOGIN]            # cuántas ofertas hay en cada estado
    python main.py recalcular-scores [--usuario LOGIN]  # repuntúa lo guardado tras editar el perfil
    python main.py crear-usuario --login LOGIN --password CLAVE
    python main.py migrar-a-multiusuario --login LOGIN --password CLAVE
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).parent
RUTA_DB = str(RAIZ / "data" / "empleos.db")


def cargar_env() -> None:
    """Lee .env sin depender de python-dotenv."""
    archivo = RAIZ / ".env"
    if not archivo.exists():
        return
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip().strip("\"'"))


def _cargar_yaml(nombre: str) -> dict:
    archivo = RAIZ / nombre
    if not archivo.exists():
        sys.exit(f"No existe {nombre}.")
    return yaml.safe_load(archivo.read_text(encoding="utf-8"))


def cmd_buscar(args) -> None:
    from src.pipeline import ejecutar_todos

    solo_portales = set(args.solo_portal) if args.solo_portal else None
    resultados = ejecutar_todos(RUTA_DB, notificar=not args.sin_avisos, solo_portales=solo_portales)
    for login, resumen in resultados.items():
        print(f"\n  {login}")
        if "error" in resumen:
            print(f"    Error: {resumen['error']}")
            continue
        print(
            f"    Recolectadas: {resumen['recolectadas']}\n"
            f"    Nuevas:       {resumen['nuevas']}\n"
            f"    Descartadas:  {resumen['descartadas']}\n"
            f"    Notificadas:  {resumen['notificadas']}"
        )
    print()


def cmd_servir(args) -> None:
    from dashboard.app import crear_app

    app = crear_app(RUTA_DB)
    print(f"\n  Dashboard en http://{args.host}:{args.puerto}\n")
    app.run(host=args.host, port=args.puerto, debug=args.debug)


def cmd_inspeccionar(args) -> None:
    """Baja una página de un portal HTML para poder ajustar sus selectores.

    Usa config.example.yaml como config genérica de desarrollo: en
    multiusuario ya no existe "el" config.yaml, cada usuario tiene el suyo
    guardado en la base, pero esta herramienta es para el desarrollador del
    scraper, no para un usuario final.
    """
    from src.scrapers import PORTALES

    cfg = _cargar_yaml("config.example.yaml")
    clase = PORTALES.get(args.portal)
    if clase is None or not hasattr(clase, "guardar_muestra"):
        sys.exit(f"«{args.portal}» no soporta inspección (¿es un portal con API?)")

    destino = RAIZ / "data" / f"muestra_{args.portal}.html"
    destino.parent.mkdir(exist_ok=True)
    consulta = args.consulta or cfg["consultas"][0]
    encontradas = clase(cfg).guardar_muestra(consulta, str(destino))

    print(f"\n  HTML guardado en: {destino}")
    print(f"  Tarjetas detectadas con los selectores actuales: {encontradas}")
    if encontradas == 0:
        print(
            "\n  Cero tarjetas significa que los selectores están desactualizados.\n"
            f"  Abre {destino} y corrige SELECTORES en src/scrapers/{args.portal}.py\n"
        )


def cmd_resumen(args) -> None:
    from src.db import Repo

    with Repo(RUTA_DB) as repo:
        usuarios = [repo.obtener_usuario_por_login(args.usuario)] if args.usuario else repo.listar_usuarios(solo_activos=False)
        usuarios = [u for u in usuarios if u is not None]

    if not usuarios:
        print("\n  No hay usuarios registrados. Corre: python main.py crear-usuario --login ... --password ...\n")
        return

    for fila in usuarios:
        with Repo(RUTA_DB, usuario_id=fila["id"]) as repo:
            conteo = repo.conteo_por_estado()
        print(f"\n  {fila['login']}")
        if not conteo:
            print("    Todavía no hay ofertas.")
            continue
        for estado, n in sorted(conteo.items(), key=lambda x: -x[1]):
            print(f"    {estado:<12} {n:>4}")
    print()


def cmd_recalcular_scores(args) -> None:
    from src.pipeline import recalcular_scores

    resultados = recalcular_scores(RUTA_DB, login=args.usuario)
    if not resultados:
        print("\n  No hay usuarios registrados (o ninguno con ese login).\n")
        return
    for login, r in resultados.items():
        print(f"\n  {login}\n    Revisadas: {r['revisadas']}\n    Con cambio de score: {r['cambiadas']}")
    print()


def cmd_crear_usuario(args) -> None:
    from werkzeug.security import generate_password_hash

    from src.db import Repo

    cfg_inicial = _cargar_yaml("config.example.yaml")

    with Repo(RUTA_DB) as repo:
        if repo.obtener_usuario_por_login(args.login) is not None:
            sys.exit(f"Ya existe un usuario con login «{args.login}».")
        repo.crear_usuario(args.login, generate_password_hash(args.password), cfg_inicial)

    print(
        f"\n  Usuario «{args.login}» creado con la config de ejemplo por defecto.\n"
        "  Que entre a /configuracion en el dashboard para ajustar sus portales,\n"
        "  palabras clave y credenciales de Telegram/Gmail.\n"
    )


def cmd_generar_invitacion(args) -> None:
    """Genera un link de un solo uso para que alguien cree su propia cuenta,
    sin pasar por `crear-usuario` (que exige acceso SSH al servidor)."""
    import secrets
    from datetime import datetime, timedelta, timezone

    from src.db import Repo

    token = secrets.token_urlsafe(24)
    expira = datetime.now(timezone.utc) + timedelta(hours=args.horas)

    with Repo(RUTA_DB) as repo:
        repo.crear_invitacion(token, expira.isoformat())

    print(
        f"\n  Link de invitación (válido {args.horas} h, un solo uso):\n\n"
        f"  {args.url_base.rstrip('/')}/invitar/{token}\n"
    )


def cmd_migrar_a_multiusuario(args) -> None:
    """Migra la base de una sola persona (esquema viejo: `ofertas` con
    `huella` como PK) al esquema multiusuario, preservando todas las ofertas
    y estados existentes bajo un usuario #1. Ver CLAUDE.md / el plan de
    migración para el detalle de por qué esto no puede ser un simple
    `ALTER TABLE` (SQLite no permite cambiar la PK de una tabla existente).
    """
    from werkzeug.security import generate_password_hash

    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    try:
        ya_existe_usuarios = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios'"
        ).fetchone()
        if ya_existe_usuarios:
            sys.exit("Esta base ya tiene tabla `usuarios` — parece que ya se migró antes.")

        columnas_ofertas = {
            fila["name"] for fila in con.execute("PRAGMA table_info(ofertas)").fetchall()
        }
        if not columnas_ofertas:
            sys.exit("No hay tabla `ofertas` todavía en esta base — no hay nada que migrar.")
        if "usuario_id" in columnas_ofertas:
            sys.exit("La tabla `ofertas` ya tiene `usuario_id` — parece que ya se migró antes.")

        cfg = _cargar_yaml("config.yaml")
        conteo_antes = con.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0]

        con.execute("BEGIN")

        con.execute("""
            CREATE TABLE usuarios (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                login               TEXT NOT NULL UNIQUE,
                clave_hash          TEXT NOT NULL,
                activo              INTEGER DEFAULT 1,
                creado              TEXT NOT NULL,
                config_json         TEXT NOT NULL,
                telegram_token      TEXT,
                telegram_chat_id    TEXT,
                gmail_user          TEXT,
                gmail_app_password  TEXT
            )
        """)

        import json
        from datetime import datetime, timezone

        cur = con.execute(
            """INSERT INTO usuarios (
                   login, clave_hash, creado, config_json,
                   telegram_token, telegram_chat_id, gmail_user, gmail_app_password
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                args.login, generate_password_hash(args.password),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                json.dumps(cfg, ensure_ascii=False),
                os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"),
                os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD"),
            ),
        )
        usuario_id = cur.lastrowid

        con.execute("ALTER TABLE ofertas RENAME TO ofertas_viejas")
        con.execute("""
            CREATE TABLE ofertas (
                usuario_id          INTEGER NOT NULL REFERENCES usuarios(id),
                huella              TEXT NOT NULL,
                titulo              TEXT NOT NULL,
                empresa             TEXT NOT NULL,
                url                 TEXT NOT NULL,
                fuente              TEXT NOT NULL,
                ubicacion           TEXT,
                modalidad           TEXT,
                descripcion         TEXT,
                salario             TEXT,
                publicada           TEXT,
                tags                TEXT,
                score               INTEGER DEFAULT 0,
                motivos             TEXT,
                estado              TEXT DEFAULT 'nueva',
                vista_primera_vez   TEXT,
                notificada          INTEGER DEFAULT 0,
                fuentes_extra       TEXT DEFAULT '',
                PRIMARY KEY (usuario_id, huella)
            )
        """)
        con.execute("CREATE INDEX idx_usuario_estado ON ofertas(usuario_id, estado)")
        con.execute("CREATE INDEX idx_usuario_score ON ofertas(usuario_id, score DESC)")

        con.execute(
            """INSERT INTO ofertas (
                   usuario_id, huella, titulo, empresa, url, fuente, ubicacion,
                   modalidad, descripcion, salario, publicada, tags, score,
                   motivos, estado, vista_primera_vez, notificada, fuentes_extra
               )
               SELECT ?, huella, titulo, empresa, url, fuente, ubicacion,
                      modalidad, descripcion, salario, publicada, tags, score,
                      motivos, estado, vista_primera_vez, notificada, fuentes_extra
               FROM ofertas_viejas""",
            (usuario_id,),
        )

        conteo_despues = con.execute(
            "SELECT COUNT(*) FROM ofertas WHERE usuario_id = ?", (usuario_id,)
        ).fetchone()[0]
        if conteo_despues != conteo_antes:
            con.execute("ROLLBACK")
            sys.exit(
                f"Abortado: {conteo_antes} ofertas antes, {conteo_despues} después de migrar. "
                "No se guardó nada."
            )

        con.execute("COMMIT")
    finally:
        con.close()

    print(
        f"\n  Migración lista. Usuario «{args.login}» creado con {conteo_antes} ofertas.\n"
        "  La tabla vieja quedó como `ofertas_viejas` (red de seguridad) — "
        "bórrala manualmente con DROP TABLE cuando confirmes que todo anda bien.\n"
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    cargar_env()

    p = argparse.ArgumentParser(description="Buscador de ofertas de trabajo")
    sub = p.add_subparsers(dest="comando", required=True)

    b = sub.add_parser("buscar", help="recolectar y notificar (todos los usuarios)")
    b.add_argument("--sin-avisos", action="store_true", help="no enviar a Telegram")
    b.add_argument(
        "--solo-portal", action="append", default=None,
        help="repetible; restringe la corrida a estos portales (ej. --solo-portal correo)",
    )
    b.set_defaults(func=cmd_buscar)

    s = sub.add_parser("servir", help="levantar el dashboard")
    s.add_argument("--puerto", type=int, default=5000)
    s.add_argument("--host", default="127.0.0.1", help="usar 0.0.0.0 para exponerlo fuera de localhost")
    s.add_argument("--debug", action="store_true")
    s.set_defaults(func=cmd_servir)

    i = sub.add_parser("inspeccionar", help="guardar HTML de muestra de un portal")
    i.add_argument("--portal", required=True)
    i.add_argument("--consulta", default=None)
    i.set_defaults(func=cmd_inspeccionar)

    r = sub.add_parser("resumen", help="conteo por estado")
    r.add_argument("--usuario", default=None, help="login; sin esto, muestra todos")
    r.set_defaults(func=cmd_resumen)

    rs = sub.add_parser(
        "recalcular-scores",
        help="vuelve a puntuar lo ya guardado con el perfil actual (tras editar /configuracion)",
    )
    rs.add_argument("--usuario", default=None, help="login; sin esto, recalcula para todos")
    rs.set_defaults(func=cmd_recalcular_scores)

    cu = sub.add_parser("crear-usuario", help="dar de alta una cuenta nueva")
    cu.add_argument("--login", required=True)
    cu.add_argument("--password", required=True)
    cu.set_defaults(func=cmd_crear_usuario)

    gi = sub.add_parser("generar-invitacion", help="link de un solo uso para que alguien cree su cuenta")
    gi.add_argument("--horas", type=int, default=48, help="validez del link (default 48h)")
    gi.add_argument("--url-base", default="http://127.0.0.1:5000", help="dominio/IP real del dashboard")
    gi.set_defaults(func=cmd_generar_invitacion)

    m = sub.add_parser("migrar-a-multiusuario", help="migra la base de una sola persona (una vez)")
    m.add_argument("--login", required=True)
    m.add_argument("--password", required=True)
    m.set_defaults(func=cmd_migrar_a_multiusuario)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
