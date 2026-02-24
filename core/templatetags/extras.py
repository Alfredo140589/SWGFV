from django import template

register = template.Library()

@register.filter
def get_item(d, key):
    """
    Uso: {{ mi_diccionario|get_item:"clave" }}
    Devuelve d[key] si existe, si no devuelve vacío.
    """
    if isinstance(d, dict):
        return d.get(key, "")
    return ""