# SWGFV (Django + Bootstrap + PostgreSQL)

## Compatibilidad
Este proyecto está ajustado para **Python 3.8** usando **Django 4.2 (LTS)**.
Proyecto base solicitado:
- Django (Python) + Bootstrap 5
- PostgreSQL con base `swgfv`
- Pantalla de login con el mismo diseño del HTML proporcionado
- 2 tipos de usuario (local, sin BD) usando sesión:
  - Admin: `admin` / `Admin$2025`
  - General: `usuario` / `Usuario$2025`
- Menú dinámico según el tipo de usuario

> Nota: Los archivos de imagen incluidos en `static/img/` son **placeholders**. Reemplázalos por tus imágenes reales
`logo1.png` e `imagen1.jpg` conservando el mismo nombre.

## 1) Crear base de datos en PostgreSQL
```sql
CREATE DATABASE swgfv;
```

## 2) Abrir en PyCharm 2025
- File > Open > selecciona la carpeta del proyecto (`swgfv_django`)
- Crea y activa un entorno virtual (venv)

## 3) Instalar dependencias
```bash
pip install -r requirements.txt
```

## 4) Configurar conexión a PostgreSQL
Copia `.env.example` a `.env` (opcional) y ajusta credenciales.
Si no usas `.env`, edita directamente `swgfv_project/settings.py`.

## 5) Migraciones y ejecución
```bash
python manage.py makemigrations
python manage.py migrate
python manage.py runserver
```

Abre: http://127.0.0.1:8000/

## Rutas
- `/` Login
- `/menu/` Menú principal (requiere sesión)
- `/logout/` Cerrar sesión
- `/recuperar/` Placeholder