"""Shared helpers for `.env`-driven commands (launch-env, provision-env)."""

from __future__ import annotations

from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Skips full-line comments, blank lines,
    and inline `<space>#<rest>` comments on unquoted values.

    Inline comments only kick in when there's whitespace before the `#`, so
    `KEY=secret#abc` keeps the `#abc` as data — quoted values always keep
    `#` as data regardless. Matches python-dotenv's convention for the
    common unquoted-value case.
    """
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        # Strip inline comments for unquoted values. Quoted values preserve
        # `#` (and trailing comments) as part of the value until the closing
        # quote — operators with `#` in passwords should quote them.
        if value and value[0] not in ('"', "'"):
            for i in range(len(value) - 1):
                if value[i] in (" ", "\t") and value[i + 1] == "#":
                    value = value[:i].rstrip()
                    break
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
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
