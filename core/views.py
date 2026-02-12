# core/views.py
import random
import csv
from datetime import timedelta
import logging

from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.core import signing
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Q

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
from django.contrib.staticfiles import finders
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

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
from .models import Usuario, Proyecto, LoginLock, AuditLog

logger = logging.getLogger(__name__)


# =========================================================
# HELPER: IP + BITÁCORA (NO DEBE CAUSAR 500)
# =========================================================
def _get_client_ip(request) -> str:
    try:
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return (xff.split(",")[0] or "").strip()
        return (request.META.get("REMOTE_ADDR") or "").strip()
    except Exception:
        return ""


def log_event(request, action: str, message: str = "", target_model: str = "", target_id=None):
    """
    Guarda evento en bitácora (AuditLog).
    IMPORTANTE: blindada para que NUNCA cause error 500.
    """
    try:
        actor_email = (request.session.get("usuario") or "").strip()
        actor_tipo = (request.session.get("tipo") or "").strip()
        actor_user_id = request.session.get("id_usuario")

        # Nota: tus migraciones dejaron actor_user_id (no actor_id)
        AuditLog.objects.create(
            actor_user_id=int(actor_user_id) if str(actor_user_id).isdigit() else None,
            actor_email=actor_email[:150],
            actor_tipo=actor_tipo[:30],
            action=(action or "").strip()[:60],
            message=(message or "").strip()[:255],
            target_model=(target_model or "").strip()[:60],
            target_id=str(target_id) if target_id is not None else "",
            ip_address=_get_client_ip(request),
        )
    except Exception:
        # Si falla la bitácora, NO tumba el sistema
        return


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
    """
    Login con captcha firmado + lock por usuario (LoginLock en BD).
    Parche: evita 500, registra el traceback en logs si algo explota.
    """
    try:
        if request.session.get("usuario") and request.session.get("tipo"):
            return redirect("core:menu_principal")

        form = LoginForm(request.POST or None)

        captcha_question, captcha_token = _new_captcha_signed()

        LOCK_MINUTES = 30
        MAX_FAILS = 3

        def _norm_user_key(usuario: str) -> str:
            return (usuario or "").strip().lower()

        def _get_lock(usuario: str) -> LoginLock:
            key = _norm_user_key(usuario)
            obj, _ = LoginLock.objects.get_or_create(usuario_key=key)
            return obj

        def _is_locked(usuario: str):
            lk = _get_lock(usuario)
            if lk.is_locked():
                return True, int(lk.remaining_minutes())
            return False, 0

        def _register_fail(usuario: str) -> int:
            lk = _get_lock(usuario)

            if lk.is_locked():
                return int(lk.fails or 0)

            lk.fails = int(lk.fails or 0) + 1
            if lk.fails >= MAX_FAILS:
                lk.locked_until = timezone.now() + timedelta(minutes=LOCK_MINUTES)

            lk.save(update_fields=["fails", "locked_until"])
            return int(lk.fails or 0)

        def _reset_lock(usuario: str):
            lk = _get_lock(usuario)
            lk.fails = 0
            lk.locked_until = None
            lk.save(update_fields=["fails", "locked_until"])

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

            locked, minutes = _is_locked(usuario_input)
            if locked:
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

            token = (request.POST.get("captcha_token") or "").strip()
            expected = _read_captcha_token(token)
            provided = (form.cleaned_data.get("captcha") or "").strip()

            if not expected or provided != expected:
                fails = _register_fail(usuario_input)

                if fails >= MAX_FAILS:
                    messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
                else:
                    messages.error(request, f"Captcha incorrecto. Intento {fails}/{MAX_FAILS}.")

                captcha_question, captcha_token = _new_captcha_signed()
                return render(
                    request,
                    "core/login.html",
                    {"form": form, "captcha_question": captcha_question, "captcha_token": captcha_token},
                )

            password = form.cleaned_data["password"]
            u = authenticate_local(usuario_input, password)

            if u:
                _reset_lock(usuario_input)

                request.session["usuario"] = u.Correo_electronico
                request.session["tipo"] = u.Tipo
                request.session["id_usuario"] = u.ID_Usuario
                request.session.modified = True

                return redirect("core:menu_principal")

            fails = _register_fail(usuario_input)

            if fails >= MAX_FAILS:
                messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
            else:
                messages.error(request, f"Usuario o contraseña incorrectos. Intento {fails}/{MAX_FAILS}.")

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

    except Exception:
        logger.exception("ERROR EN LOGIN_VIEW (POST/GET) - detalle:")

        form = LoginForm(request.POST or None)
        captcha_question, captcha_token = _new_captcha_signed()
        messages.error(request, "Ocurrió un error inesperado al iniciar sesión. Intenta de nuevo.")
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
    log_event(request, "LOGOUT", "Cierre de sesión")
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
                reset_link = request.build_absolute_uri(reverse("core:password_reset_confirm", kwargs={"token": token}))

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

                log_event(request, "PASSWORD_RECOVERY_REQUEST", f"Solicitó recuperación para {email}", "Usuario", u.ID_Usuario)

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

            log_event(request, "PASSWORD_CHANGED", "Cambió contraseña por recuperación", "Usuario", u.ID_Usuario)

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
# PROYECTOS
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

            log_event(request, "PROJECT_CREATED", f"Creó proyecto: {proyecto.Nombre_Proyecto}", "Proyecto", proyecto.id)

            messages.success(request, "✅ Proyecto registrado correctamente.")
            return redirect("core:proyecto_alta")
        messages.error(request, "Revisa el formulario e intenta nuevamente.")

    return render(request, "core/pages/proyecto_alta.html", {"form": form})


