"""Login del dashboard: usuario+clave contra la tabla `usuarios`, sesión
firmada con `flask.session` (cookie, sin estado en el servidor).

Sin roles ni recuperación de contraseña por email — son 2-3 personas de
confianza, las cuentas se crean a mano con `python main.py crear-usuario`.
"""

from __future__ import annotations

from functools import wraps

from flask import redirect, request, session, url_for


def usuario_actual_id() -> int | None:
    return session.get("usuario_id")


def requiere_login(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if usuario_actual_id() is None:
            return redirect(url_for("login", siguiente=request.path))
        return vista(*args, **kwargs)
    return envoltura
