"""Project state persistence — tracks infrastructure lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from iblai_infra.models import InfraConfig, ProjectState

WORKSPACE_ROOT = Path.home() / ".iblai-infra" / "projects"


def workspace_dir(config: InfraConfig) -> Path:
    """Return the workspace directory for a given configuration."""
    return WORKSPACE_ROOT / config.resource_prefix


def save_state(state: ProjectState) -> Path:
    """Save project state to disk."""
    ws = Path(state.workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    state_file = ws / "state.json"
    state.updated_at = datetime.now(timezone.utc)
    state_file.write_text(state.model_dump_json(indent=2))
    return state_file


def load_state(name: str) -> ProjectState | None:
    """Load project state by name. Searches all workspaces."""
    for ws in list_workspaces():
        state_file = ws / "state.json"
        if state_file.exists():
            try:
                state = ProjectState.model_validate_json(state_file.read_text())
            except Exception:
                continue
            if state.name == name:
                return state
    return None


def list_workspaces() -> list[Path]:
    """List all project workspace directories."""
    if not WORKSPACE_ROOT.exists():
        return []
    return sorted(
        [d for d in WORKSPACE_ROOT.iterdir() if d.is_dir() and (d / "state.json").exists()]
    )


def list_all_states() -> list[ProjectState]:
    """Load all project states."""
    states = []
    for ws in list_workspaces():
        state_file = ws / "state.json"
        try:
            states.append(ProjectState.model_validate_json(state_file.read_text()))
        except Exception:
            continue
    return states


# ---------------------------------------------------------------------------
# Session persistence (auth credentials across commands)
# ---------------------------------------------------------------------------

_SESSION_FILE = WORKSPACE_ROOT.parent / "session.json"


def save_session(creds) -> None:
    """Persist credentials to session file (excludes secret key for safety)."""
    _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "method": creds.method.value,
        "profile": creds.profile,
        "region": creds.region,
        "account_id": creds.account_id,
        "arn": creds.arn,
    }
    if creds.access_key_id:
        data["access_key_id"] = creds.access_key_id
    _SESSION_FILE.write_text(json.dumps(data, indent=2))


def load_session():
    """Load and validate saved session. Returns (AWSCredentials, CallerIdentity) or None."""
    from iblai_infra.models import AWSCredentials, AuthMethod
    from iblai_infra.providers.aws import validate_credentials

    if not _SESSION_FILE.exists():
        return None

    try:
        data = json.loads(_SESSION_FILE.read_text())
        method = AuthMethod(data["method"])

        if method == AuthMethod.PROFILE:
            creds = AWSCredentials(
                method=method, profile=data["profile"], region=data["region"],
            )
        elif method == AuthMethod.ENVIRONMENT:
            from iblai_infra.providers.aws import has_env_credentials
            if not has_env_credentials():
                return None
            creds = AWSCredentials(method=method, region=data["region"])
        elif method == AuthMethod.ACCESS_KEY:
            # Secret key not stored — re-prompt needed
            return None
        else:
            return None

        identity = validate_credentials(creds)
        creds.account_id = identity.account_id
        creds.arn = identity.arn
        return creds, identity

    except Exception:
        _SESSION_FILE.unlink(missing_ok=True)
        return None


def clear_session() -> None:
    """Remove saved session."""
    _SESSION_FILE.unlink(missing_ok=True)
