# core/views.py
import random
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.core import signing
from django.core.mail import send_mail
from django.conf import settings

from .forms import (
    LoginForm,
    UsuarioCreateForm,
    UsuarioUpdateForm,
    ProyectoCreateForm,
    ProyectoUpdateForm,
    PasswordRecoveryRequestForm,
    PasswordResetForm,
)
from .auth_local import authenticate_local
from .decorators import require_session_login, require_admin
from .models import Usuario, Proyecto


# =========================================================
# CAPTCHA sin sesión (firmado)
# =========================================================
CAPTCHA_SALT = "swgfv-captcha-v1"
CAPTCHA_MAX_AGE_SECONDS = 5 * 60  # 5 min

def _new_captcha_signed():
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    answer = str(a + b)
    token = signing.dumps({"a": a, "b": b, "ans": answer}, salt=CAPTCHA_SALT)
    question = f"{a} + {b} = ?"
    return question, token

def _read_captcha_token(token: str):
    try:
        data = signing.loads(token, salt=CAPTCHA_SALT, max_age=CAPTCHA_MAX_AGE_SECONDS)
        return str(data.get("ans", "")).strip()
    except signing.SignatureExpired:
        return None
    except signing.BadSignature:
        return None


# =========================================================
# Password reset token helpers (firma + expiración)
# =========================================================
RESET_SALT = "swgfv-reset-v1"
RESET_MAX_AGE_SECONDS = 15 * 60  # 15 min

def _get_user_password_hash(u: Usuario) -> str:
    return (getattr(u, "Contrasena", "") or "").strip()

def _make_reset_token(u: Usuario) -> str:
    payload = {"uid": int(u.ID_Usuario), "ph": _get_user_password_hash(u)}
    return signing.dumps(payload, salt=RESET_SALT)

def _read_reset_token(token: str):
    try:
        data = signing.loads(token, salt=RESET_SALT, max_age=RESET_MAX_AGE_SECONDS)
    except signing.SignatureExpired:
        return None, "El enlace expiró. Solicita uno nuevo."
    except signing.BadSignature:
        return None, "Enlace inválido. Solicita uno nuevo."

    uid = data.get("uid")
    ph = (data.get("ph") or "").strip()
    if not uid:
        return None, "Enlace inválido. Solicita uno nuevo."

    u = Usuario.objects.filter(ID_Usuario=uid).first()
    if not u:
        return None, "Enlace inválido. Solicita uno nuevo."

    if _get_user_password_hash(u) != ph:
        return None, "Este enlace ya no es válido. Solicita uno nuevo."

    return u, None


