"""Signed anonymous visitor identity for public deployments."""

import base64
import hashlib
import hmac
import secrets


class VisitorIdentity:
    cookie_name = "pathcue_visitor"

    def __init__(self, enabled: bool, secret: str, secure: bool = True):
        if enabled and len(secret) < 32:
            raise ValueError("PUBLIC_SESSION_SECRET must contain at least 32 characters in public mode")
        self.enabled, self.secret, self.secure = enabled, secret.encode(), secure

    def _sign(self, visitor_id: str) -> str:
        digest = hmac.new(self.secret, visitor_id.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def encode(self, visitor_id: str) -> str:
        return visitor_id + "." + self._sign(visitor_id)

    def decode(self, value: str | None) -> str | None:
        if not value or "." not in value:
            return None
        visitor_id, signature = value.rsplit(".", 1)
        if len(visitor_id) != 32 or not visitor_id.isalnum():
            return None
        return visitor_id if hmac.compare_digest(signature, self._sign(visitor_id)) else None

    def resolve(self, request, response) -> str | None:
        if not self.enabled:
            return None
        visitor_id = self.decode(request.cookies.get(self.cookie_name))
        if visitor_id:
            return visitor_id
        visitor_id = secrets.token_hex(16)
        response.set_cookie(
            self.cookie_name,
            self.encode(visitor_id),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=self.secure,
            samesite="lax",
            path="/",
        )
        return visitor_id
