"""Persistencia en SQLite.

La base cumple tres funciones: guardar las cuentas de usuario (multiusuario:
cada quien tiene su propio perfil de búsqueda y sus propias credenciales),
guardar el historial de ofertas de cada uno, y recordar cuáles ya fueron
notificadas para no avisar dos veces de lo mismo.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Oferta

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
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
);

CREATE TABLE IF NOT EXISTS ofertas (
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
);
CREATE INDEX IF NOT EXISTS idx_usuario_estado ON ofertas(usuario_id, estado);
CREATE INDEX IF NOT EXISTS idx_usuario_score  ON ofertas(usuario_id, score DESC);

-- Links de invitación de un solo uso, para que alguien cree su propia cuenta
-- sin que el dueño del servidor tenga que correr `crear-usuario` por SSH.
CREATE TABLE IF NOT EXISTS invitaciones (
    token   TEXT PRIMARY KEY,
    creado  TEXT NOT NULL,
    expira  TEXT NOT NULL,
    usado   INTEGER DEFAULT 0
);
"""

ESTADOS_VALIDOS = {"nueva", "interesada", "postulada", "descartada"}


class Repo:
    def __init__(self, ruta: str | Path = "data/empleos.db", usuario_id: int | None = None):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.usuario_id = usuario_id
        self.con = sqlite3.connect(self.ruta)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(ESQUEMA)
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Repo":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _uid(self) -> int:
        """Ninguna operación sobre `ofertas` debería poder correr sin saber de
        quién son — mejor un error ruidoso acá que un query que por accidente
        mezcle o filtre datos entre usuarios."""
        if self.usuario_id is None:
            raise ValueError("Repo necesita usuario_id para operar sobre ofertas")
        return self.usuario_id

    # --- cuentas -----------------------------------------------------------

    def crear_usuario(
        self,
        login: str,
        clave_hash: str,
        config: dict,
        telegram_token: str | None = None,
        telegram_chat_id: str | None = None,
        gmail_user: str | None = None,
        gmail_app_password: str | None = None,
    ) -> int:
        cur = self.con.execute(
            """INSERT INTO usuarios (
                   login, clave_hash, creado, config_json,
                   telegram_token, telegram_chat_id, gmail_user, gmail_app_password
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                login, clave_hash,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                json.dumps(config, ensure_ascii=False),
                telegram_token, telegram_chat_id, gmail_user, gmail_app_password,
            ),
        )
        self.con.commit()
        return cur.lastrowid

    def obtener_usuario_por_login(self, login: str) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM usuarios WHERE login = ?", (login,)
        ).fetchone()

    def obtener_usuario(self, usuario_id: int) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()

    def listar_usuarios(self, solo_activos: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM usuarios" + (" WHERE activo = 1" if solo_activos else "")
        return self.con.execute(sql).fetchall()

    def actualizar_config(self, usuario_id: int, config_json: str) -> None:
        self.con.execute(
            "UPDATE usuarios SET config_json = ? WHERE id = ?", (config_json, usuario_id)
        )
        self.con.commit()

    def actualizar_credenciales(
        self,
        usuario_id: int,
        telegram_token: str | None,
        telegram_chat_id: str | None,
        gmail_user: str | None,
        gmail_app_password: str | None,
    ) -> None:
        self.con.execute(
            """UPDATE usuarios SET telegram_token=?, telegram_chat_id=?,
               gmail_user=?, gmail_app_password=? WHERE id=?""",
            (telegram_token, telegram_chat_id, gmail_user, gmail_app_password, usuario_id),
        )
        self.con.commit()

    # --- invitaciones ------------------------------------------------------

    def crear_invitacion(self, token: str, expira_iso: str) -> None:
        self.con.execute(
            "INSERT INTO invitaciones (token, creado, expira) VALUES (?, ?, ?)",
            (token, datetime.now(timezone.utc).isoformat(timespec="seconds"), expira_iso),
        )
        self.con.commit()

    def invitacion_valida(self, token: str) -> bool:
        fila = self.con.execute(
            "SELECT expira, usado FROM invitaciones WHERE token = ?", (token,)
        ).fetchone()
        if fila is None or fila["usado"]:
            return False
        return datetime.fromisoformat(fila["expira"]) > datetime.now(timezone.utc)

    def marcar_invitacion_usada(self, token: str) -> None:
        self.con.execute("UPDATE invitaciones SET usado = 1 WHERE token = ?", (token,))
        self.con.commit()

    # --- ofertas: escritura --------------------------------------------------

    def guardar(self, oferta: Oferta) -> bool:
        """Inserta la oferta para el usuario actual. Devuelve True solo si es
        nueva para él.

        Si ya existía (mismo puesto visto en otro portal), deja registrada la
        fuente adicional pero no la vuelve a contar como novedad. El mismo
        puesto real puede existir además para OTRO usuario_id, con su propio
        estado — eso es una fila totalmente aparte, no colisiona acá.
        """
        uid = self._uid()
        fila = self.con.execute(
            "SELECT fuente, fuentes_extra FROM ofertas WHERE usuario_id = ? AND huella = ?",
            (uid, oferta.huella),
        ).fetchone()

        if fila is not None:
            conocidas = {fila["fuente"], *filter(None, fila["fuentes_extra"].split(","))}
            if oferta.fuente not in conocidas:
                conocidas.discard(fila["fuente"])
                conocidas.add(oferta.fuente)
                self.con.execute(
                    "UPDATE ofertas SET fuentes_extra = ? WHERE usuario_id = ? AND huella = ?",
                    (",".join(sorted(conocidas)), uid, oferta.huella),
                )
                self.con.commit()
            return False

        self.con.execute(
            """INSERT INTO ofertas (
                   usuario_id, huella, titulo, empresa, url, fuente, ubicacion,
                   modalidad, descripcion, salario, publicada, tags, score,
                   motivos, estado, vista_primera_vez
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uid, oferta.huella, oferta.titulo, oferta.empresa, oferta.url,
                oferta.fuente, oferta.ubicacion, oferta.modalidad,
                oferta.descripcion, oferta.salario, oferta.publicada,
                json.dumps(oferta.tags, ensure_ascii=False), oferta.score,
                json.dumps(oferta.motivos, ensure_ascii=False), oferta.estado,
                oferta.vista_primera_vez,
            ),
        )
        self.con.commit()
        return True

    def marcar_notificadas(self, huellas: list[str]) -> None:
        uid = self._uid()
        self.con.executemany(
            "UPDATE ofertas SET notificada = 1 WHERE usuario_id = ? AND huella = ?",
            [(uid, h) for h in huellas],
        )
        self.con.commit()

    def cambiar_estado(self, huella: str, estado: str) -> None:
        if estado not in ESTADOS_VALIDOS:
            raise ValueError(f"Estado desconocido: {estado}")
        self.con.execute(
            "UPDATE ofertas SET estado = ? WHERE usuario_id = ? AND huella = ?",
            (estado, self._uid(), huella),
        )
        self.con.commit()

    # --- ofertas: lectura ------------------------------------------------

    def pendientes_de_notificar(self, score_minimo: int = 0) -> list[sqlite3.Row]:
        return self.con.execute(
            """SELECT * FROM ofertas
               WHERE usuario_id = ? AND notificada = 0 AND estado != 'descartada' AND score >= ?
               ORDER BY score DESC""",
            (self._uid(), score_minimo),
        ).fetchall()

    def listar(
        self,
        estado: str | None = None,
        limite: int = 200,
        orden: str = "reciente",
        ocultar_descartadas: bool = False,
        categoria: str | None = None,
        fuente: str | None = None,
    ) -> list[sqlite3.Row]:
        # "reciente" ordena por `publicada` (fecha real del portal); las
        # ofertas sin esa fecha (Computrabajo, Laborum por correo) son NULL y
        # SQLite las manda al final en DESC, así que quedan ordenadas entre
        # ellas por `vista_primera_vez` en vez de desaparecer del criterio.
        columna = "score" if orden == "puntaje" else "publicada"

        condiciones, params = ["usuario_id = ?"], [self._uid()]
        if categoria == "excluida":
            # Auto-descartada por un término de `filtros.excluir` (senior,
            # jefe, gerente...): el cargo sí es el que buscas, pero no al
            # nivel que pediste. Se distingue del descarte manual por el
            # motivo que deja `scoring.py` al excluir, no por el estado —
            # ambos casos comparten estado='descartada'.
            condiciones.append("estado = 'descartada' AND motivos LIKE '%excluida por%'")
        elif categoria == "sin_relacion":
            # Nunca tocada a mano y sin ninguna coincidencia de rol/stack:
            # otra carrera que solo matcheó por la palabra "junior" (o ni
            # eso). Por construcción excluye a las de arriba, que quedan en
            # estado='descartada', no 'nueva'.
            condiciones.append("estado = 'nueva' AND score = 0")
        elif estado:
            condiciones.append("estado = ?")
            params.append(estado)
            if estado == "nueva" and ocultar_descartadas:
                # "Sin revisar" es justo la pestaña que más se llena de
                # ofertas score 0 (no descartadas a mano, solo sin relación
                # con el perfil) — sin este filtro, ocultarlas de "Todas" no
                # sirve de nada si igual aparecen acá.
                condiciones.append("score > 0")
        elif ocultar_descartadas:
            # Solo aplica cuando no se pidió un estado específico: si el
            # usuario está mirando justo la pestaña "Descartada" (o
            # cualquier otra donde ya trió la oferta a mano), ocultar por
            # score no tendría sentido — su triaje manual manda.
            condiciones.append("estado != 'descartada' AND score > 0")

        if fuente:
            condiciones.append("fuente = ?")
            params.append(fuente)

        filtro = f"WHERE {' AND '.join(condiciones)}"
        params.append(limite)
        sql = f"SELECT * FROM ofertas {filtro} ORDER BY {columna} DESC, vista_primera_vez DESC LIMIT ?"
        return self.con.execute(sql, params).fetchall()

    def conteo_por_estado(self) -> dict[str, int]:
        filas = self.con.execute(
            "SELECT estado, COUNT(*) AS n FROM ofertas WHERE usuario_id = ? GROUP BY estado",
            (self._uid(),),
        ).fetchall()
        return {f["estado"]: f["n"] for f in filas}

    def conteo_ocultas(self) -> dict[str, int]:
        """Cuenta las dos categorías que `listar(categoria=...)` sabe mostrar
        por separado, para los badges de esos botones en el dashboard."""
        uid = self._uid()
        excluida = self.con.execute(
            "SELECT COUNT(*) FROM ofertas WHERE usuario_id = ? "
            "AND estado = 'descartada' AND motivos LIKE '%excluida por%'",
            (uid,),
        ).fetchone()[0]
        sin_relacion = self.con.execute(
            "SELECT COUNT(*) FROM ofertas WHERE usuario_id = ? "
            "AND estado = 'nueva' AND score = 0",
            (uid,),
        ).fetchone()[0]
        return {"excluida": excluida, "sin_relacion": sin_relacion}

    def todas_para_recalculo(self) -> list[sqlite3.Row]:
        """Todo lo guardado del usuario, sin filtro de estado ni límite —
        para `recalcular_scores` en `pipeline.py`. `guardar()` no reprocesa
        nada si la huella ya existe, así que si el usuario cambia su perfil
        en `/configuracion`, lo viejo se queda con el score anterior hasta
        que se corra esto a mano."""
        return self.con.execute(
            """SELECT huella, titulo, empresa, url, fuente, descripcion, score, motivos
               FROM ofertas WHERE usuario_id = ?""",
            (self._uid(),),
        ).fetchall()

    def actualizar_score(self, huella: str, score: int, motivos: list[str]) -> None:
        """No toca `estado`: el triaje manual del usuario (interesada,
        postulada, descartada a mano) manda sobre cualquier recálculo."""
        self.con.execute(
            "UPDATE ofertas SET score = ?, motivos = ? WHERE usuario_id = ? AND huella = ?",
            (score, json.dumps(motivos, ensure_ascii=False), self._uid(), huella),
        )
        self.con.commit()

    def fuentes_usadas(self) -> list[str]:
        """Portales que efectivamente tienen ofertas guardadas para este
        usuario, para armar el filtro por portal sin hardcodear la lista
        (así incluye "laborum"/"linkedin", que llegan vía `correo.py` con su
        propio `fuente` y no como un portal "correo" genérico)."""
        filas = self.con.execute(
            "SELECT DISTINCT fuente FROM ofertas WHERE usuario_id = ? ORDER BY fuente",
            (self._uid(),),
        ).fetchall()
        return [f["fuente"] for f in filas]
