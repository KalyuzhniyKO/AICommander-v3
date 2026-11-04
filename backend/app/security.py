"""Авторизация всех ресурсов панели; секреты остаются на сервере."""
import base64
import binascii
import hmac
import os
import re

from starlette.responses import JSONResponse


from .redaction import redact


_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def _harden(response):
    response.headers.update(_SECURITY_HEADERS)
    return response


def _deny(detail, status_code, headers=None):
    return _harden(JSONResponse({"detail": detail}, status_code=status_code, headers=headers))


def _local_authority(host):
    # Validate an HTTP authority, not a general URL. Userinfo, path, query,
    # fragment, empty/non-numeric/out-of-range ports and ambiguous forms fail.
    match = re.fullmatch(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", host,
                         flags=re.IGNORECASE | re.ASCII)
    return bool(match and (match[1] is None or 1 <= int(match[1]) <= 65535))


async def secure_request(request, call_next):
    # Reject ambiguous security headers before dispatch or authentication.
    if any(len(request.headers.getlist(name)) > 1 for name in
           ("host", "authorization", "origin", "x-aicommander-request")):
        return _deny("Неоднозначные заголовки запроса", 400)
    if not _local_authority(request.headers.get("host", "")):
        return _deny("Доступ только через локальный SSH tunnel", 400)
    password = os.environ.get("PANEL_PASSWORD", "")
    username = os.environ.get("PANEL_USER", "owner")
    if len(password) < 32:
        return _deny("На сервере требуется PANEL_PASSWORD длиной минимум 32 символа", 503)
    try:
        scheme, token = request.headers.get("authorization", "").split(" ", 1)
        user, supplied = base64.b64decode(token, validate=True).decode().split(":", 1)
        valid = scheme.lower() == "basic" and hmac.compare_digest(user.encode(), username.encode()) and hmac.compare_digest(supplied.encode(), password.encode())
    except (ValueError, UnicodeError, binascii.Error):
        valid = False
    if not valid:
        return _deny("Требуется авторизация владельца", 401,
                            headers={"WWW-Authenticate": 'Basic realm="AICommander", charset="UTF-8"'})
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if request.headers.get("x-aicommander-request") != "1" or (origin and origin != str(request.base_url).rstrip("/")):
            return _deny("Запрос отклонён защитой CSRF", 403)
    request.state.owner = username
    response = await call_next(request)
    return _harden(response)


class RedactedJSONResponse(JSONResponse):
    def render(self, content):
        return super().render(redact(content))
