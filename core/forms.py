from django import forms
import re

from .models import Usuario, Proyecto, Irradiancia, PanelSolar, NumeroPaneles, GlosarioConcepto, TablaNOM
from .models import Inversor, MicroInversor

# ======================================================
# LOGIN
# ======================================================
class LoginForm(forms.Form):
    usuario = forms.CharField(
        label="Usuario",
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Ingrese su usuario"}
        ),
    )
    password = forms.CharField(
        label="Contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Ingrese su contraseña"}
        ),
    )

# ======================================================
# RECUPERACIÓN DE CONTRASEÑA
# ======================================================
class PasswordRecoveryRequestForm(forms.Form):
    email = forms.EmailField(
        label="Correo electrónico",
        max_length=150,
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "correo@dominio.com",
            "autocomplete": "email",
        })
    )


class PasswordResetForm(forms.Form):
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Nueva contraseña",
            "autocomplete": "new-password",
        })
    )
    new_password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Confirmar contraseña",
            "autocomplete": "new-password",
        })
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password")
        p2 = cleaned.get("new_password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password_confirm", "Las contraseñas no coinciden.")
        return cleaned


# ======================================================
# ALTA / MODIFICACIÓN DE USUARIO
# ======================================================
class UsuarioCreateForm(forms.ModelForm):
    password = forms.CharField(
        label="Contraseña",
        required=True,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Contraseña"}),
    )
    password_confirm = forms.CharField(
        label="Confirmar contraseña",
        required=True,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Confirmar contraseña"}),
    )

    class Meta:
        model = Usuario
        fields = [
            "Nombre",
            "Apellido_Paterno",
            "Apellido_Materno",
            "Telefono",
            "Correo_electronico",
            "Tipo",
            "Activo",
        ]
        widgets = {
            "Nombre": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Paterno": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Materno": forms.TextInput(attrs={"class": "form-control"}),
            "Telefono": forms.TextInput(attrs={"class": "form-control"}),
            "Correo_electronico": forms.EmailInput(attrs={"class": "form-control"}),
            "Tipo": forms.Select(attrs={"class": "form-select"}),
            "Activo": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
        }

    def clean_Correo_electronico(self):
        email = (self.cleaned_data.get("Correo_electronico") or "").strip().lower()
        if not email:
            return email
        if Usuario.objects.filter(Correo_electronico__iexact=email).exists():
            raise forms.ValidationError("Ya existe un usuario con ese correo.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
        user: Usuario = super().save(commit=False)
        user.set_password(self.cleaned_data.get("password"))
        if commit:
            user.save()
        return user


class UsuarioUpdateForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Nueva contraseña (opcional)",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Nueva contraseña"}),
    )
    new_password_confirm = forms.CharField(
        label="Confirmar nueva contraseña",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Confirmar nueva contraseña"}),
    )

    class Meta:
        model = Usuario
        fields = [
            "Nombre",
            "Apellido_Paterno",
            "Apellido_Materno",
            "Telefono",
            "Correo_electronico",
            "Tipo",
            "Activo",
        ]
        widgets = {
            "Nombre": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Paterno": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Materno": forms.TextInput(attrs={"class": "form-control"}),
            "Telefono": forms.TextInput(attrs={"class": "form-control"}),
            "Correo_electronico": forms.EmailInput(attrs={"class": "form-control"}),
            "Tipo": forms.Select(attrs={"class": "form-select"}),
            "Activo": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
        }

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password")
        p2 = cleaned.get("new_password_confirm")

        if p1 or p2:
            if not p1 or not p2:
                raise forms.ValidationError("Para cambiar la contraseña, llena ambos campos.")
            if p1 != p2:
                self.add_error("new_password_confirm", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
        user: Usuario = super().save(commit=False)
        new_password = self.cleaned_data.get("new_password")
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
        return user


# ======================================================
# CUENTA DE USUARIO
# ======================================================
class CuentaUpdateForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Nueva contraseña",
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Nueva contraseña"
        }),
    )
    new_password_confirm = forms.CharField(
        label="Confirmar nueva contraseña",
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Confirmar nueva contraseña"
        }),
    )

    class Meta:
        model = Usuario
        fields = [
            "Nombre",
            "Apellido_Paterno",
            "Apellido_Materno",
            "Telefono",
            "Correo_electronico",
            "Tipo",
        ]
        widgets = {
            "Nombre": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Paterno": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Materno": forms.TextInput(attrs={"class": "form-control"}),
            "Telefono": forms.TextInput(attrs={"class": "form-control"}),
            "Correo_electronico": forms.EmailInput(attrs={"class": "form-control"}),
            "Tipo": forms.Select(attrs={"class": "form-select"}),
        }

    def clean_Correo_electronico(self):
        email = (self.cleaned_data.get("Correo_electronico") or "").strip().lower()
        if not email:
            raise forms.ValidationError("El correo es obligatorio.")

        qs = Usuario.objects.filter(Correo_electronico__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError("Ya existe otro usuario con ese correo.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = (cleaned.get("new_password") or "").strip()
        p2 = (cleaned.get("new_password_confirm") or "").strip()

        if p1 or p2:
            if not p1 or not p2:
                raise forms.ValidationError("Para cambiar la contraseña, llena ambos campos.")
            if p1 != p2:
                self.add_error("new_password_confirm", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
        user: Usuario = super().save(commit=False)

        new_password = (self.cleaned_data.get("new_password") or "").strip()
        if new_password:
            user.set_password(new_password)

        if commit:
            user.save()
        return user

# ======================================================
# PROYECTOS
# ======================================================
VOLTAJE_NOMINAL_CHOICES = [
    ("127", "127"),
    ("220", "220"),
    ("240", "240"),
    ("440", "440"),
    ("480", "480"),
]

NUMERO_FASES_CHOICES = [
    (1, "1"),
    (2, "2"),
    (3, "3"),
]

SQL_RESERVED_WORDS = {
    "select", "insert", "update", "delete", "drop", "truncate", "alter",
    "create", "replace", "rename", "exec", "execute", "union", "from",
    "where", "join", "table", "database", "into", "values", "grant",
    "revoke", "or", "and", "not", "null", "like", "having", "group",
    "order", "by", "limit"
}


class ProyectoCreateForm(forms.ModelForm):
    Voltaje_Nominal = forms.ChoiceField(
        choices=[("", "Selecciona voltaje")] + VOLTAJE_NOMINAL_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        error_messages={
            "required": "El voltaje nominal es obligatorio."
        },
    )

    Numero_Fases = forms.ChoiceField(
        choices=[("", "Selecciona fases")] + NUMERO_FASES_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        error_messages={
            "required": "El número de fases es obligatorio."
        },
    )

    class Meta:
        model = Proyecto
        fields = [
            "Nombre_Proyecto",
            "Nombre_Empresa",
            "Direccion",
            "Coordenadas",
            "Voltaje_Nominal",
            "Numero_Fases",
        ]
        widgets = {
            "Nombre_Proyecto": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nombre del proyecto",
                "maxlength": "25",
            }),
            "Nombre_Empresa": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Empresa (opcional)",
                "maxlength": "20",
            }),
            "Direccion": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Dirección",
                "maxlength": "100",
            }),
            "Coordenadas": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Latitud, Longitud (ej. 19.4326, -99.1332)"
            }),
        }
        error_messages = {
            "Nombre_Proyecto": {
                "required": "El nombre del proyecto es obligatorio.",
            },
            "Direccion": {
                "required": "La dirección es obligatoria.",
            },
            "Coordenadas": {
                "required": "Las coordenadas son obligatorias.",
            },
        }

    # ==========================
    # VALIDADORES AUXILIARES
    # ==========================
    def _validate_reserved_words(self, value, label):
        tokens = re.findall(r"[A-Za-z_]+", value.lower())
        reserved_found = sorted({token for token in tokens if token in SQL_RESERVED_WORDS})
        if reserved_found:
            raise forms.ValidationError(
                f"{label}: contiene palabras no permitidas."
            )

    def _validate_dangerous_patterns(self, value, label):
        dangerous_patterns = [
            r"--",
            r";",
            r"/\*",
            r"\*/",
            r"@@",
            r"<",
            r">",
            r"`",
            r"'",
            r'"',
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, value):
                raise forms.ValidationError(
                    f"{label}: contiene caracteres o secuencias no permitidas."
                )

    def _validate_text_field(self, value, label, max_len, required=True, allowed_pattern=None):
        value = (value or "").strip()

        if required and not value:
            raise forms.ValidationError(f"{label} es obligatorio.")

        if not value:
            return ""

        if len(value) > max_len:
            raise forms.ValidationError(
                f"{label}: máximo {max_len} caracteres."
            )

        self._validate_dangerous_patterns(value, label)
        self._validate_reserved_words(value, label)

        if allowed_pattern and not re.fullmatch(allowed_pattern, value):
            raise forms.ValidationError(
                f"{label}: contiene caracteres no permitidos."
            )

        return value

    # ==========================
    # VALIDACIONES POR CAMPO
    # ==========================
    def clean_Nombre_Proyecto(self):
        value = self.cleaned_data.get("Nombre_Proyecto")
        return self._validate_text_field(
            value=value,
            label="Nombre del proyecto",
            max_len=25,
            required=True,
            allowed_pattern=r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ]+"
        )

    def clean_Nombre_Empresa(self):
        value = self.cleaned_data.get("Nombre_Empresa")
        return self._validate_text_field(
            value=value,
            label="Empresa",
            max_len=20,
            required=False,
            allowed_pattern=r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&\-()]*"
        )

    def clean_Direccion(self):
        value = self.cleaned_data.get("Direccion")
        return self._validate_text_field(
            value=value,
            label="Dirección",
            max_len=100,
            required=True,
            allowed_pattern=r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,#/\-°]+"
        )

    def clean_Coordenadas(self):
        value = (self.cleaned_data.get("Coordenadas") or "").strip()

        if not value:
            raise forms.ValidationError("Las coordenadas son obligatorias.")

        # Conserva tu validación actual
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", value)
        if not m:
            raise forms.ValidationError(
                "Coordenadas inválidas. Usa formato: latitud, longitud (ej. 19.4326, -99.1332)"
            )

        lat = float(m.group(1))
        lon = float(m.group(2))

        if not (-90 <= lat <= 90):
            raise forms.ValidationError("Latitud inválida. Debe estar entre -90 y 90.")

        if not (-180 <= lon <= 180):
            raise forms.ValidationError("Longitud inválida. Debe estar entre -180 y 180.")

        return f"{lat}, {lon}"

    def clean_Voltaje_Nominal(self):
        value = (self.cleaned_data.get("Voltaje_Nominal") or "").strip()
        valores_validos = {"127", "220", "240", "440", "480"}
        if value not in valores_validos:
            raise forms.ValidationError("Selecciona un voltaje nominal válido.")
        return value

    def clean_Numero_Fases(self):
        value = self.cleaned_data.get("Numero_Fases")
        try:
            value_int = int(value)
        except (TypeError, ValueError):
            raise forms.ValidationError("Selecciona un número de fases válido.")

        if value_int not in (1, 2, 3):
            raise forms.ValidationError("Selecciona un número de fases válido.")
        return value_int