# =========================================================
# LOGIN
# =========================================================
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.session.get("usuario") and request.session.get("tipo"):
        return redirect("core:menu_principal")

    form = LoginForm(request.POST or None)

    # Siempre generamos captcha para mostrar (GET) o para reintentar (POST fallido)
    captcha_question, captcha_token = _new_captcha_signed()

    # Lockout 3 intentos / 30 min (por usuario, guardado en sesión)
    def _lock_key(usuario: str) -> str:
        return f"lock_until::{usuario}"

    def _fail_key(usuario: str) -> str:
        return f"fails::{usuario}"

    def _get_locked_until(usuario: str):
        return request.session.get(_lock_key(usuario))

    def _set_locked_until(usuario: str, ts: int):
        request.session[_lock_key(usuario)] = ts
        request.session.modified = True

    def _get_fails(usuario: str) -> int:
        return int(request.session.get(_fail_key(usuario), 0))

    def _set_fails(usuario: str, n: int):
        request.session[_fail_key(usuario)] = int(n)
        request.session.modified = True

    def _reset_fails(usuario: str):
        request.session.pop(_fail_key(usuario), None)
        request.session.pop(_lock_key(usuario), None)
        request.session.modified = True

    if request.method == "POST":
        usuario_input = (request.POST.get("usuario") or "").strip()

        if not usuario_input:
            messages.error(request, "Ingresa tu usuario/correo.")
            captcha_question, captcha_token = _new_captcha_signed()
            return render(
                request,
                "core/login.html",
                {"form": form, "captcha_question": captcha_question, "captcha_token": captcha_token},
            )

        now_ts = int(timezone.now().timestamp())
        locked_until = _get_locked_until(usuario_input)
        if locked_until and now_ts < int(locked_until):
            remaining = int(locked_until) - now_ts
            minutes = max(1, (remaining + 59) // 60)
            messages.error(request, f"Cuenta bloqueada temporalmente. Intenta de nuevo en {minutes} minuto(s).")
            captcha_question, captcha_token = _new_captcha_signed()
            return render(
                request,
                "core/login.html",
                {"form": form, "captcha_question": captcha_question, "captcha_token": captcha_token},
            )

        if not form.is_valid():
            messages.error(request, "Revisa el formulario e intenta nuevamente.")
            captcha_question, captcha_token = _new_captcha_signed()
            return render(
                request,
                "core/login.html",
                {"form": form, "captcha_question": captcha_question, "captcha_token": captcha_token},
            )

        # CAPTCHA: ahora se valida con token firmado (no depende de sesión)
        token = (request.POST.get("captcha_token") or "").strip()
        expected = _read_captcha_token(token)
        provided = (form.cleaned_data.get("captcha") or "").strip()

        if not expected or provided != expected:
            fails = _get_fails(usuario_input) + 1
            _set_fails(usuario_input, fails)

            if fails >= 3:
                _set_locked_until(usuario_input, now_ts + (30 * 60))
                messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
            else:
                messages.error(request, f"Captcha incorrecto. Intento {fails}/3.")

            captcha_question, captcha_token = _new_captcha_signed()
            return render(
                request,
                "core/login.html",
                {"form": form, "captcha_question": captcha_question, "captcha_token": captcha_token},
            )

        # Credenciales
        password = form.cleaned_data["password"]
        u = authenticate_local(usuario_input, password)

        if u:
            _reset_fails(usuario_input)

            request.session["usuario"] = u.Correo_electronico
            request.session["tipo"] = u.Tipo
            request.session["id_usuario"] = u.ID_Usuario
            request.session.modified = True

            return redirect("core:menu_principal")

        # credenciales mal
        fails = _get_fails(usuario_input) + 1
        _set_fails(usuario_input, fails)

        if fails >= 3:
            _set_locked_until(usuario_input, now_ts + (30 * 60))
            messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
        else:
            messages.error(request, f"Usuario o contraseña incorrectos. Intento {fails}/3.")

        captcha_question, captcha_token = _new_captcha_signed()
        return render(
            request,
            "core/login.html",
            {"form": form, "captcha_question": captcha_question, "captcha_token": captcha_token},
        )

    return render(
        request,
        "core/login.html",
        {"form": form, "captcha_question": captcha_question, "captcha_token": captcha_token},
    )


# =========================================================
# MENÚ / LOGOUT / AYUDA
# =========================================================
@require_session_login
def menu_principal(request):
    return render(request, "core/menu_principal.html")

@require_session_login
def logout_view(request):
    request.session.flush()
    return redirect("core:login")

@require_session_login
def ayuda_view(request):
    return render(request, "core/ayuda.html")


# =========================================================
# RECUPERAR
# =========================================================
@require_http_methods(["GET", "POST"])
def recuperar_view(request):
    form = PasswordRecoveryRequestForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            email = form.cleaned_data["email"].strip()

            messages.success(request, "Si el correo está registrado, enviaremos un enlace para restablecer tu contraseña.")

            u = Usuario.objects.filter(Correo_electronico__iexact=email, Activo=True).first()
            if u:
                token = _make_reset_token(u)
                reset_link = request.build_absolute_uri(
                    reverse("core:password_reset_confirm", kwargs={"token": token})
                )

                subject = "SWGFV - Restablecer contraseña"
                body = (
                    "Se solicitó restablecer tu contraseña.\n\n"
                    f"Abre este enlace (expira en 15 minutos):\n{reset_link}\n\n"
                    "Si tú no lo solicitaste, ignora este correo."
                )

                try:
                    send_mail(
                        subject,
                        body,
                        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@swgfv.local"),
                        [email],
                        fail_silently=False,
                    )
                except Exception:
                    pass

            return redirect("core:recuperar")

        messages.error(request, "Revisa el formulario e intenta nuevamente.")

    return render(request, "core/recuperar.html", {"form": form})


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request, token):
    u, err = _read_reset_token(token)
    if err:
        messages.error(request, err)
        return redirect("core:recuperar")

    form = PasswordResetForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            new_pass = form.cleaned_data["new_password"]
            u.set_password(new_pass)
            u.save()
            messages.success(request, "Contraseña actualizada. Ya puedes iniciar sesión.")
            return redirect("core:login")
        messages.error(request, "Revisa el formulario. Hay errores.")

    return render(request, "core/password_reset_confirm.html", {"form": form, "email": u.Correo_electronico})


