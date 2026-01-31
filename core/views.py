from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse

from .forms import (
    LoginForm,
    UsuarioCreateForm,
    UsuarioUpdateForm,
    ProyectoCreateForm,
)
from .auth_local import authenticate_local
from .decorators import require_session_login, require_admin
from .models import Usuario, Proyecto


# =========================================================
# LOGIN
# =========================================================
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.session.get("usuario") and request.session.get("tipo"):
        return redirect("core:menu_principal")

    form = LoginForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            usuario_input = form.cleaned_data["usuario"]
            password = form.cleaned_data["password"]

            user_auth = authenticate_local(usuario_input, password)

            if user_auth:
                try:
                    u = Usuario.objects.get(Correo_electronico=usuario_input)
                except Usuario.DoesNotExist:
                    messages.error(
                        request,
                        "El usuario autenticó, pero no existe en la base de datos.",
                    )
                    return redirect("core:login")

                if not u.Activo:
                    messages.error(
                        request,
                        "Tu usuario está inactivo. Contacta al administrador.",
                    )
                    return redirect("core:login")

                request.session["usuario"] = u.Correo_electronico
                request.session["tipo"] = u.Tipo
                request.session["id_usuario"] = u.ID_Usuario

                return redirect("core:menu_principal")

            messages.error(request, "Usuario o contraseña incorrectos.")
        else:
            messages.error(request, "Revisa el formulario.")

    return render(request, "core/login.html", {"form": form})


# =========================================================
# MENÚ PRINCIPAL
# =========================================================
@require_session_login
def menu_principal(request):
    return render(request, "core/menu_principal.html")


# =========================================================
# LOGOUT / AYUDA / RECUPERAR
# =========================================================
@require_session_login
def logout_view(request):
    request.session.flush()
    return redirect("core:login")


@require_session_login
def recuperar_view(request):
    return render(request, "core/recuperar.html")


@require_session_login
def ayuda_view(request):
    return render(request, "core/ayuda.html")


# =========================================================
# 🔎 DEBUG SESIÓN (PUNTO 4)
# =========================================================
@require_session_login
def debug_sesion(request):
    """
    Vista SOLO para depuración.
    Muestra exactamente qué hay en la sesión.
    """
    return JsonResponse(
        {
            "usuario": request.session.get("usuario"),
            "tipo": request.session.get("tipo"),
            "id_usuario": request.session.get("id_usuario"),
        },
        json_dumps_params={"indent": 2},
    )


# =========================================================
# MÓDULO PROYECTOS
# =========================================================
@require_session_login
@require_http_methods(["GET", "POST"])
def proyecto_alta(request):
    session_id_usuario = request.session.get("id_usuario")

    if not session_id_usuario:
        messages.error(request, "Sesión inválida. Inicia sesión de nuevo.")
        return redirect("core:logout")

    user = Usuario.objects.filter(ID_Usuario=session_id_usuario).first()
    if not user:
        messages.error(request, "Usuario no encontrado.")
        return redirect("core:logout")

    form = ProyectoCreateForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            proyecto = form.save(commit=False)
            proyecto.ID_Usuario = user
            proyecto.save()

            messages.success(request, "Proyecto registrado correctamente.")
            return redirect("core:proyecto_alta")
        else:
            messages.error(request, "Revisa el formulario.")

    return render(
        request,
        "core/pages/proyecto_alta.html",
        {
            "form": form,
            "session_usuario": request.session.get("usuario"),
            "session_tipo": request.session.get("tipo"),
        },
    )


@require_session_login
def proyecto_consulta(request):
    session_id_usuario = request.session.get("id_usuario")
    session_tipo = request.session.get("tipo")

    if not session_id_usuario:
        messages.error(request, "Sesión inválida.")
        return redirect("core:logout")

    if session_tipo == "Administrador":
        proyectos = Proyecto.objects.select_related("ID_Usuario").all()
    else:
        proyectos = Proyecto.objects.select_related("ID_Usuario").filter(
            ID_Usuario_id=session_id_usuario
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
def proyecto_modificacion(request):
    return render(request, "core/pages/proyecto_modificacion.html")


# =========================================================
# OTROS MÓDULOS (PLACEHOLDER)
# =========================================================
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


@require_session_login
def cuenta_view(request):
    return render(request, "core/pages/cuenta.html")
