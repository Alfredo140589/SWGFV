from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    # ========================
    # Autenticación / Sesión
    # ========================
    path("", views.login_view, name="login"),
    path("menu/", views.menu_principal, name="menu_principal"),
    path("logout/", views.logout_view, name="logout"),
    path("recuperar/", views.recuperar_view, name="recuperar"),
    path("ayuda/", views.ayuda_view, name="ayuda"),

    # ========================
    # Módulo Proyecto
    # ========================
    path("proyectos/alta/", views.proyecto_alta, name="proyecto_alta"),
    path("proyectos/consulta/", views.proyecto_consulta, name="proyecto_consulta"),
    path("proyectos/modificacion/", views.proyecto_modificacion, name="proyecto_modificacion"),

    # ========================
    # Módulo Dimensionamiento
    # ========================
    path(
        "dimensionamiento/calculo-modulos/",
        views.dimensionamiento_calculo_modulos,
        name="dimensionamiento_calculo_modulos",
    ),
    path(
        "dimensionamiento/dimensionamiento/",
        views.dimensionamiento_dimensionamiento,
        name="dimensionamiento_dimensionamiento",
    ),

    # ========================
    # Módulo Cálculo
    # ========================
    path("calculos/dc/", views.calculo_dc, name="calculo_dc"),
    path("calculos/ac/", views.calculo_ac, name="calculo_ac"),
    path(
        "calculos/caida-tension/",
        views.calculo_caida_tension,
        name="calculo_caida_tension",
    ),

    # ========================
    # Módulo Recursos
    # ========================
    path("recursos/conceptos/", views.recursos_conceptos, name="recursos_conceptos"),
    path("recursos/tablas/", views.recursos_tablas, name="recursos_tablas"),
    path(
        "recursos/conceptos/alta/",
        views.recursos_alta_concepto,
        name="recursos_alta_concepto",
    ),
    path(
        "recursos/conceptos/modificacion/",
        views.recursos_modificacion_concepto,
        name="recursos_modificacion_concepto",
    ),
    path(
        "recursos/tablas/alta/",
        views.recursos_alta_tabla,
        name="recursos_alta_tabla",
    ),
    path(
        "recursos/tablas/modificacion/",
        views.recursos_modificacion_tabla,
        name="recursos_modificacion_tabla",
    ),

    # ========================
    # Cuenta
    # ========================
    path("cuenta/", views.cuenta_view, name="cuenta"),

    # ========================
    # Gestión de usuarios (Admin)
    # ========================
    path(
        "usuarios/alta/",
        views.gestion_usuarios_alta,
        name="gestion_usuarios_alta",
    ),
    path(
        "usuarios/modificacion/",
        views.gestion_usuarios_modificacion,
        name="gestion_usuarios_modificacion",
    ),
]
