# core/views.py
import random
import csv
from datetime import timedelta
import logging
import requests
from django.conf import settings
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, KeepTogether, PageBreak
from reportlab.platypus import Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from core.forms import PanelSolarCreateForm
from core.forms import InversorCreateForm, MicroInversorCreateForm
from decimal import Decimal, ROUND_UP
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from django.db import transaction
import os
from django.templatetags.static import static

from core.utils.pdf_utils import (
    build_fortia_doc,
    get_fortia_styles,
    draw_fortia_letterhead,
    add_fortia_header,
    make_info_table,
    make_data_table,
    add_fortia_footer,
)

from .forms import (
    LoginForm,
    UsuarioCreateForm,
    UsuarioUpdateForm,
    CuentaUpdateForm,
    ProyectoCreateForm,
    ProyectoUpdateForm,
    ProyectoConsultaForm,
    PasswordRecoveryRequestForm,
    PasswordResetForm,
    NumeroPanelesForm,
    GlosarioConceptoCreateForm,
    GlosarioConceptoUpdateForm,
    TablaNOMCreateForm,
    TablaNOMUpdateForm,
)

from .auth_local import authenticate_local
from .decorators import require_session_login, require_admin
from .models import (
    Usuario, Proyecto, LoginLock, AuditLog,
    Irradiancia, PanelSolar, NumeroPaneles, ResultadoPaneles,
    Inversor, MicroInversor,
    Dimensionamiento, DimensionamientoDetalle,
    Conductor, Condulet, ResultadoCalculoDC, CalculoDC,
    ResultadoCalculoAC, CalculoAC,
    ResultadoTension, CalculoTension,
    TablaConductoresAWGConReactancia,
    GlosarioConcepto,
    TablaNOM,
)

logger = logging.getLogger(__name__)

# =========================================================
# HELPER: VALIDAR PROYECTO COMPLETO PARA PDF MAESTRO
# =========================================================
def _project_completion_status(proyecto: Proyecto):
    """
    Determina si un proyecto está completo para permitir la descarga
    del PDF maestro.

    Reglas:
    - Debe existir NumeroPaneles y ResultadoPaneles
    - Debe existir Dimensionamiento y al menos un detalle
    - Si el proyecto usa INVERSOR:
        * Debe existir al menos un CalculoDC con resultado_dc
        * Debe existir al menos un CalculoAC con resultado_ac
        * Debe existir al menos un CalculoTension con resultado_tension
    - Si el proyecto usa MICRO:
        * NO se exige CalculoDC
        * Sí se exige CalculoAC con resultado_ac
        * Sí se exige CalculoTension con resultado_tension
    """
    faltantes = []

    np_obj = NumeroPaneles.objects.select_related("panel", "irradiancia").filter(proyecto=proyecto).first()
    resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first() if np_obj else None

    dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
    detalles = list(
        DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
        .select_related("inversor", "micro_inversor")
        .order_by("indice")
    ) if dim else []

    calculos_dc = list(
        CalculoDC.objects.filter(proyecto=proyecto)
        .select_related("resultado_dc", "dimensionamiento_detalle", "condulet", "conductor")
        .order_by("indice")
    )

    calculos_ac = list(
        CalculoAC.objects.filter(proyecto=proyecto)
        .select_related("resultado_ac", "dimensionamiento_detalle", "condulet", "conductor")
        .order_by("indice")
    )

    calculos_tension = list(
        CalculoTension.objects.filter(proyecto=proyecto)
        .select_related("resultado_tension", "tension_ac", "tension_dc")
        .order_by("indice", "tipo_calculo", "serie")
    )

    if not np_obj:
        faltantes.append("Cálculo de módulos")
    if np_obj and not resultado_paneles:
        faltantes.append("Resultado de cálculo de módulos")

    if not dim:
        faltantes.append("Dimensionamiento")
    if dim and not detalles:
        faltantes.append("Detalle de dimensionamiento")

    usa_micro = bool(dim and dim.tipo_inversor == "MICRO")

    # ✅ DC solo se exige si NO es micro inversor
    if not usa_micro:
        if not calculos_dc:
            faltantes.append("Cálculo DC")
        elif not any(x.resultado_dc_id for x in calculos_dc):
            faltantes.append("Resultado de cálculo DC")

    # ✅ AC siempre se exige
    if not calculos_ac:
        faltantes.append("Cálculo AC")
    elif not any(x.resultado_ac_id for x in calculos_ac):
        faltantes.append("Resultado de cálculo AC")

    # ✅ Tensión siempre se exige, pero para micro basta con resultados AC
    if not calculos_tension:
        faltantes.append("Cálculo de caída de tensión")
    else:
        if usa_micro:
            tensiones_ac = [x for x in calculos_tension if x.tipo_calculo == "AC"]
            if not tensiones_ac:
                faltantes.append("Cálculo de caída de tensión")
            elif not any(x.resultado_tension_id for x in tensiones_ac):
                faltantes.append("Resultado de caída de tensión")
        else:
            if not any(x.resultado_tension_id for x in calculos_tension):
                faltantes.append("Resultado de caída de tensión")

    return {
        "completo": len(faltantes) == 0,
        "faltantes": faltantes,
        "numero_paneles": np_obj,
        "resultado_paneles": resultado_paneles,
        "dimensionamiento": dim,
        "detalles": detalles,
        "calculos_dc": calculos_dc,
        "calculos_ac": calculos_ac,
        "calculos_tension": calculos_tension,
    }

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