class ProyectoUpdateForm(ProyectoCreateForm):
    class Meta(ProyectoCreateForm.Meta):
        pass


# =========================================================
# ✅ FORM “VIEJO/UI”: NumeroModulosForm (para que NO truene el import)
# =========================================================
FACTURACION_CHOICES = [
    ("mensual", "Mensual"),
    ("bimestral", "Bimestral"),
]

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
BIMESTRES = ["Bim1", "Bim2", "Bim3", "Bim4", "Bim5", "Bim6"]


class NumeroModulosForm(forms.Form):
    proyecto = forms.ModelChoiceField(
        queryset=Proyecto.objects.none(),
        label="Proyecto",
        empty_label="Selecciona un proyecto",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    tipo_facturacion = forms.ChoiceField(
        choices=FACTURACION_CHOICES,
        label="Tipo de facturación CFE",
        widget=forms.Select(attrs={"class": "form-select", "id": "tipo_facturacion"})
    )

    irradiancia = forms.ModelChoiceField(
        queryset=Irradiancia.objects.all().order_by("estado", "ciudad"),
        label="Irradiancia promedio (Ciudad)",
        empty_label="Selecciona ciudad/estado",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    eficiencia = forms.FloatField(
        label="Eficiencia (%)",
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 85"})
    )

    panel = forms.ModelChoiceField(
        queryset=PanelSolar.objects.all().order_by("marca", "modelo"),
        label="Marca y modelo del panel",
        empty_label="Selecciona un panel",
        widget=forms.Select(attrs={"class": "form-select"})
    )

    # Campos consumos mensuales
    for m in MESES:
        locals()[f"consumo_{m.lower()}"] = forms.FloatField(
            required=False,
            label=f"Consumo {m} (kWh)",
            widget=forms.NumberInput(attrs={"class": "form-control"})
        )

    # Campos consumos bimestrales
    for i, b in enumerate(BIMESTRES, start=1):
        locals()[f"consumo_bim{i}"] = forms.FloatField(
            required=False,
            label=f"Consumo {b} (kWh)",
            widget=forms.NumberInput(attrs={"class": "form-control"})
        )

    def __init__(self, *args, **kwargs):
        user_id = kwargs.pop("user_id", None)
        is_admin = kwargs.pop("is_admin", False)
        super().__init__(*args, **kwargs)

        if is_admin:
            self.fields["proyecto"].queryset = Proyecto.objects.all().order_by("-id")
        else:
            if user_id:
                self.fields["proyecto"].queryset = Proyecto.objects.filter(ID_Usuario_id=user_id).order_by("-id")
            else:
                self.fields["proyecto"].queryset = Proyecto.objects.none()


# =========================================================
# ✅ FORM REAL: NumeroPanelesForm (para guardar sin fórmulas)
# =========================================================
class NumeroPanelesForm(forms.ModelForm):
    """
    Form del módulo Cálculo de número de módulos.
    - Guarda en NumeroPaneles (BD)
    - Construye consumos como JSON (sin fórmulas aún)
    """

    # ✅ Acepta lo que manda tu HTML: mensual/bimestral
    tipo_facturacion = forms.ChoiceField(
        choices=[("mensual", "Mensual"), ("bimestral", "Bimestral")],
        widget=forms.Select(attrs={"class": "form-select", "id": "tipoFacturacion"}),
    )

    consumo_ene = forms.DecimalField(required=False, min_value=0)
    consumo_feb = forms.DecimalField(required=False, min_value=0)
    consumo_mar = forms.DecimalField(required=False, min_value=0)
    consumo_abr = forms.DecimalField(required=False, min_value=0)
    consumo_may = forms.DecimalField(required=False, min_value=0)
    consumo_jun = forms.DecimalField(required=False, min_value=0)
    consumo_jul = forms.DecimalField(required=False, min_value=0)
    consumo_ago = forms.DecimalField(required=False, min_value=0)
    consumo_sep = forms.DecimalField(required=False, min_value=0)
    consumo_oct = forms.DecimalField(required=False, min_value=0)
    consumo_nov = forms.DecimalField(required=False, min_value=0)
    consumo_dic = forms.DecimalField(required=False, min_value=0)

    consumo_bim1 = forms.DecimalField(required=False, min_value=0)
    consumo_bim2 = forms.DecimalField(required=False, min_value=0)
    consumo_bim3 = forms.DecimalField(required=False, min_value=0)
    consumo_bim4 = forms.DecimalField(required=False, min_value=0)
    consumo_bim5 = forms.DecimalField(required=False, min_value=0)
    consumo_bim6 = forms.DecimalField(required=False, min_value=0)

    class Meta:
        model = NumeroPaneles
        fields = ["proyecto", "tipo_facturacion", "irradiancia", "panel", "eficiencia"]
        widgets = {
            "proyecto": forms.Select(attrs={"class": "form-select"}),
            "irradiancia": forms.Select(attrs={"class": "form-select"}),
            "panel": forms.Select(attrs={"class": "form-select"}),
            "eficiencia": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}),
        }

    def __init__(self, *args, user_id=None, is_admin=False, **kwargs):
        super().__init__(*args, **kwargs)

        if is_admin:
            self.fields["proyecto"].queryset = Proyecto.objects.all().order_by("-id")
        else:
            if user_id:
                self.fields["proyecto"].queryset = Proyecto.objects.filter(ID_Usuario_id=user_id).order_by("-id")
            else:
                self.fields["proyecto"].queryset = Proyecto.objects.none()

        self.fields["irradiancia"].queryset = Irradiancia.objects.all().order_by("estado", "ciudad")
        self.fields["panel"].queryset = PanelSolar.objects.all().order_by("marca", "modelo")

    def clean_tipo_facturacion(self):
        v = (self.cleaned_data.get("tipo_facturacion") or "").strip().lower()
        if v == "mensual":
            return "MENSUAL"
        if v == "bimestral":
            return "BIMESTRAL"
        raise forms.ValidationError("Tipo de facturación inválido.")

    def clean(self):
        cleaned = super().clean()
        tipo = (cleaned.get("tipo_facturacion") or "").upper()

        consumos = {}
        if tipo == "MENSUAL":
            meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
            for m in meses:
                key = f"consumo_{m}"
                val = cleaned.get(key)
                consumos[m] = float(val) if val is not None else 0.0

        elif tipo == "BIMESTRAL":
            for i in range(1, 7):
                key = f"consumo_bim{i}"
                val = cleaned.get(key)
                consumos[f"bim{i}"] = float(val) if val is not None else 0.0

        cleaned["consumos"] = consumos
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.consumos = self.cleaned_data.get("consumos", {})
        if commit:
            obj.save()
        return obj
