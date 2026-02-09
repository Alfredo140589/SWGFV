from datetime import timedelta
import random

from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache

from .forms import (
    LoginForm,
    UsuarioCreateForm,
    UsuarioUpdateForm,
    ProyectoCreateForm,
    ProyectoUpdateForm,  # <-- IMPORTANTE para edición real del proyecto
)
from .auth_local import authenticate_local
from .decorators import require_session_login, require_admin
from .models import Usuario, Proyecto


# =========================================================
#  Helpers: CAPTCHA + Lockout
# =========================================================

def _captcha_new(request):
    """
    Crea un captcha aritmético simple y guarda la respuesta en sesión.
    """
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    request.session["login_captcha_answer"] = str(a + b)
    request.session["login_captcha_question"] = f"¿Cuánto es {a} + {b}?"
    # Para que la sesión se guarde incluso sin cambios visibles
    request.session.modified = True


def _captcha_check(request) -> bool:
    """
    Valida captcha contra lo guardado en sesión.
    """
    expected = (request.session.get("login_captcha_answer") or "").strip()
    provided = (request.POST.get("captcha_answer") or "").strip()
    return bool(expected) and provided == expected


def _lock_keys(usuario_input: str, ip: str):
    """
    Crea llaves de cache para intentos y bloqueo.
    """
    usuario_input = (usuario_input or "").strip().lower()
    ip = (ip or "").strip()
    # lock por usuario + ip (más seguro)
    base = f"login:{usuario_input}:{ip}"
    return (
        f"{base}:attempts",
        f"{base}:locked_until",
    )


