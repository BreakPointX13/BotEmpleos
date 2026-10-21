"""Tests de la lógica que no depende de la red.

Corre con:  python -m pytest tests/ -v
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
import yaml  # noqa: E402

from src.db import Repo  # noqa: E402
from src.models import Oferta  # noqa: E402
from src.scoring import DESCARTADA, puntuar  # noqa: E402
from src.scrapers.remoteok import RemoteOK, _reparar_mojibake  # noqa: E402

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.example.yaml").read_text())


def oferta(titulo, descripcion="", empresa="Acme", fuente="getonbrd"):
    return Oferta(titulo=titulo, empresa=empresa, url="https://x.cl/1",
                  fuente=fuente, descripcion=descripcion)


# --- deduplicación -------------------------------------------------------

def test_mismo_puesto_en_dos_portales_comparte_huella():
    a = oferta("Desarrollador Full Stack Junior", fuente="getonbrd")
    b = oferta("Full-Stack Developer Jr (Remoto)", fuente="computrabajo")
    assert a.huella == b.huella


def test_puestos_distintos_no_colisionan():
    assert oferta("Desarrollador Backend").huella != oferta("Diseñador UX").huella


def test_empresas_distintas_no_colisionan():
    a = oferta("Desarrollador Junior", empresa="Acme")
    b = oferta("Desarrollador Junior", empresa="Globex")
    assert a.huella != b.huella


# --- scoring -------------------------------------------------------------

def test_senior_se_descarta():
    o = puntuar(oferta("Senior Backend Developer"), CFG)
    assert o.score == DESCARTADA
    assert o.estado == "descartada"


def test_sr_no_hace_match_dentro_de_otra_palabra():
    """La regresión clásica: 'sr' como subcadena descartaría medio dataset."""
    o = puntuar(oferta("Desarrollador Junior", "Trabajarás en desarrollo web"), CFG)
    assert o.score != DESCARTADA


def _oferta_desde_crudo(scraper, crudo):
    """Pasa un item por la reparación del scraper sin tocar la red."""
    class _Resp:
        def json(self):
            return [{"legal": "aviso"}, dict(crudo)]

    scraper._get = lambda url, **kw: _Resp()
    return scraper._a_oferta(scraper._obtener_crudo()[0])


def test_mojibake_de_remoteok_no_deja_pasar_una_oferta_senior():
    """Remote OK manda el texto con doble encoding UTF-8 ("5 aÃ±os").

    Sin repararlo, "5 años" normaliza a "5 aa os", el término de exclusión no
    matchea y una oferta que pide 5+ años pasa como junior. Verificado contra
    una oferta real (Kruger NearShore, 2026-09-27).
    """
    crudo = {"id": "1", "position": "DESARROLLADOR FULL STACK",
             "company": "Kruger", "description": "Requisitos: 5+ aÃ±os de experiencia."}
    sin_reparar = puntuar(oferta(crudo["position"], crudo["description"]), CFG)
    assert sin_reparar.score != DESCARTADA, "si esto falla, el test ya no prueba nada"

    scraper = RemoteOK(CFG)
    scraper._crudo = None
    reparado = puntuar(_oferta_desde_crudo(scraper, crudo), CFG)
    assert reparado.score == DESCARTADA
    assert reparado.motivos == ["excluida por «5 años»"]


def test_reparar_mojibake_no_toca_el_texto_ya_correcto():
    """Se aplica a todo el JSON sin distinguir, así que no puede dañar nada."""
    for ok in ("Desarrollador Full Stack", "Ingeniería de software", "5 años", "Bjak"):
        assert _reparar_mojibake(ok) == ok


def test_junior_en_titulo_puntua_mas_que_en_descripcion():
    a = puntuar(oferta("Desarrollador Junior React"), CFG)
    b = puntuar(oferta("Desarrollador React", "Buscamos perfil junior"), CFG)
    assert a.score > b.score


def test_stack_tiene_techo():
    muchas = " ".join(CFG["filtros"]["stack"])
    o = puntuar(oferta("Desarrollador Junior", muchas), CFG)
    assert o.score <= 100


def test_remoto_suma():
    con = puntuar(oferta("Desarrollador Junior", "Trabajo remoto"), CFG)
    sin = puntuar(oferta("Desarrollador Junior", "Trabajo en oficina"), CFG)
    assert con.score > sin.score


# --- persistencia --------------------------------------------------------
#
# Todas las operaciones sobre `ofertas` requieren un usuario_id (ver
# `Repo._uid`); acá se usa un id fijo (1) sin pasar por `crear_usuario`
# porque SQLite no fuerza el FK a `usuarios` sin `PRAGMA foreign_keys=ON`, y
# no hace falta esa cuenta real para probar el comportamiento de `ofertas`.

USUARIO_1 = 1


def test_solo_la_primera_insercion_cuenta_como_nueva(tmp_path):
    with Repo(tmp_path / "t.db", usuario_id=USUARIO_1) as repo:
        a = oferta("Full Stack Junior", fuente="getonbrd")
        b = oferta("Junior Full-Stack Dev", fuente="computrabajo")
        assert repo.guardar(a) is True
        assert repo.guardar(b) is False   # duplicado entre portales
        assert len(repo.listar()) == 1


def test_se_registra_la_fuente_adicional(tmp_path):
    with Repo(tmp_path / "t.db", usuario_id=USUARIO_1) as repo:
        repo.guardar(oferta("Full Stack Junior", fuente="getonbrd"))
        repo.guardar(oferta("Junior Full-Stack Dev", fuente="computrabajo"))
        fila = repo.listar()[0]
        assert "computrabajo" in fila["fuentes_extra"]


def test_cambio_de_estado(tmp_path):
    with Repo(tmp_path / "t.db", usuario_id=USUARIO_1) as repo:
        o = oferta("Desarrollador Junior")
        repo.guardar(o)
        repo.cambiar_estado(o.huella, "postulada")
        assert repo.listar()[0]["estado"] == "postulada"


def test_no_se_notifica_dos_veces(tmp_path):
    with Repo(tmp_path / "t.db", usuario_id=USUARIO_1) as repo:
        o = puntuar(oferta("Desarrollador Junior React"), CFG)
        repo.guardar(o)
        assert len(repo.pendientes_de_notificar(0)) == 1
        repo.marcar_notificadas([o.huella])
        assert len(repo.pendientes_de_notificar(0)) == 0


def test_repo_sin_usuario_id_no_opera_sobre_ofertas(tmp_path):
    """Falla ruidoso en vez de arriesgarse a mezclar datos entre usuarios."""
    with Repo(tmp_path / "t.db") as repo:
        with pytest.raises(ValueError):
            repo.guardar(oferta("Desarrollador Junior"))


# --- multiusuario ----------------------------------------------------------

def test_dos_usuarios_con_el_mismo_puesto_tienen_estados_independientes(tmp_path):
    ruta = tmp_path / "t.db"
    with Repo(ruta, usuario_id=1) as repo1:
        repo1.guardar(oferta("Desarrollador Junior", empresa="Acme"))
        repo1.cambiar_estado(oferta("Desarrollador Junior", empresa="Acme").huella, "postulada")

    with Repo(ruta, usuario_id=2) as repo2:
        repo2.guardar(oferta("Desarrollador Junior", empresa="Acme"))
        # El usuario 2 nunca tocó el estado: debe seguir "nueva", sin heredar
        # nada de lo que hizo el usuario 1 sobre el mismo puesto real.
        assert repo2.listar()[0]["estado"] == "nueva"

    with Repo(ruta, usuario_id=1) as repo1:
        assert repo1.listar()[0]["estado"] == "postulada"


def test_listar_no_mezcla_ofertas_de_otro_usuario(tmp_path):
    ruta = tmp_path / "t.db"
    with Repo(ruta, usuario_id=1) as repo1:
        repo1.guardar(oferta("Desarrollador Backend"))
    with Repo(ruta, usuario_id=2) as repo2:
        repo2.guardar(oferta("Diseñador UX"))
        assert len(repo2.listar()) == 1
        assert repo2.listar()[0]["titulo"] == "Diseñador UX"


def test_usuarios_crud(tmp_path):
    with Repo(tmp_path / "t.db") as repo:
        uid = repo.crear_usuario("daniel", "hash-falso", config={"portales_activos": ["getonbrd"]})
        fila = repo.obtener_usuario_por_login("daniel")
        assert fila["id"] == uid
        assert fila["clave_hash"] == "hash-falso"
        assert json.loads(fila["config_json"])["portales_activos"] == ["getonbrd"]

        repo.actualizar_config(uid, json.dumps({"portales_activos": ["remoteok"]}))
        assert json.loads(repo.obtener_usuario(uid)["config_json"])["portales_activos"] == ["remoteok"]

        assert repo.obtener_usuario_por_login("no-existe") is None
        assert len(repo.listar_usuarios()) == 1


# --- recálculo de scores ---------------------------------------------------

def test_recalcular_scores_repuntua_tras_editar_perfil(tmp_path):
    from src.pipeline import recalcular_scores

    ruta = tmp_path / "t.db"
    with Repo(ruta) as repo:
        uid = repo.crear_usuario("ana", "hash-falso", config=CFG)

    with Repo(ruta, usuario_id=uid) as repo:
        o = puntuar(oferta("Ejecutivo Comercial Junior", "ventas"), CFG)
        repo.guardar(o)
        repo.cambiar_estado(o.huella, "interesada")
        assert repo.listar()[0]["score"] == 0

    # El usuario amplía su perfil: ahora "comercial" también es un rol que le
    # interesa. Nada de esto se reprocesa solo, porque `guardar` ya vio esa
    # huella antes.
    cfg_nuevo = json.loads(json.dumps(CFG))
    cfg_nuevo["filtros"]["roles"].append("comercial")
    with Repo(ruta) as repo:
        repo.actualizar_config(uid, json.dumps(cfg_nuevo, ensure_ascii=False))

    resultado = recalcular_scores(str(ruta), login="ana")
    assert resultado["ana"]["cambiadas"] == 1

    with Repo(ruta, usuario_id=uid) as repo:
        fila = repo.listar()[0]
        assert fila["score"] > 0
        assert fila["estado"] == "interesada"  # el triaje manual no se toca
