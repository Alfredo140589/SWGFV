# core/urls.py
from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    # Login / sesión
    path("", views.login_view, name="login"),
    path("menu/", views.menu_principal, name="menu_principal"),
    path("logout/", views.logout_view, name="logout"),
    path("ayuda/", views.ayuda_view, name="ayuda"),

    # Recuperación
    path("recuperar/", views.recuperar_view, name="recuperar"),
    path("recuperar/<str:token>/", views.password_reset_confirm, name="password_reset_confirm"),

    # Proyectos
    path("proyectos/alta/", views.proyecto_alta, name="proyecto_alta"),
    path("proyectos/consulta/", views.proyecto_consulta, name="proyecto_consulta"),
    path("proyectos/<int:proyecto_id>/pdf/", views.proyecto_pdf, name="proyecto_pdf"),
    path("proyectos/modificacion/", views.proyecto_modificacion, name="proyecto_modificacion"),

    # Usuarios
    path("usuarios/alta/", views.gestion_usuarios_alta, name="gestion_usuarios_alta"),
    path("usuarios/modificacion/", views.gestion_usuarios_modificacion, name="gestion_usuarios_modificacion"),

    # ✅ NUEVO: export + actividad
    path("usuarios/export/csv/", views.usuarios_export_csv, name="usuarios_export_csv"),
    path("usuarios/export/pdf/", views.usuarios_export_pdf, name="usuarios_export_pdf"),
    path("usuarios/actividad/", views.usuarios_actividad, name="usuarios_actividad"),

    # Cuenta
    path("cuenta/", views.cuenta_view, name="cuenta"),

    # Dimensionamiento
    path("dimensionamiento/calculo-modulos/", views.dimensionamiento_calculo_modulos, name="dimensionamiento_calculo_modulos"),
    path("dimensionamiento/", views.dimensionamiento_dimensionamiento, name="dimensionamiento_dimensionamiento"),

    # Cálculos
    path("calculos/dc/", views.calculo_dc, name="calculo_dc"),
    path("calculos/ac/", views.calculo_ac, name="calculo_ac"),
    path("calculos/caida-tension/", views.calculo_caida_tension, name="calculo_caida_tension"),

    # Recursos
    path("recursos/tablas/", views.recursos_tablas, name="recursos_tablas"),
    path("recursos/conceptos/", views.recursos_conceptos, name="recursos_conceptos"),
    path("recursos/alta-concepto/", views.recursos_alta_concepto, name="recursos_alta_concepto"),
    path("recursos/alta-tabla/", views.recursos_alta_tabla, name="recursos_alta_tabla"),
    path("recursos/modificacion-concepto/", views.recursos_modificacion_concepto, name="recursos_modificacion_concepto"),
    path("recursos/modificacion-tabla/", views.recursos_modificacion_tabla, name="recursos_modificacion_tabla"),
]

from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
