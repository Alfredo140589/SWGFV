from django import forms
from django.core.exceptions import ValidationError
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

    # Captcha simple (la validación fuerte se hace en la vista con session)
    captcha_answer = forms.CharField(
        label="Captcha",
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Resuelve la operación"}
        ),
    )


# ======================================================
# RECUPERAR CONTRASEÑA (TOKEN POR LINK)
# ======================================================
class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label="Correo electrónico",
        required=True,
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "correo@ejemplo.com"}
        ),
    )


class PasswordResetConfirmForm(forms.Form):
    new_password = forms.CharField(
        label="Nueva contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Nueva contraseña"}
        ),
    )
    new_password_confirm = forms.CharField(
        label="Confirmar nueva contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirmar nueva contraseña"}
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
# ALTA DE USUARIO
# ======================================================
class UsuarioCreateForm(forms.ModelForm):
    password = forms.CharField(
        label="Contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Contraseña"}
        ),
    )
    password_confirm = forms.CharField(
        label="Confirmar contraseña",
        required=True,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirmar contraseña"}
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
            "Nombre": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Paterno": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Materno": forms.TextInput(attrs={"class": "form-control"}),
            "Telefono": forms.TextInput(attrs={"class": "form-control"}),
            "Correo_electronico": forms.EmailInput(attrs={"class": "form-control"}),
            "Tipo": forms.Select(attrs={"class": "form-select"}),
            "Activo": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
        }

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")

        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
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
            attrs={"class": "form-control", "placeholder": "ID del usuario"}
        ),
    )
    buscar_correo = forms.EmailField(
        label="Buscar por correo",
        required=False,
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "Correo electrónico"}
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
            attrs={"class": "form-control", "placeholder": "Nueva contraseña"}
        ),
    )
    new_password_confirm = forms.CharField(
        label="Confirmar nueva contraseña",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirmar nueva contraseña"}
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
            "Nombre": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Paterno": forms.TextInput(attrs={"class": "form-control"}),
            "Apellido_Materno": forms.TextInput(attrs={"class": "form-control"}),
            "Telefono": forms.TextInput(attrs={"class": "form-control"}),
            "Correo_electronico": forms.EmailInput(attrs={"class": "form-control"}),
            "Tipo": forms.Select(attrs={"class": "form-select"}),
            "Activo": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
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

    def clean_Correo_electronico(self):
        correo = self.cleaned_data["Correo_electronico"]
        qs = Usuario.objects.filter(Correo_electronico=correo)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Ya existe un usuario con ese correo.")
        return correo

    def save(self, commit=True):
        user: Usuario = super().save(commit=False)
        new_password = self.cleaned_data.get("new_password")
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
        return user


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

    def clean_Coordenadas(self):
        coord = (self.cleaned_data.get("Coordenadas") or "").strip()
        if "," not in coord:
            raise forms.ValidationError("Formato esperado: Latitud, Longitud (con coma).")
        return coord


# ======================================================
# MODIFICACIÓN DE PROYECTO
# ======================================================
class ProyectoUpdateForm(forms.ModelForm):
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

    def clean_Coordenadas(self):
        coord = (self.cleaned_data.get("Coordenadas") or "").strip()
        if coord and "," not in coord:
            raise forms.ValidationError("Formato esperado: Latitud, Longitud (con coma).")
        return coord
