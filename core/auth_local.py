# core/auth_local.py
from django.db.models import Q
from django.contrib.auth.hashers import check_password, make_password
from .models import Usuario


def authenticate_local(usuario_o_correo: str, password: str):
    """
    Autentica contra tu tabla core.Usuario.

    - Tu BD de Render guarda la contraseña en Usuario.Contrasena (hash pbkdf2_sha256$...)
    - Por eso aquí se valida con django.contrib.auth.hashers.check_password()

    Devuelve el objeto Usuario si valida, si no, None.
    """
    if not usuario_o_correo or not password:
        return None

    value = usuario_o_correo.strip()
    pwd = password.strip()

    # Login por correo (tu sistema usa Correo_electronico)
    u = Usuario.objects.filter(
        Q(Correo_electronico__iexact=value)
    ).first()

    if not u:
        return None

    if not getattr(u, "Activo", True):
        return None

    # Campo real donde guardas contraseña (hash Django)
    stored = (getattr(u, "Contrasena", "") or "").strip()
    if not stored:
        return None

    # 1) Caso correcto: hash Django pbkdf2...
    try:
        if check_password(pwd, stored):
            return u
    except Exception:
        # Si hubiera hashes raros, no rompemos
        pass

    # 2) Compatibilidad (si en algún momento hubo texto plano)
    #    Si coincide, lo convertimos a hash y lo guardamos.
    if stored == pwd:
        try:
            u.Contrasena = make_password(pwd)
            u.save(update_fields=["Contrasena"])
            return u
        except Exception:
            return None

    return None
