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
from core.forms import PanelSolarCreateForm
from core.forms import InversorCreateForm, MicroInversorCreateForm

from .forms import (
    LoginForm,
    UsuarioCreateForm,
    UsuarioUpdateForm,
    ProyectoCreateForm,
    ProyectoUpdateForm,
    PasswordRecoveryRequestForm,
    PasswordResetForm,
    NumeroPanelesForm,
)
from .auth_local import authenticate_local
from .decorators import require_session_login, require_admin
from .models import (
    Usuario, Proyecto, LoginLock, AuditLog,
    Irradiancia, PanelSolar, NumeroPaneles, ResultadoPaneles,
    Inversor, MicroInversor,
    Dimensionamiento, DimensionamientoDetalle
)

logger = logging.getLogger(__name__)


# =========================================================
# HELPER: IP + BITÁCORA (NO DEBE CAUSAR 500)
# =========================================================
def log_event(request, action: str, message: str, target_model: str = "", target_id=None):
    """
    Guarda evento en bitácora (AuditLog).
    IMPORTANTE: Está blindada para que NUNCA cause error 500.
    """
    try:
        actor_email = (request.session.get("usuario") or "").strip()
        actor_tipo = (request.session.get("tipo") or "").strip()
        actor_user_id = request.session.get("id_usuario")

        AuditLog.objects.create(
            actor_email=actor_email,
            actor_tipo=actor_tipo,
            actor_user_id=actor_user_id if actor_user_id else None,
            action=(action or "").strip()[:80],
            message=(message or "").strip()[:255],
            target_model=(target_model or "").strip()[:50],
            target_id=str(target_id) if target_id is not None else "",
        )
    except Exception:
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
            email = (form.cleaned_data.get("email") or "").strip()

            # Mensaje genérico para NO filtrar si el correo existe
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

                try:
                    send_mail(
                        subject,
                        body,
                        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@swgfv.local"),
                        [email],
                        fail_silently=False,
                    )
                except Exception:
                    # No rompemos el flujo aunque falle el correo
                    pass

                # Bitácora (no debe romper si falla)
                log_event(
                    request,
                    "PASSWORD_RECOVERY_REQUEST",
                    f"Solicitó recuperación para {email}",
                    "Usuario",
                    u.ID_Usuario
                )

            return redirect("core:recuperar")

        messages.error(request, "Revisa el formulario e intenta nuevamente.")

    # ✅ SIEMPRE retorna una respuesta
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

    # ✅ Instancia del form (POST o vacío)
    form = ProyectoCreateForm(request.POST or None)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        # ✅ CANCELAR: no guarda nada y limpia formulario
        if action == "cancel":
            messages.info(request, "Operación cancelada. El formulario fue limpiado.")
            return redirect("core:proyecto_alta")  # recarga por GET y queda vacío

        # ✅ GUARDAR: flujo normal
        if form.is_valid():
            proyecto = form.save(commit=False)
            proyecto.ID_Usuario = user
            proyecto.save()

            log_event(
                request,
                "PROJECT_CREATED",
                f"Creó proyecto: {proyecto.Nombre_Proyecto}",
                "Proyecto",
                proyecto.id
            )

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

    # =========================
    # GET: cargar seleccionado
    # =========================
    if q_id.isdigit():
        seleccionado = Proyecto.objects.select_related("ID_Usuario").filter(id=int(q_id)).first()
        if seleccionado:
            if session_tipo != "Administrador" and int(seleccionado.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para ver/modificar este proyecto.")
                return redirect("core:proyecto_modificacion")

            form = ProyectoUpdateForm(instance=seleccionado)

    # =========================
    # POST: actualizar / eliminar
    # =========================
    if request.method == "POST":
        post_id = (request.GET.get("id") or "").strip()
        if not post_id.isdigit():
            messages.error(request, "Selecciona un proyecto válido.")
            return redirect("core:proyecto_modificacion")

        seleccionado = Proyecto.objects.select_related("ID_Usuario").filter(id=int(post_id)).first()
        if not seleccionado:
            messages.error(request, "El proyecto ya no existe.")
            return redirect("core:proyecto_modificacion")

        # permisos
        if session_tipo != "Administrador" and int(seleccionado.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para modificar este proyecto.")
            return redirect("core:proyecto_modificacion")

        action = (request.POST.get("action") or "").strip().lower()

        # ✅ ELIMINAR (NO requiere edit_mode)
        if action == "delete_project":
            # Seguridad: impedir eliminar si tiene registros relacionados (recomendado)
            tiene_relacionados = (
                NumeroPaneles.objects.filter(proyecto=seleccionado).exists()
                or Dimensionamiento.objects.filter(proyecto=seleccionado).exists()
            )
            if tiene_relacionados:
                messages.error(
                    request,
                    "No se puede eliminar el proyecto porque tiene registros relacionados "
                    "(Número de módulos y/o Dimensionamiento). Primero elimina esos registros."
                )
                return redirect(f"{reverse('core:proyecto_modificacion')}?id={seleccionado.id}")

            nombre = seleccionado.Nombre_Proyecto
            pid = seleccionado.id
            seleccionado.delete()

            log_event(request, "PROJECT_DELETED", f"Eliminó proyecto: {nombre}", "Proyecto", pid)
            messages.success(request, f"✅ Proyecto eliminado: {nombre}")
            return redirect("core:proyecto_modificacion")

        # ✅ UPDATE requiere edit_mode
        if not edit_mode:
            messages.error(request, "Para editar, primero presiona ✏️ Editar.")
            return redirect(f"{reverse('core:proyecto_modificacion')}?id={seleccionado.id}")

        form = ProyectoUpdateForm(request.POST, instance=seleccionado)
        if form.is_valid():
            form.save()
            log_event(
                request,
                "PROJECT_UPDATED",
                f"Actualizó proyecto: {seleccionado.Nombre_Proyecto}",
                "Proyecto",
                seleccionado.id
            )
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


# =========================================================
# VISTA: Dimensionamiento - Cálculo de Módulos (calculo)
# Archivo: core/views.py
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def dimensionamiento_calculo_modulos(request):
    import math

    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    # ✅ Proyectos: admin ve todos, usuario normal solo los suyos
    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.all().order_by("-id")
    else:
        proyectos = Proyecto.objects.filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    # ✅ Catálogos
    irradiancias = Irradiancia.objects.all().order_by("estado", "ciudad")
    paneles = PanelSolar.objects.all().order_by("marca", "modelo")

    # =========================================================
    # LISTAS PARA EL TEMPLATE (meses y bimestres)
    # =========================================================
    meses = [
        {"label": "Ene", "name": "consumo_ene", "key": "ene"},
        {"label": "Feb", "name": "consumo_feb", "key": "feb"},
        {"label": "Mar", "name": "consumo_mar", "key": "mar"},
        {"label": "Abr", "name": "consumo_abr", "key": "abr"},
        {"label": "May", "name": "consumo_may", "key": "may"},
        {"label": "Jun", "name": "consumo_jun", "key": "jun"},
        {"label": "Jul", "name": "consumo_jul", "key": "jul"},
        {"label": "Ago", "name": "consumo_ago", "key": "ago"},
        {"label": "Sep", "name": "consumo_sep", "key": "sep"},
        {"label": "Oct", "name": "consumo_oct", "key": "oct"},
        {"label": "Nov", "name": "consumo_nov", "key": "nov"},
        {"label": "Dic", "name": "consumo_dic", "key": "dic"},
    ]

    bimestres = [
        {"label": "Bim 1", "name": "consumo_bim1", "key": "bim1"},
        {"label": "Bim 2", "name": "consumo_bim2", "key": "bim2"},
        {"label": "Bim 3", "name": "consumo_bim3", "key": "bim3"},
        {"label": "Bim 4", "name": "consumo_bim4", "key": "bim4"},
        {"label": "Bim 5", "name": "consumo_bim5", "key": "bim5"},
        {"label": "Bim 6", "name": "consumo_bim6", "key": "bim6"},
    ]

    # =========================================================
    # ✅ Determinar proyecto seleccionado (GET o POST)
    # =========================================================
    selected_raw = (request.POST.get("proyecto") or request.GET.get("proyecto_id") or "").strip()
    selected_proyecto_id = int(selected_raw) if selected_raw.isdigit() else None

    # =========================================================
    # ✅ GET/POST: cargar np_obj y resultado para mostrar abajo
    # =========================================================
    np_obj = None
    resultado = None

    if selected_proyecto_id:
        np_obj = NumeroPaneles.objects.select_related(
            "proyecto", "irradiancia", "panel"
        ).filter(proyecto_id=selected_proyecto_id).first()

        if np_obj:
            resultado = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()

    # =========================
    # ✅ POST: Guardar + Calcular
    # =========================
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "calcular":
            proyecto_id = (request.POST.get("proyecto") or "").strip()
            tipo_fact = (request.POST.get("tipo_facturacion") or "").strip().lower()
            irradiancia_id = (request.POST.get("irradiancia") or "").strip()
            panel_id = (request.POST.get("panel") or "").strip()
            eficiencia = (request.POST.get("eficiencia") or "").strip()

            # ✅ Eficiencia: SOLO 0.7 o 0.8
            try:
                eff = float(eficiencia)
            except ValueError:
                eff = None

            if eff not in (0.7, 0.8):
                messages.error(request, "Eficiencia inválida. Solo se permite 0.7 o 0.8.")
                return redirect(
                    f"{reverse('core:dimensionamiento_calculo_modulos')}?proyecto_id={proyecto_id}"
                    if str(proyecto_id).isdigit()
                    else reverse("core:dimensionamiento_calculo_modulos")
                )

            if not (proyecto_id and irradiancia_id and panel_id and eficiencia and tipo_fact):
                messages.error(request, "Revisa el formulario. Hay errores.")
                return redirect(reverse("core:dimensionamiento_calculo_modulos"))

            # Normalizar tipo_facturacion para BD
            if tipo_fact == "mensual":
                tipo_fact_db = "MENSUAL"
                consumos = {m["key"]: float(request.POST.get(m["name"]) or 0) for m in meses}
            elif tipo_fact == "bimestral":
                tipo_fact_db = "BIMESTRAL"
                consumos = {f"bim{i}": float(request.POST.get(f"consumo_bim{i}") or 0) for i in range(1, 7)}
            else:
                messages.error(request, "Tipo de facturación inválido.")
                return redirect(reverse("core:dimensionamiento_calculo_modulos"))

            # Validar proyecto + permisos
            proyecto = Proyecto.objects.filter(id=proyecto_id).first()
            if not proyecto:
                messages.error(request, "Proyecto inválido.")
                return redirect(reverse("core:dimensionamiento_calculo_modulos"))

            if session_tipo != "Administrador" and int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para usar este proyecto.")
                return redirect(reverse("core:dimensionamiento_calculo_modulos"))

            irradiancia = Irradiancia.objects.filter(id=irradiancia_id).first()
            panel = PanelSolar.objects.filter(id=panel_id).first()

            if not irradiancia:
                messages.error(request, "Irradiancia inválida.")
                return redirect(reverse("core:dimensionamiento_calculo_modulos"))
            if not panel:
                messages.error(request, "Panel inválido.")
                return redirect(reverse("core:dimensionamiento_calculo_modulos"))

            # Guardar/Actualizar NumeroPaneles
            obj, created = NumeroPaneles.objects.update_or_create(
                proyecto=proyecto,
                defaults={
                    "tipo_facturacion": tipo_fact_db,
                    "irradiancia": irradiancia,
                    "panel": panel,
                    "eficiencia": eff,
                    "consumos": consumos,
                },
            )

            # ResultadoPaneles
            resultado_obj, _ = ResultadoPaneles.objects.get_or_create(
                numero_paneles=obj,
                defaults={
                    "no_modulos": 0,
                    "potencia_total": 0,
                    "generacion_por_periodo": {},
                    "generacion_anual": 0,
                },
            )

            # =========================================================
            # ✅ CÁLCULO REAL
            # =========================================================
            eff = float(obj.eficiencia)  # 0.7 o 0.8
            pot_panel_kw = float(panel.potencia) / 1000.0

            if obj.tipo_facturacion == "MENSUAL":
                consumo_promedio = (sum(float(v) for v in (obj.consumos or {}).values()) / 12.0) if obj.consumos else 0.0
                dias_ref = 30.0
            else:
                consumo_promedio = (sum(float(v) for v in (obj.consumos or {}).values()) / 6.0) if obj.consumos else 0.0
                dias_ref = 60.0

            hsp_ref = float(irradiancia.promedio)
            energia_por_modulo_ref = pot_panel_kw * hsp_ref * eff * dias_ref

            if energia_por_modulo_ref > 0:
                no_modulos = math.ceil((consumo_promedio / energia_por_modulo_ref) * 1.1)
            else:
                no_modulos = 0

            potencia_total = round(no_modulos * pot_panel_kw, 4)

            gen_por_periodo = {}
            if obj.tipo_facturacion == "MENSUAL":
                for k in ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]:
                    insol = float(getattr(irradiancia, k))
                    gen_por_periodo[k] = round(potencia_total * insol * eff * 30.0, 4)
            else:
                mapeo = {"bim1":"feb","bim2":"abr","bim3":"jun","bim4":"ago","bim5":"oct","bim6":"dic"}
                for bim, mes in mapeo.items():
                    insol = float(getattr(irradiancia, mes))
                    gen_por_periodo[bim] = round(potencia_total * insol * eff * 60.0, 4)

            generacion_anual = round(sum(gen_por_periodo.values()), 4)

            resultado_obj.no_modulos = no_modulos
            resultado_obj.potencia_total = potencia_total
            resultado_obj.generacion_por_periodo = gen_por_periodo
            resultado_obj.generacion_anual = generacion_anual
            resultado_obj.save(update_fields=[
                "no_modulos",
                "potencia_total",
                "generacion_por_periodo",
                "generacion_anual",
            ])

            messages.success(request, "✅ Cálculo realizado correctamente.")

            # ✅ redirigir para reconstruir resultados (GET)
            return redirect(f"{reverse('core:dimensionamiento_calculo_modulos')}?proyecto_id={proyecto.id}")

    # =========================================================
    # ✅ SIEMPRE construir TABLA + datos para gráficas (GET o POST)
    # =========================================================
    tabla_periodos = []
    chart_labels = []
    chart_consumo = []
    chart_generacion = []

    if np_obj and resultado:
        cons = np_obj.consumos or {}
        genp = resultado.generacion_por_periodo or {}

        if np_obj.tipo_facturacion == "MENSUAL":
            orden = [
                ("ene","Ene"),("feb","Feb"),("mar","Mar"),("abr","Abr"),("may","May"),("jun","Jun"),
                ("jul","Jul"),("ago","Ago"),("sep","Sep"),("oct","Oct"),("nov","Nov"),("dic","Dic"),
            ]
        else:
            orden = [("bim1","Bim 1"),("bim2","Bim 2"),("bim3","Bim 3"),("bim4","Bim 4"),("bim5","Bim 5"),("bim6","Bim 6")]

        for key, label in orden:
            c = float(cons.get(key, 0) or 0)
            g = float(genp.get(key, 0) or 0)
            tabla_periodos.append({"label": label, "consumo": round(c, 3), "generacion": round(g, 3)})
            chart_labels.append(label)
            chart_consumo.append(round(c, 3))
            chart_generacion.append(round(g, 3))

    # ✅ Render normal (IMPORTANTE: ahora ya va todo al template)
    context = {
        "proyectos": proyectos,
        "irradiancias": irradiancias,
        "paneles": paneles,
        "meses": meses,
        "bimestres": bimestres,

        "np_obj": np_obj,
        "resultado": resultado,
        "selected_proyecto_id": selected_proyecto_id,

        # ✅ tabla + charts (para tu template con json_script)
        "tabla_periodos": tabla_periodos,
        "chart_labels": chart_labels,
        "chart_consumo": chart_consumo,
        "chart_generacion": chart_generacion,
    }
    return render(request, "core/pages/dimensionamiento_calculo_modulos.html", context)
# =========================================================
# CATÁLOGOS: Alta de Panel Solar
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def panel_solar_alta(request):
    session_tipo = (request.session.get("tipo") or "").strip()

    # ✅ Seguridad recomendada: solo Admin puede insertar catálogos
    if session_tipo != "Administrador":
        messages.error(request, "No tienes permisos para agregar paneles solares.")
        return redirect("core:dimensionamiento_calculo_modulos")

    next_url = (request.GET.get("next") or "").strip()
    form = PanelSolarCreateForm(request.POST or None)

    # ✅ Siguiente id_modulo automático (último + 1)
    ultimo = PanelSolar.objects.order_by("-id_modulo").first()
    next_id = (ultimo.id_modulo + 1) if ultimo else 1

    # ✅ Prefill solo en GET (form vacío)
    if request.method == "GET":
        form.initial["id_modulo"] = next_id

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        # ✅ Cancelar: no guarda nada
        if action == "cancel":
            messages.info(request, "Operación cancelada. No se guardó ningún panel.")
            return redirect(next_url or "core:dimensionamiento_calculo_modulos")

        # ✅ Guardar
        if form.is_valid():
            panel = form.save(commit=False)

            # ✅ Fuerza id_modulo automático en servidor
            ultimo = PanelSolar.objects.order_by("-id_modulo").first()
            panel.id_modulo = (ultimo.id_modulo + 1) if ultimo else 1

            panel.save()

            messages.success(request, f"✅ Panel agregado: {panel.marca} {panel.modelo}")
            return redirect(next_url or "core:dimensionamiento_calculo_modulos")

        messages.error(request, "Revisa el formulario. Hay errores.")

    return render(request, "core/pages/panel_solar_alta.html", {"form": form, "next_url": next_url})

# =========================================================
# DIMENSIONAMIENTO (por inversor)
# Archivo: core/views.py
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def dimensionamiento_dimensionamiento(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    # ✅ Proyectos por permisos
    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.all().order_by("-id")
    else:
        proyectos = Proyecto.objects.filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    # ✅ Proyecto seleccionado (GET o POST)
    selected_raw = (request.POST.get("proyecto") or request.GET.get("proyecto_id") or "").strip()
    selected_proyecto_id = int(selected_raw) if selected_raw.isdigit() else None

    proyecto = None
    np_obj = None
    resultado = None
    potencia_total = None

    dim = None
    detalles = []

    # ✅ Catálogos
    inversores = Inversor.objects.all().order_by("marca", "modelo")
    micro_inversores = MicroInversor.objects.all().order_by("marca", "modelo")

    # =========================================================
    # ✅ GET: cargar datos del proyecto + datos guardados
    # =========================================================
    if selected_proyecto_id:
        proyecto = Proyecto.objects.filter(id=selected_proyecto_id).first()

        # permisos
        if proyecto and session_tipo != "Administrador":
            if int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para acceder a ese proyecto.")
                return redirect(reverse("core:dimensionamiento_dimensionamiento"))

        if proyecto:
            # traer numero paneles y resultado
            np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
            if np_obj:
                resultado = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()

            # ✅ Potencia total (kW) desde ResultadoPaneles
            potencia_total = getattr(resultado, "potencia_total", None)

            # dimensionamiento existente
            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
            if dim:
                detalles = list(dim.detalles.all().order_by("indice"))

    # =========================================================
    # ✅ POST: guardar
    # =========================================================
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "guardar":
            proyecto_id_raw = (request.POST.get("proyecto") or "").strip()
            if not proyecto_id_raw.isdigit():
                messages.error(request, "Selecciona un proyecto válido.")
                return redirect(reverse("core:dimensionamiento_dimensionamiento"))

            proyecto = Proyecto.objects.filter(id=int(proyecto_id_raw)).first()
            if not proyecto:
                messages.error(request, "Proyecto inválido.")
                return redirect(reverse("core:dimensionamiento_dimensionamiento"))

            # permisos
            if session_tipo != "Administrador" and int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para guardar en ese proyecto.")
                return redirect(reverse("core:dimensionamiento_dimensionamiento"))

            tipo = (request.POST.get("tipo_inversor") or "").strip().upper()  # INVERSOR / MICRO
            no_inv_raw = (request.POST.get("no_inversores") or "").strip()

            if tipo not in ("INVERSOR", "MICRO"):
                messages.error(request, "Tipo de instalación inválido.")
                return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

            if not no_inv_raw.isdigit() or int(no_inv_raw) < 1:
                messages.error(request, "Número de inversores inválido.")
                return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

            no_inversores = int(no_inv_raw)

            # Guardar/actualizar cabecera
            dim, _ = Dimensionamiento.objects.update_or_create(
                proyecto=proyecto,
                defaults={
                    "tipo_inversor": tipo,
                    "no_inversores": no_inversores,
                }
            )

            # Guardar detalles
            errores = False
            saved_indices = set()

            for i in range(1, no_inversores + 1):
                modelo_raw = (request.POST.get(f"modelo_{i}") or "").strip()
                cadenas_raw = (request.POST.get(f"cadenas_{i}") or "").strip()

                if not modelo_raw.isdigit():
                    messages.error(request, f"Selecciona el modelo para el inversor {i}.")
                    errores = True
                    continue

                if not cadenas_raw.isdigit() or int(cadenas_raw) < 1:
                    messages.error(request, f"Número de cadenas inválido en inversor {i}.")
                    errores = True
                    continue

                cadenas = int(cadenas_raw)

                # ✅ NUEVO: leer módulos por cada cadena (modulos_{i}_1, modulos_{i}_2, ...)
                lista_modulos = []
                for c in range(1, cadenas + 1):
                    mv = (request.POST.get(f"modulos_{i}_{c}") or "").strip()
                    if not mv.isdigit() or int(mv) < 1:
                        messages.error(request, f"Módulos inválidos en inversor {i}, cadena {c}.")
                        errores = True
                        lista_modulos = []
                        break
                    lista_modulos.append(int(mv))

                if errores:
                    continue

                # ✅ Campo resumen legacy (para compatibilidad): guardamos el máximo
                modulos = max(lista_modulos) if lista_modulos else 1

                inversor_fk = None
                micro_fk = None

                if tipo == "INVERSOR":
                    inversor_fk = Inversor.objects.filter(id=int(modelo_raw)).first()
                    if not inversor_fk:
                        messages.error(request, f"Modelo de inversor inválido en inversor {i}.")
                        errores = True
                        continue
                else:
                    micro_fk = MicroInversor.objects.filter(id=int(modelo_raw)).first()
                    if not micro_fk:
                        messages.error(request, f"Modelo de micro inversor inválido en inversor {i}.")
                        errores = True
                        continue

                DimensionamientoDetalle.objects.update_or_create(
                    dimensionamiento=dim,
                    indice=i,
                    defaults={
                        "inversor": inversor_fk,
                        "micro_inversor": micro_fk,
                        "no_cadenas": cadenas,
                        "modulos_por_cadena": modulos,  # (legacy)
                        "modulos_por_cadena_lista": lista_modulos,  # ✅ NUEVO
                    }
                )

                saved_indices.add(i)

            # ✅ borrar sobrantes si redujo cantidad
            DimensionamientoDetalle.objects.filter(dimensionamiento=dim).exclude(indice__in=saved_indices).delete()

            if errores:
                return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

            messages.success(request, "✅ Dimensionamiento guardado correctamente.")
            return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

    # =========================================================
    # ✅ Context
    # =========================================================
    info_modulos = {
        "no_modulos": getattr(resultado, "no_modulos", None),
        "modelo_modulo": None,
    }

    if np_obj and getattr(np_obj, "panel", None):
        info_modulos["modelo_modulo"] = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"

    # valores actuales guardados
    current_tipo = getattr(dim, "tipo_inversor", "INVERSOR") if dim else "INVERSOR"
    current_no_inv = getattr(dim, "no_inversores", 1) if dim else 1

    # precarga para JS
    precarga = []
    for i in range(1, current_no_inv + 1):
        d = next((x for x in detalles if x.indice == i), None)
        precarga.append({
            "indice": i,
            "modelo_id": (d.inversor_id if d and d.inversor_id else (d.micro_inversor_id if d else None)),
            "no_cadenas": (d.no_cadenas if d else 1),
            "modulos_por_cadena": (d.modulos_por_cadena if d else 1),  # legacy
            "modulos_por_cadena_lista": (d.modulos_por_cadena_lista if d and d.modulos_por_cadena_lista else []),
            # ✅ NUEVO
        })

    # ✅ Detalles guardados (para mostrar tabla debajo)
    detalles_guardados = []
    if dim:
        detalles_guardados = list(
            DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
            .select_related("inversor", "micro_inversor")
            .order_by("indice")
        )

    context = {
        "proyectos": proyectos,
        "selected_proyecto_id": selected_proyecto_id,
        "proyecto": proyecto,

        "np_obj": np_obj,
        "resultado": resultado,
        "info_modulos": info_modulos,

        "inversores": inversores,
        "micro_inversores": micro_inversores,

        "current_tipo": current_tipo,
        "current_no_inv": current_no_inv,
        "precarga": precarga,

        "potencia_total": potencia_total,
        "detalles_guardados": detalles_guardados,
    }

    return render(request, "core/pages/dimensionamiento_dimensionamiento.html", context)

# =========================================================
# CATÁLOGOS: Alta de Inversor
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def inversor_alta(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    if session_tipo != "Administrador":
        messages.error(request, "No tienes permisos para acceder a este módulo.")
        return redirect("core:menu_principal")

    next_url = (request.GET.get("next") or "").strip() or reverse("core:dimensionamiento_dimensionamiento")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            return redirect(next_url)

        form = InversorCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ Inversor guardado correctamente.")
            return redirect(next_url)
        messages.error(request, "Revisa el formulario.")
    else:
        form = InversorCreateForm()

    return render(request, "core/pages/inversor_alta.html", {
        "form": form,
        "next_url": next_url,
    })

# =========================================================
# CATÁLOGOS: Alta de MicroInversor
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def micro_inversor_alta(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    if session_tipo != "Administrador":
        messages.error(request, "No tienes permisos para acceder a este módulo.")
        return redirect("core:menu_principal")

    next_url = (request.GET.get("next") or "").strip() or reverse("core:dimensionamiento_dimensionamiento")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            return redirect(next_url)

        form = MicroInversorCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ Micro inversor guardado correctamente.")
            return redirect(next_url)
        messages.error(request, "Revisa el formulario.")
    else:
        form = MicroInversorCreateForm()

    return render(request, "core/pages/micro_inversor_alta.html", {
        "form": form,
        "next_url": next_url,
    })

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

from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics import renderPDF
from reportlab.lib.colors import HexColor

@require_session_login
@require_http_methods(["GET"])
def numero_modulos_pdf(request, proyecto_id: int):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    proyecto = Proyecto.objects.select_related("ID_Usuario").filter(id=proyecto_id).first()
    if not proyecto:
        messages.error(request, "Proyecto no encontrado.")
        return redirect("core:numero_modulos")

    if session_tipo != "Administrador":
        if not session_id_usuario or int(proyecto.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos.")
            return redirect("core:numero_modulos")

    np_obj = NumeroPaneles.objects.select_related("irradiancia", "panel").filter(proyecto=proyecto).first()
    if not np_obj:
        messages.error(request, "No hay cálculo para este proyecto.")
        return redirect("core:numero_modulos")

    resultado = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()
    if not resultado:
        messages.error(request, "No hay resultado calculado para este proyecto.")
        return redirect("core:numero_modulos")

    # Orden de periodos
    if np_obj.tipo_facturacion == "MENSUAL":
        orden = [
            ("ene","Ene"),("feb","Feb"),("mar","Mar"),("abr","Abr"),("may","May"),("jun","Jun"),
            ("jul","Jul"),("ago","Ago"),("sep","Sep"),("oct","Oct"),("nov","Nov"),("dic","Dic"),
        ]
    else:
        orden = [("bim1","Bim 1"),("bim2","Bim 2"),("bim3","Bim 3"),("bim4","Bim 4"),("bim5","Bim 5"),("bim6","Bim 6")]

    cons = np_obj.consumos or {}
    genp = resultado.generacion_por_periodo or {}

    labels = [lbl for _, lbl in orden]
    consumo_vals = [float(cons.get(k, 0) or 0) for k, _ in orden]
    gen_vals = [float(genp.get(k, 0) or 0) for k, _ in orden]

    # PDF response
    filename = f"SWGFV_NumeroModulos_Proyecto_{proyecto.id}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(
        response,
        pagesize=letter,
        leftMargin=2.0 * cm,
        rightMargin=2.0 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        title=f"Número de módulos - Proyecto {proyecto.id}",
        author="SWGFV",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "T",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=colors.HexColor("#001F3F"),
        spaceAfter=10,
    )
    sub_style = ParagraphStyle(
        "S",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.HexColor("#333333"),
        spaceAfter=8,
    )

    elements = []

    # ✅ LOGO
    logo_path = finders.find("core/img/logo1.png")
    if logo_path:
        try:
            elements.append(RLImage(logo_path, width=3.0 * cm, height=3.0 * cm))
            elements.append(Spacer(1, 0.2 * cm))
        except Exception:
            pass

    elements.append(Paragraph("SWGFV - Reporte: Número de módulos", title_style))

    elements.append(Paragraph(f"<b>Proyecto:</b> {proyecto.Nombre_Proyecto or '—'}", sub_style))
    elements.append(Paragraph(f"<b>Tipo de facturación:</b> {np_obj.tipo_facturacion}", sub_style))
    elements.append(Paragraph(f"<b>Eficiencia:</b> {np_obj.eficiencia}", sub_style))
    elements.append(Paragraph(f"<b>Módulo:</b> {np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)", sub_style))
    elements.append(Paragraph(f"<b>Número de módulos:</b> {resultado.no_modulos}", sub_style))
    elements.append(Paragraph(f"<b>Potencia total instalada (kW):</b> {resultado.potencia_total}", sub_style))
    elements.append(Paragraph(f"<b>Generación anual (kWh):</b> {resultado.generacion_anual}", sub_style))
    elements.append(Spacer(1, 0.3 * cm))

    # ✅ TABLA Consumo vs Generación
    data = [["Periodo", "Consumo (kWh)", "Generación (kWh)"]]
    for i in range(len(labels)):
        data.append([labels[i], f"{consumo_vals[i]:.3f}", f"{gen_vals[i]:.3f}"])

    table = Table(data, colWidths=[4.0 * cm, 6.0 * cm, 6.0 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#001F3F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0B7C3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#F3F6FA")]),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 0.4 * cm))

    # ✅ Gráfica 1: Generación por periodo (ReportLab chart)
    def make_bar_chart(title, series, cat_names):
        d = Drawing(500, 220)
        chart = VerticalBarChart()
        chart.x = 30
        chart.y = 30
        chart.height = 150
        chart.width = 440
        chart.data = [series]
        chart.categoryAxis.categoryNames = cat_names
        chart.valueAxis.valueMin = 0

        chart.bars[0].fillColor = HexColor("#2E86DE")
        d.add(chart)

        d_title = Paragraph(f"<b>{title}</b>", sub_style)
        return d_title, d

    t1, g1 = make_bar_chart("Gráfica 1: Generación por periodo (kWh)", gen_vals, labels)
    elements.append(t1)
    elements.append(Spacer(1, 0.1 * cm))
    elements.append(g1)
    elements.append(Spacer(1, 0.3 * cm))

    # ✅ Gráfica 2: Generación vs Consumo (2 series)
    d2 = Drawing(500, 240)
    chart2 = VerticalBarChart()
    chart2.x = 30
    chart2.y = 30
    chart2.height = 160
    chart2.width = 440
    chart2.data = [consumo_vals, gen_vals]   # dos series
    chart2.categoryAxis.categoryNames = labels
    chart2.valueAxis.valueMin = 0

    chart2.bars[0].fillColor = HexColor("#E67E22")  # consumo
    chart2.bars[1].fillColor = HexColor("#2ECC71")  # generación

    legend = Legend()
    legend.x = 360
    legend.y = 200
    legend.alignment = "right"
    legend.colorNamePairs = [
        (HexColor("#E67E22"), "Consumo (kWh)"),
        (HexColor("#2ECC71"), "Generación (kWh)"),
    ]

    d2.add(chart2)
    d2.add(legend)

    elements.append(Paragraph("<b>Gráfica 2: Generación vs Consumo</b>", sub_style))
    elements.append(Spacer(1, 0.1 * cm))
    elements.append(d2)

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
# PDF DIMENSIONAMIENTO
# ==========================
from django.http import HttpResponse
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.platypus import TableStyle

def dimensionamiento_pdf(request, proyecto_id):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    proyecto = Proyecto.objects.get(id=proyecto_id)
    detalles = DimensionamientoDetalle.objects.filter(
        dimensionamiento__proyecto=proyecto
    ).select_related("inversor", "micro_inversor").order_by("indice")
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename=dimensionamiento_{proyecto_id}.pdf"

    doc = SimpleDocTemplate(response)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph(f"Proyecto: {proyecto.Nombre_Proyecto}", styles["Heading2"]))
    elements.append(Spacer(1, 12))

    data = [["Inversor", "Modelo", "Cadenas", "Módulos/cadena"]]

    for d in detalles:
        modelo = d.inversor or d.micro_inversor
        mods = d.modulos_por_cadena_lista or []
        if mods:
            mods_txt = ", ".join([f"Cad {idx + 1}: {val}" for idx, val in enumerate(mods)])
        else:
            mods_txt = str(d.modulos_por_cadena)

        data.append([d.indice, str(modelo), d.no_cadenas, mods_txt])

    table = Table(data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 1, colors.black),
    ]))

    elements.append(table)
    doc.build(elements)

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

# ==========================================
# VISTA: Número de módulos (SOLO interfaz)
# Archivo: core/views.py
# ==========================================
# from .forms import NumeroModulosForm
@require_session_login
@require_http_methods(["GET", "POST"])
def numero_modulos_view(request):
    import math
    from decimal import Decimal, ROUND_HALF_UP

    def D(x, nd=3):
        return Decimal(str(x)).quantize(Decimal("1." + "0" * nd), rounding=ROUND_HALF_UP)

    user_id = request.session.get("id_usuario")
    session_tipo = (request.session.get("tipo") or "").strip()

    # ✅ Proyectos
    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.all().order_by("-id")
    else:
        proyectos = Proyecto.objects.filter(ID_Usuario_id=user_id).order_by("-id")

    irradiancias = Irradiancia.objects.all().order_by("estado", "ciudad")
    paneles = PanelSolar.objects.all().order_by("marca", "modelo")

    meses = [
        {"label": "Ene", "name": "consumo_ene", "key": "ene"},
        {"label": "Feb", "name": "consumo_feb", "key": "feb"},
        {"label": "Mar", "name": "consumo_mar", "key": "mar"},
        {"label": "Abr", "name": "consumo_abr", "key": "abr"},
        {"label": "May", "name": "consumo_may", "key": "may"},
        {"label": "Jun", "name": "consumo_jun", "key": "jun"},
        {"label": "Jul", "name": "consumo_jul", "key": "jul"},
        {"label": "Ago", "name": "consumo_ago", "key": "ago"},
        {"label": "Sep", "name": "consumo_sep", "key": "sep"},
        {"label": "Oct", "name": "consumo_oct", "key": "oct"},
        {"label": "Nov", "name": "consumo_nov", "key": "nov"},
        {"label": "Dic", "name": "consumo_dic", "key": "dic"},
    ]

    bimestres = [
        {"label": "Bim 1", "name": "consumo_bim1", "key": "bim1"},
        {"label": "Bim 2", "name": "consumo_bim2", "key": "bim2"},
        {"label": "Bim 3", "name": "consumo_bim3", "key": "bim3"},
        {"label": "Bim 4", "name": "consumo_bim4", "key": "bim4"},
        {"label": "Bim 5", "name": "consumo_bim5", "key": "bim5"},
        {"label": "Bim 6", "name": "consumo_bim6", "key": "bim6"},
    ]

    # ✅ Proyecto seleccionado (GET o POST)
    selected_raw = (request.POST.get("proyecto") or request.GET.get("proyecto_id") or "").strip()
    selected_proyecto_id = int(selected_raw) if selected_raw.isdigit() else None

    np_obj = None
    resultado = None

    if selected_proyecto_id:
        np_obj = NumeroPaneles.objects.select_related("proyecto", "irradiancia", "panel").filter(
            proyecto_id=selected_proyecto_id
        ).first()
        if np_obj:
            resultado = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()

    # =========================
    # ✅ POST: guardar y calcular
    # =========================
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "calcular":
            proyecto_id = (request.POST.get("proyecto") or "").strip()
            tipo_fact = (request.POST.get("tipo_facturacion") or "").strip().lower()
            irradiancia_id = (request.POST.get("irradiancia") or "").strip()
            panel_id = (request.POST.get("panel") or "").strip()
            eficiencia = (request.POST.get("eficiencia") or "").strip()

            # ✅ eficiencia solo 0.7 o 0.8
            try:
                eff = float(eficiencia)
            except ValueError:
                eff = None

            if eff not in (0.7, 0.8):
                messages.error(request, "Eficiencia inválida. Solo se permite 0.7 o 0.8.")
                return redirect(reverse("core:numero_modulos"))

            if not (proyecto_id and tipo_fact and irradiancia_id and panel_id and eficiencia):
                messages.error(request, "Revisa el formulario. Hay errores.")
                return redirect(reverse("core:numero_modulos"))

            # tipo_fact -> DB
            if tipo_fact == "mensual":
                tipo_fact_db = "MENSUAL"
                consumos = {m["key"]: float(request.POST.get(m["name"]) or 0) for m in meses}
            elif tipo_fact == "bimestral":
                tipo_fact_db = "BIMESTRAL"
                consumos = {f"bim{i}": float(request.POST.get(f"consumo_bim{i}") or 0) for i in range(1, 7)}
            else:
                messages.error(request, "Tipo de facturación inválido.")
                return redirect(reverse("core:numero_modulos"))

            # objetos + permisos
            proyecto = Proyecto.objects.filter(id=proyecto_id).first()
            if not proyecto:
                messages.error(request, "Proyecto inválido.")
                return redirect(reverse("core:numero_modulos"))

            if session_tipo != "Administrador" and int(proyecto.ID_Usuario_id) != int(user_id):
                messages.error(request, "No tienes permisos para usar este proyecto.")
                return redirect(reverse("core:numero_modulos"))

            irradiancia = Irradiancia.objects.filter(id=irradiancia_id).first()
            panel = PanelSolar.objects.filter(id=panel_id).first()
            if not irradiancia or not panel:
                messages.error(request, "Irradiancia o panel inválidos.")
                return redirect(reverse("core:numero_modulos"))

            # guardar NumeroPaneles
            obj, created = NumeroPaneles.objects.update_or_create(
                proyecto=proyecto,
                defaults={
                    "tipo_facturacion": tipo_fact_db,
                    "irradiancia": irradiancia,
                    "panel": panel,
                    "eficiencia": eff,
                    "consumos": consumos,
                },
            )

            # resultado
            resultado_obj, _ = ResultadoPaneles.objects.get_or_create(numero_paneles=obj)

            # ====== CÁLCULO REAL (el que ya te funciona) ======
            pot_kw = float(panel.potencia) / 1000.0
            eff = float(obj.eficiencia)

            if tipo_fact_db == "MENSUAL":
                consumo_prom = sum(float(consumos.get(k, 0) or 0) for k in ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]) / 12.0
                dias_periodo_ref = 30.0
                hsp_prom = (
                    float(irradiancia.ene)+float(irradiancia.feb)+float(irradiancia.mar)+float(irradiancia.abr)+
                    float(irradiancia.may)+float(irradiancia.jun)+float(irradiancia.jul)+float(irradiancia.ago)+
                    float(irradiancia.sep)+float(irradiancia.oct)+float(irradiancia.nov)+float(irradiancia.dic)
                ) / 12.0
            else:
                consumo_prom = sum(float(consumos.get(f"bim{i}", 0) or 0) for i in range(1, 7)) / 6.0
                dias_periodo_ref = 60.0
                hsp_prom = (
                    float(irradiancia.feb)+float(irradiancia.abr)+float(irradiancia.jun)+
                    float(irradiancia.ago)+float(irradiancia.oct)+float(irradiancia.dic)
                ) / 6.0

            energia_modulo_periodo = pot_kw * hsp_prom * eff * dias_periodo_ref
            no_modulos = math.ceil((consumo_prom * 1.1) / energia_modulo_periodo) if energia_modulo_periodo > 0 else 0

            potencia_total = float(D(no_modulos * pot_kw, 3))

            gen_por_periodo = {}
            if tipo_fact_db == "MENSUAL":
                for k in ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]:
                    gen_por_periodo[k] = float(D(potencia_total * float(getattr(irradiancia, k)) * eff * 30.0, 3))
            else:
                mapeo = {"bim1":"feb","bim2":"abr","bim3":"jun","bim4":"ago","bim5":"oct","bim6":"dic"}
                for bim, mes in mapeo.items():
                    gen_por_periodo[bim] = float(D(potencia_total * float(getattr(irradiancia, mes)) * eff * 60.0, 3))

            generacion_anual = float(D(sum(float(v) for v in gen_por_periodo.values()), 3))

            resultado_obj.no_modulos = no_modulos
            resultado_obj.potencia_total = D(potencia_total, 3)
            resultado_obj.generacion_anual = D(generacion_anual, 3)
            resultado_obj.generacion_por_periodo = gen_por_periodo
            resultado_obj.save(update_fields=["no_modulos", "potencia_total", "generacion_anual", "generacion_por_periodo"])

            messages.success(request, "✅ Cálculo realizado correctamente.")

            # ✅ IMPORTANTE: redirigir con proyecto_id para que el GET reconstruya tabla+gráficas
            return redirect(f"{reverse('core:numero_modulos')}?proyecto_id={proyecto.id}")

    # =========================
    # ✅ SIEMPRE armar tabla + chart data si hay np_obj y resultado (GET o POST)
    # =========================
    tabla_periodos = []
    chart_labels = []
    chart_consumo = []
    chart_generacion = []

    if np_obj and resultado:
        cons = np_obj.consumos or {}
        genp = (resultado.generacion_por_periodo or {})

        if np_obj.tipo_facturacion == "MENSUAL":
            orden = [("ene","Ene"),("feb","Feb"),("mar","Mar"),("abr","Abr"),("may","May"),("jun","Jun"),
                     ("jul","Jul"),("ago","Ago"),("sep","Sep"),("oct","Oct"),("nov","Nov"),("dic","Dic")]
        else:
            orden = [("bim1","Bim 1"),("bim2","Bim 2"),("bim3","Bim 3"),("bim4","Bim 4"),("bim5","Bim 5"),("bim6","Bim 6")]

        for key, label in orden:
            c = float(cons.get(key, 0) or 0)
            g = float(genp.get(key, 0) or 0)
            tabla_periodos.append({"label": label, "consumo": round(c, 3), "generacion": round(g, 3)})
            chart_labels.append(label)
            chart_consumo.append(round(c, 3))
            chart_generacion.append(round(g, 3))

    context = {
        "proyectos": proyectos,
        "irradiancias": irradiancias,
        "paneles": paneles,
        "meses": meses,
        "bimestres": bimestres,

        "np_obj": np_obj,
        "resultado": resultado,
        "selected_proyecto_id": selected_proyecto_id,

        # ✅ HTML tabla + charts (NOMBRES NUEVOS)
        "tabla_periodos": tabla_periodos,
        "chart_labels": chart_labels,
        "chart_consumo": chart_consumo,
        "chart_generacion": chart_generacion,

        # ✅ ALIAS para que tu template viejo funcione (NOMBRES QUE USA EL TEMPLATE)
        "chart_cons": chart_consumo,
        "chart_gen": chart_generacion,
    }
    return render(request, "core/pages/numero_modulos.html", context)

