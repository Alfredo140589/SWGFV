from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.core.validators import MinValueValidator, MaxValueValidator


# =========================
# MODELO USUARIO (tabla usuarios)
# =========================
class Usuario(models.Model):
    ID_Usuario = models.AutoField(primary_key=True)

    Nombre = models.CharField(max_length=50)
    Apellido_Paterno = models.CharField(max_length=50)
    Apellido_Materno = models.CharField(max_length=50)
    Telefono = models.CharField(max_length=20)

    Correo_electronico = models.EmailField(max_length=150, unique=True)
    Contrasena = models.CharField(max_length=255)

    TIPO_CHOICES = (
        ("Administrador", "Administrador"),
        ("General", "General"),
    )
    Tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default="General")
    Activo = models.BooleanField(default=True)

    class Meta:
        db_table = "usuarios"

    def set_password(self, raw_password):
        self.Contrasena = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.Contrasena)

    def __str__(self):
        return f"{self.Nombre} ({self.Tipo})"


# =========================
# MODELO PROYECTO (tabla proyectos)
# =========================
class Proyecto(models.Model):
    ID_Proyecto = models.AutoField(primary_key=True)

    # Un usuario tiene muchos proyectos
    ID_Usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        db_column="ID_Usuario",
        related_name="proyectos",
    )

    Nombre_Proyecto = models.CharField(max_length=100)
    Nombre_Empresa = models.CharField(max_length=100, blank=True, null=True)
    Direccion = models.CharField(max_length=255)
    Coordenadas = models.CharField(max_length=50)
    Voltaje_Nominal = models.CharField(max_length=20)
    Numero_Fases = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(3)]
    )

    class Meta:
        db_table = "proyectos"

    def __str__(self):
        return self.Nombre_Proyecto
