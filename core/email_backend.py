# core/email_backend.py
from django.core.mail.backends.smtp import EmailBackend
from django.conf import settings


class SWGFVEmailBackend(EmailBackend):
    """
    Fuerza un HELO/EHLO válido para servidores SMTP que rechazan el hostname
    por "Invalid HELO name".
    """

    def open(self):
        if self.connection:
            return False

        # IMPORTANTE: hostname válido, sin espacios, sin guiones bajos, etc.
        local_hostname = getattr(settings, "EMAIL_HELO_HOSTNAME", None) or "localhost"

        connection_params = {
            "local_hostname": local_hostname,
        }

        if self.use_ssl:
            self.connection = self.connection_class(
                self.host,
                self.port,
                timeout=self.timeout,
                **connection_params,
            )
        else:
            self.connection = self.connection_class(
                self.host,
                self.port,
                timeout=self.timeout,
                **connection_params,
            )

        if self.use_tls:
            self.connection.starttls(keyfile=self.ssl_keyfile, certfile=self.ssl_certfile)

        if self.username and self.password:
            self.connection.login(self.username, self.password)

        return True
