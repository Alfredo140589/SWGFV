def session_user(request):
    return {
        "session_usuario": request.session.get("usuario"),
        "session_tipo": request.session.get("tipo"),
    }
