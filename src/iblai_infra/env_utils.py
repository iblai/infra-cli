"""Shared helpers for `.env`-driven commands (launch-env, provision-env)."""

from __future__ import annotations

from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Skips comments and blank lines."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


def mask(value: str) -> str:
    """Mask a secret value for display."""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    """Parse a .env-style boolean. Matches launch-env conventions."""
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes")
