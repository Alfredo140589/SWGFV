from django import forms
from django.core.exceptions import ValidationError
from .models import Usuario


# ======================================================
# LOGIN
# ======================================================
class LoginForm(forms.Form):
    usuario = forms.CharField(
        label="Usuario",
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ingrese su usuario",
                "autocomplete": "username",
            }
        ),
    )
    password = forms.CharField(
        label="Contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ingrese su contraseña",
                "autocomplete": "current-password",
            }
        ),
    )


# ======================================================
# ALTA DE USUARIO
# ======================================================
class UsuarioCreateForm(forms.ModelForm):
    password = forms.CharField(
        label="Contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Contraseña",
                "autocomplete": "new-password",
            }
        ),
    )
    password_confirm = forms.CharField(
        label="Confirmar contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Confirmar contraseña",
                "autocomplete": "new-password",
            }
        ),
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
            "Nombre": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Nombre(s)", "maxlength": "100"}
            ),
            "Apellido_Paterno": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Apellido paterno", "maxlength": "100"}
            ),
            "Apellido_Materno": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Apellido materno", "maxlength": "100"}
            ),
            "Telefono": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Teléfono",
                    "inputmode": "tel",
                    "autocomplete": "tel",
                    "maxlength": "20",
                }
            ),
            "Correo_electronico": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Correo electrónico",
                    "autocomplete": "email",
                }
            ),
            "Tipo": forms.Select(attrs={"class": "form-select"}),
            "Activo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")

        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Las contraseñas no coinciden.")

        return cleaned

    def save(self, commit=True):
        """
        IMPORTANTE: guardar contraseña hasheada en el campo Contrasena
        usando el método set_password() de tu modelo Usuario.
        """
        user: Usuario = super().save(commit=False)
        raw_password = self.cleaned_data.get("password")
        user.set_password(raw_password)

        if commit:
            user.save()
        return user


# ======================================================
# FORM DE BÚSQUEDA (para Modificación)
# ======================================================
class UsuarioSearchForm(forms.Form):
    buscar_id = forms.IntegerField(
        label="Buscar por ID",
        required=False,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "ID del usuario", "inputmode": "numeric"}
        ),
    )
    buscar_correo = forms.EmailField(
        label="Buscar por correo",
        required=False,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "Correo electrónico",
                "autocomplete": "email",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        buscar_id = cleaned.get("buscar_id")
        buscar_correo = cleaned.get("buscar_correo")

        if not buscar_id and not buscar_correo:
            raise ValidationError("Ingresa un ID o un correo para buscar.")

        return cleaned


# ======================================================
# MODIFICACIÓN DE USUARIO
# ======================================================
class UsuarioUpdateForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Nueva contraseña (opcional)",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Nueva contraseña",
                "autocomplete": "new-password",
            }
        ),
    )
    new_password_confirm = forms.CharField(
        label="Confirmar nueva contraseña",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Confirmar nueva contraseña",
                "autocomplete": "new-password",
            }
        ),
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
            "Nombre": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Nombre(s)", "maxlength": "100"}
            ),
            "Apellido_Paterno": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Apellido paterno", "maxlength": "100"}
            ),
            "Apellido_Materno": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Apellido materno", "maxlength": "100"}
            ),
            "Telefono": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Teléfono",
                    "inputmode": "tel",
                    "autocomplete": "tel",
                    "maxlength": "20",
                }
            ),
            "Correo_electronico": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Correo electrónico",
                    "autocomplete": "email",
                }
            ),
            "Tipo": forms.Select(attrs={"class": "form-select"}),
            "Activo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password")
        p2 = cleaned.get("new_password_confirm")

        # Si el usuario intenta cambiar password, ambos deben venir y coincidir
        if p1 or p2:
            if not p1 or not p2:
                raise forms.ValidationError("Para cambiar la contraseña, llena ambos campos.")
            if p1 != p2:
                self.add_error("new_password_confirm", "Las contraseñas no coinciden.")

        return cleaned

    def clean_Correo_electronico(self):
        """
        Evita el error de 'correo ya existe' cuando el usuario no cambia su propio correo.
        """
        correo = self.cleaned_data["Correo_electronico"]
        qs = Usuario.objects.filter(Correo_electronico=correo)

        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError("Ya existe un usuario con ese correo.")
        return correo

    def save(self, commit=True):
        """
        Si viene nueva contraseña, se hashea y se guarda.
        """
        user: Usuario = super().save(commit=False)
        new_password = self.cleaned_data.get("new_password")

        if new_password:
            user.set_password(new_password)

        if commit:
            user.save()
        return user

from .models import Proyecto


# ======================================================
# ALTA DE PROYECTO
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
            "Nombre_Proyecto": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Nombre del proyecto"}
            ),
            "Nombre_Empresa": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Empresa (opcional)"}
            ),
            "Direccion": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Dirección"}
            ),
            "Coordenadas": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Latitud, Longitud (ej. 19.4326, -99.1332)",
                }
            ),
            "Voltaje_Nominal": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Voltaje nominal"}
            ),
            "Numero_Fases": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 3}
            ),
        }

    def clean_Numero_Fases(self):
        fases = self.cleaned_data.get("Numero_Fases")
        if fases is None:
            return fases
        if fases < 1 or fases > 3:
            raise forms.ValidationError("El número de fases debe ser 1, 2 o 3.")
        return fases
from django.core.validators import MinValueValidator, MaxValueValidator
from .models import Proyecto


# ======================================================
# ALTA DE PROYECTO
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
            "Nombre_Proyecto": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Nombre del proyecto"}
            ),
            "Nombre_Empresa": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Empresa (opcional)"}
            ),
            "Direccion": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Dirección"}
            ),
            "Coordenadas": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Latitud, Longitud (ej. 19.4326, -99.1332)",
                }
            ),
            "Voltaje_Nominal": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Voltaje nominal"}
            ),
            "Numero_Fases": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 3}
            ),
        }

    def clean_Numero_Fases(self):
        fases = self.cleaned_data.get("Numero_Fases")
        if fases is None:
            return fases
        if fases < 1 or fases > 3:
            raise forms.ValidationError("El número de fases debe ser 1, 2 o 3.")
        return fases