# =========================================================
# ✅ FORM: Alta de Panel Solar (Catálogo)
# =========================================================
class PanelSolarCreateForm(forms.ModelForm):
    class Meta:
        model = PanelSolar
        fields = ["id_modulo", "marca", "modelo", "potencia", "voc", "isc", "vmp", "imp"]
        widgets = {
            "id_modulo": forms.NumberInput(attrs={"class": "form-control", "readonly": "readonly"}),
            "marca": forms.TextInput(attrs={"class": "form-control", "placeholder": "Marca"}),
            "modelo": forms.TextInput(attrs={"class": "form-control", "placeholder": "Modelo"}),
            "potencia": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "voc": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "isc": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "vmp": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "imp": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
        }

    def clean_id_modulo(self):
        """
        Ya no exigimos que el usuario capture id_modulo.
        El servidor lo asigna automáticamente.
        Solo validamos si llega un valor manual por manipulación.
        """
        v = self.cleaned_data.get("id_modulo")
        if v is None:
            return v  # el server lo pondrá

        if PanelSolar.objects.filter(id_modulo=v).exists():
            raise forms.ValidationError("Ese id_modulo ya existe. Se asignará automáticamente.")
        return v

    def clean_modelo(self):
        v = (self.cleaned_data.get("modelo") or "").strip()
        if not v:
            raise forms.ValidationError("El modelo es obligatorio.")
        return v