from django.views.decorators.http import require_GET

@require_session_login
@require_GET
def numero_modulos_data(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")
    is_admin = (session_tipo == "Administrador")

    proyecto_id = (request.GET.get("proyecto_id") or "").strip()
    if not proyecto_id.isdigit():
        return JsonResponse({"ok": False, "error": "proyecto_id inválido"}, status=400)

    proyecto = Proyecto.objects.filter(id=int(proyecto_id)).first()
    if not proyecto:
        return JsonResponse({"ok": False, "error": "Proyecto no existe"}, status=404)

    # Permisos: si NO es admin, el proyecto debe ser del usuario
    if not is_admin and int(proyecto.ID_Usuario_id) != int(session_id_usuario):
        return JsonResponse({"ok": False, "error": "Sin permisos"}, status=403)

    np = NumeroPaneles.objects.filter(proyecto=proyecto).select_related("irradiancia", "panel").first()
    if not np:
        # No existe aún registro para ese proyecto => ok pero vacío
        return JsonResponse({"ok": True, "exists": False})

    return JsonResponse({
        "ok": True,
        "exists": True,
        "data": {
            "tipo_facturacion": (np.tipo_facturacion or "").lower(),  # "mensual" / "bimestral"
            "eficiencia": str(np.eficiencia) if np.eficiencia is not None else "",
            "irradiancia_id": np.irradiancia_id,
            "panel_id": np.panel_id,
            "consumos": np.consumos or {},
        }
    })
