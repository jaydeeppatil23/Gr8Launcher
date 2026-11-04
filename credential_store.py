"""Ely.by sessions in native credential storage, with a memory-only fallback.

Only native keyring backends are accepted. Missing dependencies, locked vaults,
and backend errors must never cause tokens to be written to a normal file.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading


_NATIVE_BACKENDS = {
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.libsecret",
    "keyring.backends.kwallet",
}

_SECRET_FIELD = re.compile(
    r'''(?i)((?:--)?(?:access[_-]?token|client[_-]?token|refresh[_-]?token|password|totp_code)\b["']?[ \t]*(?:[=:][ \t]*|[ \t]+))(\[REDACTED\]|["'][^"'\r\n]*["']|[^\s,;\]}]+)'''
)


def redact_credentials(text: str, sensitive_values=()) -> str:
    """Remove credentials before displaying, exporting, or writing game logs."""
    for value in sensitive_values:
        if isinstance(value, str) and value:
            text = text.replace(value, "[REDACTED]")
    return _SECRET_FIELD.sub(lambda match: match[1] + "[REDACTED]", text)


def _native_backend():
    try:
        import keyring

        selected = keyring.get_keyring()
        candidates = (
            selected.backends
            if type(selected).__module__ == "keyring.backends.chainer"
            else [selected]
        )
        for backend in candidates:
            if type(backend).__module__ in _NATIVE_BACKENDS and backend.priority > 0:
                return backend
    except Exception:
        # Never log backend exceptions: some include the supplied credentials.
        pass
    return None


def _tokens(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {
        name: value[name]
        for name in ("access_token", "client_token")
        if isinstance(value.get(name), str) and value[name]
    }
    return result if result.get("access_token") else {}


class CredentialStore:
    def __init__(self, namespace: str):
        scope = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:24]
        self.service = f"Gr8Launcher.Ely.by.{scope}"
        self._backend = None
        self._backend_checked = False
        self._sessions: dict[str, dict[str, str]] = {}
        self._persistent: set[str] = set()
        self._lock = threading.RLock()

    def _get_backend(self):
        if not self._backend_checked:
            self._backend = _native_backend()
            self._backend_checked = True
        return self._backend

    def get(self, reference: str) -> dict[str, str]:
        with self._lock:
            if reference not in self._sessions:
                tokens = {}
                backend = self._get_backend()
                if backend is not None:
                    try:
                        value = backend.get_password(self.service, reference)
                        tokens = _tokens(json.loads(value)) if value else {}
                    except Exception:
                        pass
                self._sessions[reference] = tokens
                if tokens:
                    self._persistent.add(reference)
            return self._sessions[reference].copy()

    def put(self, reference: str, tokens: dict[str, str]) -> bool:
        with self._lock:
            clean = _tokens(tokens)
            self._sessions[reference] = clean
            self._persistent.discard(reference)
            if not clean:
                return False
            backend = self._get_backend()
            if backend is not None:
                try:
                    payload = json.dumps(clean)
                    backend.set_password(self.service, reference, payload)
                    # Migration may remove plaintext only after a verified save.
                    if _tokens(json.loads(backend.get_password(self.service, reference))) == clean:
                        self._persistent.add(reference)
                        return True
                except Exception:
                    pass
            return False

    def delete(self, reference: str) -> None:
        with self._lock:
            # A failed vault deletion must never reload the session in this run.
            self._sessions[reference] = {}
            self._persistent.discard(reference)
            backend = self._get_backend()
            if backend is not None:
                try:
                    backend.delete_password(self.service, reference)
                except Exception:
                    pass

    def status(self, reference: str) -> str:
        with self._lock:
            if reference in self._persistent:
                return "saved"
            if reference in self._sessions:
                return "session" if self._sessions[reference] else "missing"
            return "unknown"
