import random

from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

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
# CAPTCHA helper
# =========================================================
def _new_captcha(request):
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    request.session["captcha_a"] = a
    request.session["captcha_b"] = b
    request.session["captcha_answer"] = str(a + b)  # guardamos string
    request.session.modified = True
    return f"{a} + {b} = ?"


# =========================================================
# Password reset token helpers (firma + expiración)
# =========================================================
signer = TimestampSigner()
RESET_MAX_AGE_SECONDS = 30 * 60  # 30 min


def _build_absolute_uri(request, path: str) -> str:
    return request.build_absolute_uri(path)


# ------------------------
# LOGIN
# ------------------------
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.session.get("usuario") and request.session.get("tipo"):
        return redirect("core:menu_principal")

    # captcha question
    if "captcha_answer" not in request.session:
        captcha_question = _new_captcha(request)
    else:
        captcha_question = f"{request.session.get('captcha_a')} + {request.session.get('captcha_b')} = ?"

    form = LoginForm(request.POST or None)

    # Lockout 3 intentos / 30 min (guardado en session)
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
            messages.error(request, "Ingresa tu usuario.")
            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

        # bloqueado?
        now_ts = int(timezone.now().timestamp())
        locked_until = _get_locked_until(usuario_input)
        if locked_until and now_ts < int(locked_until):
            remaining = int(locked_until) - now_ts
            minutes = max(1, (remaining + 59) // 60)
            messages.error(request, f"Cuenta bloqueada temporalmente. Intenta de nuevo en {minutes} minuto(s).")
            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

        # ✅ Importante: ahora el LoginForm tiene campo "captcha"
        if not form.is_valid():
            messages.error(request, "Revise el formulario y vuelva a intentar.")
            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

        # validar captcha
        expected = (request.session.get("captcha_answer") or "").strip()
        provided = (form.cleaned_data.get("captcha") or "").strip()

        if not expected or provided != expected:
            fails = _get_fails(usuario_input) + 1
            _set_fails(usuario_input, fails)

            if fails >= 3:
                _set_locked_until(usuario_input, now_ts + (30 * 60))
                messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
            else:
                messages.error(request, f"Verificación incorrecta. Intento {fails}/3.")

            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

        # captcha ok -> autenticar
        password = form.cleaned_data["password"]
        user_auth = authenticate_local(usuario_input, password)

        if user_auth:
            u = Usuario.objects.filter(Correo_electronico__iexact=usuario_input).first()
            if not u:
                fails = _get_fails(usuario_input) + 1
                _set_fails(usuario_input, fails)
                messages.error(request, f"Usuario o contraseña incorrectos. Intento {fails}/3.")
                captcha_question = _new_captcha(request)
                return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

            if not u.Activo:
                messages.error(request, "Tu usuario está inactivo. Contacta al administrador.")
                captcha_question = _new_captcha(request)
                return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

            _reset_fails(usuario_input)

            request.session["usuario"] = u.Correo_electronico
            request.session["tipo"] = u.Tipo
            request.session["id_usuario"] = u.ID_Usuario

            return redirect("core:menu_principal")

        # credenciales mal
        fails = _get_fails(usuario_input) + 1
        _set_fails(usuario_input, fails)

        if fails >= 3:
            _set_locked_until(usuario_input, now_ts + (30 * 60))
            messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
        else:
            messages.error(request, f"Usuario o contraseña incorrectos. Intento {fails}/3.")

        captcha_question = _new_captcha(request)
        return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

    return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})


# ------------------------
# MENÚ PRINCIPAL
# ------------------------
@require_session_login
def menu_principal(request):
    return render(request, "core/menu_principal.html")


# ------------------------
# LOGOUT
# ------------------------
@require_session_login
def logout_view(request):
    request.session.flush()
    return redirect("core:login")


# ------------------------
# AYUDA
# ------------------------
@require_session_login
def ayuda_view(request):
    return render(request, "core/ayuda.html")