def verificar_recaptcha_google(request) -> bool:
    """
    Valida el Google reCAPTCHA v2 checkbox.
    Retorna True si Google lo valida correctamente.
    """
    recaptcha_response = (request.POST.get("g-recaptcha-response") or "").strip()

    if not recaptcha_response:
        return False

    try:
        data = {
            "secret": settings.RECAPTCHA_PRIVATE_KEY,
            "response": recaptcha_response,
        }
        r = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data=data,
            timeout=10,
        )
        result = r.json()
        return bool(result.get("success", False))
    except Exception:
        return False

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
    Login con Google reCAPTCHA + lock por usuario (LoginLock en BD).
    El reCAPTCHA NO cuenta como intento fallido.
    Solo usuario/contraseña incorrectos cuentan para bloqueo.
    """
    try:
        if request.session.get("usuario") and request.session.get("tipo"):
            return redirect("core:menu_principal")

        form = LoginForm(request.POST or None)

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

        def _render_login():
            return render(
                request,
                "core/login.html",
                {
                    "form": form,
                    "RECAPTCHA_PUBLIC_KEY": settings.RECAPTCHA_PUBLIC_KEY,
                },
            )

        if request.method == "POST":
            usuario_input = (request.POST.get("usuario") or "").strip()

            if not usuario_input:
                messages.error(request, "Ingresa tu usuario/correo.")
                return _render_login()

            locked, minutes = _is_locked(usuario_input)
            if locked:
                messages.error(request, f"Cuenta bloqueada temporalmente. Intenta de nuevo en {minutes} minuto(s).")
                return _render_login()

            if not form.is_valid():
                messages.error(request, "Revisa el formulario e intenta nuevamente.")
                return _render_login()

            # =====================================================
            # Validación Google reCAPTCHA
            # IMPORTANTE: no cuenta como intento fallido
            # =====================================================
            if not verificar_recaptcha_google(request):
                messages.error(request, "Verifica que no eres un robot.")
                return _render_login()

            # =====================================================
            # Validación de usuario y contraseña
            # SOLO esto cuenta como intento fallido
            # =====================================================
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

            return _render_login()

        return _render_login()

    except Exception:
        logger.exception("ERROR EN LOGIN_VIEW (POST/GET) - detalle:")

        form = LoginForm(request.POST or None)
        messages.error(request, "Ocurrió un error inesperado al iniciar sesión. Intenta de nuevo.")
        return render(
            request,
            "core/login.html",
            {
                "form": form,
                "RECAPTCHA_PUBLIC_KEY": settings.RECAPTCHA_PUBLIC_KEY,
            },
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

    form = ProyectoCreateForm(request.POST or None)
    proyecto_creado = None
    missing_required_fields = []

    proyecto_id = (request.GET.get("created") or "").strip()
    if proyecto_id.isdigit():
        proyecto_creado = Proyecto.objects.filter(
            id=int(proyecto_id),
            ID_Usuario_id=session_id_usuario
        ).first()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "cancel":
            messages.info(request, "Operación cancelada. El formulario fue limpiado.")
            return redirect("core:proyecto_alta")

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
            return redirect(f"{reverse('core:proyecto_alta')}?created={proyecto.id}")

        # Detectar campos obligatorios faltantes para popup
        required_field_map = {
            "Nombre_Proyecto": "Nombre del proyecto",
            "Direccion": "Dirección",
            "Coordenadas": "Coordenadas",
            "Voltaje_Nominal": "Voltaje nominal",
            "Numero_Fases": "Número de fases",
        }

        for field_name, field_label in required_field_map.items():
            raw_value = (request.POST.get(field_name) or "").strip()
            if not raw_value:
                missing_required_fields.append(field_label)

    return render(
        request,
        "core/pages/proyecto_alta.html",
        {
            "form": form,
            "proyecto_creado": proyecto_creado,
            "missing_required_fields": missing_required_fields,
        }
    )



# =========================================================
# CONSULTA DE PROYECTOS (CORREGIDO)
# =========================================================
@require_session_login
@require_http_methods(["GET"])
def proyecto_consulta(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")
    es_admin = session_tipo == "Administrador"

    if es_admin:
        proyectos_dropdown = Proyecto.objects.select_related("ID_Usuario").all().order_by("-id")
        qs_base = Proyecto.objects.select_related("ID_Usuario").all().order_by("-id")
    else:
        proyectos_dropdown = Proyecto.objects.select_related("ID_Usuario").filter(
            ID_Usuario_id=session_id_usuario
        ).order_by("-id")
        qs_base = proyectos_dropdown

    form = ProyectoConsultaForm(
        request.GET or None,
        proyectos_dropdown=proyectos_dropdown,
        es_admin=es_admin,
    )

    proyectos = []
    mostrar_lista = False
    show_required_popup = False
    proyecto_select_int = None

    q_id = ""
    q_nombre = ""
    q_empresa = ""
    q_usuario = ""

    if request.GET:
        if form.is_valid():
            proyecto_select = (form.cleaned_data.get("proyecto") or "").strip()
            q_id = (form.cleaned_data.get("id") or "").strip()
            q_nombre = (form.cleaned_data.get("nombre") or "").strip()
            q_empresa = (form.cleaned_data.get("empresa") or "").strip()
            q_usuario = ""

            if es_admin and "usuario" in form.cleaned_data:
                q_usuario = (form.cleaned_data.get("usuario") or "").strip()

            qs = qs_base
            mostrar_lista = True

            if proyecto_select:
                proyecto_select_int = int(proyecto_select)
                qs = qs.filter(id=proyecto_select_int)

            if q_id:
                qs = qs.filter(id=int(q_id))

            if q_nombre:
                qs = qs.filter(Nombre_Proyecto__icontains=q_nombre)

            if q_empresa:
                qs = qs.filter(Nombre_Empresa__icontains=q_empresa)

            if q_usuario and es_admin:
                qs = qs.filter(ID_Usuario__Correo_electronico__icontains=q_usuario)

            proyectos = list(qs)

            # ✅ ESTADO PDF
            for p in proyectos:
                estado = _project_completion_status(p)
                p.pdf_completo = estado["completo"]
                p.pdf_faltantes = estado["faltantes"]

        else:
            non_field_errors = form.non_field_errors()
            if non_field_errors and any(
                "Debes ingresar al menos un campo para buscar." in str(err)
                for err in non_field_errors
            ):
                show_required_popup = True

            proyecto_raw = (request.GET.get("proyecto") or "").strip()
            q_id = (request.GET.get("id") or "").strip()
            q_nombre = (request.GET.get("nombre") or "").strip()
            q_empresa = (request.GET.get("empresa") or "").strip()
            q_usuario = (request.GET.get("usuario") or "").strip()

            if proyecto_raw.isdigit():
                proyecto_select_int = int(proyecto_raw)

    return render(
        request,
        "core/pages/proyecto_consulta.html",
        {
            "form": form,
            "proyectos_dropdown": proyectos_dropdown,
            "proyectos": proyectos,
            "mostrar_lista": mostrar_lista,
            "es_admin": es_admin,
            "proyecto_select_int": proyecto_select_int,
            "q_id": q_id,
            "q_nombre": q_nombre,
            "q_empresa": q_empresa,
            "q_usuario": q_usuario,
            "show_required_popup": show_required_popup,
        }
    )

@require_session_login
@require_http_methods(["GET", "POST"])
def proyecto_modificacion(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")
    es_admin = session_tipo == "Administrador"

    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    q_empresa = (request.GET.get("empresa") or "").strip()
    q_usuario = (request.GET.get("usuario") or "").strip()
    mostrar_todos = (request.GET.get("mostrar_todos") or "").strip() == "1"
    edit_mode = (request.GET.get("edit") or "").strip() == "1"
    search_submitted = (request.GET.get("action") or "").strip() == "search"

    form_busqueda = ProyectoConsultaForm(
        request.GET if request.GET else None,
        proyectos_dropdown=None,
        es_admin=es_admin,
    )

    if es_admin:
        qs_base = Proyecto.objects.select_related("ID_Usuario").all().order_by("-id")
    else:
        qs_base = Proyecto.objects.select_related("ID_Usuario").filter(
            ID_Usuario_id=session_id_usuario
        ).order_by("-id")

    proyectos = Proyecto.objects.none()
    mostrar_lista = False
    show_edit_popup = False
    missing_required_fields = []

    # ==========================
    # BÚSQUEDA / LISTADO
    # ==========================
    solo_mostrar_todos = mostrar_todos and es_admin and not search_submitted and not any([q_id, q_nombre, q_empresa, q_usuario])

    if solo_mostrar_todos:
        mostrar_lista = True
        proyectos = qs_base

    elif search_submitted:
        if form_busqueda.is_valid():
            q_id = (form_busqueda.cleaned_data.get("id") or "").strip()
            q_nombre = (form_busqueda.cleaned_data.get("nombre") or "").strip()
            q_empresa = (form_busqueda.cleaned_data.get("empresa") or "").strip()
            q_usuario = ""
            if es_admin and "usuario" in form_busqueda.cleaned_data:
                q_usuario = (form_busqueda.cleaned_data.get("usuario") or "").strip()

            mostrar_lista = True
            qs = qs_base

            if q_id:
                qs = qs.filter(id=int(q_id))

            if q_nombre:
                qs = qs.filter(Nombre_Proyecto__icontains=q_nombre)

            if q_empresa:
                qs = qs.filter(Nombre_Empresa__icontains=q_empresa)

            if q_usuario and es_admin:
                qs = qs.filter(ID_Usuario__Correo_electronico__icontains=q_usuario)

            proyectos = qs
        else:
            # Mantener el formulario con errores visibles debajo de cada campo
            mostrar_lista = False
            proyectos = Proyecto.objects.none()

    # ==========================
    # PROYECTO SELECCIONADO
    # ==========================
    seleccionado = None
    form = None

    if q_id.isdigit():
        seleccionado = Proyecto.objects.select_related("ID_Usuario").filter(id=int(q_id)).first()

        if seleccionado:
            if not es_admin and int(seleccionado.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para ver/modificar este proyecto.")
                return redirect("core:proyecto_modificacion")

            form = ProyectoUpdateForm(instance=seleccionado)

    # ==========================
    # POST: GUARDAR / ELIMINAR
    # ==========================
    if request.method == "POST":
        post_id = (request.GET.get("id") or "").strip()

        if not post_id.isdigit():
            messages.error(request, "Selecciona un proyecto válido.")
            return redirect("core:proyecto_modificacion")

        seleccionado = Proyecto.objects.select_related("ID_Usuario").filter(id=int(post_id)).first()
        if not seleccionado:
            messages.error(request, "El proyecto ya no existe.")
            return redirect("core:proyecto_modificacion")

        if not es_admin and int(seleccionado.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para modificar este proyecto.")
            return redirect("core:proyecto_modificacion")

        action = (request.POST.get("action") or "").strip().lower()

        if action == "delete_project":
            try:
                with transaction.atomic():
                    nombre = seleccionado.Nombre_Proyecto
                    pid = seleccionado.id

                    calculos_dc = list(
                        CalculoDC.objects.filter(proyecto=seleccionado).select_related(
                            "condulet", "resultado_dc"
                        )
                    )
                    condulet_ids_dc = [c.condulet_id for c in calculos_dc if getattr(c, "condulet_id", None)]
                    resultado_dc_ids = [c.resultado_dc_id for c in calculos_dc if getattr(c, "resultado_dc_id", None)]

                    calculos_ac = list(
                        CalculoAC.objects.filter(proyecto=seleccionado).select_related(
                            "condulet", "resultado_ac"
                        )
                    )
                    condulet_ids_ac = [c.condulet_id for c in calculos_ac if getattr(c, "condulet_id", None)]
                    resultado_ac_ids = [c.resultado_ac_id for c in calculos_ac if getattr(c, "resultado_ac_id", None)]

                    calculos_tension = list(
                        CalculoTension.objects.filter(proyecto=seleccionado).select_related(
                            "resultado_tension"
                        )
                    )
                    resultado_tension_ids = [c.resultado_tension_id for c in calculos_tension if getattr(c, "resultado_tension_id", None)]

                    CalculoTension.objects.filter(proyecto=seleccionado).delete()
                    CalculoAC.objects.filter(proyecto=seleccionado).delete()
                    CalculoDC.objects.filter(proyecto=seleccionado).delete()

                    condulet_ids = list(set(condulet_ids_dc + condulet_ids_ac))
                    if condulet_ids:
                        Condulet.objects.filter(id__in=condulet_ids).delete()

                    if resultado_dc_ids:
                        ResultadoCalculoDC.objects.filter(id__in=resultado_dc_ids).delete()

                    if resultado_ac_ids:
                        ResultadoCalculoAC.objects.filter(id__in=resultado_ac_ids).delete()

                    if resultado_tension_ids:
                        ResultadoTension.objects.filter(id__in=resultado_tension_ids).delete()

                    seleccionado.delete()

                log_event(request, "PROJECT_DELETED", f"Eliminó proyecto: {nombre}", "Proyecto", pid)
                messages.success(
                    request,
                    f"✅ Proyecto eliminado correctamente junto con todos sus registros relacionados: {nombre}"
                )
                return redirect("core:proyecto_modificacion")

            except Exception as e:
                messages.error(request, f"Ocurrió un error al eliminar el proyecto: {str(e)}")
                return redirect(f"{reverse('core:proyecto_modificacion')}?id={seleccionado.id}")

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

        required_field_map = {
            "Nombre_Proyecto": "Nombre del proyecto",
            "Direccion": "Dirección",
            "Coordenadas": "Coordenadas",
            "Voltaje_Nominal": "Voltaje nominal",
            "Numero_Fases": "Número de fases",
        }

        for field_name, field_label in required_field_map.items():
            raw_value = (request.POST.get(field_name) or "").strip()
            if not raw_value:
                missing_required_fields.append(field_label)

        if missing_required_fields:
            show_edit_popup = True

    context = {
        "form_busqueda": form_busqueda,
        "proyectos": proyectos,
        "mostrar_lista": mostrar_lista,
        "q_id": q_id,
        "q_nombre": q_nombre,
        "q_empresa": q_empresa,
        "q_usuario": q_usuario,
        "es_admin": es_admin,
        "seleccionado": seleccionado,
        "form": form,
        "edit_mode": edit_mode,
        "mostrar_todos": mostrar_todos,
        "show_edit_popup": show_edit_popup,
        "missing_required_fields": missing_required_fields,
    }
    return render(request, "core/pages/proyecto_modificacion.html", context)

# =========================================================
# USUARIOS
# =========================================================
@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_alta(request):
    form = UsuarioCreateForm(request.POST or None)
    show_required_popup = False
    missing_required_fields = []

    if request.method == "POST":
        if form.is_valid():
            nuevo = form.save()
            log_event(request, "USER_CREATED", f"Creó usuario: {nuevo.Correo_electronico}", "Usuario", nuevo.ID_Usuario)
            messages.success(request, "Usuario dado de alta correctamente.")
            return redirect("core:gestion_usuarios_alta")

        required_field_map = {
            "Nombre": "Nombre",
            "Apellido_Paterno": "Apellido paterno",
            "Apellido_Materno": "Apellido materno",
            "Telefono": "Teléfono",
            "Correo_electronico": "Correo electrónico",
            "Tipo": "Tipo de usuario",
            "password": "Contraseña",
            "password_confirm": "Confirmación de contraseña",
        }

        for field_name, field_label in required_field_map.items():
            raw_value = (request.POST.get(field_name) or "").strip()
            if not raw_value:
                missing_required_fields.append(field_label)

        if missing_required_fields:
            show_required_popup = True

    return render(
        request,
        "core/pages/gestion_usuarios_alta.html",
        {
            "form": form,
            "show_required_popup": show_required_popup,
            "missing_required_fields": missing_required_fields,
        }
    )


@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_modificacion(request):
    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    q_ap = (request.GET.get("ap") or "").strip()
    q_am = (request.GET.get("am") or "").strip()
    mostrar_todos = (request.GET.get("mostrar_todos") or "").strip() == "1"
    edit_mode = (request.GET.get("edit") or "").strip() == "1"
    search_submitted = (request.GET.get("action") or "").strip() == "search"

    error_id = None
    show_search_popup = False
    show_edit_popup = False
    missing_required_fields = []

    mostrar_lista = False
    usuarios = Usuario.objects.none()

    if mostrar_todos and not search_submitted and not any([q_id, q_nombre, q_ap, q_am]):
        mostrar_lista = True
        usuarios = Usuario.objects.all().order_by("ID_Usuario")

    elif search_submitted:
        if not any([q_id, q_nombre, q_ap, q_am]):
            show_search_popup = True
        else:
            mostrar_lista = True
            qs = Usuario.objects.all().order_by("ID_Usuario")

            if q_id:
                if q_id.isdigit():
                    qs = qs.filter(ID_Usuario=int(q_id))
                else:
                    qs = Usuario.objects.none()
                    error_id = "El ID debe contener solo números enteros."

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
            obj = form.save(commit=False)
            obj.Correo_electronico = (form.cleaned_data.get("Correo_electronico") or "").strip().lower()
            obj.save()
            if form.cleaned_data.get("new_password"):
                obj.set_password(form.cleaned_data.get("new_password"))
                obj.save()

            log_event(request, "USER_UPDATED", f"Actualizó usuario: {obj.Correo_electronico}", "Usuario", obj.ID_Usuario)
            messages.success(request, "Usuario actualizado correctamente.")
            return redirect(f"{reverse('core:gestion_usuarios_modificacion')}?id={seleccionado.ID_Usuario}")

        required_field_map = {
            "Nombre": "Nombre",
            "Apellido_Paterno": "Apellido paterno",
            "Apellido_Materno": "Apellido materno",
            "Telefono": "Teléfono",
            "Correo_electronico": "Correo electrónico",
            "Tipo": "Tipo de usuario",
        }

        for field_name, field_label in required_field_map.items():
            raw_value = (request.POST.get(field_name) or "").strip()
            if not raw_value:
                missing_required_fields.append(field_label)

        if missing_required_fields:
            show_edit_popup = True

    context = {
        "q_id": q_id,
        "q_nombre": q_nombre,
        "q_ap": q_ap,
        "q_am": q_am,
        "mostrar_lista": mostrar_lista,
        "mostrar_todos": mostrar_todos,
        "usuarios": usuarios,
        "seleccionado": seleccionado,
        "form": form,
        "edit_mode": edit_mode,
        "show_search_popup": show_search_popup,
        "show_edit_popup": show_edit_popup,
        "missing_required_fields": missing_required_fields,
        "error_id": error_id,
    }
    return render(request, "core/pages/gestion_usuarios_modificacion.html", context)

@require_session_login
@require_http_methods(["GET", "POST"])
def cuenta_view(request):
    session_id_usuario = request.session.get("id_usuario")

    if not session_id_usuario:
        messages.error(request, "Sesión inválida. Inicia sesión nuevamente.")
        return redirect("core:logout")

    usuario = Usuario.objects.filter(ID_Usuario=session_id_usuario).first()
    if not usuario:
        messages.error(request, "No se encontró la cuenta del usuario.")
        return redirect("core:logout")

    edit_mode = (request.GET.get("edit") or "").strip() == "1"
    form = CuentaUpdateForm(instance=usuario)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            messages.info(request, "Operación cancelada.")
            return redirect("core:cuenta")

        form = CuentaUpdateForm(request.POST, instance=usuario)

        if form.is_valid():
            obj = form.save()

            # Actualizar datos de sesión por si cambió correo o tipo
            request.session["usuario"] = obj.Correo_electronico
            request.session["tipo"] = obj.Tipo
            request.session["id_usuario"] = obj.ID_Usuario
            request.session.modified = True

            log_event(
                request,
                "ACCOUNT_UPDATED",
                f"Actualizó su cuenta: {obj.Correo_electronico}",
                "Usuario",
                obj.ID_Usuario
            )

            messages.success(request, "✅ Información de cuenta actualizada correctamente.")
            return redirect("core:cuenta")

        messages.error(request, "Revisa el formulario. Hay errores.")
        edit_mode = True

    context = {
        "usuario_obj": usuario,
        "form": form,
        "edit_mode": edit_mode,
    }
    return render(request, "core/pages/cuenta.html", context)


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

    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.all().order_by("-id")
    else:
        proyectos = Proyecto.objects.filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    selected_raw = (request.POST.get("proyecto") or request.GET.get("proyecto_id") or "").strip()
    selected_proyecto_id = int(selected_raw) if selected_raw.isdigit() else None

    proyecto = None
    np_obj = None
    resultado = None
    potencia_total = None

    dim = None
    detalles = []

    inversores = Inversor.objects.all().order_by("marca", "modelo")
    micro_inversores = MicroInversor.objects.all().order_by("marca", "modelo")

    def to_decimal_or_none(value):
        try:
            if value is None:
                return None
            txt = str(value).strip()
            if txt == "":
                return None
            return Decimal(txt)
        except Exception:
            return None

    def evaluar_voc(valor_comparacion, voltaje_maximo_entrada, es_micro=False):
        if valor_comparacion is None:
            return {
                "estado": "error",
                "titulo": "Error",
                "mensaje": "No fue posible validar porque el panel no tiene Voc disponible."
            }

        if voltaje_maximo_entrada is None:
            return {
                "estado": "error",
                "titulo": "Error",
                "mensaje": "No fue posible validar porque el inversor no tiene voltaje máximo de entrada registrado."
            }

        vc = Decimal(str(valor_comparacion))
        vm = Decimal(str(voltaje_maximo_entrada))

        etiqueta = "Voc del panel" if es_micro else "Voc string"

        if vc < vm:
            return {
                "estado": "ok",
                "titulo": "Correcto",
                "mensaje": f"{etiqueta} ({vc} V) es menor que el voltaje máximo de entrada ({vm} V)."
            }
        elif vc == vm:
            return {
                "estado": "advertencia",
                "titulo": "Advertencia",
                "mensaje": f"{etiqueta} ({vc} V) es igual al voltaje máximo de entrada ({vm} V)."
            }
        else:
            return {
                "estado": "error",
                "titulo": "Error",
                "mensaje": f"{etiqueta} ({vc} V) supera el voltaje máximo de entrada ({vm} V)."
            }

    if selected_proyecto_id:
        proyecto = Proyecto.objects.filter(id=selected_proyecto_id).first()

        if proyecto and session_tipo != "Administrador":
            if int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para acceder a ese proyecto.")
                return redirect(reverse("core:dimensionamiento_dimensionamiento"))

        if proyecto:
            np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
            if np_obj:
                resultado = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()

            potencia_total = getattr(resultado, "potencia_total", None)

            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
            if dim:
                detalles = list(
                    DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                    .select_related("inversor", "micro_inversor")
                    .order_by("indice")
                )

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

            if session_tipo != "Administrador" and int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para guardar en ese proyecto.")
                return redirect(reverse("core:dimensionamiento_dimensionamiento"))

            tipo = (request.POST.get("tipo_inversor") or "").strip().upper()
            no_inv_raw = (request.POST.get("no_inversores") or "").strip()

            if tipo not in ("INVERSOR", "MICRO"):
                messages.error(request, "Tipo de instalación inválido.")
                return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

            if not no_inv_raw.isdigit() or int(no_inv_raw) < 1:
                messages.error(request, "Número de inversores inválido.")
                return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

            no_inversores = int(no_inv_raw)

            dim, _ = Dimensionamiento.objects.update_or_create(
                proyecto=proyecto,
                defaults={
                    "tipo_inversor": tipo,
                    "no_inversores": no_inversores,
                }
            )

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
                        "modulos_por_cadena": modulos,
                        "modulos_por_cadena_lista": lista_modulos,
                    }
                )

                saved_indices.add(i)

            DimensionamientoDetalle.objects.filter(dimensionamiento=dim).exclude(indice__in=saved_indices).delete()

            if errores:
                return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

            messages.success(request, "✅ Dimensionamiento guardado correctamente.")
            return redirect(f"{reverse('core:dimensionamiento_dimensionamiento')}?proyecto_id={proyecto.id}")

    info_modulos = {
        "no_modulos": getattr(resultado, "no_modulos", None),
        "modelo_modulo": None,
        "voc_modulo": None,
        "voltaje_maximo_entrada": None,
    }

    panel_voc = None
    if np_obj and getattr(np_obj, "panel", None):
        info_modulos["modelo_modulo"] = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"
        panel_voc = to_decimal_or_none(np_obj.panel.voc)
        info_modulos["voc_modulo"] = panel_voc

    current_tipo = getattr(dim, "tipo_inversor", "INVERSOR") if dim else "INVERSOR"
    current_no_inv = getattr(dim, "no_inversores", 1) if dim else 1

    precarga = []
    for i in range(1, current_no_inv + 1):
        d = next((x for x in detalles if x.indice == i), None)
        precarga.append({
            "indice": i,
            "modelo_id": (d.inversor_id if d and d.inversor_id else (d.micro_inversor_id if d else None)),
            "no_cadenas": (d.no_cadenas if d else 1),
            "modulos_por_cadena": (d.modulos_por_cadena if d else 1),
            "modulos_por_cadena_lista": (d.modulos_por_cadena_lista if d and d.modulos_por_cadena_lista else []),
        })

        detalles_guardados = []
        if dim:
            detalles_guardados = list(
                DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                .select_related("inversor", "micro_inversor")
                .order_by("indice")
            )

            # Obtener voltaje máximo del primer inversor o micro inversor
            if detalles_guardados:
                primer_equipo = (
                    detalles_guardados[0].inversor
                    if detalles_guardados[0].inversor_id
                    else detalles_guardados[0].micro_inversor
                )

                info_modulos["voltaje_maximo_entrada"] = to_decimal_or_none(
                    getattr(primer_equipo, "voltaje_maximo_entrada", None)
                )

            for d in detalles_guardados:
                equipo = d.inversor if d.inversor_id else d.micro_inversor

                voltaje_maximo_entrada = to_decimal_or_none(
                    getattr(equipo, "voltaje_maximo_entrada", None)
                )

            lista_modulos = d.modulos_por_cadena_lista or []
            if not lista_modulos:
                lista_modulos = [int(d.modulos_por_cadena or 0)] * int(d.no_cadenas or 0)

            validaciones_voc = []

            # MICROINVERSOR:
            # solo validar Voc del panel < voltaje máximo de entrada
            if d.micro_inversor_id:
                resultado_validacion = evaluar_voc(
                    panel_voc,
                    voltaje_maximo_entrada,
                    es_micro=True,
                )

                validaciones_voc.append({
                    "cadena": 1,
                    "modulos": lista_modulos[0] if lista_modulos else None,
                    "voc_string": panel_voc,  # aquí se mostrará como valor comparado
                    "voltaje_maximo_entrada": voltaje_maximo_entrada,
                    "resultado": resultado_validacion,
                    "es_micro": True,
                })

            # INVERSOR:
            # validar Voc string por cada cadena
            else:
                for idx_cad, modulos_cad in enumerate(lista_modulos, start=1):
                    modulos_dec = to_decimal_or_none(modulos_cad)
                    voc_string = None

                    if panel_voc is not None and modulos_dec is not None:
                        voc_string = panel_voc * modulos_dec

                    resultado_validacion = evaluar_voc(
                        voc_string,
                        voltaje_maximo_entrada,
                        es_micro=False,
                    )

                    validaciones_voc.append({
                        "cadena": idx_cad,
                        "modulos": modulos_cad,
                        "voc_string": voc_string,
                        "voltaje_maximo_entrada": voltaje_maximo_entrada,
                        "resultado": resultado_validacion,
                        "es_micro": False,
                    })

            d.validaciones_voc = validaciones_voc

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
@require_http_methods(["GET", "POST"])
def calculo_dc(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    # Proyectos por permisos
    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.all().order_by("-id")
    else:
        proyectos = Proyecto.objects.filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    selected_raw = (request.POST.get("proyecto") or request.GET.get("proyecto_id") or "").strip()
    selected_proyecto_id = int(selected_raw) if selected_raw.isdigit() else None

    proyecto = None
    dim = None
    detalles = []
    np_obj = None
    resultado_paneles = None

    bloques = []
    dc_bloqueado_micro = False

    # resumen general
    resumen = {
        "no_modulos": None,
        "modelo_modulo": None,
        "voc_modulo": None,
        "isc_modulo": None,
        "no_inversores": None,
        "numero_fases": None,
    }

    # =========================
    # Cargar proyecto + número de módulos + dimensionamiento
    # =========================
    if selected_proyecto_id:
        proyecto = Proyecto.objects.filter(id=selected_proyecto_id).first()

        if proyecto and session_tipo != "Administrador":
            if int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para acceder a ese proyecto.")
                return redirect("core:calculo_dc")

        if proyecto:
            np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
            if np_obj:
                resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()

            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()

            # ✅ Si el proyecto usa micro inversores, bloquear módulo DC
            if dim and dim.tipo_inversor == "MICRO":
                dc_bloqueado_micro = True

            if dim and not dc_bloqueado_micro:
                detalles = list(
                    DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                    .select_related("inversor", "micro_inversor")
                    .order_by("indice")
                )

            # resumen superior
            if resultado_paneles:
                resumen["no_modulos"] = resultado_paneles.no_modulos

            if np_obj and np_obj.panel:
                resumen["modelo_modulo"] = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"
                resumen["voc_modulo"] = np_obj.panel.voc
                resumen["isc_modulo"] = np_obj.panel.isc

            if dim:
                resumen["no_inversores"] = dim.no_inversores
            resumen["numero_fases"] = proyecto.Numero_Fases

            if not dc_bloqueado_micro:
                existentes = {
                    int(x.indice): x
                    for x in CalculoDC.objects.filter(proyecto=proyecto).select_related("condulet", "resultado_dc", "conductor")
                }

                for d in detalles:
                    calc = existentes.get(int(d.indice))
                    modelo_txt = str(d.inversor or d.micro_inversor or "—")

                    # número de módulos por inversor = suma de la lista por cadena
                    lista_modulos = d.modulos_por_cadena_lista or []
                    if lista_modulos:
                        total_modulos_inversor = sum(int(v or 0) for v in lista_modulos)
                    else:
                        total_modulos_inversor = int(d.no_cadenas or 0) * int(d.modulos_por_cadena or 0)

                    bloques.append({
                        "indice": d.indice,
                        "modelo": modelo_txt,
                        "tipo": dim.tipo_inversor if dim else "INVERSOR",
                        "detalle_id": d.id,
                        "no_cadenas": d.no_cadenas,
                        "modulos_por_inversor": total_modulos_inversor,
                        "potencia_equipo": (
                            d.inversor.potencia if d.inversor_id else
                            d.micro_inversor.potencia if d.micro_inversor_id else None
                        ),
                        "val": calc,
                        "res": (calc.resultado_dc if calc and calc.resultado_dc_id else None),
                        "condulet": (calc.condulet if calc and calc.condulet_id else None),
                        "metros_por_serie": (
                            calc.metros_lineales_por_serie if calc and calc.metros_lineales_por_serie else []
                        ),
                        "series": [
                            {
                                "serie": i + 1,
                                "modulos": v,
                                "metros": (
                                    calc.metros_lineales_por_serie[i]
                                    if calc and calc.metros_lineales_por_serie and i < len(calc.metros_lineales_por_serie)
                                    else ""
                                ),
                            }
                            for i, v in enumerate(
                                lista_modulos if lista_modulos else [int(d.modulos_por_cadena or 0)] * int(d.no_cadenas or 0)
                            )
                        ],
                    })

    # =========================
    # POST: calcular / cancelar
    # =========================
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            return redirect("core:calculo_dc")

        if action == "calcular":
            proyecto_id_raw = (request.POST.get("proyecto") or "").strip()
            if not proyecto_id_raw.isdigit():
                messages.error(request, "Selecciona un proyecto válido.")
                return redirect("core:calculo_dc")

            proyecto = Proyecto.objects.filter(id=int(proyecto_id_raw)).first()
            if not proyecto:
                messages.error(request, "Proyecto inválido.")
                return redirect("core:calculo_dc")

            if session_tipo != "Administrador" and int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para calcular en ese proyecto.")
                return redirect("core:calculo_dc")

            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
            if not dim:
                messages.error(request, "Primero guarda el Dimensionamiento del proyecto.")
                return redirect(f"{reverse('core:calculo_dc')}?proyecto_id={proyecto.id}")

            # ✅ Bloquear cálculo DC si el proyecto es de micro inversores
            if dim.tipo_inversor == "MICRO":
                messages.error(
                    request,
                    "El cálculo DC no está permitido para este proyecto porque fue configurado con micro inversores. En los micro inversores solo se calcula AC."
                )
                return redirect(f"{reverse('core:calculo_dc')}?proyecto_id={proyecto.id}")

            detalles = list(
                DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                .select_related("inversor", "micro_inversor")
                .order_by("indice")
            )
            if not detalles:
                messages.error(request, "No hay detalles de dimensionamiento para este proyecto.")
                return redirect(f"{reverse('core:calculo_dc')}?proyecto_id={proyecto.id}")

            np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
            if not np_obj or not np_obj.panel or np_obj.panel.isc is None:
                messages.error(request, "No se pudo obtener Isc del panel. Primero completa 'Cálculo de módulos' y selecciona un panel con Isc.")
                return redirect(f"{reverse('core:calculo_dc')}?proyecto_id={proyecto.id}")

            isc = Decimal(str(np_obj.panel.isc))

            def resolver_amperaje_proteccion(isc_value: Decimal) -> Decimal:
                calculado = isc_value * Decimal("1.25") * Decimal("1.25")
                if calculado <= Decimal("20"):
                    return Decimal("20")
                elif calculado <= Decimal("25"):
                    return Decimal("25")
                else:
                    return Decimal("32")

            amperaje_fusible = resolver_amperaje_proteccion(isc)

            def resolver_calibre_tuberia(conductor: Conductor, hilos: int):
                cols = [
                    ("tubo_1_2_pulgada", "Tubo 1/2\" pared delgada"),
                    ("tubo_3_4_pulgada", "Tubo 3/4\" pared delgada"),
                    ("tubo_1_pulgada", "Tubo 1\" pared delgada"),
                    ("tubo_1_1_4_pulgada", "Tubo 1 1/4\" pared delgada"),
                    ("tubo_1_1_2_pulgada", "Tubo 1 1/2\" pared delgada"),
                    ("tubo_2_pulgada", "Tubo 2\" pared delgada"),
                    ("tubo_2_1_2_pulgada", "Tubo 2 1/2\" pared delgada"),
                ]

                for attr, label in cols:
                    cap = int(getattr(conductor, attr, 0) or 0)
                    if cap >= int(hilos):
                        return label

                return cols[-1][1]

            hubo_error = False

            for d in detalles:
                idx = int(d.indice)

                calibre_raw = (request.POST.get(f"calibre_cable_solar_{idx}") or "").strip()
                hilos_raw = (request.POST.get(f"hilos_tuberia_{idx}") or "").strip()

                ll_raw = (request.POST.get(f"condulet_ll_{idx}") or "0").strip()
                lr_raw = (request.POST.get(f"condulet_lr_{idx}") or "0").strip()
                lb_raw = (request.POST.get(f"condulet_lb_{idx}") or "0").strip()
                t_raw = (request.POST.get(f"condulet_t_{idx}") or "0").strip()
                c_raw = (request.POST.get(f"condulet_c_{idx}") or "0").strip()

                lista_modulos = d.modulos_por_cadena_lista or []
                if not lista_modulos:
                    lista_modulos = [int(d.modulos_por_cadena or 0)] * int(d.no_cadenas or 0)

                metros_lineales_por_serie = []
                for num_serie in range(1, len(lista_modulos) + 1):
                    metros_raw = (request.POST.get(f"metros_lineales_{idx}_{num_serie}") or "").strip()
                    try:
                        metros_serie = Decimal(metros_raw)
                        if metros_serie <= 0:
                            raise ValueError()
                    except Exception:
                        messages.error(request, f"Metros lineales inválidos en inversor {idx}, serie {num_serie}.")
                        hubo_error = True
                        metros_lineales_por_serie = []
                        break
                    metros_lineales_por_serie.append(float(metros_serie))

                if not metros_lineales_por_serie:
                    continue

                metros_lineales = Decimal(str(max(metros_lineales_por_serie)))

                if not calibre_raw:
                    messages.error(request, f"Selecciona calibre del cable solar en inversor {idx}.")
                    hubo_error = True
                    continue

                if not hilos_raw.isdigit() or int(hilos_raw) < 1:
                    messages.error(request, f"Hilos por tubería inválidos en inversor {idx}.")
                    hubo_error = True
                    continue
                hilos = int(hilos_raw)

                def to_int0(x):
                    try:
                        v = int(x)
                        return v if v >= 0 else 0
                    except Exception:
                        return 0

                ll = to_int0(ll_raw)
                lr = to_int0(lr_raw)
                lb = to_int0(lb_raw)
                tt = to_int0(t_raw)
                cc = to_int0(c_raw)

                conductor = Conductor.objects.filter(calibre_cable__iexact=calibre_raw).first()
                if not conductor:
                    messages.error(request, f"No se encontró el calibre '{calibre_raw}' en la tabla conductores.")
                    hubo_error = True
                    continue

                calibre_tuberia = resolver_calibre_tuberia(conductor, hilos)

                # total de cadenas por inversor (NO global)
                total_cadenas = int(d.no_cadenas or 0)
                total_fusibles = total_cadenas * 2

                # metros totales cable = total_cadenas * 2 * metros_lineales
                metros_totales_cable = Decimal(str(total_cadenas)) * Decimal("2") * metros_lineales

                # número total de tubos = metros_lineales / 3, redondeado hacia arriba
                total_tubos = int((metros_lineales / Decimal("3")).quantize(Decimal("1"), rounding=ROUND_UP))

                condulet_obj = Condulet.objects.create(
                    tipo_ll=ll,
                    tipo_lr=lr,
                    tipo_lb=lb,
                    tipo_t=tt,
                    tipo_c=cc,
                )

                resultado_obj = ResultadoCalculoDC.objects.create(
                    amperaje_fusible=amperaje_fusible,
                    total_de_cadenas=total_cadenas,
                    total_fusibles=total_fusibles,
                    metros_totales_cable=metros_totales_cable,
                    calibre_tuberia=calibre_tuberia,
                    total_tubos=total_tubos,
                )

                existente = CalculoDC.objects.filter(proyecto=proyecto, indice=idx).select_related("condulet", "resultado_dc").first()
                if existente:
                    old_condulet = existente.condulet
                    old_res = existente.resultado_dc

                    existente.metros_lineales = metros_lineales
                    existente.metros_lineales_por_serie = metros_lineales_por_serie
                    existente.calibre_cable_solar = calibre_raw
                    existente.hilos_tuberia = hilos
                    existente.conductor = conductor
                    existente.condulet = condulet_obj
                    existente.resultado_dc = resultado_obj
                    existente.dimensionamiento_detalle = d
                    existente.save()

                    if old_condulet:
                        old_condulet.delete()
                    if old_res:
                        old_res.delete()
                else:
                    CalculoDC.objects.create(
                        proyecto=proyecto,
                        dimensionamiento_detalle=d,
                        indice=idx,
                        metros_lineales=metros_lineales,
                        metros_lineales_por_serie=metros_lineales_por_serie,
                        calibre_cable_solar=calibre_raw,
                        hilos_tuberia=hilos,
                        conductor=conductor,
                        condulet=condulet_obj,
                        resultado_dc=resultado_obj,
                    )

            if hubo_error:
                return redirect(f"{reverse('core:calculo_dc')}?proyecto_id={proyecto.id}")

            messages.success(request, "✅ Cálculo DC realizado y guardado correctamente.")
            return redirect(f"{reverse('core:calculo_dc')}?proyecto_id={proyecto.id}")

    calibres = list(Conductor.objects.values_list("calibre_cable", flat=True).order_by("id_conductor"))

    context = {
        "proyectos": proyectos,
        "selected_proyecto_id": selected_proyecto_id,
        "proyecto": proyecto,
        "bloques": bloques,
        "calibres": calibres,
        "panel_voc": resumen["voc_modulo"],
        "resumen": resumen,
        "dc_bloqueado_micro": dc_bloqueado_micro,
    }
    return render(request, "core/pages/calculo_dc.html", context)

@require_session_login
@require_http_methods(["GET"])
def calculo_dc_pdf(request, proyecto_id: int):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    proyecto = Proyecto.objects.select_related("ID_Usuario").filter(id=proyecto_id).first()
    if not proyecto:
        messages.error(request, "Proyecto no encontrado.")
        return redirect("core:calculo_dc")

    if session_tipo != "Administrador":
        if not session_id_usuario or int(proyecto.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para descargar este PDF.")
            return redirect("core:calculo_dc")

    registros = list(
        CalculoDC.objects.filter(proyecto=proyecto)
        .select_related(
            "resultado_dc",
            "condulet",
            "dimensionamiento_detalle",
            "dimensionamiento_detalle__inversor",
            "dimensionamiento_detalle__micro_inversor",
            "conductor",
        )
        .order_by("indice")
    )

    if not registros:
        messages.error(request, "No hay cálculos DC guardados para este proyecto.")
        return redirect(f"{reverse('core:calculo_dc')}?proyecto_id={proyecto.id}")

    np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
    resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first() if np_obj else None
    dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()

    filename = f"SWGFV_CalculoDC_Proyecto_{proyecto.id}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(
        response,
        pagesize=letter,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
        title=f"Cálculo DC - Proyecto {proyecto.id}",
        author="SWGFV",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DC_Title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0B2E59"),
        spaceAfter=5,
        alignment=TA_JUSTIFY,
    )

    subtitle_style = ParagraphStyle(
        "DC_Subtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#555555"),
        spaceAfter=3,
        alignment=TA_CENTER,
    )

    section_style = ParagraphStyle(
        "DC_Section",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        textColor=colors.white,
        backColor=colors.HexColor("#0B2E59"),
        borderPadding=(4, 4, 4),
        spaceBefore=8,
        spaceAfter=8,
        alignment=TA_CENTER,
    )

    label_style = ParagraphStyle(
        "DC_Label",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        textColor=colors.HexColor("#0B2E59"),
        leading=10,
    )

    value_style = ParagraphStyle(
        "DC_Value",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        textColor=colors.HexColor("#222222"),
        leading=10,
    )

    small_style = ParagraphStyle(
        "DC_Small",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER,
    )

    elements = []

    # =========================================================
    # DATOS PREVIOS
    # =========================================================
    modelo_modulo = "—"
    voc_modulo = "—"
    isc_modulo = "—"
    no_modulos = "—"
    no_inversores = "—"

    if np_obj and np_obj.panel:
        modelo_modulo = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"
        voc_modulo = str(np_obj.panel.voc or "—")
        isc_modulo = str(np_obj.panel.isc or "—")

    if resultado_paneles:
        no_modulos = str(resultado_paneles.no_modulos or "—")

    if dim:
        no_inversores = str(dim.no_inversores or "—")

    fecha_generacion = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    # =========================================================
    # TÍTULO
    # =========================================================
    elements.append(Spacer(1, 1.4 * cm))
    elements.append(Paragraph("Reporte técnico de cálculo de corriente continua (DC)", title_style))
    elements.append(Paragraph("Sistema Web de Gestión de Proyectos Fotovoltaicos", subtitle_style))
    elements.append(Spacer(1, 0.15 * cm))

    # =========================================================
    # DATOS GENERALES
    # =========================================================
    elements.append(Paragraph("Datos generales del proyecto", section_style))

    general_data = [
        [
            Paragraph("<b>Proyecto</b>", label_style),
            Paragraph(str(proyecto.Nombre_Proyecto or "—"), value_style),
            Paragraph("<b>Empresa</b>", label_style),
            Paragraph(str(proyecto.Nombre_Empresa or "—"), value_style),
        ],
        [
            Paragraph("<b>Voltaje nominal</b>", label_style),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), value_style),
            Paragraph("<b>Número de fases</b>", label_style),
            Paragraph(str(proyecto.Numero_Fases or "—"), value_style),
        ],
        [
            Paragraph("<b>Número de módulos</b>", label_style),
            Paragraph(no_modulos, value_style),
            Paragraph("<b>Número de inversores</b>", label_style),
            Paragraph(no_inversores, value_style),
        ],
        [
            Paragraph("<b>Voc del módulo</b>", label_style),
            Paragraph(voc_modulo, value_style),
            Paragraph("<b>Isc del módulo</b>", label_style),
            Paragraph(isc_modulo, value_style),
        ],
        [
            Paragraph("<b>Modelo del módulo</b>", label_style),
            Paragraph(modelo_modulo, value_style),
            Paragraph("<b>Fecha de generación</b>", label_style),
            Paragraph(fecha_generacion, value_style),
        ],
    ]

    general_table = Table(general_data, colWidths=[3.0 * cm, 5.5 * cm, 3.0 * cm, 5.2 * cm])
    general_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.Color(1, 1, 1, alpha=0.90)),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#C9D3E0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(general_table)
    elements.append(Spacer(1, 0.35 * cm))

    # =========================================================
    # RESULTADOS POR INVERSOR
    # =========================================================
    elements.append(Paragraph("Resultados por inversor / micro inversor", section_style))

    for r in registros:
        det = r.dimensionamiento_detalle
        modelo = str((det.inversor if det else None) or (det.micro_inversor if det else None) or "—")
        tipo_equipo = "Micro inversor" if det and det.micro_inversor_id else "Inversor"

        lista_modulos = det.modulos_por_cadena_lista if det else []
        if lista_modulos:
            modulos_por_inversor = sum(int(v or 0) for v in lista_modulos)
            modulos_cadena_txt = ", ".join([f"Cad {i+1}: {v}" for i, v in enumerate(lista_modulos)])
        else:
            modulos_por_inversor = int((det.no_cadenas or 0) * (det.modulos_por_cadena or 0)) if det else 0
            modulos_cadena_txt = str(det.modulos_por_cadena or "—") if det else "—"

        res = r.resultado_dc
        con = r.condulet

        block_title = Paragraph(
            f"{tipo_equipo} {r.indice} - {modelo}",
            ParagraphStyle(
                f"block_{r.indice}",
                parent=styles["Heading4"],
                fontName="Helvetica-Bold",
                fontSize=9.5,
                textColor=colors.HexColor("#0B2E59"),
                spaceAfter=6,
                spaceBefore=4,
                alignment=TA_JUSTIFY,
            )
        )
        elements.append(block_title)

        data = [
            [
                Paragraph("<b>Número de series</b>", label_style),
                Paragraph(str(det.no_cadenas if det else "—"), value_style),
                Paragraph("<b>Número de módulos por inversor</b>", label_style),
                Paragraph(str(modulos_por_inversor), value_style),
            ],
            [
                Paragraph("<b>Módulos por cadena</b>", label_style),
                Paragraph(modulos_cadena_txt, value_style),
                Paragraph("<b>Metros lineales</b>", label_style),
                Paragraph(str(r.metros_lineales or "—"), value_style),
            ],
            [
                Paragraph("<b>Calibre cable solar</b>", label_style),
                Paragraph(str(r.calibre_cable_solar or "—"), value_style),
                Paragraph("<b>Hilos por tubería</b>", label_style),
                Paragraph(str(r.hilos_tuberia or "—"), value_style),
            ],
            [
                Paragraph("<b>Amperaje protección</b>", label_style),
                Paragraph(f"{getattr(res, 'amperaje_fusible', '—')} A" if res else "—", value_style),
                Paragraph("<b>Total de cadenas</b>", label_style),
                Paragraph(str(getattr(res, "total_de_cadenas", "—")) if res else "—", value_style),
            ],
            [
                Paragraph("<b>Total fusibles</b>", label_style),
                Paragraph(str(getattr(res, "total_fusibles", "—")) if res else "—", value_style),
                Paragraph("<b>Metros totales cable</b>", label_style),
                Paragraph(str(getattr(res, "metros_totales_cable", "—")) if res else "—", value_style),
            ],
            [
                Paragraph("<b>Calibre tubería</b>", label_style),
                Paragraph(str(getattr(res, "calibre_tuberia", "—")) if res else "—", value_style),
                Paragraph("<b>Total tubos</b>", label_style),
                Paragraph(str(getattr(res, "total_tubos", "—")) if res else "—", value_style),
            ],
            [
                Paragraph("<b>Condulets LL / LR / LB / T / C</b>", label_style),
                Paragraph(
                    f"{con.tipo_ll if con else 0} / {con.tipo_lr if con else 0} / {con.tipo_lb if con else 0} / {con.tipo_t if con else 0} / {con.tipo_c if con else 0}",
                    value_style
                ),
                Paragraph("<b>Total condulets</b>", label_style),
                Paragraph(str(con.total() if con else 0), value_style),
            ],
        ]

        table = Table(data, colWidths=[3.8 * cm, 4.1 * cm, 4.0 * cm, 4.1 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.Color(1, 1, 1, alpha=0.92)),
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#C9D3E0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.Color(1, 1, 1, alpha=0.96), colors.HexColor("#F8FBFF")]),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.25 * cm))

    # =========================================================
    # FONDO / MEMBRETE
    # =========================================================
    def draw_page_background(canvas, doc):
        width, height = letter

        bg_path = finders.find("core/img/hoja_membretada.png")
        if bg_path:
            try:
                canvas.drawImage(bg_path, 0, 0, width=width, height=height, preserveAspectRatio=False, mask='auto')
            except Exception:
                pass

    doc.build(elements, onFirstPage=draw_page_background, onLaterPages=draw_page_background)
    return response

@require_session_login
@require_http_methods(["GET"])
def calculo_ac_pdf(request, proyecto_id: int):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    proyecto = Proyecto.objects.select_related("ID_Usuario").filter(id=proyecto_id).first()
    if not proyecto:
        messages.error(request, "Proyecto no encontrado.")
        return redirect("core:calculo_ac")

    if session_tipo != "Administrador":
        if not session_id_usuario or int(proyecto.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para descargar este PDF.")
            return redirect("core:calculo_ac")

    registros = list(
        CalculoAC.objects.filter(proyecto=proyecto)
        .select_related(
            "resultado_ac",
            "condulet",
            "dimensionamiento_detalle",
            "dimensionamiento_detalle__inversor",
            "dimensionamiento_detalle__micro_inversor",
            "conductor",
        )
        .order_by("indice")
    )

    if not registros:
        messages.error(request, "No hay cálculos AC guardados para este proyecto.")
        return redirect(f"{reverse('core:calculo_ac')}?proyecto_id={proyecto.id}")

    np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
    resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first() if np_obj else None
    dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()

    filename = f"SWGFV_CalculoAC_Proyecto_{proyecto.id}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = build_fortia_doc(response, f"Cálculo AC - Proyecto {proyecto.id}")
    pdfs = get_fortia_styles()
    elements = []

    add_fortia_header(
        elements,
        "Reporte técnico de cálculo de corriente alterna (AC)",
        "Sistema Web de Gestión de Proyectos Fotovoltaicos",
        pdfs
    )

    elements.append(Paragraph("Datos generales del proyecto", pdfs["section"]))

    modelo_modulo = "—"
    voc_modulo = "—"
    isc_modulo = "—"
    no_modulos = "—"
    no_inversores = "—"

    if np_obj and np_obj.panel:
        modelo_modulo = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"
        voc_modulo = str(np_obj.panel.voc or "—")
        isc_modulo = str(np_obj.panel.isc or "—")

    if resultado_paneles:
        no_modulos = str(resultado_paneles.no_modulos or "—")

    if dim:
        no_inversores = str(dim.no_inversores or "—")

    fecha_generacion = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    general_data = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Proyecto or "—"), pdfs["value"]),
            Paragraph("<b>Empresa</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Empresa or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Voltaje nominal</b>", pdfs["label"]),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), pdfs["value"]),
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de módulos</b>", pdfs["label"]),
            Paragraph(no_modulos, pdfs["value"]),
            Paragraph("<b>Número de inversores</b>", pdfs["label"]),
            Paragraph(no_inversores, pdfs["value"]),
        ],
        [
            Paragraph("<b>Voc del módulo</b>", pdfs["label"]),
            Paragraph(voc_modulo, pdfs["value"]),
            Paragraph("<b>Isc del módulo</b>", pdfs["label"]),
            Paragraph(isc_modulo, pdfs["value"]),
        ],
        [
            Paragraph("<b>Modelo del módulo</b>", pdfs["label"]),
            Paragraph(modelo_modulo, pdfs["value"]),
            Paragraph("<b>Fecha de generación</b>", pdfs["label"]),
            Paragraph(fecha_generacion, pdfs["value"]),
        ],
    ]

    elements.append(make_info_table(general_data, [3.2 * cm, 5.2 * cm, 3.3 * cm, 4.8 * cm]))
    elements.append(Spacer(1, 0.25 * cm))
    elements.append(Paragraph("Resultados por inversor / micro inversor", pdfs["section"]))

    for r in registros:
        det = r.dimensionamiento_detalle
        modelo = str((det.inversor if det else None) or (det.micro_inversor if det else None) or "—")
        tipo_equipo = "Micro inversor" if det and det.micro_inversor_id else "Inversor"

        lista_modulos = det.modulos_por_cadena_lista if det else []
        if lista_modulos:
            modulos_por_inversor = sum(int(v or 0) for v in lista_modulos)
            modulos_cadena_txt = ", ".join([f"Cad {i+1}: {v}" for i, v in enumerate(lista_modulos)])
        else:
            modulos_por_inversor = int((det.no_cadenas or 0) * (det.modulos_por_cadena or 0)) if det else 0
            modulos_cadena_txt = str(det.modulos_por_cadena or "—") if det else "—"

        corriente_salida = "—"
        if det:
            if det.inversor_id and det.inversor and det.inversor.corriente_salida is not None:
                corriente_salida = f"{det.inversor.corriente_salida} A"
            elif det.micro_inversor_id and det.micro_inversor and det.micro_inversor.corriente_salida is not None:
                corriente_salida = f"{det.micro_inversor.corriente_salida} A"

        res = r.resultado_ac
        con = r.condulet

        elements.append(Paragraph(f"{tipo_equipo} {r.indice} — {modelo}", pdfs["block_title"]))

        data = [
            [
                Paragraph("<b>Número de series</b>", pdfs["label"]),
                Paragraph(str(det.no_cadenas if det else "—"), pdfs["value"]),
                Paragraph("<b>Número de módulos por inversor</b>", pdfs["label"]),
                Paragraph(str(modulos_por_inversor), pdfs["value"]),
            ],
            [
                Paragraph("<b>Módulos por cadena</b>", pdfs["label"]),
                Paragraph(modulos_cadena_txt, pdfs["value"]),
                Paragraph("<b>Corriente de salida</b>", pdfs["label"]),
                Paragraph(corriente_salida, pdfs["value"]),
            ],
            [
                Paragraph("<b>Metros lineales por fase</b>", pdfs["label"]),
                Paragraph(str(r.metros_lineales_ac or "—"), pdfs["value"]),
                Paragraph("<b>Calibre cable THHW</b>", pdfs["label"]),
                Paragraph(str(r.calibre_cable_thhw or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Hilos por tubería</b>", pdfs["label"]),
                Paragraph(str(r.hilos_tuberia_ac or "—"), pdfs["value"]),
                Paragraph("<b>Amperaje protección</b>", pdfs["label"]),
                Paragraph(f"{getattr(res, 'amperaje_proteccion', '—')} A" if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Total de cadenas</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_de_cadenas_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Total protecciones</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_protecciones", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Metros totales cable</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "metros_totales_cable_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Calibre tubería</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "calibre_tuberia_ac", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Total tubos</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_tubos_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Condulets LL / LR / LB / T / C</b>", pdfs["label"]),
                Paragraph(
                    f"{con.tipo_ll if con else 0} / {con.tipo_lr if con else 0} / {con.tipo_lb if con else 0} / {con.tipo_t if con else 0} / {con.tipo_c if con else 0}",
                    pdfs["value"]
                ),
            ],
        ]

        elements.append(make_info_table(data, [3.8 * cm, 4.1 * cm, 4.0 * cm, 4.1 * cm]))
        elements.append(Spacer(1, 0.2 * cm))

    add_fortia_footer(elements, pdfs)
    doc.build(elements, onFirstPage=draw_fortia_letterhead, onLaterPages=draw_fortia_letterhead)
    return response

@require_session_login
@require_http_methods(["GET", "POST"])
def calculo_ac(request):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.all().order_by("-id")
    else:
        proyectos = Proyecto.objects.filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    selected_raw = (request.POST.get("proyecto") or request.GET.get("proyecto_id") or "").strip()
    selected_proyecto_id = int(selected_raw) if selected_raw.isdigit() else None

    proyecto = None
    dim = None
    detalles = []
    np_obj = None
    resultado_paneles = None

    bloques = []

    resumen = {
        "no_modulos": None,
        "modelo_modulo": None,
        "voc_modulo": None,
        "isc_modulo": None,
        "no_inversores": None,
        "numero_fases": None,
        "corrientes_salida": [],
    }

    def evaluar_proteccion(corriente_salida, amperaje_proteccion):
        if corriente_salida is None or amperaje_proteccion is None:
            return None

        cs = Decimal(str(corriente_salida))
        ap = Decimal(str(amperaje_proteccion))

        if ap < cs:
            return {
                "estado": "error",
                "titulo": "Error",
                "mensaje": f"La protección ({ap} A) es menor que la corriente de salida del inversor ({cs} A)."
            }
        elif ap == cs:
            return {
                "estado": "advertencia",
                "titulo": "Advertencia",
                "mensaje": f"La protección ({ap} A) es igual a la corriente de salida del inversor ({cs} A)."
            }
        else:
            return {
                "estado": "ok",
                "titulo": "Correcto",
                "mensaje": f"La protección ({ap} A) es mayor que la corriente de salida del inversor ({cs} A)."
            }

    if selected_proyecto_id:
        proyecto = Proyecto.objects.filter(id=selected_proyecto_id).first()

        if proyecto and session_tipo != "Administrador":
            if int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para acceder a ese proyecto.")
                return redirect("core:calculo_ac")

        if proyecto:
            np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
            if np_obj:
                resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()

            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
            if dim:
                detalles = list(
                    DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                    .select_related("inversor", "micro_inversor")
                    .order_by("indice")
                )

            if resultado_paneles:
                resumen["no_modulos"] = resultado_paneles.no_modulos

            if np_obj and np_obj.panel:
                resumen["modelo_modulo"] = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"
                resumen["voc_modulo"] = np_obj.panel.voc
                resumen["isc_modulo"] = np_obj.panel.isc

            if dim:
                resumen["no_inversores"] = dim.no_inversores

            resumen["numero_fases"] = proyecto.Numero_Fases

            existentes = {
                int(x.indice): x
                for x in CalculoAC.objects.filter(proyecto=proyecto).select_related("condulet", "resultado_ac", "conductor")
            }

            corrientes_salida_resumen = []

            for d in detalles:
                calc = existentes.get(int(d.indice))
                modelo_txt = str(d.inversor or d.micro_inversor or "—")

                lista_modulos = d.modulos_por_cadena_lista or []
                if lista_modulos:
                    total_modulos_inversor = sum(int(v or 0) for v in lista_modulos)
                else:
                    total_modulos_inversor = int(d.no_cadenas or 0) * int(d.modulos_por_cadena or 0)

                corriente_salida = None
                if d.inversor_id and d.inversor and d.inversor.corriente_salida is not None:
                    corriente_salida = d.inversor.corriente_salida
                elif d.micro_inversor_id and d.micro_inversor and d.micro_inversor.corriente_salida is not None:
                    corriente_salida = d.micro_inversor.corriente_salida

                if corriente_salida is not None:
                    tipo_equipo = "Micro inversor" if d.micro_inversor_id else "Inversor"
                    corrientes_salida_resumen.append({
                        "indice": d.indice,
                        "tipo": tipo_equipo,
                        "valor": corriente_salida,
                    })

                res_obj = calc.resultado_ac if calc and calc.resultado_ac_id else None
                validacion_proteccion = evaluar_proteccion(
                    corriente_salida,
                    getattr(res_obj, "amperaje_proteccion", None)
                )

                bloques.append({
                    "indice": d.indice,
                    "modelo": modelo_txt,
                    "tipo": dim.tipo_inversor if dim else "INVERSOR",
                    "detalle_id": d.id,
                    "no_cadenas": d.no_cadenas,
                    "modulos_por_inversor": total_modulos_inversor,
                    "potencia_equipo": (
                        d.inversor.potencia if d.inversor_id else
                        d.micro_inversor.potencia if d.micro_inversor_id else None
                    ),
                    "corriente_salida": corriente_salida,
                    "val": calc,
                    "res": res_obj,
                    "condulet": (calc.condulet if calc and calc.condulet_id else None),
                    "validacion_proteccion": validacion_proteccion,
                })

            resumen["corrientes_salida"] = corrientes_salida_resumen

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            return redirect("core:calculo_ac")

        if action == "calcular":
            proyecto_id_raw = (request.POST.get("proyecto") or "").strip()
            if not proyecto_id_raw.isdigit():
                messages.error(request, "Selecciona un proyecto válido.")
                return redirect("core:calculo_ac")

            proyecto = Proyecto.objects.filter(id=int(proyecto_id_raw)).first()
            if not proyecto:
                messages.error(request, "Proyecto inválido.")
                return redirect("core:calculo_ac")

            if session_tipo != "Administrador" and int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para calcular en ese proyecto.")
                return redirect("core:calculo_ac")

            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
            if not dim:
                messages.error(request, "Primero guarda el Dimensionamiento del proyecto.")
                return redirect(f"{reverse('core:calculo_ac')}?proyecto_id={proyecto.id}")

            detalles = list(
                DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                .select_related("inversor", "micro_inversor")
                .order_by("indice")
            )
            if not detalles:
                messages.error(request, "No hay detalles de dimensionamiento para este proyecto.")
                return redirect(f"{reverse('core:calculo_ac')}?proyecto_id={proyecto.id}")

            voltaje_txt = str(proyecto.Voltaje_Nominal or "").strip()
            voltaje_num = None
            try:
                parte = voltaje_txt.split("/")[0].strip()
                voltaje_num = Decimal(parte)
            except Exception:
                messages.error(request, "El voltaje nominal del proyecto no es válido para cálculo AC.")
                return redirect(f"{reverse('core:calculo_ac')}?proyecto_id={proyecto.id}")

            def resolver_proteccion_comercial(valor_decimal: Decimal) -> Decimal:
                opciones = [
                    Decimal("20"), Decimal("25"), Decimal("32"), Decimal("40"),
                    Decimal("50"), Decimal("63"), Decimal("80"), Decimal("100"),
                    Decimal("125"), Decimal("160"), Decimal("200"), Decimal("250"),
                ]
                for op in opciones:
                    if valor_decimal <= op:
                        return op
                return opciones[-1]

            def resolver_calibre_tuberia(conductor: Conductor, hilos: int):
                cols = [
                    ("tubo_1_2_pulgada", "Tubo 1/2\" pared delgada"),
                    ("tubo_3_4_pulgada", "Tubo 3/4\" pared delgada"),
                    ("tubo_1_pulgada", "Tubo 1\" pared delgada"),
                    ("tubo_1_1_4_pulgada", "Tubo 1 1/4\" pared delgada"),
                    ("tubo_1_1_2_pulgada", "Tubo 1 1/2\" pared delgada"),
                    ("tubo_2_pulgada", "Tubo 2\" pared delgada"),
                    ("tubo_2_1_2_pulgada", "Tubo 2 1/2\" pared delgada"),
                ]
                for attr, label in cols:
                    cap = int(getattr(conductor, attr, 0) or 0)
                    if cap >= int(hilos):
                        return label
                return cols[-1][1]

            hubo_error = False
            numero_fases = int(proyecto.Numero_Fases or 0)

            for d in detalles:
                idx = int(d.indice)

                metros_raw = (request.POST.get(f"metros_lineales_ac_{idx}") or "").strip()
                calibre_raw = (request.POST.get(f"calibre_cable_thhw_{idx}") or "").strip()
                hilos_raw = (request.POST.get(f"hilos_tuberia_ac_{idx}") or "").strip()

                ll_raw = (request.POST.get(f"condulet_ll_{idx}") or "0").strip()
                lr_raw = (request.POST.get(f"condulet_lr_{idx}") or "0").strip()
                lb_raw = (request.POST.get(f"condulet_lb_{idx}") or "0").strip()
                t_raw = (request.POST.get(f"condulet_t_{idx}") or "0").strip()
                c_raw = (request.POST.get(f"condulet_c_{idx}") or "0").strip()

                try:
                    metros_lineales_ac = Decimal(metros_raw)
                    if metros_lineales_ac <= 0:
                        raise ValueError()
                except Exception:
                    messages.error(request, f"Metros lineales por fase inválidos en inversor {idx}.")
                    hubo_error = True
                    continue

                if not calibre_raw:
                    messages.error(request, f"Selecciona calibre del cable THHW en inversor {idx}.")
                    hubo_error = True
                    continue

                if not hilos_raw.isdigit() or int(hilos_raw) < 1:
                    messages.error(request, f"Hilos por tubería inválidos en inversor {idx}.")
                    hubo_error = True
                    continue
                hilos = int(hilos_raw)

                def to_int0(x):
                    try:
                        v = int(x)
                        return v if v >= 0 else 0
                    except Exception:
                        return 0

                ll = to_int0(ll_raw)
                lr = to_int0(lr_raw)
                lb = to_int0(lb_raw)
                tt = to_int0(t_raw)
                cc = to_int0(c_raw)

                conductor = Conductor.objects.filter(calibre_cable__iexact=calibre_raw).first()
                if not conductor:
                    messages.error(request, f"No se encontró el calibre '{calibre_raw}' en la tabla conductores.")
                    hubo_error = True
                    continue

                potencia_equipo = None
                if d.inversor_id and d.inversor and d.inversor.potencia is not None:
                    potencia_equipo = Decimal(str(d.inversor.potencia))
                elif d.micro_inversor_id and d.micro_inversor and d.micro_inversor.potencia is not None:
                    potencia_equipo = Decimal(str(d.micro_inversor.potencia))

                if potencia_equipo is None or potencia_equipo <= 0:
                    messages.error(request, f"No se encontró potencia válida para el inversor {idx}.")
                    hubo_error = True
                    continue

                if numero_fases in (1, 2):
                    amperaje_calculado = (potencia_equipo / voltaje_num) * Decimal("1.25")
                else:
                    amperaje_calculado = (potencia_equipo / (voltaje_num * Decimal("1.732050"))) * Decimal("1.25")

                amperaje_proteccion = resolver_proteccion_comercial(amperaje_calculado)

                total_de_cadenas_ac = int(d.no_cadenas or 0)
                total_protecciones = 1
                metros_totales_cable_ac = metros_lineales_ac * Decimal(str(numero_fases))
                calibre_tuberia_ac = resolver_calibre_tuberia(conductor, hilos)
                total_tubos_ac = int((metros_lineales_ac / Decimal("3")).quantize(Decimal("1"), rounding=ROUND_UP))

                condulet_obj = Condulet.objects.create(
                    tipo_ll=ll,
                    tipo_lr=lr,
                    tipo_lb=lb,
                    tipo_t=tt,
                    tipo_c=cc,
                )

                resultado_obj = ResultadoCalculoAC.objects.create(
                    amperaje_proteccion=amperaje_proteccion,
                    total_de_cadenas_ac=total_de_cadenas_ac,
                    total_protecciones=total_protecciones,
                    metros_totales_cable_ac=metros_totales_cable_ac,
                    calibre_tuberia_ac=calibre_tuberia_ac,
                    total_tubos_ac=total_tubos_ac,
                )

                existente = CalculoAC.objects.filter(proyecto=proyecto, indice=idx).select_related("condulet", "resultado_ac").first()
                if existente:
                    old_condulet = existente.condulet
                    old_res = existente.resultado_ac

                    existente.metros_lineales_ac = metros_lineales_ac
                    existente.calibre_cable_thhw = calibre_raw
                    existente.hilos_tuberia_ac = hilos
                    existente.conductor = conductor
                    existente.condulet = condulet_obj
                    existente.resultado_ac = resultado_obj
                    existente.dimensionamiento_detalle = d
                    existente.save()

                    if old_condulet:
                        old_condulet.delete()
                    if old_res:
                        old_res.delete()
                else:
                    CalculoAC.objects.create(
                        proyecto=proyecto,
                        dimensionamiento_detalle=d,
                        indice=idx,
                        metros_lineales_ac=metros_lineales_ac,
                        calibre_cable_thhw=calibre_raw,
                        hilos_tuberia_ac=hilos,
                        conductor=conductor,
                        condulet=condulet_obj,
                        resultado_ac=resultado_obj,
                    )

            if hubo_error:
                return redirect(f"{reverse('core:calculo_ac')}?proyecto_id={proyecto.id}")

            messages.success(request, "✅ Cálculo AC realizado y guardado correctamente.")
            return redirect(f"{reverse('core:calculo_ac')}?proyecto_id={proyecto.id}")

    calibres = list(Conductor.objects.values_list("calibre_cable", flat=True).order_by("id_conductor"))

    context = {
        "proyectos": proyectos,
        "selected_proyecto_id": selected_proyecto_id,
        "proyecto": proyecto,
        "bloques": bloques,
        "calibres": calibres,
        "resumen": resumen,
    }
    return render(request, "core/pages/calculo_ac.html", context)

@require_session_login
@require_http_methods(["GET", "POST"])
def calculo_caida_tension(request):
    from decimal import Decimal, ROUND_HALF_UP
    import math

    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.all().order_by("-id")
    else:
        proyectos = Proyecto.objects.filter(ID_Usuario_id=session_id_usuario).order_by("-id")

    selected_raw = (request.POST.get("proyecto") or request.GET.get("proyecto_id") or "").strip()
    selected_proyecto_id = int(selected_raw) if selected_raw.isdigit() else None

    proyecto = None
    dim = None
    detalles = []
    np_obj = None
    resultado_paneles = None

    bloques = []

    resumen = {
        "no_modulos": None,
        "modelo_modulo": None,
        "voc_modulo": None,
        "isc_modulo": None,
        "no_inversores": None,
        "numero_fases": None,
        "voltaje_sitio": None,
    }

    def D(val, nd=6):
        return Decimal(str(val)).quantize(Decimal("1." + "0" * nd), rounding=ROUND_HALF_UP)

    def extraer_awg(calibre_txt: str):
        txt = (calibre_txt or "").strip().upper()
        if "AWG" not in txt:
            return None
        base = txt.replace("AWG", "").strip()
        if "/" in base:
            return None
        return int(base) if base.isdigit() else None

    def evaluar_caida_ac(porcentaje):
        if porcentaje is None:
            return None

        p = Decimal(str(porcentaje))
        if p > Decimal("5"):
            return {
                "estado": "error",
                "titulo": "Error",
                "mensaje": f"La caída de tensión AC es {p}% y supera el 5%."
            }
        elif p > Decimal("3"):
            return {
                "estado": "advertencia",
                "titulo": "Advertencia",
                "mensaje": f"La caída de tensión AC es {p}% y supera el 3%."
            }
        else:
            return {
                "estado": "ok",
                "titulo": "Correcto",
                "mensaje": f"La caída de tensión AC es {p}% y está dentro del límite recomendado."
            }

    def resolver_conductor_desde_calculo(calc_obj, campo_calibre):
        """
        Si el cálculo existe pero viene sin FK conductor, intenta resolverlo por calibre.
        Esto corrige registros viejos que sí tenían datos guardados pero no relación.
        """
        if not calc_obj:
            return None

        if getattr(calc_obj, "conductor", None):
            return calc_obj.conductor

        calibre_txt = getattr(calc_obj, campo_calibre, None)
        if not calibre_txt:
            return None

        conductor = Conductor.objects.filter(calibre_cable__iexact=str(calibre_txt).strip()).first()
        if conductor:
            calc_obj.conductor = conductor
            calc_obj.save(update_fields=["conductor"])
        return conductor

    if selected_proyecto_id:
        proyecto = Proyecto.objects.filter(id=selected_proyecto_id).first()

        if proyecto and session_tipo != "Administrador":
            if int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para acceder a ese proyecto.")
                return redirect("core:calculo_caida_tension")

        if proyecto:
            np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
            if np_obj:
                resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first()

            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
            if dim:
                detalles = list(
                    DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                    .select_related("inversor", "micro_inversor")
                    .order_by("indice")
                )

            if resultado_paneles:
                resumen["no_modulos"] = resultado_paneles.no_modulos

            if np_obj and np_obj.panel:
                resumen["modelo_modulo"] = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"
                resumen["voc_modulo"] = np_obj.panel.voc
                resumen["isc_modulo"] = np_obj.panel.isc

            if dim:
                resumen["no_inversores"] = dim.no_inversores

            resumen["numero_fases"] = proyecto.Numero_Fases
            resumen["voltaje_sitio"] = proyecto.Voltaje_Nominal

            calculos_ac = {
                int(x.indice): x
                for x in CalculoAC.objects.filter(proyecto=proyecto).select_related("resultado_ac", "conductor")
            }

            calculos_dc = {
                int(x.indice): x
                for x in CalculoDC.objects.filter(proyecto=proyecto).select_related("resultado_dc", "conductor")
            }

            tensiones = list(
                CalculoTension.objects.filter(proyecto=proyecto)
                .select_related("resultado_tension", "tension_ac", "tension_dc")
                .order_by("indice", "tipo_calculo", "serie")
            )

            tensiones_ac = {}
            tensiones_dc = {}
            for t in tensiones:
                if t.tipo_calculo == "AC":
                    tensiones_ac[int(t.indice)] = t
                else:
                    tensiones_dc[(int(t.indice), int(t.serie or 0))] = t

            for d in detalles:
                idx = int(d.indice)
                calc_ac = calculos_ac.get(idx)
                calc_dc = calculos_dc.get(idx)

                modelo_txt = str(d.inversor or d.micro_inversor or "—")

                lista_modulos = d.modulos_por_cadena_lista or []
                if lista_modulos:
                    total_modulos_inversor = sum(int(v or 0) for v in lista_modulos)
                    series_dc = [{"serie": i + 1, "modulos": v} for i, v in enumerate(lista_modulos)]
                else:
                    total_modulos_inversor = int(d.no_cadenas or 0) * int(d.modulos_por_cadena or 0)
                    series_dc = [{"serie": i + 1, "modulos": int(d.modulos_por_cadena or 0)} for i in range(int(d.no_cadenas or 0))]

                corriente_salida = None
                if d.inversor_id and d.inversor:
                    corriente_salida = d.inversor.corriente_salida
                elif d.micro_inversor_id and d.micro_inversor:
                    corriente_salida = d.micro_inversor.corriente_salida

                tension_ac_obj = tensiones_ac.get(idx)
                validacion_caida_ac = None
                if tension_ac_obj and tension_ac_obj.resultado_tension:
                    validacion_caida_ac = evaluar_caida_ac(
                        getattr(tension_ac_obj.resultado_tension, "porcentaje_voltaje_tension_ac", None)
                    )

                bloques.append({
                    "indice": idx,
                    "tipo": dim.tipo_inversor if dim else "INVERSOR",
                    "modelo": modelo_txt,
                    "corriente_salida": corriente_salida,
                    "no_cadenas": d.no_cadenas,
                    "modulos_por_inversor": total_modulos_inversor,
                    "longitud_total_ac": getattr(calc_ac, "metros_lineales_ac", None),
                    "longitud_total_dc": getattr(calc_dc, "metros_lineales", None),
                    "calc_ac": calc_ac,
                    "tension_ac": tension_ac_obj,
                    "validacion_caida_ac": validacion_caida_ac,
                    "series_dc": [
                        {
                            "serie": s["serie"],
                            "modulos": s["modulos"],
                            "tension_dc": tensiones_dc.get((idx, s["serie"]))
                        }
                        for s in series_dc
                    ],
                })

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            return redirect("core:calculo_caida_tension")

        if action == "calcular":
            proyecto_id_raw = (request.POST.get("proyecto") or "").strip()
            if not proyecto_id_raw.isdigit():
                messages.error(request, "Selecciona un proyecto válido.")
                return redirect("core:calculo_caida_tension")

            proyecto = Proyecto.objects.filter(id=int(proyecto_id_raw)).first()
            if not proyecto:
                messages.error(request, "Proyecto inválido.")
                return redirect("core:calculo_caida_tension")

            if session_tipo != "Administrador" and int(proyecto.ID_Usuario_id) != int(session_id_usuario):
                messages.error(request, "No tienes permisos para calcular en ese proyecto.")
                return redirect("core:calculo_caida_tension")

            dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
            if not dim:
                messages.error(request, "Primero guarda el Dimensionamiento del proyecto.")
                return redirect(f"{reverse('core:calculo_caida_tension')}?proyecto_id={proyecto.id}")

            np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
            if not np_obj or not np_obj.panel:
                messages.error(request, "Primero realiza el cálculo de módulos.")
                return redirect(f"{reverse('core:calculo_caida_tension')}?proyecto_id={proyecto.id}")

            detalles = list(
                DimensionamientoDetalle.objects.filter(dimensionamiento=dim)
                .select_related("inversor", "micro_inversor")
                .order_by("indice")
            )

            voltaje_txt = str(proyecto.Voltaje_Nominal or "").strip()
            try:
                voltaje_num = Decimal(voltaje_txt.split("/")[0].strip())
            except Exception:
                messages.error(request, "Voltaje nominal del proyecto inválido.")
                return redirect(f"{reverse('core:calculo_caida_tension')}?proyecto_id={proyecto.id}")

            hubo_error = False

            for d in detalles:
                idx = int(d.indice)
                calc_ac = CalculoAC.objects.filter(proyecto=proyecto, indice=idx).select_related("conductor").first()
                conductor_ac = resolver_conductor_desde_calculo(calc_ac, "calibre_cable_thhw")

                if not calc_ac or not conductor_ac:
                    messages.error(request, f"Primero realiza y guarda correctamente el cálculo AC del inversor {idx}.")
                    hubo_error = True
                    continue

                tipo_cable_ac = (request.POST.get(f"tipo_cable_ac_{idx}") or "").strip().lower()
                temp_ac_raw = (request.POST.get(f"temperatura_ac_{idx}") or "").strip()
                fp_ac_raw = (request.POST.get(f"factor_potencia_ac_{idx}") or "").strip()

                if tipo_cable_ac not in ("cobre", "aluminio"):
                    messages.error(request, f"Selecciona tipo de cable AC válido en inversor {idx}.")
                    hubo_error = True
                    continue

                try:
                    temperatura_ac = Decimal(temp_ac_raw)
                    factor_potencia_ac = Decimal(fp_ac_raw)
                except Exception:
                    messages.error(request, f"Temperatura o factor de potencia AC inválidos en inversor {idx}.")
                    hubo_error = True
                    continue

                awg = extraer_awg(calc_ac.calibre_cable_thhw)
                if awg is None:
                    messages.error(request, f"No se pudo relacionar el calibre THHW del inversor {idx} con tabla AWG.")
                    hubo_error = True
                    continue

                tabla_awg = TablaConductoresAWGConReactancia.objects.filter(calibre_awg=awg).first()
                if not tabla_awg:
                    messages.error(request, f"No existe registro AWG {awg} en tabla_conductores_awg_con_reactancia.")
                    hubo_error = True
                    continue

                corriente_salida = None
                if d.inversor_id and d.inversor and d.inversor.corriente_salida is not None:
                    corriente_salida = Decimal(str(d.inversor.corriente_salida))
                elif d.micro_inversor_id and d.micro_inversor and d.micro_inversor.corriente_salida is not None:
                    corriente_salida = Decimal(str(d.micro_inversor.corriente_salida))

                if corriente_salida is None:
                    messages.error(request, f"No se encontró corriente de salida válida para el inversor {idx}.")
                    hubo_error = True
                    continue

                longitud_ac = Decimal(str(calc_ac.metros_lineales_ac or 0)) / Decimal("1000")
                resistencia_ca = Decimal(str(tabla_awg.resistencia_ca or 0))
                reactancia = Decimal(str(tabla_awg.reactancia or 0))

                coef = Decimal("0.00393") if tipo_cable_ac == "cobre" else Decimal("0.00403")
                calculo_rt_ac = resistencia_ca * (Decimal("1") + coef * (temperatura_ac - Decimal("20")))
                raiz_fp = Decimal(str(math.sqrt(max(0.0, 1.0 - float(factor_potencia_ac) ** 2))))

                if int(proyecto.Numero_Fases or 0) in (1, 2):
                    voltaje_tension_ac_calculado = Decimal("2") * corriente_salida * longitud_ac * (
                        (calculo_rt_ac * factor_potencia_ac) + (reactancia * raiz_fp)
                    )
                else:
                    voltaje_tension_ac_calculado = Decimal(str(math.sqrt(3))) * corriente_salida * longitud_ac * (
                        (calculo_rt_ac * factor_potencia_ac) + (reactancia * raiz_fp)
                    )

                # =====================================================
                # AJUSTE SOLICITADO:
                # - "Voltaje de caída de tensión AC" tomará el valor que
                #   actualmente estaba usando "Porcentaje de caída..."
                # - "Porcentaje de caída de tensión AC" se recalcula como:
                #   (Voltaje de caída de tensión AC / voltaje de sitio) * 100
                # =====================================================
                voltaje_tension_ac = (
                    (voltaje_tension_ac_calculado / voltaje_num) * Decimal("100")
                    if voltaje_num > 0 else Decimal("0")
                )

                porcentaje_voltaje_tension_ac = (
                    (voltaje_tension_ac / voltaje_num) * Decimal("100")
                    if voltaje_num > 0 else Decimal("0")
                )

                resultado_obj = ResultadoTension.objects.create(
                    voltaje_tension_ac=D(voltaje_tension_ac),
                    porcentaje_voltaje_tension_ac=D(porcentaje_voltaje_tension_ac),
                    calculo_rt_ac=D(calculo_rt_ac),
                    corriente_corregida=D(corriente_salida),
                )

                existente = CalculoTension.objects.filter(
                    proyecto=proyecto,
                    indice=idx,
                    tipo_calculo="AC",
                    serie__isnull=True,
                ).select_related("resultado_tension").first()

                if existente and existente.resultado_tension:
                    existente.resultado_tension.delete()

                if existente:
                    existente.tension_ac = calc_ac
                    existente.factor_potencia_ac = factor_potencia_ac
                    existente.temperatura_ac = temperatura_ac
                    existente.longitud_ac = longitud_ac
                    existente.tipo_cable_ac = tipo_cable_ac
                    existente.resultado_tension = resultado_obj
                    existente.save()
                else:
                    CalculoTension.objects.create(
                        proyecto=proyecto,
                        tension_ac=calc_ac,
                        indice=idx,
                        tipo_calculo="AC",
                        serie=None,
                        factor_potencia_ac=factor_potencia_ac,
                        temperatura_ac=temperatura_ac,
                        longitud_ac=longitud_ac,
                        tipo_cable_ac=tipo_cable_ac,
                        resultado_tension=resultado_obj,
                    )

            for d in detalles:
                if d.micro_inversor_id:
                    continue

                idx = int(d.indice)
                calc_dc = CalculoDC.objects.filter(proyecto=proyecto, indice=idx).select_related("conductor").first()
                conductor_dc = resolver_conductor_desde_calculo(calc_dc, "calibre_cable_solar")

                if not calc_dc or not conductor_dc:
                    messages.error(request, f"Primero realiza y guarda correctamente el cálculo DC del inversor {idx}.")
                    hubo_error = True
                    continue

                tipo_cable_dc = (request.POST.get(f"tipo_cable_dc_{idx}") or "").strip().lower()
                temp_dc_raw = (request.POST.get(f"temperatura_dc_{idx}") or "").strip()

                if tipo_cable_dc not in ("cobre", "aluminio"):
                    messages.error(request, f"Selecciona tipo de cable DC válido en inversor {idx}.")
                    hubo_error = True
                    continue

                try:
                    temperatura_dc = Decimal(temp_dc_raw)
                except Exception:
                    messages.error(request, f"Temperatura DC inválida en inversor {idx}.")
                    hubo_error = True
                    continue

                awg = extraer_awg(calc_dc.calibre_cable_solar)
                if awg is None:
                    messages.error(request, f"No se pudo relacionar el calibre solar del inversor {idx} con tabla AWG.")
                    hubo_error = True
                    continue

                tabla_awg = TablaConductoresAWGConReactancia.objects.filter(calibre_awg=awg).first()
                if not tabla_awg:
                    messages.error(request, f"No existe registro AWG {awg} en tabla_conductores_awg_con_reactancia.")
                    hubo_error = True
                    continue

                corriente_dc = Decimal(str(np_obj.panel.isc or 0))
                voc_modulo = Decimal(str(np_obj.panel.voc or 0))
                resistencia_cc = Decimal(str(tabla_awg.resistencia_cc or 0))

                coef = Decimal("0.00393") if tipo_cable_dc == "cobre" else Decimal("0.00403")
                calculo_rt_dc = resistencia_cc * (Decimal("1") + coef * (temperatura_dc - Decimal("20")))

                lista_modulos = d.modulos_por_cadena_lista or []
                if not lista_modulos:
                    lista_modulos = [int(d.modulos_por_cadena or 0)] * int(d.no_cadenas or 0)

                lista_longitudes = calc_dc.metros_lineales_por_serie or []

                for num_serie, modulos_serie in enumerate(lista_modulos, start=1):
                    if len(lista_longitudes) >= num_serie:
                        longitud_dc = Decimal(str(lista_longitudes[num_serie - 1])) / Decimal("1000")
                    else:
                        longitud_dc = Decimal(str(calc_dc.metros_lineales or 0)) / Decimal("1000")

                    voltaje_cadena = voc_modulo * Decimal(str(modulos_serie))
                    voltaje_tension_dc = Decimal("2") * corriente_dc * longitud_dc * calculo_rt_dc
                    porcentaje_voltaje_tension_dc = (voltaje_tension_dc / voltaje_cadena) * Decimal("100") if voltaje_cadena > 0 else Decimal("0")

                    resultado_obj = ResultadoTension.objects.create(
                        voltaje_tension_dc=D(voltaje_tension_dc),
                        porcentaje_voltaje_tension_dc=D(porcentaje_voltaje_tension_dc),
                        calculo_rt_dc=D(calculo_rt_dc),
                        corriente_corregida=D(corriente_dc),
                    )

                    existente = CalculoTension.objects.filter(
                        proyecto=proyecto,
                        indice=idx,
                        tipo_calculo="DC",
                        serie=num_serie,
                    ).select_related("resultado_tension").first()

                    if existente and existente.resultado_tension:
                        existente.resultado_tension.delete()

                    if existente:
                        existente.tension_dc = calc_dc
                        existente.factor_potencia_dc = None
                        existente.temperatura_dc = temperatura_dc
                        existente.longitud_dc = longitud_dc
                        existente.tipo_cable_dc = tipo_cable_dc
                        existente.serie = num_serie
                        existente.resultado_tension = resultado_obj
                        existente.save()
                    else:
                        CalculoTension.objects.create(
                            proyecto=proyecto,
                            tension_dc=calc_dc,
                            indice=idx,
                            tipo_calculo="DC",
                            serie=num_serie,
                            temperatura_dc=temperatura_dc,
                            longitud_dc=longitud_dc,
                            tipo_cable_dc=tipo_cable_dc,
                            resultado_tension=resultado_obj,
                        )

            if hubo_error:
                return redirect(f"{reverse('core:calculo_caida_tension')}?proyecto_id={proyecto.id}")

            messages.success(request, "✅ Cálculo de caída de tensión realizado correctamente.")
            return redirect(f"{reverse('core:calculo_caida_tension')}?proyecto_id={proyecto.id}")

    context = {
        "proyectos": proyectos,
        "selected_proyecto_id": selected_proyecto_id,
        "proyecto": proyecto,
        "resumen": resumen,
        "bloques": bloques,
    }
    return render(request, "core/pages/calculo_caida_tension.html", context)

@require_session_login
@require_http_methods(["GET"])
def calculo_caida_tension_pdf(request, proyecto_id: int):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    proyecto = Proyecto.objects.select_related("ID_Usuario").filter(id=proyecto_id).first()
    if not proyecto:
        messages.error(request, "Proyecto no encontrado.")
        return redirect("core:calculo_caida_tension")

    if session_tipo != "Administrador":
        if not session_id_usuario or int(proyecto.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para descargar este PDF.")
            return redirect("core:calculo_caida_tension")

    registros = list(
        CalculoTension.objects.filter(proyecto=proyecto)
        .select_related("resultado_tension", "tension_ac", "tension_dc")
        .order_by("indice", "tipo_calculo", "serie")
    )

    if not registros:
        messages.error(request, "No hay cálculos de caída de tensión guardados para este proyecto.")
        return redirect(f"{reverse('core:calculo_caida_tension')}?proyecto_id={proyecto.id}")

    filename = f"SWGFV_CaidaTension_Proyecto_{proyecto.id}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = build_fortia_doc(response, f"Caida de tension - Proyecto {proyecto.id}")
    pdfs = get_fortia_styles()
    elements = []

    add_fortia_header(
        elements,
        "Reporte técnico de caída de tensión",
        "Sistema Web de Gestión de Proyectos Fotovoltaicos",
        pdfs
    )

    elements.append(Paragraph("Proyecto", pdfs["section"]))

    general = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Proyecto or "—"), pdfs["value"]),
            Paragraph("<b>Voltaje del sitio</b>", pdfs["label"]),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases or "—"), pdfs["value"]),
            Paragraph("<b>Fecha</b>", pdfs["label"]),
            Paragraph(str(timezone.localtime().strftime("%d/%m/%Y %H:%M")), pdfs["value"]),
        ],
    ]

    elements.append(make_info_table(general, [3.2 * cm, 5.2 * cm, 3.3 * cm, 4.8 * cm]))
    elements.append(Spacer(1, 0.25 * cm))
    elements.append(Paragraph("Resultados guardados", pdfs["section"]))

    for r in registros:
        res = r.resultado_tension
        titulo = f"{r.tipo_calculo} - Inversor {r.indice}"
        if r.tipo_calculo == "DC" and r.serie:
            titulo += f" - Serie {r.serie}"

        elements.append(Paragraph(titulo, pdfs["block_title"]))

        data = [
            [
                Paragraph("<b>Temperatura AC</b>", pdfs["label"]),
                Paragraph(str(r.temperatura_ac or "—"), pdfs["value"]),
                Paragraph("<b>Temperatura DC</b>", pdfs["label"]),
                Paragraph(str(r.temperatura_dc or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Factor potencia AC</b>", pdfs["label"]),
                Paragraph(str(r.factor_potencia_ac or "—"), pdfs["value"]),
                Paragraph("<b>Longitud AC</b>", pdfs["label"]),
                Paragraph(str(r.longitud_ac or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Longitud DC</b>", pdfs["label"]),
                Paragraph(str(r.longitud_dc or "—"), pdfs["value"]),
                Paragraph("<b>Corriente corregida</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "corriente_corregida", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Voltaje caída AC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "voltaje_tension_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>% caída AC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "porcentaje_voltaje_tension_ac", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Voltaje caída DC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "voltaje_tension_dc", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>% caída DC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "porcentaje_voltaje_tension_dc", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>RT AC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "calculo_rt_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>RT DC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "calculo_rt_dc", "—")) if res else "—", pdfs["value"]),
            ],
        ]

        elements.append(make_info_table(data, [3.2 * cm, 5.0 * cm, 3.2 * cm, 5.1 * cm]))
        elements.append(Spacer(1, 0.18 * cm))

    add_fortia_footer(elements, pdfs)
    doc.build(elements, onFirstPage=draw_fortia_letterhead, onLaterPages=draw_fortia_letterhead)
    return response

@require_session_login
@require_http_methods(["GET"])
def recursos_conceptos(request):
    q = (request.GET.get("q") or "").strip()
    categoria = (request.GET.get("categoria") or "").strip()

    conceptos = GlosarioConcepto.objects.all().order_by("nombre_concepto")

    if q:
        conceptos = conceptos.filter(
            Q(nombre_concepto__icontains=q) |
            Q(descripcion__icontains=q) |
            Q(formula__icontains=q) |
            Q(categoria__icontains=q)
        )

    if categoria:
        conceptos = conceptos.filter(categoria__iexact=categoria)

    categorias = list(
        GlosarioConcepto.objects.exclude(categoria__exact="")
        .values_list("categoria", flat=True)
        .distinct()
        .order_by("categoria")
    )

    context = {
        "conceptos": conceptos,
        "q": q,
        "categoria": categoria,
        "categorias": categorias,
    }
    return render(request, "core/pages/recursos_conceptos.html", context)


@require_admin
@require_http_methods(["GET", "POST"])
def recursos_alta_concepto(request):
    form = GlosarioConceptoCreateForm(request.POST or None)
    show_required_popup = False
    missing_required_fields = []

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            messages.info(request, "Operación cancelada.")
            return redirect("core:recursos_alta_concepto")

        if form.is_valid():
            obj = form.save()

            log_event(
                request,
                "RESOURCE_CONCEPT_CREATED",
                f"Creó concepto: {obj.nombre_concepto}",
                "GlosarioConcepto",
                obj.id
            )

            messages.success(request, "✅ Concepto dado de alta correctamente.")
            return redirect("core:recursos_alta_concepto")

        required_field_map = {
            "nombre_concepto": "Nombre del concepto",
            "descripcion": "Descripción",
        }

        for field_name, field_label in required_field_map.items():
            raw_value = (request.POST.get(field_name) or "").strip()
            if not raw_value:
                missing_required_fields.append(field_label)

        if missing_required_fields:
            show_required_popup = True

    return render(
        request,
        "core/pages/recursos_alta_concepto.html",
        {
            "form": form,
            "show_required_popup": show_required_popup,
            "missing_required_fields": missing_required_fields,
        }
    )

@require_admin
@require_http_methods(["GET", "POST"])
def recursos_modificacion_concepto(request):
    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    mostrar_todos = (request.GET.get("mostrar_todos") or "").strip() == "1"
    edit_mode = (request.GET.get("edit") or "").strip() == "1"
    search_submitted = (request.GET.get("action") or "").strip() == "search"

    error_id = None
    conceptos = GlosarioConcepto.objects.none()
    mostrar_lista = False
    show_edit_popup = False
    missing_required_fields = []

    # ==========================
    # BÚSQUEDA / LISTADO
    # ==========================
    if mostrar_todos and not search_submitted and not any([q_id, q_nombre]):
        mostrar_lista = True
        conceptos = GlosarioConcepto.objects.all().order_by("nombre_concepto")

    elif search_submitted:
        mostrar_lista = True
        qs = GlosarioConcepto.objects.all().order_by("nombre_concepto")

        if q_id:
            if not q_id.isdigit():
                qs = GlosarioConcepto.objects.none()
                error_id = "El ID debe contener solo números enteros."
            else:
                qs = qs.filter(id=int(q_id))

        if q_nombre:
            qs = qs.filter(nombre_concepto__icontains=q_nombre)

        conceptos = qs

    seleccionado = None
    form = None

    if q_id.isdigit():
        seleccionado = GlosarioConcepto.objects.filter(id=int(q_id)).first()
        if seleccionado:
            form = GlosarioConceptoUpdateForm(instance=seleccionado)

    if request.method == "POST":
        post_id = (request.GET.get("id") or "").strip()

        if not post_id.isdigit():
            messages.error(request, "Selecciona un concepto válido.")
            return redirect("core:recursos_modificacion_concepto")

        seleccionado = GlosarioConcepto.objects.filter(id=int(post_id)).first()
        if not seleccionado:
            messages.error(request, "El concepto ya no existe.")
            return redirect("core:recursos_modificacion_concepto")

        action = (request.POST.get("action") or "").strip().lower()

        if action == "delete":
            nombre = seleccionado.nombre_concepto
            cid = seleccionado.id
            seleccionado.delete()

            log_event(
                request,
                "RESOURCE_CONCEPT_DELETED",
                f"Eliminó concepto: {nombre}",
                "GlosarioConcepto",
                cid
            )

            messages.success(request, f"✅ Concepto eliminado correctamente: {nombre}")
            return redirect("core:recursos_modificacion_concepto")

        if not edit_mode:
            messages.error(request, "Para editar, primero presiona ✏️ Editar.")
            return redirect(f"{reverse('core:recursos_modificacion_concepto')}?id={seleccionado.id}")

        form = GlosarioConceptoUpdateForm(request.POST, instance=seleccionado)

        if form.is_valid():
            obj = form.save()

            log_event(
                request,
                "RESOURCE_CONCEPT_UPDATED",
                f"Actualizó concepto: {obj.nombre_concepto}",
                "GlosarioConcepto",
                obj.id
            )

            messages.success(request, "Concepto actualizado correctamente.")
            return redirect(f"{reverse('core:recursos_modificacion_concepto')}?id={obj.id}")

        required_field_map = {
            "nombre_concepto": "Nombre del concepto",
            "descripcion": "Descripción",
        }

        for field_name, field_label in required_field_map.items():
            raw_value = (request.POST.get(field_name) or "").strip()
            if not raw_value:
                missing_required_fields.append(field_label)

        if missing_required_fields:
            show_edit_popup = True

    context = {
        "q_id": q_id,
        "q_nombre": q_nombre,
        "mostrar_lista": mostrar_lista,
        "mostrar_todos": mostrar_todos,
        "conceptos": conceptos,
        "seleccionado": seleccionado,
        "form": form,
        "edit_mode": edit_mode,
        "show_edit_popup": show_edit_popup,
        "missing_required_fields": missing_required_fields,
        "error_id": error_id,
    }
    return render(request, "core/pages/recursos_modificacion_concepto.html", context)

@require_session_login
@require_http_methods(["GET"])
def recursos_tablas(request):
    q = (request.GET.get("q") or "").strip()
    tabla_id = (request.GET.get("tabla") or "").strip()

    tablas = TablaNOM.objects.all().order_by("nombre_tabla")

    if q:
        tablas = tablas.filter(
            Q(nombre_tabla__icontains=q) |
            Q(notas__icontains=q)
        )

    seleccionada = None
    imagen_url = ""

    if tabla_id.isdigit():
        seleccionada = TablaNOM.objects.filter(id=int(tabla_id)).first()

        if seleccionada and seleccionada.imagen:
            try:
                imagen_url = seleccionada.imagen.url
            except Exception:
                imagen_url = ""

    context = {
        "tablas": tablas,
        "q": q,
        "seleccionada": seleccionada,
        "imagen_url": imagen_url,
    }
    return render(request, "core/pages/recursos_tablas.html", context)

@require_admin
@require_http_methods(["GET", "POST"])
def recursos_alta_tabla(request):
    form = TablaNOMCreateForm(request.POST or None, request.FILES or None)
    show_required_popup = False
    missing_required_fields = []

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "cancel":
            messages.info(request, "Operación cancelada.")
            return redirect("core:recursos_alta_tabla")

        if form.is_valid():
            obj = form.save()

            log_event(
                request,
                "RESOURCE_TABLE_CREATED",
                f"Creó tabla NOM: {obj.nombre_tabla}",
                "TablaNOM",
                obj.id
            )

            messages.success(request, "✅ Tabla NOM dada de alta correctamente.")
            return redirect("core:recursos_alta_tabla")

        required_field_map = {
            "nombre_tabla": "Nombre de la tabla",
            "notas": "Notas de la tabla",
            "imagen": "Imagen",
        }

        for field_name, field_label in required_field_map.items():
            if field_name == "imagen":
                if not request.FILES.get("imagen"):
                    missing_required_fields.append(field_label)
            else:
                raw_value = (request.POST.get(field_name) or "").strip()
                if not raw_value:
                    missing_required_fields.append(field_label)

        if missing_required_fields:
            show_required_popup = True

    return render(
        request,
        "core/pages/recursos_alta_tabla.html",
        {
            "form": form,
            "show_required_popup": show_required_popup,
            "missing_required_fields": missing_required_fields,
        }
    )

@require_admin
@require_http_methods(["GET", "POST"])
def recursos_modificacion_tabla(request):
    q_id = (request.GET.get("id") or "").strip()
    q_nombre = (request.GET.get("nombre") or "").strip()
    mostrar_todos = (request.GET.get("mostrar_todos") or "").strip() == "1"
    edit_mode = (request.GET.get("edit") or "").strip() == "1"
    search_submitted = (request.GET.get("action") or "").strip() == "search"

    error_id = None
    tablas = TablaNOM.objects.none()
    mostrar_lista = False
    show_edit_popup = False
    missing_required_fields = []

    if mostrar_todos and not search_submitted and not any([q_id, q_nombre]):
        mostrar_lista = True
        tablas = TablaNOM.objects.all().order_by("nombre_tabla")

    elif search_submitted:
        mostrar_lista = True
        qs = TablaNOM.objects.all().order_by("nombre_tabla")

        if q_id:
            if not q_id.isdigit():
                qs = TablaNOM.objects.none()
                error_id = "El ID debe contener solo números enteros."
            else:
                qs = qs.filter(id=int(q_id))

        if q_nombre:
            qs = qs.filter(nombre_tabla__icontains=q_nombre)

        tablas = qs

    seleccionada = None
    form = None

    if q_id.isdigit():
        seleccionada = TablaNOM.objects.filter(id=int(q_id)).first()
        if seleccionada:
            form = TablaNOMUpdateForm(instance=seleccionada)

    if request.method == "POST":
        post_id = (request.GET.get("id") or "").strip()

        if not post_id.isdigit():
            messages.error(request, "Selecciona una tabla válida.")
            return redirect("core:recursos_modificacion_tabla")

        seleccionada = TablaNOM.objects.filter(id=int(post_id)).first()
        if not seleccionada:
            messages.error(request, "La tabla ya no existe.")
            return redirect("core:recursos_modificacion_tabla")

        action = (request.POST.get("action") or "").strip().lower()

        if action == "delete":
            nombre = seleccionada.nombre_tabla
            tid = seleccionada.id
            seleccionada.delete()

            log_event(
                request,
                "RESOURCE_TABLE_DELETED",
                f"Eliminó tabla NOM: {nombre}",
                "TablaNOM",
                tid
            )

            messages.success(request, f"✅ Tabla eliminada correctamente: {nombre}")
            return redirect("core:recursos_modificacion_tabla")

        if not edit_mode:
            messages.error(request, "Para editar, primero presiona ✏️ Editar.")
            return redirect(f"{reverse('core:recursos_modificacion_tabla')}?id={seleccionada.id}")

        form = TablaNOMUpdateForm(request.POST or None, request.FILES or None, instance=seleccionada)

        if form.is_valid():
            obj = form.save()

            log_event(
                request,
                "RESOURCE_TABLE_UPDATED",
                f"Actualizó tabla NOM: {obj.nombre_tabla}",
                "TablaNOM",
                obj.id
            )

            messages.success(request, "Tabla actualizada correctamente.")
            return redirect(f"{reverse('core:recursos_modificacion_tabla')}?id={obj.id}")

        required_field_map = {
            "nombre_tabla": "Nombre de la tabla",
            "notas": "Notas de la tabla",
            "imagen": "Imagen",
        }

        for field_name, field_label in required_field_map.items():
            if field_name == "imagen":
                uploaded = request.FILES.get("imagen")
                current_exists = bool(seleccionada and seleccionada.imagen)
                if not uploaded and not current_exists:
                    missing_required_fields.append(field_label)
            else:
                raw_value = (request.POST.get(field_name) or "").strip()
                if not raw_value:
                    missing_required_fields.append(field_label)

        if missing_required_fields:
            show_edit_popup = True

    context = {
        "q_id": q_id,
        "q_nombre": q_nombre,
        "mostrar_lista": mostrar_lista,
        "mostrar_todos": mostrar_todos,
        "tablas": tablas,
        "seleccionada": seleccionada,
        "form": form,
        "edit_mode": edit_mode,
        "show_edit_popup": show_edit_popup,
        "missing_required_fields": missing_required_fields,
        "error_id": error_id,
    }
    return render(request, "core/pages/recursos_modificacion_tabla.html", context)

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

    numero_paneles = NumeroPaneles.objects.select_related("irradiancia", "panel").filter(proyecto=proyecto).first()
    resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=numero_paneles).first() if numero_paneles else None

    dimensionamiento = Dimensionamiento.objects.filter(proyecto=proyecto).first()
    detalles_dimensionamiento = list(
        DimensionamientoDetalle.objects.filter(dimensionamiento=dimensionamiento)
        .select_related("inversor", "micro_inversor")
        .order_by("indice")
    ) if dimensionamiento else []

    calculos_dc = list(
        CalculoDC.objects.filter(proyecto=proyecto)
        .select_related(
            "resultado_dc",
            "condulet",
            "dimensionamiento_detalle",
            "dimensionamiento_detalle__inversor",
            "dimensionamiento_detalle__micro_inversor",
            "conductor",
        )
        .order_by("indice")
    )

    calculos_ac = list(
        CalculoAC.objects.filter(proyecto=proyecto)
        .select_related(
            "resultado_ac",
            "condulet",
            "dimensionamiento_detalle",
            "dimensionamiento_detalle__inversor",
            "dimensionamiento_detalle__micro_inversor",
            "conductor",
        )
        .order_by("indice")
    )

    calculos_tension = list(
        CalculoTension.objects.filter(proyecto=proyecto)
        .select_related("resultado_tension", "tension_ac", "tension_dc")
        .order_by("indice", "tipo_calculo", "serie")
    )

    usa_micro = bool(dimensionamiento and dimensionamiento.tipo_inversor == "MICRO")

    if usa_micro:
        tensiones_ac = [x for x in calculos_tension if x.tipo_calculo == "AC"]
        completo = all([
            numero_paneles is not None,
            resultado_paneles is not None,
            dimensionamiento is not None,
            len(detalles_dimensionamiento) > 0,
            len(calculos_ac) > 0,
            len(tensiones_ac) > 0,
        ])
    else:
        completo = all([
            numero_paneles is not None,
            resultado_paneles is not None,
            dimensionamiento is not None,
            len(detalles_dimensionamiento) > 0,
            len(calculos_dc) > 0,
            len(calculos_ac) > 0,
            len(calculos_tension) > 0,
        ])

    if not completo:
        if usa_micro:
            messages.error(
                request,
                "El proyecto aún no está completo para generar el PDF integral. En proyectos con micro inversores no se requiere cálculo DC, pero sí deben estar completos módulos, dimensionamiento, AC y caída de tensión AC."
            )
        else:
            messages.error(
                request,
                "El proyecto aún no está completo en todos los cálculos para generar el PDF integral."
            )
        return redirect("core:proyecto_consulta")

    filename = f"SWGFV_Proyecto_Completo_{proyecto.id}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = build_fortia_doc(response, f"Proyecto completo {proyecto.id}")
    pdfs = get_fortia_styles()
    elements = []

    generado_por = request.session.get("usuario", "")
    tipo = request.session.get("tipo", "")
    fecha = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    add_fortia_header(
        elements,
        "Memoria técnica integral del proyecto",
        "Sistema Web de Gestión de Proyectos Fotovoltaicos",
        pdfs
    )

    # =========================================================
    # DATOS GENERALES
    # =========================================================
    elements.append(Paragraph("Datos generales del proyecto", pdfs["section"]))

    data = [
        [
            Paragraph("<b>ID</b>", pdfs["label"]),
            Paragraph(str(proyecto.id), pdfs["value"]),
            Paragraph("<b>Fecha de generación</b>", pdfs["label"]),
            Paragraph(fecha, pdfs["value"]),
        ],
        [
            Paragraph("<b>Nombre del proyecto</b>", pdfs["label"]),
            Paragraph(proyecto.Nombre_Proyecto or "—", pdfs["value"]),
            Paragraph("<b>Generado por</b>", pdfs["label"]),
            Paragraph(f"{generado_por} ({tipo})", pdfs["value"]),
        ],
        [
            Paragraph("<b>Empresa</b>", pdfs["label"]),
            Paragraph(proyecto.Nombre_Empresa or "—", pdfs["value"]),
            Paragraph("<b>Usuario asociado</b>", pdfs["label"]),
            Paragraph(getattr(proyecto.ID_Usuario, "Correo_electronico", "—") or "—", pdfs["value"]),
        ],
        [
            Paragraph("<b>Dirección</b>", pdfs["label"]),
            Paragraph(proyecto.Direccion or "—", pdfs["value"]),
            Paragraph("<b>Coordenadas</b>", pdfs["label"]),
            Paragraph(proyecto.Coordenadas or "—", pdfs["value"]),
        ],
        [
            Paragraph("<b>Voltaje nominal</b>", pdfs["label"]),
            Paragraph(proyecto.Voltaje_Nominal or "—", pdfs["value"]),
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases), pdfs["value"]),
        ],
    ]
    elements.append(make_info_table(data, [3.2 * cm, 5.2 * cm, 3.3 * cm, 4.8 * cm]))
    elements.append(Spacer(1, 0.25 * cm))

    # =========================================================
    # NÚMERO DE MÓDULOS
    # =========================================================
    elements.append(Paragraph("Cálculo de número de módulos", pdfs["section"]))

    resumen_modulos = [
        [
            Paragraph("<b>Tipo de facturación</b>", pdfs["label"]),
            Paragraph(numero_paneles.tipo_facturacion or "—", pdfs["value"]),
            Paragraph("<b>Eficiencia</b>", pdfs["label"]),
            Paragraph(str(numero_paneles.eficiencia or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Módulo seleccionado</b>", pdfs["label"]),
            Paragraph(
                f"{numero_paneles.panel.marca} - {numero_paneles.panel.modelo} ({numero_paneles.panel.potencia} W)"
                if numero_paneles and numero_paneles.panel else "—",
                pdfs["value"]
            ),
            Paragraph("<b>Número de módulos</b>", pdfs["label"]),
            Paragraph(str(resultado_paneles.no_modulos or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Potencia total (kW)</b>", pdfs["label"]),
            Paragraph(str(resultado_paneles.potencia_total or "—"), pdfs["value"]),
            Paragraph("<b>Generación anual (kWh)</b>", pdfs["label"]),
            Paragraph(str(resultado_paneles.generacion_anual or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Irradiancia</b>", pdfs["label"]),
            Paragraph(
                f"{numero_paneles.irradiancia.ciudad}, {numero_paneles.irradiancia.estado}"
                if numero_paneles and numero_paneles.irradiancia else "—",
                pdfs["value"]
            ),
            Paragraph("<b>Panel</b>", pdfs["label"]),
            Paragraph(
                f"Voc: {numero_paneles.panel.voc} / Isc: {numero_paneles.panel.isc}"
                if numero_paneles and numero_paneles.panel else "—",
                pdfs["value"]
            ),
        ],
    ]
    elements.append(make_info_table(resumen_modulos, [3.2 * cm, 5.2 * cm, 3.3 * cm, 4.8 * cm]))
    elements.append(Spacer(1, 0.2 * cm))

    cons = numero_paneles.consumos or {}
    genp = resultado_paneles.generacion_por_periodo or {}

    if numero_paneles.tipo_facturacion == "MENSUAL":
        orden = [
            ("ene", "Ene"), ("feb", "Feb"), ("mar", "Mar"), ("abr", "Abr"),
            ("may", "May"), ("jun", "Jun"), ("jul", "Jul"), ("ago", "Ago"),
            ("sep", "Sep"), ("oct", "Oct"), ("nov", "Nov"), ("dic", "Dic"),
        ]
    else:
        orden = [
            ("bim1", "Bim 1"), ("bim2", "Bim 2"), ("bim3", "Bim 3"),
            ("bim4", "Bim 4"), ("bim5", "Bim 5"), ("bim6", "Bim 6"),
        ]

    labels = [lbl for _, lbl in orden]
    consumo_vals = [float(cons.get(k, 0) or 0) for k, _ in orden]
    gen_vals = [float(genp.get(k, 0) or 0) for k, _ in orden]

    tabla_periodos = [["Periodo", "Consumo (kWh)", "Generación (kWh)"]]
    for i in range(len(labels)):
        tabla_periodos.append([
            labels[i],
            f"{consumo_vals[i]:.3f}",
            f"{gen_vals[i]:.3f}",
        ])

    elements.append(make_data_table(tabla_periodos, [4.0 * cm, 6.0 * cm, 6.0 * cm]))
    elements.append(Spacer(1, 0.25 * cm))

    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    from reportlab.lib.colors import HexColor

    # Gráfica 1
    d1 = Drawing(500, 220)
    chart1 = VerticalBarChart()
    chart1.x = 30
    chart1.y = 30
    chart1.height = 150
    chart1.width = 440
    chart1.data = [gen_vals]
    chart1.categoryAxis.categoryNames = labels
    chart1.valueAxis.valueMin = 0
    chart1.bars[0].fillColor = HexColor("#2E86DE")
    d1.add(chart1)

    elements.append(Paragraph("Gráfica 1: Generación por periodo (kWh)", pdfs["block_title"]))
    elements.append(d1)
    elements.append(Spacer(1, 0.2 * cm))

    # Gráfica 2
    d2 = Drawing(500, 240)
    chart2 = VerticalBarChart()
    chart2.x = 30
    chart2.y = 30
    chart2.height = 160
    chart2.width = 440
    chart2.data = [consumo_vals, gen_vals]
    chart2.categoryAxis.categoryNames = labels
    chart2.valueAxis.valueMin = 0
    chart2.bars[0].fillColor = HexColor("#E67E22")
    chart2.bars[1].fillColor = HexColor("#2ECC71")

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

    elements.append(Paragraph("Gráfica 2: Generación vs consumo", pdfs["block_title"]))
    elements.append(d2)
    elements.append(PageBreak())

    # =========================================================
    # DIMENSIONAMIENTO
    # =========================================================
    elements.append(Paragraph("Dimensionamiento", pdfs["section"]))

    modelo_modulo = "—"
    if numero_paneles and numero_paneles.panel:
        modelo_modulo = f"{numero_paneles.panel.marca} - {numero_paneles.panel.modelo} ({numero_paneles.panel.potencia} W)"

    resumen_dim = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(proyecto.Nombre_Proyecto or "—", pdfs["value"]),
            Paragraph("<b>Tipo de instalación</b>", pdfs["label"]),
            Paragraph(dimensionamiento.tipo_inversor if dimensionamiento else "—", pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de inversores</b>", pdfs["label"]),
            Paragraph(str(dimensionamiento.no_inversores if dimensionamiento else "—"), pdfs["value"]),
            Paragraph("<b>Voltaje nominal</b>", pdfs["label"]),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de módulos</b>", pdfs["label"]),
            Paragraph(str(resultado_paneles.no_modulos or "—"), pdfs["value"]),
            Paragraph("<b>Potencia total (kW)</b>", pdfs["label"]),
            Paragraph(str(resultado_paneles.potencia_total or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Módulo seleccionado</b>", pdfs["label"]),
            Paragraph(modelo_modulo, pdfs["value"]),
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases or "—"), pdfs["value"]),
        ],
    ]
    elements.append(make_info_table(resumen_dim, [3.2 * cm, 5.2 * cm, 3.3 * cm, 4.8 * cm]))
    elements.append(Spacer(1, 0.25 * cm))

    for d in detalles_dimensionamiento:
        modelo = d.inversor or d.micro_inversor
        mods = d.modulos_por_cadena_lista or []

        if mods:
            mods_txt = "<br/>".join([f"Cad {idx + 1}: {val}" for idx, val in enumerate(mods)])
            total_modulos_inversor = sum(int(v or 0) for v in mods)
        else:
            mods_txt = str(d.modulos_por_cadena or "—")
            total_modulos_inversor = int(d.no_cadenas or 0) * int(d.modulos_por_cadena or 0)

        elements.append(Paragraph(f"Inversor {d.indice} — {modelo}", pdfs["block_title"]))

        bloque_dim = [
            [
                Paragraph("<b>Cadenas</b>", pdfs["label"]),
                Paragraph(str(d.no_cadenas), pdfs["value"]),
                Paragraph("<b>Módulos por inversor</b>", pdfs["label"]),
                Paragraph(str(total_modulos_inversor), pdfs["value"]),
            ],
            [
                Paragraph("<b>Módulos por cadena</b>", pdfs["label"]),
                Paragraph(mods_txt, pdfs["wrap"]),
                Paragraph("<b>Tipo de equipo</b>", pdfs["label"]),
                Paragraph("Micro inversor" if d.micro_inversor_id else "Inversor", pdfs["value"]),
            ],
        ]
        elements.append(make_info_table(bloque_dim, [3.2 * cm, 5.8 * cm, 3.3 * cm, 4.2 * cm]))
        elements.append(Spacer(1, 0.18 * cm))

    # =========================================================
    # CÁLCULO DC
    # =========================================================
    elements.append(PageBreak())
    elements.append(Paragraph("Cálculo DC", pdfs["section"]))

    np_obj = numero_paneles
    resultado_paneles_local = resultado_paneles
    dim_local = dimensionamiento

    modelo_modulo_dc = "—"
    voc_modulo = "—"
    isc_modulo = "—"
    no_modulos = "—"
    no_inversores = "—"

    if np_obj and np_obj.panel:
        modelo_modulo_dc = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"
        voc_modulo = str(np_obj.panel.voc or "—")
        isc_modulo = str(np_obj.panel.isc or "—")

    if resultado_paneles_local:
        no_modulos = str(resultado_paneles_local.no_modulos or "—")

    if dim_local:
        no_inversores = str(dim_local.no_inversores or "—")

    general_dc = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Proyecto or "—"), pdfs["value"]),
            Paragraph("<b>Empresa</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Empresa or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Voltaje nominal</b>", pdfs["label"]),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), pdfs["value"]),
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de módulos</b>", pdfs["label"]),
            Paragraph(no_modulos, pdfs["value"]),
            Paragraph("<b>Número de inversores</b>", pdfs["label"]),
            Paragraph(no_inversores, pdfs["value"]),
        ],
        [
            Paragraph("<b>Voc del módulo</b>", pdfs["label"]),
            Paragraph(voc_modulo, pdfs["value"]),
            Paragraph("<b>Isc del módulo</b>", pdfs["label"]),
            Paragraph(isc_modulo, pdfs["value"]),
        ],
        [
            Paragraph("<b>Modelo del módulo</b>", pdfs["label"]),
            Paragraph(modelo_modulo_dc, pdfs["value"]),
            Paragraph("<b>Fecha</b>", pdfs["label"]),
            Paragraph(fecha, pdfs["value"]),
        ],
    ]
    elements.append(make_info_table(general_dc, [3.0 * cm, 5.5 * cm, 3.0 * cm, 5.2 * cm]))
    elements.append(Spacer(1, 0.25 * cm))

    for r in calculos_dc:
        det = r.dimensionamiento_detalle
        modelo = str((det.inversor if det else None) or (det.micro_inversor if det else None) or "—")
        tipo_equipo = "Micro inversor" if det and det.micro_inversor_id else "Inversor"

        lista_modulos = det.modulos_por_cadena_lista if det else []
        if lista_modulos:
            modulos_por_inversor = sum(int(v or 0) for v in lista_modulos)
            modulos_cadena_txt = "<br/>".join([f"Cad {i + 1}: {v}" for i, v in enumerate(lista_modulos)])
        else:
            modulos_por_inversor = int((det.no_cadenas or 0) * (det.modulos_por_cadena or 0)) if det else 0
            modulos_cadena_txt = str(det.modulos_por_cadena or "—") if det else "—"

        res = r.resultado_dc
        con = r.condulet

        elements.append(Paragraph(f"{tipo_equipo} {r.indice} — {modelo}", pdfs["block_title"]))

        data_dc = [
            [
                Paragraph("<b>Número de series</b>", pdfs["label"]),
                Paragraph(str(det.no_cadenas if det else "—"), pdfs["value"]),
                Paragraph("<b>Número de módulos por inversor</b>", pdfs["label"]),
                Paragraph(str(modulos_por_inversor), pdfs["value"]),
            ],
            [
                Paragraph("<b>Módulos por cadena</b>", pdfs["label"]),
                Paragraph(modulos_cadena_txt, pdfs["wrap"]),
                Paragraph("<b>Metros lineales</b>", pdfs["label"]),
                Paragraph(str(r.metros_lineales or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Calibre cable solar</b>", pdfs["label"]),
                Paragraph(str(r.calibre_cable_solar or "—"), pdfs["value"]),
                Paragraph("<b>Hilos por tubería</b>", pdfs["label"]),
                Paragraph(str(r.hilos_tuberia or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Amperaje protección</b>", pdfs["label"]),
                Paragraph(f"{getattr(res, 'amperaje_fusible', '—')} A" if res else "—", pdfs["value"]),
                Paragraph("<b>Total de cadenas</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_de_cadenas", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Total fusibles</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_fusibles", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Metros totales cable</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "metros_totales_cable", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Calibre tubería</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "calibre_tuberia", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Total tubos</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_tubos", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Condulets LL / LR / LB / T / C</b>", pdfs["label"]),
                Paragraph(
                    f"{con.tipo_ll if con else 0} / {con.tipo_lr if con else 0} / {con.tipo_lb if con else 0} / {con.tipo_t if con else 0} / {con.tipo_c if con else 0}",
                    pdfs["value"]
                ),
                Paragraph("<b>Total condulets</b>", pdfs["label"]),
                Paragraph(str(con.total() if con else 0), pdfs["value"]),
            ],
        ]
        elements.append(make_info_table(data_dc, [3.6 * cm, 4.6 * cm, 3.8 * cm, 4.8 * cm]))
        elements.append(Spacer(1, 0.18 * cm))

    # =========================================================
    # CÁLCULO AC
    # =========================================================
    elements.append(PageBreak())
    elements.append(Paragraph("Cálculo AC", pdfs["section"]))

    general_ac = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Proyecto or "—"), pdfs["value"]),
            Paragraph("<b>Empresa</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Empresa or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Voltaje nominal</b>", pdfs["label"]),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), pdfs["value"]),
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de módulos</b>", pdfs["label"]),
            Paragraph(no_modulos, pdfs["value"]),
            Paragraph("<b>Número de inversores</b>", pdfs["label"]),
            Paragraph(no_inversores, pdfs["value"]),
        ],
        [
            Paragraph("<b>Voc del módulo</b>", pdfs["label"]),
            Paragraph(voc_modulo, pdfs["value"]),
            Paragraph("<b>Isc del módulo</b>", pdfs["label"]),
            Paragraph(isc_modulo, pdfs["value"]),
        ],
        [
            Paragraph("<b>Modelo del módulo</b>", pdfs["label"]),
            Paragraph(modelo_modulo_dc, pdfs["value"]),
            Paragraph("<b>Fecha</b>", pdfs["label"]),
            Paragraph(fecha, pdfs["value"]),
        ],
    ]
    elements.append(make_info_table(general_ac, [3.0 * cm, 5.5 * cm, 3.0 * cm, 5.2 * cm]))
    elements.append(Spacer(1, 0.25 * cm))

    for r in calculos_ac:
        det = r.dimensionamiento_detalle
        modelo = str((det.inversor if det else None) or (det.micro_inversor if det else None) or "—")
        tipo_equipo = "Micro inversor" if det and det.micro_inversor_id else "Inversor"

        lista_modulos = det.modulos_por_cadena_lista if det else []
        if lista_modulos:
            modulos_por_inversor = sum(int(v or 0) for v in lista_modulos)
            modulos_cadena_txt = "<br/>".join([f"Cad {i + 1}: {v}" for i, v in enumerate(lista_modulos)])
        else:
            modulos_por_inversor = int((det.no_cadenas or 0) * (det.modulos_por_cadena or 0)) if det else 0
            modulos_cadena_txt = str(det.modulos_por_cadena or "—") if det else "—"

        res = r.resultado_ac
        con = r.condulet

        elements.append(Paragraph(f"{tipo_equipo} {r.indice} — {modelo}", pdfs["block_title"]))

        corriente_salida = "—"
        if det:
            if det.inversor_id and det.inversor and det.inversor.corriente_salida is not None:
                corriente_salida = f"{det.inversor.corriente_salida} A"
            elif det.micro_inversor_id and det.micro_inversor and det.micro_inversor.corriente_salida is not None:
                corriente_salida = f"{det.micro_inversor.corriente_salida} A"

        data_ac = [
            [
                Paragraph("<b>Número de series</b>", pdfs["label"]),
                Paragraph(str(det.no_cadenas if det else "—"), pdfs["value"]),
                Paragraph("<b>Número de módulos por inversor</b>", pdfs["label"]),
                Paragraph(str(modulos_por_inversor), pdfs["value"]),
            ],
            [
                Paragraph("<b>Módulos por cadena</b>", pdfs["label"]),
                Paragraph(modulos_cadena_txt, pdfs["wrap"]),
                Paragraph("<b>Corriente de salida</b>", pdfs["label"]),
                Paragraph(corriente_salida, pdfs["value"]),
            ],
            [
                Paragraph("<b>Metros lineales por fase</b>", pdfs["label"]),
                Paragraph(str(r.metros_lineales_ac or "—"), pdfs["value"]),
                Paragraph("<b>Calibre cable THHW</b>", pdfs["label"]),
                Paragraph(str(r.calibre_cable_thhw or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Hilos por tubería</b>", pdfs["label"]),
                Paragraph(str(r.hilos_tuberia_ac or "—"), pdfs["value"]),
                Paragraph("<b>Amperaje protección</b>", pdfs["label"]),
                Paragraph(f"{getattr(res, 'amperaje_proteccion', '—')} A" if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Total de cadenas</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_de_cadenas_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Total protecciones</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_protecciones", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Metros totales cable</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "metros_totales_cable_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Calibre tubería</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "calibre_tuberia_ac", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Total tubos</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "total_tubos_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>Condulets LL / LR / LB / T / C</b>", pdfs["label"]),
                Paragraph(
                    f"{con.tipo_ll if con else 0} / {con.tipo_lr if con else 0} / {con.tipo_lb if con else 0} / {con.tipo_t if con else 0} / {con.tipo_c if con else 0}",
                    pdfs["value"]
                ),
            ],
        ]
        elements.append(make_info_table(data_ac, [3.6 * cm, 4.6 * cm, 3.8 * cm, 4.8 * cm]))
        elements.append(Spacer(1, 0.18 * cm))

    # =========================================================
    # CAÍDA DE TENSIÓN
    # =========================================================
    elements.append(PageBreak())
    elements.append(Paragraph("Caída de tensión", pdfs["section"]))

    general_tension = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(str(proyecto.Nombre_Proyecto or "—"), pdfs["value"]),
            Paragraph("<b>Voltaje del sitio</b>", pdfs["label"]),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases or "—"), pdfs["value"]),
            Paragraph("<b>Fecha</b>", pdfs["label"]),
            Paragraph(fecha, pdfs["value"]),
        ],
    ]
    elements.append(make_info_table(general_tension, [3.2 * cm, 5.2 * cm, 3.3 * cm, 4.8 * cm]))
    elements.append(Spacer(1, 0.25 * cm))

    for r in calculos_tension:
        res = r.resultado_tension
        titulo = f"{r.tipo_calculo} - Inversor {r.indice}"
        if r.tipo_calculo == "DC" and r.serie:
            titulo += f" - Serie {r.serie}"

        elements.append(Paragraph(titulo, pdfs["block_title"]))

        data_tension = [
            [
                Paragraph("<b>Temperatura AC</b>", pdfs["label"]),
                Paragraph(str(r.temperatura_ac or "—"), pdfs["value"]),
                Paragraph("<b>Temperatura DC</b>", pdfs["label"]),
                Paragraph(str(r.temperatura_dc or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Factor potencia AC</b>", pdfs["label"]),
                Paragraph(str(r.factor_potencia_ac or "—"), pdfs["value"]),
                Paragraph("<b>Longitud AC</b>", pdfs["label"]),
                Paragraph(str(r.longitud_ac or "—"), pdfs["value"]),
            ],
            [
                Paragraph("<b>Longitud DC</b>", pdfs["label"]),
                Paragraph(str(r.longitud_dc or "—"), pdfs["value"]),
                Paragraph("<b>Corriente corregida</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "corriente_corregida", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Voltaje caída AC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "voltaje_tension_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>% caída AC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "porcentaje_voltaje_tension_ac", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>Voltaje caída DC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "voltaje_tension_dc", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>% caída DC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "porcentaje_voltaje_tension_dc", "—")) if res else "—", pdfs["value"]),
            ],
            [
                Paragraph("<b>RT AC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "calculo_rt_ac", "—")) if res else "—", pdfs["value"]),
                Paragraph("<b>RT DC</b>", pdfs["label"]),
                Paragraph(str(getattr(res, "calculo_rt_dc", "—")) if res else "—", pdfs["value"]),
            ],
        ]

        elements.append(make_info_table(data_tension, [3.2 * cm, 5.0 * cm, 3.2 * cm, 5.1 * cm]))
        elements.append(Spacer(1, 0.18 * cm))

    add_fortia_footer(elements, pdfs)
    doc.build(elements, onFirstPage=draw_fortia_letterhead, onLaterPages=draw_fortia_letterhead)
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

    if np_obj.tipo_facturacion == "MENSUAL":
        orden = [
            ("ene", "Ene"), ("feb", "Feb"), ("mar", "Mar"), ("abr", "Abr"),
            ("may", "May"), ("jun", "Jun"), ("jul", "Jul"), ("ago", "Ago"),
            ("sep", "Sep"), ("oct", "Oct"), ("nov", "Nov"), ("dic", "Dic"),
        ]
    else:
        orden = [
            ("bim1", "Bim 1"), ("bim2", "Bim 2"), ("bim3", "Bim 3"),
            ("bim4", "Bim 4"), ("bim5", "Bim 5"), ("bim6", "Bim 6")
        ]

    cons = np_obj.consumos or {}
    genp = resultado.generacion_por_periodo or {}

    labels = [lbl for _, lbl in orden]
    consumo_vals = [float(cons.get(k, 0) or 0) for k, _ in orden]
    gen_vals = [float(genp.get(k, 0) or 0) for k, _ in orden]

    filename = f"SWGFV_NumeroModulos_Proyecto_{proyecto.id}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    doc = build_fortia_doc(response, f"Número de módulos - Proyecto {proyecto.id}")
    pdfs = get_fortia_styles()
    elements = []

    add_fortia_header(
        elements,
        "Reporte técnico de número de módulos",
        "Sistema Web de Gestión de Proyectos Fotovoltaicos",
        pdfs
    )

    elements.append(Paragraph("Resumen del cálculo", pdfs["section"]))

    resumen_data = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(proyecto.Nombre_Proyecto or "—", pdfs["value"]),
            Paragraph("<b>Tipo de facturación</b>", pdfs["label"]),
            Paragraph(np_obj.tipo_facturacion, pdfs["value"]),
        ],
        [
            Paragraph("<b>Eficiencia</b>", pdfs["label"]),
            Paragraph(str(np_obj.eficiencia), pdfs["value"]),
            Paragraph("<b>Número de módulos</b>", pdfs["label"]),
            Paragraph(str(resultado.no_modulos), pdfs["value"]),
        ],
        [
            Paragraph("<b>Módulo</b>", pdfs["label"]),
            Paragraph(f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)", pdfs["value"]),
            Paragraph("<b>Potencia total (kW)</b>", pdfs["label"]),
            Paragraph(str(resultado.potencia_total), pdfs["value"]),
        ],
        [
            Paragraph("<b>Generación anual (kWh)</b>", pdfs["label"]),
            Paragraph(str(resultado.generacion_anual), pdfs["value"]),
            Paragraph("<b>Irradiancia</b>", pdfs["label"]),
            Paragraph(f"{np_obj.irradiancia.ciudad}, {np_obj.irradiancia.estado}", pdfs["value"]),
        ],
    ]

    elements.append(make_info_table(resumen_data, [3.0 * cm, 5.4 * cm, 3.2 * cm, 4.9 * cm]))
    elements.append(Spacer(1, 0.25 * cm))

    elements.append(Paragraph("Consumo vs generación por periodo", pdfs["section"]))

    data = [["Periodo", "Consumo (kWh)", "Generación (kWh)"]]
    for i in range(len(labels)):
        data.append([labels[i], f"{consumo_vals[i]:.3f}", f"{gen_vals[i]:.3f}"])

    elements.append(make_data_table(data, [4.0 * cm, 6.0 * cm, 6.0 * cm]))
    elements.append(Spacer(1, 0.35 * cm))

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
        return Paragraph(f"<b>{title}</b>", pdfs["value"]), d

    t1, g1 = make_bar_chart("Gráfica 1: Generación por periodo (kWh)", gen_vals, labels)

    grafica1 = [
        t1,
        Spacer(1, 0.1 * cm),
        g1,
    ]

    elements.append(KeepTogether(grafica1))
    elements.append(Spacer(1, 0.25 * cm))

    d2 = Drawing(500, 240)
    chart2 = VerticalBarChart()
    chart2.x = 30
    chart2.y = 30
    chart2.height = 160
    chart2.width = 440
    chart2.data = [consumo_vals, gen_vals]
    chart2.categoryAxis.categoryNames = labels
    chart2.valueAxis.valueMin = 0
    chart2.bars[0].fillColor = HexColor("#E67E22")
    chart2.bars[1].fillColor = HexColor("#2ECC71")

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

    grafica2 = [
        Paragraph("<b>Gráfica 2: Generación vs consumo</b>", pdfs["value"]),
        Spacer(1, 0.1 * cm),
        d2
    ]

    elements.append(KeepTogether(grafica2))

    add_fortia_footer(elements, pdfs)

    doc.build(elements, onFirstPage=draw_fortia_letterhead, onLaterPages=draw_fortia_letterhead)
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

    doc = build_fortia_doc(response, "Usuarios SWGFV")
    pdfs = get_fortia_styles()
    elements = []

    add_fortia_header(
        elements,
        "Listado de usuarios",
        "Sistema Web de Gestión de Proyectos Fotovoltaicos",
        pdfs
    )

    elements.append(Paragraph("Usuarios registrados en el sistema", pdfs["section"]))

    data = [[
        "ID",
        "Nombre completo",
        "Correo electrónico",
        "Tipo",
        "Activo"
    ]]

    for u in Usuario.objects.all().order_by("ID_Usuario"):
        nombre = f"{u.Nombre} {u.Apellido_Paterno} {u.Apellido_Materno}"
        data.append([
            str(u.ID_Usuario),
            nombre,
            u.Correo_electronico,
            u.Tipo,
            "Sí" if u.Activo else "No",
        ])

    tabla = make_data_table(data, [1.2 * cm, 5.5 * cm, 6.0 * cm, 2.3 * cm, 1.8 * cm])
    elements.append(tabla)

    add_fortia_footer(elements, pdfs)

    doc.build(elements, onFirstPage=draw_fortia_letterhead, onLaterPages=draw_fortia_letterhead)

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

@require_session_login
@require_http_methods(["GET"])
def dimensionamiento_pdf(request, proyecto_id):
    session_tipo = (request.session.get("tipo") or "").strip()
    session_id_usuario = request.session.get("id_usuario")

    proyecto = Proyecto.objects.select_related("ID_Usuario").filter(id=proyecto_id).first()
    if not proyecto:
        messages.error(request, "Proyecto no encontrado.")
        return redirect("core:dimensionamiento_dimensionamiento")

    if session_tipo != "Administrador":
        if not session_id_usuario or int(proyecto.ID_Usuario_id) != int(session_id_usuario):
            messages.error(request, "No tienes permisos para descargar este PDF.")
            return redirect("core:dimensionamiento_dimensionamiento")

    detalles = list(
        DimensionamientoDetalle.objects.filter(dimensionamiento__proyecto=proyecto)
        .select_related("inversor", "micro_inversor")
        .order_by("indice")
    )

    if not detalles:
        messages.error(request, "No hay dimensionamiento guardado para este proyecto.")
        return redirect("core:dimensionamiento_dimensionamiento")

    dim = Dimensionamiento.objects.filter(proyecto=proyecto).first()
    np_obj = NumeroPaneles.objects.select_related("panel").filter(proyecto=proyecto).first()
    resultado_paneles = ResultadoPaneles.objects.filter(numero_paneles=np_obj).first() if np_obj else None

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="dimensionamiento_{proyecto_id}.pdf"'

    doc = build_fortia_doc(response, f"Dimensionamiento {proyecto_id}")
    pdfs = get_fortia_styles()
    elements = []

    add_fortia_header(
        elements,
        "Reporte técnico de dimensionamiento",
        "Sistema Web de Gestión de Proyectos Fotovoltaicos",
        pdfs
    )

    elements.append(Paragraph("Resumen del proyecto", pdfs["section"]))

    modelo_modulo = "—"
    no_modulos = "—"
    potencia_total = "—"

    if np_obj and np_obj.panel:
        modelo_modulo = f"{np_obj.panel.marca} - {np_obj.panel.modelo} ({np_obj.panel.potencia} W)"

    if resultado_paneles:
        no_modulos = str(resultado_paneles.no_modulos or "—")
        potencia_total = str(resultado_paneles.potencia_total or "—")

    resumen_data = [
        [
            Paragraph("<b>Proyecto</b>", pdfs["label"]),
            Paragraph(proyecto.Nombre_Proyecto or "—", pdfs["value"]),
            Paragraph("<b>Tipo de instalación</b>", pdfs["label"]),
            Paragraph(dim.tipo_inversor if dim else "—", pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de inversores</b>", pdfs["label"]),
            Paragraph(str(dim.no_inversores if dim else "—"), pdfs["value"]),
            Paragraph("<b>Voltaje nominal</b>", pdfs["label"]),
            Paragraph(str(proyecto.Voltaje_Nominal or "—"), pdfs["value"]),
        ],
        [
            Paragraph("<b>Número de módulos</b>", pdfs["label"]),
            Paragraph(no_modulos, pdfs["value"]),
            Paragraph("<b>Potencia total (kW)</b>", pdfs["label"]),
            Paragraph(potencia_total, pdfs["value"]),
        ],
        [
            Paragraph("<b>Módulo seleccionado</b>", pdfs["label"]),
            Paragraph(modelo_modulo, pdfs["value"]),
            Paragraph("<b>Número de fases</b>", pdfs["label"]),
            Paragraph(str(proyecto.Numero_Fases or "—"), pdfs["value"]),
        ],
    ]

    elements.append(make_info_table(resumen_data, [3.2 * cm, 5.2 * cm, 3.3 * cm, 4.8 * cm]))
    elements.append(Spacer(1, 0.25 * cm))
    elements.append(Paragraph("Configuración por inversor / micro inversor", pdfs["section"]))

    for d in detalles:
        modelo = d.inversor or d.micro_inversor
        mods = d.modulos_por_cadena_lista or []

        if mods:
            mods_txt = "<br/>".join([f"Cad {idx + 1}: {val}" for idx, val in enumerate(mods)])
            total_modulos_inversor = sum(int(v or 0) for v in mods)
        else:
            mods_txt = str(d.modulos_por_cadena or "—")
            total_modulos_inversor = int(d.no_cadenas or 0) * int(d.modulos_por_cadena or 0)

        elements.append(Paragraph(f"Inversor {d.indice} — {modelo}", pdfs["block_title"]))

        bloque = [
            [
                Paragraph("<b>Cadenas</b>", pdfs["label"]),
                Paragraph(str(d.no_cadenas), pdfs["value"]),
                Paragraph("<b>Módulos por inversor</b>", pdfs["label"]),
                Paragraph(str(total_modulos_inversor), pdfs["value"]),
            ],
            [
                Paragraph("<b>Módulos por cadena</b>", pdfs["label"]),
                Paragraph(mods_txt, pdfs["wrap"]),
                Paragraph("<b>Tipo de equipo</b>", pdfs["label"]),
                Paragraph("Micro inversor" if d.micro_inversor_id else "Inversor", pdfs["value"]),
            ],
        ]

        elements.append(make_info_table(bloque, [3.2 * cm, 5.8 * cm, 3.3 * cm, 4.2 * cm]))
        elements.append(Spacer(1, 0.2 * cm))

    add_fortia_footer(elements, pdfs)
    doc.build(elements, onFirstPage=draw_fortia_letterhead, onLaterPages=draw_fortia_letterhead)
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

