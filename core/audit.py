# core/audit.py
from django.utils import timezone
from .models import Usuario, AuditLog

def _get_ip(request):
    # Render / proxies: X-Forwarded-For puede venir
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")

def log_event(request, action: str, message: str = "", target_model: str = "", target_id: str = ""):
    """
    Registra un evento en AuditLog.
    NO guarda contraseñas.
    """
    actor = None
    actor_email = None

    session_user_id = request.session.get("id_usuario")
    session_email = request.session.get("usuario")

    if session_user_id:
        actor = Usuario.objects.filter(ID_Usuario=session_user_id).first()
    if session_email:
        actor_email = session_email

    AuditLog.objects.create(
        created_at=timezone.now(),
        actor=actor,
        actor_email=actor_email,
        action=action,
        message=message or "",
        target_model=target_model or "",
        target_id=str(target_id) if target_id is not None else "",
        ip_address=_get_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
    )