@require_session_login
@require_http_methods(["GET"])
def proyecto_consulta(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    q_empresa = (request.GET.get("empresa") or "").strip()
    q_usuario = (request.GET.get("usuario") or "").strip()

    mostrar_lista = any([q_id, q_nombre, q_empresa, q_usuario])

    if session_tipo == "Administrador":
        qs = Proyecto.objects.select_related("ID_Usuario").all().order_by("-id")
    else:
        qs = Proyecto.objects.select_related("ID_Usuario").filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    if q_id:
        if q_id.isdigit():
            qs = qs.filter(id=int(q_id))
        else:
            qs = Proyecto.objects.none()

    if q_nombre:
        qs = qs.filter(Nombre_Proyecto__icontains=q_nombre)

    if q_empresa:
        qs = qs.filter(Nombre_Empresa__icontains=q_empresa)

    if q_usuario and session_tipo == "Administrador":
        qs = qs.filter(ID_Usuario__Correo_electronico__icontains=q_usuario)

    context = {
        "proyectos": qs,
        "mostrar_lista": mostrar_lista,
        "q_id": q_id,
        "q_nombre": q_nombre,
        "q_empresa": q_empresa,
        "q_usuario": q_usuario,
        "es_admin": (session_tipo == "Administrador"),
    }
    return render(request, "core/pages/proyecto_consulta.html", context)


@require_session_login
@require_http_methods(["GET", "POST"])
def proyecto_modificacion(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    q_empresa = (request.GET.get("empresa") or "").strip()
    q_usuario = (request.GET.get("usuario") or "").strip()

    edit_mode = (request.GET.get("edit") or "").strip() == "1"
    mostrar_lista = any([q_id, q_nombre, q_empresa, q_usuario])

    if session_tipo == "Administrador":
        qs = Proyecto.objects.select_related("ID_Usuario").all().order_by("-id")
    else:
        qs = Proyecto.objects.select_related("ID_Usuario").filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    if mostrar_lista:
        if q_id:
            if q_id.isdigit():
                qs = qs.filter(id=int(q_id))
            else:
                qs = Proyecto.objects.none()

        if q_nombre:
            qs = qs.filter(Nombre_Proyecto__icontains=q_nombre)

        if q_empresa:
            qs = qs.filter(Nombre_Empresa__icontains=q_empresa)

        if q_usuario and session_tipo == "Administrador":
            qs = qs.filter(ID_Usuario__Correo_electronico__icontains=q_usuario)

        proyectos = qs
    else:
        proyectos = Proyecto.objects.none()

    seleccionado = None
    form = None

    if q_id.isdigit():
        seleccionado = Proyecto.objects.select_related("ID_Usuario").filter(id=int(q_id)).first()
        if seleccionado:
            if session_tipo != "Administrador" and int(seleccionado.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para ver/modificar este proyecto.")
                return redirect("core:proyecto_modificacion")

            form = ProyectoUpdateForm(instance=seleccionado)

    if request.method == "POST":
        post_id = (request.GET.get("id") or "").strip()
        if not post_id.isdigit():
            messages.error(request, "Selecciona un proyecto válido.")
            return redirect("core:proyecto_modificacion")

        seleccionado = Proyecto.objects.select_related("ID_Usuario").filter(id=int(post_id)).first()
        if not seleccionado:
            messages.error(request, "El proyecto ya no existe.")
            return redirect("core:proyecto_modificacion")

        if session_tipo != "Administrador" and int(seleccionado.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para modificar este proyecto.")
            return redirect("core:proyecto_modificacion")

        if not edit_mode:
            messages.error(request, "Para editar, primero presiona ✏️ Editar.")
            return redirect(f"{reverse('core:proyecto_modificacion')}?id={seleccionado.id}")

        form = ProyectoUpdateForm(request.POST, instance=seleccionado)
        if form.is_valid():
            form.save()
            log_event(request, "PROJECT_UPDATED", f"Actualizó proyecto: {seleccionado.Nombre_Proyecto}", "Proyecto", seleccionado.id)
            messages.success(request, "Proyecto actualizado correctamente.")
            return redirect(f"{reverse('core:proyecto_modificacion')}?id={seleccionado.id}")

        messages.error(request, "Revisa el formulario. Hay errores.")

    context = {
        "proyectos": proyectos,
        "mostrar_lista": mostrar_lista,
        "q_id": q_id,
        "q_nombre": q_nombre,
        "q_empresa": q_empresa,
        "q_usuario": q_usuario,
        "es_admin": (session_tipo == "Administrador"),
        "seleccionado": seleccionado,
        "form": form,
        "edit_mode": edit_mode,
    }
    return render(request, "core/pages/proyecto_modificacion.html", context)


# =========================================================
# USUARIOS
# =========================================================
@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_alta(request):
    form = UsuarioCreateForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            nuevo = form.save()
            log_event(request, "USER_CREATED", f"Creó usuario: {nuevo.Correo_electronico}", "Usuario", nuevo.ID_Usuario)
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

    edit_mode = (request.GET.get("edit") or "").strip() == "1"
    mostrar_lista = any([q_id, q_nombre, q_ap, q_am])

    usuarios = Usuario.objects.none()
    if mostrar_lista:
        qs = Usuario.objects.all().order_by("ID_Usuario")

        if q_id.isdigit():
            qs = qs.filter(ID_Usuario=int(q_id))
        elif q_id:
            qs = Usuario.objects.none()

        if q_nombre:
            qs = qs.filter(Nombre__icontains=q_nombre)
        if q_ap:
            qs = qs.filter(Apellido_Paterno__icontains=q_ap)
        if q_am:
            qs = qs.filter(Apellido_Materno__icontains=q_am)

        usuarios = qs

    seleccionado = None
    form = None

    if q_id.isdigit():
        seleccionado = Usuario.objects.filter(ID_Usuario=int(q_id)).first()
        if seleccionado:
            form = UsuarioUpdateForm(instance=seleccionado)

    if request.method == "POST":
        post_id = (request.GET.get("id") or "").strip()

        if not post_id.isdigit():
            messages.error(request, "Selecciona un usuario válido para modificar.")
            return redirect("core:gestion_usuarios_modificacion")

        seleccionado = Usuario.objects.filter(ID_Usuario=int(post_id)).first()
        if not seleccionado:
            messages.error(request, "El usuario seleccionado ya no existe.")
            return redirect("core:gestion_usuarios_modificacion")

        action = (request.POST.get("action") or "").strip().lower()
        current_user_id = request.session.get("id_usuario")

        if action == "activate":
            seleccionado.Activo = True
            seleccionado.save()
            log_event(request, "USER_ACTIVATED", f"Activó usuario: {seleccionado.Correo_electronico}", "Usuario", seleccionado.ID_Usuario)
            messages.success(request, "Usuario activado correctamente.")
            return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

        if action == "deactivate":
            if current_user_id and int(current_user_id) == int(seleccionado.ID_Usuario):
                messages.error(request, "No puedes desactivarte a ti mismo estando en sesión.")
                return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

            seleccionado.Activo = False
            seleccionado.save()
            log_event(request, "USER_DEACTIVATED", f"Desactivó usuario: {seleccionado.Correo_electronico}", "Usuario", seleccionado.ID_Usuario)
            messages.success(request, "Usuario desactivado correctamente.")
            return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

        if action == "delete":
            if current_user_id and int(current_user_id) == int(seleccionado.ID_Usuario):
                messages.error(request, "No puedes eliminar tu propio usuario estando en sesión.")
                return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

            correo = seleccionado.Correo_electronico
            log_event(request, "USER_DELETED", f"Eliminó usuario: {correo}", "Usuario", seleccionado.ID_Usuario)
            seleccionado.delete()
            messages.success(request, f"Usuario eliminado correctamente: {correo}")
            return redirect("core:gestion_usuarios_modificacion")

        if not edit_mode:
            messages.error(request, "Para editar, primero presiona ✏️ Editar.")
            return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

        form = UsuarioUpdateForm(request.POST, instance=seleccionado)

        if form.is_valid():
            email = (form.cleaned_data.get("Correo_electronico") or "").strip().lower()

            if Usuario.objects.filter(Correo_electronico__iexact=email).exclude(ID_Usuario=seleccionado.ID_Usuario).exists():
                form.add_error("Correo_electronico", "Ya existe otro usuario con ese correo.")
            else:
                obj = form.save(commit=False)
                obj.Correo_electronico = email
                obj.save()
                log_event(request, "USER_UPDATED", f"Actualizó usuario: {obj.Correo_electronico}", "Usuario", obj.ID_Usuario)
                messages.success(request, "Usuario actualizado correctamente.")
                return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

        messages.error(request, "Revisa el formulario. Hay errores.")

    context = {
        "q_id": q_id,
        "q_nombre": q_nombre,
        "q_ap": q_ap,
        "q_am": q_am,
        "mostrar_lista": mostrar_lista,
        "usuarios": usuarios,
        "seleccionado": seleccionado,
        "form": form,
        "edit_mode": edit_mode,
    }
    return render(request, "core/pages/gestion_usuarios_modificacion.html", context)


@require_session_login
def cuenta_view(request):
    return render(request, "core/pages/cuenta.html")


# ==========================
# PLACEHOLDERS DEL MENÚ
# ==========================
from django.template import TemplateDoesNotExist


def _render_menu_page(request, template_path: str, title: str):
    try:
        return render(request, template_path, {"title": title})
    except TemplateDoesNotExist:
        return render(request, "core/menu_principal.html", {"title": title, "messages": []})


@require_session_login
def dimensionamiento_calculo_modulos(request):
    return _render_menu_page(request, "core/pages/dimensionamiento_calculo_modulos.html", "Cálculo de Módulos")


@require_session_login
def dimensionamiento_dimensionamiento(request):
    return _render_menu_page(request, "core/pages/dimensionamiento_dimensionamiento.html", "Dimensionamiento")


@require_session_login
def calculo_dc(request):
    return _render_menu_page(request, "core/pages/calculo_dc.html", "Cálculo DC")


@require_session_login
def calculo_ac(request):
    return _render_menu_page(request, "core/pages/calculo_ac.html", "Cálculo AC")


@require_session_login
def calculo_caida_tension(request):
    return _render_menu_page(request, "core/pages/calculo_caida_tension.html", "Caída de Tensión")


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


# ==========================
# PDF PROYECTO
# ==========================
@require_session_login
@require_http_methods(["GET"])
def proyecto_pdf(request, proyecto_id: int):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    proyecto = Proyecto.objects.select_related("ID_Usuario").filter(id=proyecto_id).first()
    if not proyecto:
        messages.error(request, "Proyecto no encontrado.")
        return redirect("core:proyecto_consulta")

    if session_tipo != "Administrador":
        if not session_id_usuario or int(proyecto.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para descargar este proyecto.")
            return redirect("core:proyecto_consulta")

    filename = f"SWGFV_Proyecto_{proyecto.id}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(
        response,
        pagesize=letter,
        leftMargin=2.0 * cm,
        rightMargin=2.0 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        title=f"Proyecto {proyecto.id}",
        author="SWGFV",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SWGFVTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=colors.HexColor("#001F3F"),
        spaceAfter=10,
    )
    sub_style = ParagraphStyle(
        "SWGFVSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.HexColor("#333333"),
        spaceAfter=10,
    )

    elements = []

    logo_path = finders.find("core/img/logo1.png")
    if logo_path:
        try:
            img = RLImage(logo_path, width=3.0 * cm, height=3.0 * cm)
            elements.append(img)
            elements.append(Spacer(1, 0.3 * cm))
        except Exception:
            pass

    elements.append(Paragraph("SWGFV - Ficha del Proyecto", title_style))

    generado_por = request.session.get("usuario", "")
    tipo = request.session.get("tipo", "")
    fecha = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    elements.append(Paragraph(f"<b>Generado por:</b> {generado_por} ({tipo})", sub_style))
    elements.append(Paragraph(f"<b>Fecha:</b> {fecha}", sub_style))
    elements.append(Spacer(1, 0.2 * cm))

    data = [
        ["Campo", "Valor"],
        ["ID", str(proyecto.id)],
        ["Nombre del proyecto", proyecto.Nombre_Proyecto or "—"],
        ["Empresa", proyecto.Nombre_Empresa or "—"],
        ["Dirección", proyecto.Direccion or "—"],
        ["Coordenadas", proyecto.Coordenadas or "—"],
        ["Voltaje nominal", proyecto.Voltaje_Nominal or "—"],
        ["Número de fases", str(proyecto.Numero_Fases)],
        ["Usuario asociado", getattr(proyecto.ID_Usuario, "Correo_electronico", "—") or "—"],
    ]

    table = Table(data, colWidths=[5.2 * cm, 11.5 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#001F3F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#111111")),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#B0B7C3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#F3F6FA")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 0.6 * cm))

    doc.build(elements)
    return response


# ==========================
# EXPORTAR USUARIOS CSV
# ==========================
@require_admin
@require_http_methods(["GET"])
def usuarios_export_csv(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="SWGFV_Usuarios.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow(["ID", "Nombre", "Apellido Paterno", "Apellido Materno", "Telefono", "Correo", "Tipo", "Activo"])

    for u in Usuario.objects.all().order_by("ID_Usuario"):
        writer.writerow([
            u.ID_Usuario,
            u.Nombre,
            u.Apellido_Paterno,
            u.Apellido_Materno,
            u.Telefono,
            u.Correo_electronico,
            u.Tipo,
            "Si" if u.Activo else "No",
        ])

    log_event(request, "USERS_EXPORT_CSV", "Descargó listado de usuarios en CSV", "Usuario", "")
    return response


# ==========================
# EXPORTAR USUARIOS PDF
# ==========================
@require_admin
@require_http_methods(["GET"])
def usuarios_export_pdf(request):
    filename = "SWGFV_Usuarios.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(
        response,
        pagesize=letter,
        leftMargin=2.0 * cm,
        rightMargin=2.0 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        title="Usuarios SWGFV",
        author="SWGFV",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleSWGFV",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=colors.HexColor("#001F3F"),
        spaceAfter=10,
    )

    elements = []

    logo_path = finders.find("core/img/logo1.png")
    if logo_path:
        try:
            elements.append(RLImage(logo_path, width=3.0 * cm, height=3.0 * cm))
            elements.append(Spacer(1, 0.3 * cm))
        except Exception:
            pass

    elements.append(Paragraph("SWGFV - Listado de Usuarios", title_style))
    elements.append(Spacer(1, 0.3 * cm))

    data = [["ID", "Nombre completo", "Correo", "Tipo", "Activo"]]
    for u in Usuario.objects.all().order_by("ID_Usuario"):
        nombre = f"{u.Nombre} {u.Apellido_Paterno} {u.Apellido_Materno}"
        data.append([str(u.ID_Usuario), nombre, u.Correo_electronico, u.Tipo, "Sí" if u.Activo else "No"])

    table = Table(data, colWidths=[1.2 * cm, 6.0 * cm, 5.5 * cm, 2.6 * cm, 1.8 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#001F3F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#B0B7C3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#F3F6FA")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(table)
    doc.build(elements)

    log_event(request, "USERS_EXPORT_PDF", "Descargó listado de usuarios en PDF", "Usuario", "")
    return response


# ==========================
# ACTIVIDAD / BITÁCORA
# ==========================
@require_admin
@require_http_methods(["GET"])
def usuarios_actividad(request):
    q_user = (request.GET.get("user") or "").strip()
    q_action = (request.GET.get("action") or "").strip()
    q_text = (request.GET.get("q") or "").strip()

    logs = AuditLog.objects.all()

    if q_user:
        logs = logs.filter(actor_email__icontains=q_user)

    if q_action:
        logs = logs.filter(action__icontains=q_action)

    if q_text:
        logs = logs.filter(
            Q(message__icontains=q_text) |
            Q(target_model__icontains=q_text) |
            Q(target_id__icontains=q_text)
        )

    logs = logs.order_by("-created_at")[:300]

    context = {"logs": logs, "q_user": q_user, "q_action": q_action, "q_text": q_text}
    return render(request, "core/pages/usuarios_actividad.html", context)
