from django import forms
import re

from .models import Usuario, Proyecto, Irradiancia, PanelSolar, NumeroPaneles


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
    captcha = forms.CharField(
        label="Captcha",
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Escribe el resultado"}
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
# PROYECTOS
# ======================================================
class ProyectoCreateForm(forms.ModelForm):
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
            "Nombre_Proyecto": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre del proyecto"}),
            "Nombre_Empresa": forms.TextInput(attrs={"class": "form-control", "placeholder": "Empresa (opcional)"}),
            "Direccion": forms.TextInput(attrs={"class": "form-control", "placeholder": "Dirección"}),
            "Coordenadas": forms.TextInput(attrs={"class": "form-control", "placeholder": "Latitud, Longitud (ej. 19.4326, -99.1332)"}),
            "Voltaje_Nominal": forms.TextInput(attrs={"class": "form-control", "placeholder": "Voltaje nominal (ej. 127/220)"}),
            "Numero_Fases": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 3}),
        }

    def clean_Coordenadas(self):
        value = (self.cleaned_data.get("Coordenadas") or "").strip()
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", value)
        if not m:
            raise forms.ValidationError("Coordenadas inválidas. Usa formato: latitud, longitud (ej. 19.4326, -99.1332)")
        lat = float(m.group(1))
        lon = float(m.group(2))
        if not (-90 <= lat <= 90):
            raise forms.ValidationError("Latitud inválida. Debe estar entre -90 y 90.")
        if not (-180 <= lon <= 180):
            raise forms.ValidationError("Longitud inválida. Debe estar entre -180 y 180.")
        return f"{lat}, {lon}"

    def clean_Voltaje_Nominal(self):
        value = (self.cleaned_data.get("Voltaje_Nominal") or "").strip()
        if not value:
            raise forms.ValidationError("El voltaje nominal es obligatorio.")
        return value


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