# =========================================================
# RECUPERAR (PÚBLICO) - envía link con token
# =========================================================
@require_http_methods(["GET", "POST"])
def recuperar_view(request):
    form = PasswordRecoveryRequestForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            email = form.cleaned_data["email"].strip()

            # ✅ mensaje genérico siempre
            generic_msg = "Si el correo está registrado, enviaremos un enlace para restablecer tu contraseña."
            user = Usuario.objects.filter(Correo_electronico__iexact=email, Activo=True).first()

            if user:
                # Si no hay SMTP configurado, no intentes enviar (evita error inmediato)
                if not getattr(settings, "EMAIL_HOST", "") or not getattr(settings, "EMAIL_HOST_USER", ""):
                    messages.error(request, "No se pudo enviar el correo en este momento. Intenta más tarde.")
                    messages.success(request, generic_msg)
                    return render(request, "core/recuperar.html", {"form": PasswordRecoveryRequestForm()})

                try:
                    token = signer.sign(str(user.ID_Usuario))
                    reset_path = reverse("core:reset_password", args=[token])
                    reset_url = _build_absolute_uri(request, reset_path)

                    subject = "Restablecer contraseña - SWGFV"
                    body = (
                        f"Hola {user.Nombre},\n\n"
                        "Recibimos una solicitud para restablecer tu contraseña.\n"
                        "Da clic en el enlace para crear una nueva contraseña (expira en 30 minutos):\n\n"
                        f"{reset_url}\n\n"
                        "Si tú no solicitaste este cambio, ignora este correo.\n\n"
                        "SWGFV - Fortia PV"
                    )

                    send_mail(
                        subject,
                        body,
                        settings.DEFAULT_FROM_EMAIL,
                        [user.Correo_electronico],
                        fail_silently=False,
                    )
                except Exception:
                    messages.error(request, "No se pudo enviar el correo en este momento. Intenta más tarde.")
                    messages.success(request, generic_msg)
                    return render(request, "core/recuperar.html", {"form": PasswordRecoveryRequestForm()})

            messages.success(request, generic_msg)
            return render(request, "core/recuperar.html", {"form": PasswordRecoveryRequestForm()})

        messages.error(request, "Revisa el formulario e intenta nuevamente.")

    return render(request, "core/recuperar.html", {"form": form})


@require_http_methods(["GET", "POST"])
def reset_password_view(request, token):
    """
    Vista compatible con core/urls.py:
    path("recuperar/<str:token>/", views.reset_password_view, name="reset_password")
    """
    try:
        user_id = signer.unsign(token, max_age=RESET_MAX_AGE_SECONDS)
    except SignatureExpired:
        messages.error(request, "El enlace expiró. Solicita uno nuevo.")
        return redirect("core:recuperar")
    except BadSignature:
        messages.error(request, "Enlace inválido.")
        return redirect("core:recuperar")

    user = Usuario.objects.filter(ID_Usuario=user_id, Activo=True).first()
    if not user:
        messages.error(request, "Enlace inválido o usuario no disponible.")
        return redirect("core:recuperar")

    form = PasswordResetForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            new_pass = form.cleaned_data["new_password"]
            user.set_password(new_pass)
            user.save()
            messages.success(request, "Contraseña actualizada. Ya puedes iniciar sesión.")
            return redirect("core:login")
        messages.error(request, "Revisa el formulario. Hay errores.")

    return render(request, "core/reset_password.html", {"form": form, "email": user.Correo_electronico})


# ------------------------
# DEBUG SESIÓN (opcional)
# ------------------------
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
# MÓDULO PROYECTO (SIN CAMBIOS)
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def proyecto_alta(request):
    session_usuario = request.session.get("usuario")
    session_tipo = request.session.get("tipo")
    session_id_usuario = request.session.get("id_usuario")

    if not session_id_usuario:
        messages.error(request, "Sesión incompleta. Inicia sesión nuevamente.")
        return redirect("core:logout")

    user = Usuario.objects.filter(ID_Usuario=session_id_usuario).first()
    if not user:
        messages.error(request, "No se encontró el usuario en la base de datos. Inicia sesión de nuevo.")
        return redirect("core:logout")

    if not user.Activo:
        messages.error(request, "Tu usuario está inactivo. Contacta al administrador.")
        return redirect("core:logout")

    form = ProyectoCreateForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            proyecto = form.save(commit=False)
            proyecto.ID_Usuario = user
            proyecto.save()
            messages.success(request, "✅ Proyecto registrado correctamente.")
            return redirect("core:proyecto_alta")
        else:
            messages.error(request, "Revisa el formulario e intenta nuevamente.")

    return render(
        request,
        "core/pages/proyecto_alta.html",
        {"form": form, "session_usuario": session_usuario, "session_tipo": session_tipo},
    )


@require_session_login
def proyecto_consulta(request):
    session_tipo = request.session.get("tipo")
    session_id_usuario = request.session.get("id_usuario")

    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.select_related("ID_Usuario").all().order_by("-id")
    else:
        proyectos = (
            Proyecto.objects.select_related("ID_Usuario")
            .filter(ID_Usuario_id=session_id_usuario)
            .order_by("-id")
        )

    return render(
        request,
        "core/pages/proyecto_consulta.html",
        {
            "proyectos": proyectos,
            "session_usuario": request.session.get("usuario"),
            "session_tipo": session_tipo,
        },
    )


