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


def resolve_pinned_cli_ops_tag(
    git_token: str,
    github_org: str,
    prod_images_repo: str,
    prod_images_tag: str,
    subdir: str | None = None,
    timeout: int = 15,
) -> str | None:
    """Resolve the iblai-cli-ops tag pinned by a prod-images release.

    `iblai-prod-images`' pyproject.toml pins its `ibl-cli` dependency via
    `[tool.uv.sources]` (e.g. rev = "5.39.0"). uv ignores that table when the
    package is installed from a git URL (it's project config, not package
    metadata), so the Ansible role must force-install iblai-cli-ops at an
    explicit tag — but the *correct* tag is knowable from the pin. This
    fetches `pyproject.toml` at `{prod_images_tag}` via the GitHub contents
    API (raw media type) using the operator's PAT and returns the pinned rev.

    Returns None on any failure (no access, tag missing, no pin, path-style
    pin in a monorepo) — callers fall back to asking the operator.
    """
    import json
    import tomllib
    import urllib.request

    path = f"{subdir}/pyproject.toml" if subdir else "pyproject.toml"
    url = (
        f"https://api.github.com/repos/{github_org}/{prod_images_repo}"
        f"/contents/{path}?ref={prod_images_tag}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {git_token}",
            "Accept": "application/vnd.github.raw",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = tomllib.loads(raw)
        source = (
            data.get("tool", {}).get("uv", {}).get("sources", {}).get("ibl-cli", {})
        )
        rev = source.get("rev") if isinstance(source, dict) else None
        if isinstance(rev, str) and rev.strip():
            return rev.strip()
    except Exception:
        pass
    return None
