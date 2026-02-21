from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.core.validators import MinValueValidator, MaxValueValidator
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


# =========================
# BLOQUEO LOGIN (TABLA login_locks)
# =========================
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

# =========================
# BITÁCORA / AUDIT (TABLA audit_logs)
# =========================
class AuditLog(models.Model):
    created_at = models.DateTimeField(default=timezone.now)

    actor_user_id = models.IntegerField(blank=True, null=True)
    actor_email = models.CharField(max_length=150, blank=True, null=True)
    actor_tipo = models.CharField(max_length=50, blank=True, null=True)

    action = models.CharField(max_length=80)
    message = models.TextField(blank=True, null=True)

    target_model = models.CharField(max_length=80, blank=True, null=True)
    target_id = models.CharField(max_length=80, blank=True, null=True)

    class Meta:
        db_table = "audit_logs"   # ✅ IMPORTANTE: coincide con tu tabla en Render
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at} {self.actor_email} {self.action}"

# =========================
# [MODULO] IRRADIANCIA (CATÁLOGO)
# Ruta: core/models.py
# =========================
from django.db import models
from django.utils import timezone

class Irradiancia(models.Model):
    no = models.IntegerField(unique=True)
    tarifa = models.CharField(max_length=10, blank=True, default="")
    region = models.CharField(max_length=50, blank=True, default="")
    estado = models.CharField(max_length=60, blank=True, default="")
    ciudad = models.CharField(max_length=80)

    ene = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feb = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    mar = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    abr = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    may = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    jun = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    jul = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    ago = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    sep = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    oct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    nov = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    dic = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    promedio = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "Irradiancia"
        verbose_name_plural = "Irradiancias"
        ordering = ["estado", "ciudad"]

    def __str__(self):
        return f"{self.ciudad}, {self.estado} ({self.promedio or 0} kWh/m²/día)"


# =========================
# [MODULO] PANELES SOLARES (CATÁLOGO)
# Ruta: core/models.py
# =========================
class PanelSolar(models.Model):
    # “PK Id_modulo” del CSV
    id_modulo = models.IntegerField(unique=True)

    marca = models.CharField(max_length=80)
    modelo = models.CharField(max_length=120)

    potencia = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)  # W
    voc = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)       # V
    isc = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)       # A
    vmp = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)       # V
    imp = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)       # A

    # Opcional (si luego nos pasas eficiencia del módulo)
    module_efficiency = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "Panel Solar"
        verbose_name_plural = "Paneles Solares"
        ordering = ["marca", "modelo"]

    def __str__(self):
        return f"{self.marca} {self.modelo} ({self.potencia or 0}W)"


# =========================
# [MODULO] NUMERO DE PANELES (CAPTURA)
# Ruta: core/models.py
# =========================
class NumeroPaneles(models.Model):
    TIPO_FACTURACION = (
        ("MENSUAL", "Mensual"),
        ("BIMESTRAL", "Bimestral"),
    )

    # IMPORTANTE:
    # Un proyecto tiene un registro de “numero_paneles” (1 a 1).
    # Si necesitas permitir varios cálculos por proyecto después, lo cambiamos a ForeignKey.
    proyecto = models.OneToOneField("Proyecto", on_delete=models.CASCADE, related_name="numero_paneles")

    panel = models.ForeignKey(PanelSolar, on_delete=models.PROTECT, related_name="calculos_numero_paneles")
    irradiancia = models.ForeignKey(Irradiancia, on_delete=models.PROTECT, related_name="calculos_numero_paneles")

    tipo_facturacion = models.CharField(max_length=10, choices=TIPO_FACTURACION)

    # Captura:
    eficiencia = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # % o factor (lo definimos después)
    consumos = models.JSONField(default=dict)  # aquí guardaremos 12 meses o 6 bimestres

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Cálculo número de paneles"
        verbose_name_plural = "Cálculos número de paneles"
        ordering = ["-created_at"]

    def __str__(self):
        return f"NumeroPaneles - Proyecto {self.proyecto_id}"


# =========================
# [MODULO] RESULTADO PANELES (RESULTADOS)
# Ruta: core/models.py
# =========================
class ResultadoPaneles(models.Model):
    numero_paneles = models.OneToOneField(NumeroPaneles, on_delete=models.CASCADE, related_name="resultado")

    no_modulos = models.IntegerField(null=True, blank=True)
    generacion_por_periodo = models.JSONField(default=dict, blank=True)  # mensual o bimestral
    generacion_anual = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # placeholders para gráficas (luego lo hacemos con Chart.js y guardamos config/datos)
    grafica_1 = models.JSONField(default=dict, blank=True)
    grafica_2 = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Resultado cálculo paneles"
        verbose_name_plural = "Resultados cálculo paneles"
        ordering = ["-created_at"]

    def __str__(self):
        return f"ResultadoPaneles - NumeroPaneles {self.numero_paneles_id}"