def _get_ip(request):
    """
    Render/Proxy: prioriza X-Forwarded-For si existe.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        # toma la primera IP
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


# ------------------------
# LOGIN
# ------------------------
@require_http_methods(["GET", "POST"])
def login_view(request):
    # Si ya hay sesión, manda al menú
    if request.session.get("usuario") and request.session.get("tipo"):
        return redirect("core:menu_principal")

    # --- CAPTCHA: en GET generamos pregunta ---
    if request.method == "GET":
        a = random.randint(1, 9)
        b = random.randint(1, 9)
        request.session["captcha_a"] = a
        request.session["captcha_b"] = b
        request.session["captcha_answer"] = a + b

    captcha_question = f"{request.session.get('captcha_a', 1)} + {request.session.get('captcha_b', 1)} = ?"

    form = LoginForm(request.POST or None)

    if request.method == "POST":
        usuario_input = (request.POST.get("usuario") or "").strip().lower()

        # --- LOCKOUT por usuario: 3 intentos => 30 minutos ---
        # Se bloquea "la cuenta" (por usuario ingresado)
        lock_key = f"login_lock:{usuario_input}"
        fail_key = f"login_fail:{usuario_input}"

        locked_until_ts = cache.get(lock_key)
        now_ts = int(timezone.now().timestamp())

        if locked_until_ts and now_ts < int(locked_until_ts):
            remaining = int(locked_until_ts) - now_ts
            minutes = max(1, (remaining + 59) // 60)
            messages.error(request, f"Cuenta bloqueada temporalmente. Intenta de nuevo en {minutes} minuto(s).")
            # regenerar captcha para evitar reuso
            a = random.randint(1, 9)
            b = random.randint(1, 9)
            request.session["captcha_a"] = a
            request.session["captcha_b"] = b
            request.session["captcha_answer"] = a + b
            captcha_question = f"{a} + {b} = ?"
            return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

        # Validación normal del form (incluye captcha como int)
        if form.is_valid():
            # --- Validar captcha contra sesión ---
            expected = request.session.get("captcha_answer")
            user_captcha = form.cleaned_data.get("captcha")

            if expected is None or user_captcha != expected:
                # cuenta como intento fallido
                fails = int(cache.get(fail_key) or 0) + 1
                cache.set(fail_key, fails, timeout=30 * 60)

                if fails >= 3:
                    cache.set(lock_key, now_ts + (30 * 60), timeout=30 * 60)
                    cache.delete(fail_key)
                    messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
                else:
                    messages.error(request, f"Verificación incorrecta. Intento {fails}/3.")

                # regenerar captcha
                a = random.randint(1, 9)
                b = random.randint(1, 9)
                request.session["captcha_a"] = a
                request.session["captcha_b"] = b
                request.session["captcha_answer"] = a + b
                captcha_question = f"{a} + {b} = ?"
                return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})

            # --- Si captcha ok => autenticar usuario ---
            password = form.cleaned_data["password"]
            user_auth = authenticate_local(usuario_input, password)

            if user_auth:
                try:
                    u = Usuario.objects.get(Correo_electronico=usuario_input)
                except Usuario.DoesNotExist:
                    # también cuenta como fallo (evita brute force)
                    fails = int(cache.get(fail_key) or 0) + 1
                    cache.set(fail_key, fails, timeout=30 * 60)
                    messages.error(request, "Usuario o contraseña incorrectos.")
                else:
                    if not u.Activo:
                        messages.error(request, "Tu usuario está inactivo. Contacta al administrador.")
                    else:
                        # ✅ LOGIN OK => limpiar contadores
                        cache.delete(fail_key)
                        cache.delete(lock_key)

                        request.session["usuario"] = u.Correo_electronico
                        request.session["tipo"] = u.Tipo
                        request.session["id_usuario"] = u.ID_Usuario

                        return redirect("core:menu_principal")
            else:
                # fallo de credenciales
                fails = int(cache.get(fail_key) or 0) + 1
                cache.set(fail_key, fails, timeout=30 * 60)

                if fails >= 3:
                    cache.set(lock_key, now_ts + (30 * 60), timeout=30 * 60)
                    cache.delete(fail_key)
                    messages.error(request, "Cuenta bloqueada por 30 minutos (demasiados intentos).")
                else:
                    messages.error(request, f"Usuario o contraseña incorrectos. Intento {fails}/3.")
        else:
            messages.error(request, "Revise el formulario e intente nuevamente.")

        # regenerar captcha al final de POST (éxito ya retornó arriba)
        a = random.randint(1, 9)
        b = random.randint(1, 9)
        request.session["captcha_a"] = a
        request.session["captcha_b"] = b
        request.session["captcha_answer"] = a + b
        captcha_question = f"{a} + {b} = ?"

    return render(request, "core/login.html", {"form": form, "captcha_question": captcha_question})


# ------------------------
# MENÚ PRINCIPAL
# ------------------------
@require_session_login
def menu_principal(request):
    return render(request, "core/menu_principal.html")


# ------------------------
# LOGOUT / RECUPERAR / AYUDA
# ------------------------
@require_session_login
def logout_view(request):
    request.session.flush()
    return redirect("core:login")


# OJO: esta vista DEBE ser pública para el link "¿Olvidaste...?"
def recuperar_view(request):
    return render(request, "core/recuperar.html")


@require_session_login
def ayuda_view(request):
    return render(request, "core/ayuda.html")


# ------------------------
# DEBUG SESIÓN (opcional pero útil)
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
#                    MÓDULO PROYECTO
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
        {
            "form": form,
            "session_usuario": session_usuario,
            "session_tipo": session_tipo,
        },
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

    # Si viene selección explícita por ?id=xxx, permitimos edición real (POST)
    # Nota: en tu flujo, "Seleccionar" envía ?id=...
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
#                    MÓDULO DIMENSIONAMIENTO
# =========================================================
@require_session_login
def dimensionamiento_calculo_modulos(request):
    return render(request, "core/pages/dimensionamiento_calculo_modulos.html")


@require_session_login
def dimensionamiento_dimensionamiento(request):
    return render(request, "core/pages/dimensionamiento_dimensionamiento.html")


# =========================================================
#                    MÓDULO CÁLCULO
# =========================================================
@require_session_login
def calculo_dc(request):
    return render(request, "core/pages/calculo_dc.html")


@require_session_login
def calculo_ac(request):
    return render(request, "core/pages/calculo_ac.html")


@require_session_login
def calculo_caida_tension(request):
    return render(request, "core/pages/calculo_caida_tension.html")


# =========================================================
#                    MÓDULO RECURSOS
# =========================================================
@require_session_login
def recursos_conceptos(request):
    return render(request, "core/pages/recursos_conceptos.html")


@require_session_login
def recursos_tablas(request):
    return render(request, "core/pages/recursos_tablas.html")


@require_admin
def recursos_alta_concepto(request):
    return render(request, "core/pages/recursos_alta_concepto.html")


@require_admin
def recursos_modificacion_concepto(request):
    return render(request, "core/pages/recursos_modificacion_concepto.html")


@require_admin
def recursos_alta_tabla(request):
    return render(request, "core/pages/recursos_alta_tabla.html")


@require_admin
def recursos_modificacion_tabla(request):
    return render(request, "core/pages/recursos_modificacion_tabla.html")


# ------------------------
# CUENTA
# ------------------------
@require_session_login
def cuenta_view(request):
    return render(request, "core/pages/cuenta.html")


# =========================================================
#             GESTIÓN DE USUARIOS (REAL CON BD)
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
