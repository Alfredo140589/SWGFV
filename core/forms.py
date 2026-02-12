from django import forms
from .models import Usuario, Proyecto


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
    # Debe coincidir con name="captcha" en login.html
    captcha = forms.CharField(
        label="Captcha",
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Escribe el resultado"}
        ),
    )


# ======================================================
# RECUPERACIÓN DE CONTRASEÑA (POR TOKEN LINK)
# ======================================================
class PasswordRecoveryRequestForm(forms.Form):
    email = forms.EmailField(
        label="Correo electrónico",
        required=True,
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "Correo registrado"}
        ),
    )


class PasswordResetForm(forms.Form):
    new_password = forms.CharField(
        label="Nueva contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Nueva contraseña"}
        ),
    )
    new_password_confirm = forms.CharField(
        label="Confirmar contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirmar contraseña"}
        ),
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
from django import forms
from .models import Usuario


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

        # Validación case-insensitive
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
import re
from django import forms
from .models import Proyecto


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
        # Esperamos "lat, lon"
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
class ProyectoUpdateForm(ProyectoCreateForm):
    """
    Reutiliza las mismas validaciones y widgets del create.
    """
    class Meta(ProyectoCreateForm.Meta):
        pass
