"""Thin HTTP client for the Schoolers gateway (:8000) used by the seeder.

All requests go through the same `/api/v1` gateway the frontend uses, so the
seeded data is created exactly the way the UI would create it. The only direct
database work is in `seed/db.py` (logins the app has no endpoint for).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
OTP_RE = re.compile(r"code is:\s*(\d{6})")


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body):
        super().__init__(f"{method} {path} -> {status}: {body}")
        self.status = status
        self.body = body


class Api:
    def __init__(self, base: str = DEFAULT_BASE, timeout: float = 60.0):
        self.base = base
        self._client = httpx.Client(base_url=base, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def set_token(self, token: str | None) -> None:
        if token:
            self._client.headers["Authorization"] = f"Bearer {token}"
        else:
            self._client.headers.pop("Authorization", None)

    def request(self, method: str, path: str, expect=(200, 201), **kwargs):
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code not in expect:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise ApiError(method, path, resp.status_code, body)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def get(self, path, expect=(200,), **kw):
        return self.request("GET", path, expect=expect, **kw)

    def post(self, path, expect=(200, 201), **kw):
        return self.request("POST", path, expect=expect, **kw)

    def put(self, path, expect=(200, 201), **kw):
        return self.request("PUT", path, expect=expect, **kw)

    def patch(self, path, expect=(200,), **kw):
        return self.request("PATCH", path, expect=expect, **kw)

    def delete(self, path, expect=(200, 204), **kw):
        return self.request("DELETE", path, expect=expect, **kw)

    # -- auth helpers ----------------------------------------------------
    def login(self, username: str, password: str) -> dict:
        return self.post("/auth/login", json={"username": username, "password": password})

    def login_token(self, username: str, password: str) -> str:
        data = self.login(username, password)
        return data["access_token"]

    def reset_password_via_otp(
        self, identifier: str, new_password: str, smtp_log: Path, timeout: float = 20.0
    ) -> None:
        """Drive the real forgot-password flow, reading the OTP from the mock sink.

        Requires the stack to be running against `seed/mock_smtp.py`; the OTP only
        ever exists in the captured email, never in the HTTP response.
        """
        start = smtp_log.stat().st_size if smtp_log.exists() else 0
        self.post("/auth/forgot-password", json={"identifier": identifier})
        deadline = time.time() + timeout
        otp = None
        while time.time() < deadline:
            if smtp_log.exists() and smtp_log.stat().st_size > start:
                chunk = smtp_log.read_text(errors="replace")[start:]
                match = OTP_RE.search(chunk)
                if match:
                    otp = match.group(1)
                    break
            time.sleep(0.5)
        if not otp:
            raise RuntimeError(f"No OTP captured from {smtp_log} for {identifier!r}")
        self.post(
            "/auth/forgot-password/reset",
            json={"identifier": identifier, "otp": otp, "new_password": new_password},
        )

    def change_password(self, token: str, current_password: str, new_password: str) -> None:
        prev = self._client.headers.get("Authorization")
        self.set_token(token)
        try:
            self.post(
                "/auth/change-password",
                json={"current_password": current_password, "new_password": new_password},
            )
        finally:
            self.set_token(prev and prev.split(" ", 1)[1])
