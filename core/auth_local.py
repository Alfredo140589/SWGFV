from .models import Usuario


class AuthUser:
    def __init__(self, username: str, role: str):
        self.username = username
        self.role = role


def authenticate_local(usuario: str, password: str):
    """
    Autenticación contra la tabla 'usuarios' (modelo Usuario).
    - usuario: se compara contra Correo_electronico (en tu caso permite 'admin' y 'usuario')
    - password: se valida contra contraseña hasheada
    """
    try:
        u = Usuario.objects.get(Correo_electronico=usuario, Activo=True)
    except Usuario.DoesNotExist:
        return None

    if u.check_password(password):
        return AuthUser(username=u.Correo_electronico, role=u.Tipo)

    return None
