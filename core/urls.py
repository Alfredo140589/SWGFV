from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.login_view, name="login"),
    path("menu/", views.menu_principal, name="menu_principal"),
    path("logout/", views.logout_view, name="logout"),
    path("ayuda/", views.ayuda_view, name="ayuda"),

    # Recuperación
    path("recuperar/", views.recuperar_view, name="recuperar"),
    path("recuperar/<str:token>/", views.reset_password_view, name="reset_password"),

    # Proyectos
    path("proyectos/alta/", views.proyecto_alta, name="proyecto_alta"),
    path("proyectos/consulta/", views.proyecto_consulta, name="proyecto_consulta"),
    path("proyectos/modificacion/", views.proyecto_modificacion, name="proyecto_modificacion"),

    # Usuarios
    path("usuarios/alta/", views.gestion_usuarios_alta, name="gestion_usuarios_alta"),
    path("usuarios/modificacion/", views.gestion_usuarios_modificacion, name="gestion_usuarios_modificacion"),

    # Cuenta
    path("cuenta/", views.cuenta_view, name="cuenta"),
]