# =========================================================
# ✅ FORMS: Alta de Catálogos (Inversor / MicroInversor)
# =========================================================
class InversorCreateForm(forms.ModelForm):
    class Meta:
        model = Inversor
        fields = [
            "marca", "modelo",
            "potencia",
            "corriente_entrada", "corriente_salida",
            "voltaje_arranque", "voltaje_maximo_entrada",
            "no_mppt", "no_fases",
            "voltaje_nominal",
        ]
        widgets = {
            "marca": forms.TextInput(attrs={"class": "form-control", "placeholder": "Marca"}),
            "modelo": forms.TextInput(attrs={"class": "form-control", "placeholder": "Modelo"}),

            "potencia": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),

            "corriente_entrada": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "corriente_salida": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),

            "voltaje_arranque": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "voltaje_maximo_entrada": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),

            "no_mppt": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "no_fases": forms.NumberInput(attrs={"class": "form-control", "min": 1}),

            "voltaje_nominal": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. 127/220"}),
        }

    # ✅ obligatorios en UI
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        requeridos = [
            "marca","modelo","potencia","corriente_entrada","corriente_salida",
            "voltaje_arranque","voltaje_maximo_entrada","no_mppt","no_fases","voltaje_nominal"
        ]
        for f in requeridos:
            self.fields[f].required = True


