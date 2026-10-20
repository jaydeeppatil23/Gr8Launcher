"""
Authentication client for Ely.by Yggdrasil API.
Handles authentication, 2FA detection, token validation, refresh, and session revocation.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
import requests

ELY_AUTH_SERVER = "https://authserver.ely.by"
REQUEST_TIMEOUT = 10.0


class ElyAuthError(Exception):
    """Base exception for Ely.by authentication errors."""
    def __init__(self, message: str, error_type: str = "AuthError"):
        super().__init__(message)
        self.message = message
        self.error_type = error_type


class Ely2FARequiredError(ElyAuthError):
    """Raised when Ely.by account is protected with Two-Factor Authentication."""
    def __init__(self, message: str = "Two-factor authentication code required."):
        super().__init__(message, error_type="2FA_REQUIRED")


class ElyInvalidCredentialsError(ElyAuthError):
    """Raised when username or password is incorrect."""
    def __init__(self, message: str = "Invalid username or password."):
        super().__init__(message, error_type="INVALID_CREDENTIALS")


def authenticate(
    login: str,
    password: str,
    totp_code: str | None = None,
    client_token: str | None = None,
) -> dict[str, Any]:
    """Authenticates with Ely.by Yggdrasil authentication endpoint.

    Args:
        login: Ely.by account username or email.
        password: Ely.by account password.
        totp_code: Optional 6-digit TOTP code if 2FA is active.
        client_token: Optional client token UUID. Generated if omitted.

    Returns:
        dict containing:
            - access_token: str
            - client_token: str
            - username: str
            - uuid: str (32-character hex)
            - user_id: str | None

    Raises:
        Ely2FARequiredError: If 2FA is active and code was missing or rejected.
        ElyInvalidCredentialsError: If credentials are invalid.
        ElyAuthError: For general connection or API errors.
    """
    clean_login = login.strip()
    clean_password = password.strip()
    if not clean_login or not clean_password:
        raise ElyInvalidCredentialsError("Username/email and password cannot be empty.")

    if not client_token:
        client_token = str(uuid.uuid4())

    effective_password = clean_password
    if totp_code:
        clean_totp = totp_code.strip()
        if clean_totp:
            effective_password = f"{clean_password}:{clean_totp}"

    payload = {
        "agent": {
            "name": "Minecraft",
            "version": 1,
        },
        "username": clean_login,
        "password": effective_password,
        "clientToken": client_token,
        "requestUser": True,
    }

    url = f"{ELY_AUTH_SERVER}/auth/authenticate"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ElyAuthError(f"Could not connect to Ely.by auth server: {exc}") from exc

    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError as exc:
            raise ElyAuthError("Invalid response format received from Ely.by.") from exc

        selected_profile = data.get("selectedProfile", {})
        user_info = data.get("user", {})

        return {
            "access_token": data.get("accessToken", ""),
            "client_token": data.get("clientToken", client_token),
            "username": selected_profile.get("name", clean_login),
            "uuid": selected_profile.get("id", ""),
            "user_id": user_info.get("id"),
        }

    # Error handling
    try:
        err_json = response.json()
        error_name = err_json.get("error", "")
        error_msg = err_json.get("errorMessage", "")
    except Exception:
        error_name = "HttpError"
        error_msg = response.text or f"HTTP {response.status_code}"

    lower_msg = error_msg.lower()
    if "two-factor" in lower_msg or "2fa" in lower_msg:
        raise Ely2FARequiredError(error_msg or "Account is protected by two-factor authentication.")

    if "invalid credentials" in lower_msg or error_name == "ForbiddenOperationException":
        raise ElyInvalidCredentialsError(error_msg or "Invalid username or password.")

    raise ElyAuthError(error_msg or f"Authentication failed ({error_name}).", error_type=error_name)


def validate_token(access_token: str, client_token: str | None = None) -> bool:
    """Checks whether the given accessToken is still valid on Ely.by.

    Returns:
        True if valid, False otherwise.
    """
    if not access_token:
        return False

    payload: dict[str, str] = {"accessToken": access_token}
    if client_token:
        payload["clientToken"] = client_token

    url = f"{ELY_AUTH_SERVER}/auth/validate"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        return response.status_code == 204
    except Exception:
        return False


def refresh_token(access_token: str, client_token: str) -> dict[str, Any] | None:
    """Refreshes an expired or existing accessToken using clientToken.

    Returns:
        dict with updated access_token, username, and uuid, or None if refresh failed.
    """
    if not access_token or not client_token:
        return None

    payload = {
        "accessToken": access_token,
        "clientToken": client_token,
        "requestUser": True,
    }

    url = f"{ELY_AUTH_SERVER}/auth/refresh"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return None

        data = response.json()
        selected_profile = data.get("selectedProfile", {})
        return {
            "access_token": data.get("accessToken", ""),
            "client_token": data.get("clientToken", client_token),
            "username": selected_profile.get("name", ""),
            "uuid": selected_profile.get("id", ""),
            "user_id": data.get("user", {}).get("id"),
        }
    except Exception:
        return None


def invalidate_token(access_token: str, client_token: str) -> bool:
    """Invalidates the session token on Ely.by (used on logout)."""
    if not access_token or not client_token:
        return False

    payload = {
        "accessToken": access_token,
        "clientToken": client_token,
    }

    url = f"{ELY_AUTH_SERVER}/auth/invalidate"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        return response.status_code == 204
    except Exception:
        return False
