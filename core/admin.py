from django.contrib import admin
from .models import Usuario, Proyecto


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ("ID_Usuario", "Nombre", "Apellido_Paterno", "Correo_electronico", "Tipo", "Activo")
    search_fields = ("Nombre", "Apellido_Paterno", "Apellido_Materno", "Correo_electronico")
    list_filter = ("Tipo", "Activo")


@admin.register(Proyecto)
class ProyectoAdmin(admin.ModelAdmin):
    # "id" es el PK automático de Django
    list_display = ("id", "Nombre_Proyecto", "Nombre_Empresa", "Voltaje_Nominal", "Numero_Fases", "ID_Usuario")
    search_fields = ("Nombre_Proyecto", "Nombre_Empresa", "Direccion", "Coordenadas")
    list_filter = ("Numero_Fases",)