# =========================================================
# DEBUG
# =========================================================
def debug_sesion(request):
    session_usuario = request.session.get("usuario")
    session_tipo = request.session.get("tipo")
    session_id_usuario = request.session.get("id_usuario")

    u = None
    if session_id_usuario:
        u = Usuario.objects.filter(ID_Usuario=session_id_usuario).first()

    data = {
        "session_usuario": session_usuario,
        "session_tipo": session_tipo,
        "session_id_usuario": session_id_usuario,
        "session_keys": list(request.session.keys()),
        "usuario_en_bd": bool(u),
        "usuario_bd_ID_Usuario": getattr(u, "ID_Usuario", None),
        "usuario_bd_Correo": getattr(u, "Correo_electronico", None),
        "usuario_bd_Tipo": getattr(u, "Tipo", None),
        "usuario_bd_Activo": getattr(u, "Activo", None),
    }
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False, "indent": 2})


# =========================================================
# PROYECTOS / USUARIOS (como estaban)
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def proyecto_alta(request):
    session_id_usuario = request.session.get("id_usuario")
    if not session_id_usuario:
        messages.error(request, "Sesión incompleta. Inicia sesión nuevamente.")
        return redirect("core:logout")

    user = Usuario.objects.filter(ID_Usuario=session_id_usuario).first()
    if not user or not user.Activo:
        messages.error(request, "Usuario inválido o inactivo.")
        return redirect("core:logout")

    form = ProyectoCreateForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            proyecto = form.save(commit=False)
            proyecto.ID_Usuario = user
            proyecto.save()
            messages.success(request, "✅ Proyecto registrado correctamente.")
            return redirect("core:proyecto_alta")
        messages.error(request, "Revisa el formulario e intenta nuevamente.")

    return render(request, "core/pages/proyecto_alta.html", {"form": form})

@require_session_login
def proyecto_consulta(request):
    session_tipo = request.session.get("tipo")
    session_id_usuario = request.session.get("id_usuario")

    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.select_related("ID_Usuario").all().order_by("-id")
    else:
        proyectos = Proyecto.objects.select_related("ID_Usuario").filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    return render(request, "core/pages/proyecto_consulta.html", {"proyectos": proyectos})

@require_admin
@require_http_methods(["GET", "POST"])
def proyecto_modificacion(request):
    return render(request, "core/pages/proyecto_modificacion.html")

@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_alta(request):
    form = UsuarioCreateForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Usuario dado de alta correctamente.")
            return redirect("core:gestion_usuarios_alta")
        messages.error(request, "Revisa el formulario. Hay errores.")
    return render(request, "core/pages/gestion_usuarios_alta.html", {"form": form})

@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_modificacion(request):
    """
    Gestión de usuarios (Admin):
    - GET con filtros: muestra lista
    - GET con ?id=: selecciona usuario para editar
    - POST: guarda cambios del usuario seleccionado
    - POST action=deactivate: desactiva usuario
    """
    # ---------
    # Query params (búsqueda)
    # ---------
    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    q_ap = (request.GET.get("ap") or "").strip()
    q_am = (request.GET.get("am") or "").strip()

    # Mostrar lista SOLO si hay búsqueda (no cargar todos por defecto)
    mostrar_lista = any([q_id, q_nombre, q_ap, q_am])

    usuarios = Usuario.objects.none()
    if mostrar_lista:
        qs = Usuario.objects.all().order_by("ID_Usuario")

        if q_id.isdigit():
            qs = qs.filter(ID_Usuario=int(q_id))
        elif q_id:
            # si puso algo no numérico, que no truene y no devuelva todo
            qs = Usuario.objects.none()

        if q_nombre:
            qs = qs.filter(Nombre__icontains=q_nombre)

        if q_ap:
            qs = qs.filter(Apellido_Paterno__icontains=q_ap)

        if q_am:
            qs = qs.filter(Apellido_Materno__icontains=q_am)

        usuarios = qs

    # ---------
    # Selección por ID (para editar)
    # IMPORTANTE: el template usa ?id= para seleccionar
    # ---------
    seleccionado = None
    form = None

    # Si el ?id= es numérico, intentamos seleccionar usuario
    if q_id.isdigit():
        seleccionado = Usuario.objects.filter(ID_Usuario=int(q_id)).first()
        if seleccionado:
            form = UsuarioUpdateForm(instance=seleccionado)

    # ---------
    # POST: Guardar cambios o desactivar
    # ---------
    if request.method == "POST":
        # En POST, el usuario seleccionado viene del querystring ?id=
        post_id = (request.GET.get("id") or "").strip()

        if not post_id.isdigit():
            messages.error(request, "Selecciona un usuario válido para modificar.")
            return redirect("core:gestion_usuarios_modificacion")

        seleccionado = Usuario.objects.filter(ID_Usuario=int(post_id)).first()
        if not seleccionado:
            messages.error(request, "El usuario seleccionado ya no existe.")
            return redirect("core:gestion_usuarios_modificacion")

        action = (request.POST.get("action") or "").strip().lower()

        # Desactivar
        if action == "deactivate":
            seleccionado.Activo = False
            seleccionado.save()
            messages.success(request, "Usuario desactivado correctamente.")
            return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

        # Guardar edición normal
        form = UsuarioUpdateForm(request.POST, instance=seleccionado)

        # Normalizar correo antes de validar (evitar duplicados por mayúsculas)
        if form.is_valid():
            email = (form.cleaned_data.get("Correo_electronico") or "").strip().lower()

            # Validar que el correo no esté usado por otro usuario
            if Usuario.objects.filter(Correo_electronico__iexact=email).exclude(ID_Usuario=seleccionado.ID_Usuario).exists():
                form.add_error("Correo_electronico", "Ya existe otro usuario con ese correo.")
            else:
                # Guardar correo normalizado y resto de campos
                obj = form.save(commit=False)
                obj.Correo_electronico = email
                obj.save()
                messages.success(request, "Usuario actualizado correctamente.")
                return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

        messages.error(request, "Revisa el formulario. Hay errores.")

    # ---------
    # Render
    # ---------
    context = {
        "q_id": q_id,
        "q_nombre": q_nombre,
        "q_ap": q_ap,
        "q_am": q_am,
        "mostrar_lista": mostrar_lista,
        "usuarios": usuarios,
        "seleccionado": seleccionado,
        "form": form,
    }
    return render(request, "core/pages/gestion_usuarios_modificacion.html", context)

