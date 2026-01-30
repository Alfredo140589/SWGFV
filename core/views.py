from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse

from .forms import LoginForm, UsuarioCreateForm, UsuarioUpdateForm, ProyectoCreateForm
from .auth_local import authenticate_local
from .decorators import require_session_login, require_admin
from .models import Usuario, Proyecto


# ------------------------
# LOGIN
# ------------------------
@require_http_methods(["GET", "POST"])
def login_view(request):
    # Si ya hay sesión, manda al menú
    if request.session.get("usuario") and request.session.get("tipo"):
        return redirect("core:menu_principal")

    form = LoginForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            usuario_input = form.cleaned_data["usuario"]  # normalmente correo
            password = form.cleaned_data["password"]

            # 1) Autenticación local (tu lógica actual)
            user_auth = authenticate_local(usuario_input, password)

            if user_auth:
                # 2) Buscar el usuario real en tu tabla "usuarios"
                #    (asumimos que el login es por Correo_electronico)
                try:
                    u = Usuario.objects.get(Correo_electronico=usuario_input)
                except Usuario.DoesNotExist:
                    messages.error(
                        request,
                        "El usuario autenticó, pero no existe en la base de datos.",
                    )
                    return redirect("core:login")

                if not u.Activo:
                    messages.error(request, "Tu usuario está inactivo. Contacta al administrador.")
                    return redirect("core:login")

                # 3) Guardar sesión consistente con tu BD
                request.session["usuario"] = u.Correo_electronico   # ✅ correo real
                request.session["tipo"] = u.Tipo                    # ✅ "Administrador" o "General"
                request.session["id_usuario"] = u.ID_Usuario        # ✅ FK para proyectos

                return redirect("core:menu_principal")

            messages.error(request, "Usuario o contraseña incorrectos. Intente de nuevo.")
        else:
            messages.error(request, "Revise el formulario e intente nuevamente.")

    return render(request, "core/login.html", {"form": form})


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


@require_session_login
def recuperar_view(request):
    return render(request, "core/recuperar.html")


@require_session_login
def ayuda_view(request):
    return render(request, "core/ayuda.html")


# =========================================================
#                    MÓDULOS
# =========================================================

# ------------------------
# Módulo Proyecto
# ------------------------
@require_session_login
@require_http_methods(["GET", "POST"])
def proyecto_alta(request):
    """
    Alta REAL de proyecto:
    - Guarda en la tabla 'proyectos'
    - Asigna automáticamente el ID_Usuario del que inició sesión
    """
    # Datos de sesión para mostrar en el template
    session_usuario = request.session.get("usuario")
    session_tipo = request.session.get("tipo")
    session_id_usuario = request.session.get("id_usuario")

    # Validación fuerte de sesión
    if not session_id_usuario:
        messages.error(request, "Sesión incompleta. Inicia sesión nuevamente.")
        return redirect("core:logout")

    # Traer usuario real
    user = Usuario.objects.filter(ID_Usuario=session_id_usuario).first()
    if not user:
        messages.error(request, "No se encontró el usuario en la base de datos. Inicia sesión de nuevo.")
        return redirect("core:logout")

    if not user.Activo:
        messages.error(request, "Tu usuario está inactivo. Contacta al administrador.")
        return redirect("core:logout")

    # Formulario
    form = ProyectoCreateForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            proyecto = form.save(commit=False)

            # Relación: el proyecto pertenece al usuario logueado
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
    return render(request, "core/pages/proyecto_consulta.html")


@require_admin
def proyecto_modificacion(request):
    return render(request, "core/pages/proyecto_modificacion.html")


# ------------------------
# Módulo Dimensionamiento
# ------------------------
@require_session_login
def dimensionamiento_calculo_modulos(request):
    return render(request, "core/pages/dimensionamiento_calculo_modulos.html")


@require_session_login
def dimensionamiento_dimensionamiento(request):
    return render(request, "core/pages/dimensionamiento_dimensionamiento.html")


# ------------------------
# Módulo Cálculo
# ------------------------
@require_session_login
def calculo_dc(request):
    return render(request, "core/pages/calculo_dc.html")


@require_session_login
def calculo_ac(request):
    return render(request, "core/pages/calculo_ac.html")


@require_session_login
def calculo_caida_tension(request):
    return render(request, "core/pages/calculo_caida_tension.html")


# ------------------------
# Módulo Recursos
# ------------------------
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
# Cuenta
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
    """
    Alta REAL en la tabla Usuario.
    Guarda y hashea la contraseña.
    """
    form = UsuarioCreateForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save(commit=False)

            # contraseña hasheada
            obj.set_password(form.cleaned_data["password"])
            obj.save()

            messages.success(request, "Usuario dado de alta correctamente.")
            return redirect("core:gestion_usuarios_alta")

        messages.error(request, "Revisa el formulario. Hay errores.")

    return render(request, "core/pages/gestion_usuarios_alta.html", {"form": form})


@require_admin
@require_http_methods(["GET", "POST"])
def gestion_usuarios_modificacion(request):
    """
    - Búsqueda por ID o por Nombre/Apellidos (GET)
    - Edición real (POST)
    - Desactivación (POST) -> Activo=False (recomendado)
    """
    # filtros GET (búsqueda)
    q_id = request.GET.get("id", "").strip()
    q_nombre = request.GET.get("nombre", "").strip()
    q_ap = request.GET.get("ap", "").strip()
    q_am = request.GET.get("am", "").strip()

    usuarios = Usuario.objects.all().order_by("ID_Usuario")

    seleccionado = None
    form = None

    # 1) Buscar por ID (prioridad)
    if q_id:
        try:
            seleccionado = Usuario.objects.get(ID_Usuario=q_id)
        except Usuario.DoesNotExist:
            messages.error(request, "Usuario no encontrado por ID.")

    # 2) Buscar por nombre completo (si no se seleccionó por ID)
    elif q_nombre or q_ap or q_am:
        qs = Usuario.objects.all()
        if q_nombre:
            qs = qs.filter(Nombre__icontains=q_nombre)
        if q_ap:
            qs = qs.filter(Apellido_Paterno__icontains=q_ap)
        if q_am:
            qs = qs.filter(Apellido_Materno__icontains=q_am)

        if qs.count() == 1:
            seleccionado = qs.first()
        elif qs.count() == 0:
            messages.error(request, "No se encontró usuario con esos datos.")
        else:
            messages.info(request, "Se encontraron varios resultados. Selecciona desde la lista.")

    # Si hay un usuario seleccionado, se puede editar o desactivar
    if seleccionado:
        if request.method == "POST":
            action = request.POST.get("action", "").strip()

            # Desactivar (eliminación lógica)
            if action == "deactivate":
                seleccionado.Activo = False
                seleccionado.save()
                messages.success(request, "Usuario desactivado correctamente.")
                return redirect("core:gestion_usuarios_modificacion")

            # Guardar cambios
            form = UsuarioUpdateForm(request.POST, instance=seleccionado)
            if form.is_valid():
                obj = form.save(commit=False)

                new_pass = form.cleaned_data.get("new_password")
                if new_pass:
                    obj.set_password(new_pass)

                obj.save()
                messages.success(request, "Cambios guardados correctamente.")

                # regresar mostrando el usuario (con querystring id=)
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
        },
    )