class MicroInversorCreateForm(forms.ModelForm):
    class Meta:
        model = MicroInversor
        fields = [
            "marca", "modelo",
            "potencia",
            "corriente_entrada", "corriente_salida",
            "voltaje_arranque", "voltaje_maximo_entrada",
            "no_mppt", "no_fases",
            "voltaje_nominal",
        ]
        widgets = {
            "marca": forms.TextInput(attrs={"class": "form-control", "placeholder": "Marca"}),
            "modelo": forms.TextInput(attrs={"class": "form-control", "placeholder": "Modelo"}),

            "potencia": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),

            "corriente_entrada": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "corriente_salida": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),

            "voltaje_arranque": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "voltaje_maximo_entrada": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),

            "no_mppt": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "no_fases": forms.NumberInput(attrs={"class": "form-control", "min": 1}),

            "voltaje_nominal": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. 127/220"}),
        }

    # ✅ obligatorios en UI
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        requeridos = [
            "marca","modelo","potencia","corriente_entrada","corriente_salida",
            "voltaje_arranque","voltaje_maximo_entrada","no_mppt","no_fases","voltaje_nominal"
        ]
        for f in requeridos:
            self.fields[f].required = True
# core/forms.py
from django import forms
from .models import Dimensionamiento, DimensionamientoDetalle, Inversor, MicroInversor