@require_admin
@require_http_methods(["GET", "POST"])
def proyecto_modificacion(request):
    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    q_empresa = (request.GET.get("empresa") or "").strip()

    hay_busqueda = bool(q_id or q_nombre or q_empresa)

    proyectos = Proyecto.objects.none()
    seleccionado = None
    form = None

    if hay_busqueda:
        qs = Proyecto.objects.select_related("ID_Usuario")

        if q_id:
            if q_id.isdigit():
                qs = qs.filter(id=int(q_id))
            else:
                messages.error(request, "El ID debe ser numérico.")
                qs = Proyecto.objects.none()

        if q_nombre:
            qs = qs.filter(Nombre_Proyecto__icontains=q_nombre)

        if q_empresa:
            qs = qs.filter(Nombre_Empresa__icontains=q_empresa)

        proyectos = qs.order_by("-id")

        if proyectos.count() == 1:
            seleccionado = proyectos.first()
        elif proyectos.count() == 0:
            messages.error(request, "No se encontraron proyectos con esos criterios.")
        else:
            messages.info(request, "Se encontraron varios proyectos. Selecciona uno.")

    if q_id and q_id.isdigit():
        seleccionado = Proyecto.objects.filter(id=int(q_id)).first()
        if seleccionado:
            if request.method == "POST":
                action = (request.POST.get("action") or "").strip()
                if action == "delete":
                    seleccionado.delete()
                    messages.success(request, "Proyecto eliminado correctamente.")
                    return redirect("core:proyecto_modificacion")

                form = ProyectoUpdateForm(request.POST, instance=seleccionado)
                if form.is_valid():
                    form.save()
                    messages.success(request, "Cambios guardados correctamente.")
                    url = reverse("core:proyecto_modificacion")
                    return HttpResponseRedirect(f"{url}?id={seleccionado.id}")
                messages.error(request, "Revisa el formulario. Hay errores.")
            else:
                form = ProyectoUpdateForm(instance=seleccionado)

    return render(
        request,
        "core/pages/proyecto_modificacion.html",
        {
            "proyectos": proyectos,
            "seleccionado": seleccionado,
            "form": form,
            "q_id": q_id,
            "q_nombre": q_nombre,
            "q_empresa": q_empresa,
            "mostrar_lista": hay_busqueda,
        },
    )


# =========================================================
# USUARIOS (SIN CAMBIOS)
# =========================================================
@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_alta(request):
    form = UsuarioCreateForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save(commit=False)
            obj.set_password(form.cleaned_data["password"])
            obj.save()
            messages.success(request, "Usuario dado de alta correctamente.")
            return redirect("core:gestion_usuarios_alta")
        messages.error(request, "Revisa el formulario. Hay errores.")

    return render(request, "core/pages/gestion_usuarios_alta.html", {"form": form})


@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_modificacion(request):
    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    q_ap = (request.GET.get("ap") or "").strip()
    q_am = (request.GET.get("am") or "").strip()

    hay_busqueda = bool(q_id or q_nombre or q_ap or q_am)
    usuarios = Usuario.objects.none()

    seleccionado = None
    form = None

    if hay_busqueda:
        if q_id:
            if not q_id.isdigit():
                messages.error(request, "El ID debe ser numérico.")
                usuarios = Usuario.objects.none()
            else:
                try:
                    seleccionado = Usuario.objects.get(ID_Usuario=int(q_id))
                    usuarios = Usuario.objects.filter(ID_Usuario=int(q_id))
                except Usuario.DoesNotExist:
                    messages.error(request, "Usuario no encontrado por ID.")
                    usuarios = Usuario.objects.none()
        else:
            qs = Usuario.objects.all()
            if q_nombre:
                qs = qs.filter(Nombre__icontains=q_nombre)
            if q_ap:
                qs = qs.filter(Apellido_Paterno__icontains=q_ap)
            if q_am:
                qs = qs.filter(Apellido_Materno__icontains=q_am)

            usuarios = qs.order_by("ID_Usuario")

            if usuarios.count() == 1:
                seleccionado = usuarios.first()
            elif usuarios.count() == 0:
                messages.error(request, "No se encontró usuario con esos datos.")
            else:
                messages.info(request, "Se encontraron varios resultados. Selecciona desde la lista.")

    if seleccionado:
        if request.method == "POST":
            action = (request.POST.get("action") or "").strip()

            if action == "deactivate":
                seleccionado.Activo = False
                seleccionado.save()
                messages.success(request, "Usuario desactivado correctamente.")
                url = reverse("core:gestion_usuarios_modificacion")
                return HttpResponseRedirect(f"{url}?id={seleccionado.ID_Usuario}")

            form = UsuarioUpdateForm(request.POST, instance=seleccionado)
            if form.is_valid():
                obj = form.save(commit=False)
                new_pass = form.cleaned_data.get("new_password")
                if new_pass:
                    obj.set_password(new_pass)
                obj.save()

                messages.success(request, "Cambios guardados correctamente.")
                url = reverse("core:gestion_usuarios_modificacion")
                return HttpResponseRedirect(f"{url}?id={obj.ID_Usuario}")

            messages.error(request, "Revisa el formulario. Hay errores.")
        else:
            form = UsuarioUpdateForm(instance=seleccionado)

    return render(
        request,
        "core/pages/gestion_usuarios_modificacion.html",
        {
            "usuarios": usuarios,
            "seleccionado": seleccionado,
            "form": form,
            "q_id": q_id,
            "q_nombre": q_nombre,
            "q_ap": q_ap,
            "q_am": q_am,
            "mostrar_lista": hay_busqueda,
        },
    )
