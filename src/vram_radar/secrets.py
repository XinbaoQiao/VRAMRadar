from __future__ import annotations

import keyring


SERVICE_NAME = "VRAMRadar"


class SecretStore:
    """OS-backed secret storage. Profile files contain only these references."""

    def get(self, auth_ref: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, auth_ref)

    def set(self, auth_ref: str, secret: str) -> None:
        if not auth_ref.strip() or not secret:
            raise ValueError("auth_ref and secret are required")
        keyring.set_password(SERVICE_NAME, auth_ref, secret)

    def delete(self, auth_ref: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, auth_ref)
        except keyring.errors.PasswordDeleteError:
            pass