class DimensionamientoForm(forms.ModelForm):
    class Meta:
        model = Dimensionamiento
        fields = ["tipo_inversor", "no_inversores"]
        widgets = {
            "tipo_inversor": forms.Select(attrs={"class": "form-select", "id": "tipoInstalacion"}),
            "no_inversores": forms.NumberInput(attrs={"class": "form-control", "min": 1, "id": "noInversores"}),
        }


class DimensionamientoDetalleForm(forms.ModelForm):
    """
    OJO:
    - En UI solo se usará UNO (inversor o micro_inversor) según el tipo.
    - Igual dejamos ambos campos para poder validar y guardar.
    """
    class Meta:
        model = DimensionamientoDetalle
        fields = ["inversor", "micro_inversor", "no_cadenas", "modulos_por_cadena", "indice"]
        widgets = {
            "indice": forms.NumberInput(attrs={"class": "form-control", "readonly": "readonly"}),
            "inversor": forms.Select(attrs={"class": "form-select"}),
            "micro_inversor": forms.Select(attrs={"class": "form-select"}),
            "no_cadenas": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "modulos_por_cadena": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }

# =========================================================
# FORMULARIOS: GLOSARIO DE CONCEPTOS
# =========================================================
class GlosarioConceptoCreateForm(forms.ModelForm):
    class Meta:
        model = GlosarioConcepto
        fields = ["nombre_concepto", "descripcion", "formula", "categoria"]
        widgets = {
            "nombre_concepto": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nombre del concepto"
            }),
            "descripcion": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Descripción del concepto",
                "rows": 6
            }),
            "formula": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Fórmula (opcional)",
                "rows": 3
            }),
            "categoria": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Categoría (ej. Eléctrico, Fotovoltaico, Normativo)"
            }),
        }

    def clean_nombre_concepto(self):
        valor = (self.cleaned_data.get("nombre_concepto") or "").strip()
        if not valor:
            raise forms.ValidationError("El nombre del concepto es obligatorio.")
        if GlosarioConcepto.objects.filter(nombre_concepto__iexact=valor).exists():
            raise forms.ValidationError("Ya existe un concepto con ese nombre.")
        return valor

    def clean_descripcion(self):
        valor = (self.cleaned_data.get("descripcion") or "").strip()
        if not valor:
            raise forms.ValidationError("La descripción es obligatoria.")
        return valor

    def clean_formula(self):
        return (self.cleaned_data.get("formula") or "").strip()

    def clean_categoria(self):
        return (self.cleaned_data.get("categoria") or "").strip()


