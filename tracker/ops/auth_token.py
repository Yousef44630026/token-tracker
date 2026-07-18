"""Load the shared local collector bearer without serializing it into task definitions."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def default_auth_token_file(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    configured = env.get("TRACKER_AUTH_TOKEN_FILE")
    if configured:
        return Path(configured).expanduser()
    store = env.get("TRACKER_STORE")
    if store:
        return Path(store).expanduser().resolve().parent / "config" / "collector-auth.token"
    if os.name == "nt":
        return Path(r"C:\ai-token-tracker-data\config\collector-auth.token")
    return Path.home() / ".local" / "share" / "ai-token-tracker" / "collector-auth.token"


def load_auth_token(
    environment: Mapping[str, str] | None = None,
    *,
    allow_default_file: bool | None = None,
) -> str | None:
    """Load a direct env bearer or a strong token file, failing closed if malformed."""
    env = os.environ if environment is None else environment
    direct = env.get("TRACKER_AUTH_TOKEN")
    if direct:
        return direct
    if allow_default_file is None:
        # Explicit test/config mappings are host-independent unless they name a file.
        allow_default_file = environment is None or bool(env.get("TRACKER_AUTH_TOKEN_FILE"))
    if not allow_default_file:
        return None
    path = default_auth_token_file(env)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"collector auth token file is unreadable: {path}: {type(exc).__name__}") from exc
    token = raw.strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ValueError(f"collector auth token file is malformed: {path}")
    return token


__all__ = ["default_auth_token_file", "load_auth_token"]
