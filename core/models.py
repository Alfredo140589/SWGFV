from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.core.validators import MinValueValidator, MaxValueValidator
from django.contrib.auth.hashers import make_password, check_password as dj_check_password
from django.utils import timezone

# =========================
# MODELO USUARIO
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

    def set_password(self, raw_password: str):
        """Guarda contraseña hasheada en el campo Contrasena."""
        self.Contrasena = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        """Valida contraseña contra el hash guardado en Contrasena."""
        return check_password(raw_password, self.Contrasena)

    def __str__(self):
        return f"{self.Nombre} ({self.Tipo})"

# =========================
# MODELO PROYECTO (TABLA proyectos)
# =========================
class Proyecto(models.Model):
    # Django crea automáticamente el PK "id"
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

class LoginLock(models.Model):
    usuario_key = models.CharField(max_length=150, unique=True)  # correo/usuario normalizado
    fails = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "login_locks"

    def is_locked(self) -> bool:
        if not self.locked_until:
            return False
        return timezone.now() < self.locked_until

    def remaining_minutes(self) -> int:
        if not self.is_locked():
            return 0
        delta = self.locked_until - timezone.now()
        seconds = max(0, int(delta.total_seconds()))
        return max(1, (seconds + 59) // 60)