@require_session_login
def cuenta_view(request):
    return render(request, "core/pages/cuenta.html")
# ==========================
# PLACEHOLDERS DEL MENÚ
# (Para que NO falle el deploy y puedas navegar como antes)
# ==========================
from django.contrib.auth.decorators import login_required
from django.template import TemplateDoesNotExist

def _render_menu_page(request, template_path: str, title: str):
    """
    Renderiza pantallas del menú.
    Si el template no existe, muestra un mensaje claro (no revienta deploy).
    """
    try:
        return render(request, template_path, {"title": title})
    except TemplateDoesNotExist:
        # IMPORTANTE: Esto NO es "template genérico para todo".
        # Solo evita que el sistema muera si aún no existe el HTML.
        return render(request, "core/menu_principal.html", {
            "title": title,
            "messages": [],
        })

# Dimensionamiento
@require_session_login
def dimensionamiento_calculo_modulos(request):
    return _render_menu_page(request, "core/pages/dimensionamiento_calculo_modulos.html", "Cálculo de Módulos")

@require_session_login
def dimensionamiento_dimensionamiento(request):
    return _render_menu_page(request, "core/pages/dimensionamiento_dimensionamiento.html", "Dimensionamiento")

# Cálculos
@require_session_login
def calculo_dc(request):
    return _render_menu_page(request, "core/pages/calculo_dc.html", "Cálculo DC")

@require_session_login
def calculo_ac(request):
    return _render_menu_page(request, "core/pages/calculo_ac.html", "Cálculo AC")

@require_session_login
def calculo_caida_tension(request):
    return _render_menu_page(request, "core/pages/calculo_caida_tension.html", "Caída de Tensión")

# Recursos
@require_session_login
def recursos_tablas(request):
    return _render_menu_page(request, "core/pages/recursos_tablas.html", "Tablas")

@require_session_login
def recursos_conceptos(request):
    return _render_menu_page(request, "core/pages/recursos_conceptos.html", "Conceptos")

@require_admin
def recursos_alta_concepto(request):
    return _render_menu_page(request, "core/pages/recursos_alta_concepto.html", "Alta de Concepto")

@require_admin
def recursos_alta_tabla(request):
    return _render_menu_page(request, "core/pages/recursos_alta_tabla.html", "Alta de Tabla")

@require_admin
def recursos_modificacion_concepto(request):
    return _render_menu_page(request, "core/pages/recursos_modificacion_concepto.html", "Modificar Concepto")

@require_admin
def recursos_modificacion_tabla(request):
    return _render_menu_page(request, "core/pages/recursos_modificacion_tabla.html", "Modificar Tabla")
