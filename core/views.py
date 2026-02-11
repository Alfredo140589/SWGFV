# core/views.py
import random

from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.core import signing
from django.core.mail import get_connection, EmailMessage
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
# CAPTCHA helper
# =========================================================
def _new_captcha(request):
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    request.session["captcha_a"] = a
    request.session["captcha_b"] = b
    request.session["captcha_answer"] = str(a + b)
    request.session.modified = True
    return f"{a} + {b} = ?"


# =========================================================
# Password reset token helpers (firma + expiración)
# =========================================================
RESET_SALT = "swgfv-reset-v1"
RESET_MAX_AGE_SECONDS = 15 * 60  # 15 min


def _get_user_password_hash(u: Usuario) -> str:
    # Cambia automáticamente cuando cambias la contraseña => invalida tokens viejos
    return (getattr(u, "password", "") or "").strip()


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

    # captcha question
    if "captcha_answer" not in request.session:
        captcha_question = _new_captcha(request)
    else:
        captcha_question = f"{request.session.get('captcha_a')} + {request.session.get('captcha_b')} = ?"

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
        # ✅ Validación directa (evita mismatch con LoginForm)
        usuario_input = (request.POST.get("usuario") or "").strip()
        password = request.POST.get("password") or ""
        captcha_input = (request.POST.get("captcha") or "").strip()

        if not usuario_input:
            messages.error(request, "Ingresa tu usuario/correo.")
            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"captcha_question": captcha_question})

        # bloqueado?
        now_ts = int(timezone.now().timestamp())
        locked_until = _get_locked_until(usuario_input)
        if locked_until and now_ts < int(locked_until):
            remaining = int(locked_until) - now_ts
            minutes = max(1, (remaining + 59) // 60)
            messages.error(request, f"Cuenta bloqueada temporalmente. Intenta de nuevo en {minutes} minuto(s).")
            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"captcha_question": captcha_question})

        if not password:
            messages.error(request, "Ingresa tu contraseña.")
            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"captcha_question": captcha_question})

        # validar captcha
        expected = (request.session.get("captcha_answer") or "").strip()
        if not expected or captcha_input != expected:
            fails = _get_fails(usuario_input) + 1
            _set_fails(usuario_input, fails)

            if fails >= 3:
                _set_locked_until(usuario_input, now_ts + (30 * 60))
                messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
            else:
                messages.error(request, f"Captcha incorrecto. Intento {fails}/3.")

            captcha_question = _new_captcha(request)
            return render(request, "core/login.html", {"captcha_question": captcha_question})

        # autenticar contra core.Usuario
        u = authenticate_local(usuario_input, password)

        if u:
            _reset_fails(usuario_input)

            request.session["usuario"] = u.Correo_electronico
            request.session["tipo"] = u.Tipo
            request.session["id_usuario"] = u.ID_Usuario

            # ✅ fuerza guardado de sesión
            request.session.modified = True
            request.session.save()

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
        return render(request, "core/login.html", {"captcha_question": captcha_question})

    return render(request, "core/login.html", {"captcha_question": captcha_question})


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
# RECUPERAR (PÚBLICO) - TOKEN POR LINK + SMTP CON TIMEOUT
# =========================================================
@require_http_methods(["GET", "POST"])
def recuperar_view(request):
    form = PasswordRecoveryRequestForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            email = form.cleaned_data["email"].strip()

            # Siempre mensaje genérico (seguridad)
            messages.success(
                request,
                "Si el correo está registrado, enviaremos un enlace para restablecer tu contraseña."
            )

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

                # ✅ ENVÍO SEGURO: conexión explícita con timeout (evita 500/worker timeout)
                try:
                    timeout = int(getattr(settings, "EMAIL_TIMEOUT", 15))
                    connection = get_connection(timeout=timeout)

                    msg = EmailMessage(
                        subject=subject,
                        body=body,
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@swgfv.local"),
                        to=[email],
                        connection=connection,
                    )
                    msg.send(fail_silently=False)

                except Exception:
                    # No mostramos error al usuario por seguridad; revisa logs si falla
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
# DEBUG (opcional)
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
# PROYECTOS (TU IMPLEMENTACIÓN COMPLETA)
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
# USUARIOS (TU IMPLEMENTACIÓN COMPLETA)
# =========================================================
@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_alta(request):
    form = UsuarioCreateForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save(commit=False)
            # UsuarioCreateForm ya hace set_password en save(), pero lo dejamos seguro:
            if form.cleaned_data.get("password"):
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


# =========================================================
# CUENTA (si existe tu template)
# =========================================================
@require_session_login
def cuenta_view(request):
    return render(request, "core/pages/cuenta.html")

# ==========================
# PLACEHOLDERS PARA PÁGINAS DEL MENÚ
# (evita NoReverseMatch y evita que "parezca" que el login no funciona)
# ==========================

@require_session_login
def dimensionamiento_calculo_modulos(request):
    return render(request, "core/pages/dimensionamiento_calculo_modulos.html")

@require_session_login
def dimensionamiento_dimensionamiento(request):
    return render(request, "core/pages/dimensionamiento_dimensionamiento.html")

@require_session_login
def calculo_dc(request):
    return render(request, "core/pages/calculo_dc.html")

@require_session_login
def calculo_ac(request):
    return render(request, "core/pages/calculo_ac.html")

@require_session_login
def calculo_caida_tension(request):
    return render(request, "core/pages/calculo_caida_tension.html")

@require_session_login
def recursos_tablas(request):
    return render(request, "core/pages/recursos_tablas.html")

@require_session_login
def recursos_conceptos(request):
    return render(request, "core/pages/recursos_conceptos.html")

@require_session_login
def recursos_alta_concepto(request):
    return render(request, "core/pages/recursos_alta_concepto.html")

@require_session_login
def recursos_alta_tabla(request):
    return render(request, "core/pages/recursos_alta_tabla.html")

@require_session_login
def recursos_modificacion_concepto(request):
    return render(request, "core/pages/recursos_modificacion_concepto.html")

@require_session_login
def recursos_modificacion_tabla(request):
    return render(request, "core/pages/recursos_modificacion_tabla.html")