class GlosarioConceptoUpdateForm(forms.ModelForm):
    class Meta:
        model = GlosarioConcepto
        fields = ["nombre_concepto", "descripcion", "formula", "categoria"]
        widgets = {
            "nombre_concepto": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nombre del concepto"
            }),
            "descripcion": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Descripción del concepto",
                "rows": 6
            }),
            "formula": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Fórmula (opcional)",
                "rows": 3
            }),
            "categoria": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Categoría"
            }),
        }

    def clean_nombre_concepto(self):
        valor = (self.cleaned_data.get("nombre_concepto") or "").strip()
        if not valor:
            raise forms.ValidationError("El nombre del concepto es obligatorio.")

        qs = GlosarioConcepto.objects.filter(nombre_concepto__iexact=valor)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError("Ya existe otro concepto con ese nombre.")
        return valor

    def clean_descripcion(self):
        valor = (self.cleaned_data.get("descripcion") or "").strip()
        if not valor:
            raise forms.ValidationError("La descripción es obligatoria.")
        return valor

    def clean_formula(self):
        return (self.cleaned_data.get("formula") or "").strip()

    def clean_categoria(self):
        return (self.cleaned_data.get("categoria") or "").strip()
# =========================================================
# FORMULARIOS: TABLAS NOM
# =========================================================
class TablaNOMCreateForm(forms.ModelForm):
    class Meta:
        model = TablaNOM
        fields = ["nombre_tabla", "notas", "imagen"]
        widgets = {
            "nombre_tabla": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nombre de la tabla"
            }),
            "notas": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Notas o descripción de la tabla",
                "rows": 5
            }),
            "imagen": forms.ClearableFileInput(attrs={
                "class": "form-control"
            }),
        }

    def clean_nombre_tabla(self):
        valor = (self.cleaned_data.get("nombre_tabla") or "").strip()
        if not valor:
            raise forms.ValidationError("El nombre de la tabla es obligatorio.")
        if TablaNOM.objects.filter(nombre_tabla__iexact=valor).exists():
            raise forms.ValidationError("Ya existe una tabla con ese nombre.")
        return valor

    def clean_notas(self):
        return (self.cleaned_data.get("notas") or "").strip()

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        if not imagen:
            raise forms.ValidationError("La imagen es obligatoria.")
        return imagen


class TablaNOMUpdateForm(forms.ModelForm):
    class Meta:
        model = TablaNOM
        fields = ["nombre_tabla", "notas", "imagen"]
        widgets = {
            "nombre_tabla": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nombre de la tabla"
            }),
            "notas": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Notas o descripción de la tabla",
                "rows": 5
            }),
            "imagen": forms.ClearableFileInput(attrs={
                "class": "form-control"
            }),
        }

    def clean_nombre_tabla(self):
        valor = (self.cleaned_data.get("nombre_tabla") or "").strip()
        if not valor:
            raise forms.ValidationError("El nombre de la tabla es obligatorio.")

        qs = TablaNOM.objects.filter(nombre_tabla__iexact=valor)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError("Ya existe otra tabla con ese nombre.")
        return valor

    def clean_notas(self):
        return (self.cleaned_data.get("notas") or "").strip()

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        if imagen:
            return imagen
        if self.instance and self.instance.pk and self.instance.imagen:
            return self.instance.imagen
        raise forms.ValidationError("La imagen es obligatoria.")
