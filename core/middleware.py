# core/middleware.py
from datetime import timedelta
from django.shortcuts import redirect
from django.utils import timezone

class SessionIdleTimeoutMiddleware:
    """
    Cierra la sesión si NO hay actividad (requests) por más de IDLE_MINUTES.
    """
    IDLE_MINUTES = 10
    SESSION_KEY = "last_activity"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Solo aplica si hay sesión iniciada en tu sistema
        if request.session.get("usuario") and request.session.get("tipo"):
            now = timezone.now()
            last = request.session.get(self.SESSION_KEY)

            if last:
                try:
                    last_dt = timezone.datetime.fromisoformat(last)
                    if timezone.is_naive(last_dt):
                        last_dt = timezone.make_aware(last_dt, timezone.get_current_timezone())
                except Exception:
                    last_dt = now
            else:
                last_dt = now

            if now - last_dt > timedelta(minutes=self.IDLE_MINUTES):
                # Cerrar sesión por inactividad
                request.session.flush()
                return redirect("core:login")

            # Actualiza actividad
            request.session[self.SESSION_KEY] = now.isoformat()
            request.session.modified = True

        return self.get_response(request)
