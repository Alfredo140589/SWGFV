from functools import wraps
from django.shortcuts import redirect


def require_session_login(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.session.get("usuario") and request.session.get("tipo"):
            return view_func(request, *args, **kwargs)
        return redirect("core:login")
    return _wrapped


def require_admin(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.session.get("tipo") == "Administrador":
            return view_func(request, *args, **kwargs)
        return redirect("core:menu_principal")
    return _wrapped
