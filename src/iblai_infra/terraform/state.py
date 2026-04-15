"""Project state persistence — tracks infrastructure lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from iblai_infra.models import InfraConfig, ProjectState

WORKSPACE_ROOT = Path.home() / ".iblai-infra" / "projects"

_TFVAR_RE = None  # lazy-compiled


def read_tfvar(workspace_path: Path, key: str) -> str | None:
    """Read a single variable value from terraform.tfvars in the workspace.

    The tfvars format is ``key = "value"`` per line.  Returns *None* if the
    file does not exist or the key is not found.
    """
    import re

    global _TFVAR_RE  # noqa: PLW0603
    if _TFVAR_RE is None:
        _TFVAR_RE = re.compile(r'^(\w+)\s*=\s*"(.*)"', re.MULTILINE)

    tfvars = workspace_path / "terraform.tfvars"
    if not tfvars.exists():
        return None

    for m in _TFVAR_RE.finditer(tfvars.read_text()):
        if m.group(1) == key:
            return m.group(2)
    return None


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


# ---------------------------------------------------------------------------
# Ingress registry (pre-provisioned domain endpoints)
# ---------------------------------------------------------------------------

_INGRESS_FILE = WORKSPACE_ROOT.parent / "ingress.json"
_LOCKS_DIR = WORKSPACE_ROOT.parent / "locks"


def load_ingress_registry():
    """Load the full ingress registry (entries + lock config).

    Handles backward compat: if the file is a plain list, wraps it.
    """
    from iblai_infra.models import IngressRegistry

    if not _INGRESS_FILE.exists():
        return IngressRegistry()
    try:
        data = json.loads(_INGRESS_FILE.read_text())
        # Backward compat: old format was a bare list of entries
        if isinstance(data, list):
            return IngressRegistry(entries=data)
        return IngressRegistry.model_validate(data)
    except Exception:
        return IngressRegistry()


def save_ingress_registry(registry) -> None:
    """Save the full ingress registry to disk."""
    _INGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _INGRESS_FILE.write_text(registry.model_dump_json(indent=2))


def load_ingress() -> list:
    """Load all registered ingress entries."""
    return load_ingress_registry().entries


def save_ingress(entries: list) -> None:
    """Save ingress entries to disk (preserves lock config)."""
    registry = load_ingress_registry()
    registry.entries = entries
    save_ingress_registry(registry)


def add_ingress(name: str, domain: str):
    """Add a new ingress entry. Raises ValueError if name already exists."""
    from iblai_infra.models import IngressEntry

    registry = load_ingress_registry()
    if any(e.name == name for e in registry.entries):
        raise ValueError(f"Ingress '{name}' already exists")
    entry = IngressEntry(name=name, domain=domain)
    registry.entries.append(entry)
    save_ingress_registry(registry)
    return entry


def remove_ingress(name: str) -> bool:
    """Remove an ingress entry by name. Returns True if found and removed."""
    registry = load_ingress_registry()
    filtered = [e for e in registry.entries if e.name != name]
    if len(filtered) == len(registry.entries):
        return False
    registry.entries = filtered
    save_ingress_registry(registry)
    return True


# ---------------------------------------------------------------------------
# Ingress lock configuration
# ---------------------------------------------------------------------------


def configure_ingress_lock(bucket: str, prefix: str = "ingress-locks") -> None:
    """Configure S3 as the lock backend for ingress slots."""
    registry = load_ingress_registry()
    registry.lock.backend = "s3"
    registry.lock.bucket = bucket
    registry.lock.prefix = prefix
    save_ingress_registry(registry)


# ---------------------------------------------------------------------------
# Lock backends — local filesystem
# ---------------------------------------------------------------------------


def _local_read_lock(name: str) -> dict | None:
    lock_file = _LOCKS_DIR / f"{name}.lock"
    if not lock_file.exists():
        return None
    try:
        return json.loads(lock_file.read_text())
    except Exception:
        return None


def _local_write_lock(name: str, domain: str, claimed_by: str) -> bool:
    """Write a local lock file. Returns False if already locked."""
    lock_file = _LOCKS_DIR / f"{name}.lock"
    if lock_file.exists():
        return False
    _LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps({
        "claimed_by": claimed_by,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "domain": domain,
    }))
    return True


def _local_delete_lock(name: str) -> bool:
    lock_file = _LOCKS_DIR / f"{name}.lock"
    if not lock_file.exists():
        return False
    lock_file.unlink()
    return True


# ---------------------------------------------------------------------------
# Lock backends — S3
# ---------------------------------------------------------------------------


def _s3_read_lock(bucket: str, prefix: str, name: str) -> dict | None:
    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.client("s3")
    try:
        resp = s3.get_object(Bucket=bucket, Key=f"{prefix}/{name}.lock")
        return json.loads(resp["Body"].read())
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise


def _s3_write_lock(bucket: str, prefix: str, name: str, domain: str, claimed_by: str) -> bool:
    """Write an S3 lock object. Returns False if already locked."""
    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.client("s3")
    key = f"{prefix}/{name}.lock"

    # Check if already claimed
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return False
    except ClientError as e:
        if e.response["Error"]["Code"] != "404":
            raise

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps({
            "claimed_by": claimed_by,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "domain": domain,
        }),
        ContentType="application/json",
    )
    return True


def _s3_delete_lock(bucket: str, prefix: str, name: str) -> bool:
    """Delete an S3 lock object. Returns False if not found."""
    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.client("s3")
    key = f"{prefix}/{name}.lock"
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise
    s3.delete_object(Bucket=bucket, Key=key)
    return True


# ---------------------------------------------------------------------------
# Ingress claim / release / status
# ---------------------------------------------------------------------------


def _read_lock(lock_cfg, name: str) -> dict | None:
    if lock_cfg.backend == "s3" and lock_cfg.bucket:
        return _s3_read_lock(lock_cfg.bucket, lock_cfg.prefix, name)
    return _local_read_lock(name)


def _write_lock(lock_cfg, name: str, domain: str, claimed_by: str) -> bool:
    if lock_cfg.backend == "s3" and lock_cfg.bucket:
        return _s3_write_lock(lock_cfg.bucket, lock_cfg.prefix, name, domain, claimed_by)
    return _local_write_lock(name, domain, claimed_by)


def _delete_lock(lock_cfg, name: str) -> bool:
    if lock_cfg.backend == "s3" and lock_cfg.bucket:
        return _s3_delete_lock(lock_cfg.bucket, lock_cfg.prefix, name)
    return _local_delete_lock(name)


def claim_ingress(name: str | None = None, claimed_by: str = "") -> tuple[str, str] | None:
    """Claim a free ingress slot. Returns (name, domain) or None if all occupied.

    If *name* is given, claims that specific slot. Otherwise picks the first free one.
    """
    registry = load_ingress_registry()
    if not registry.entries:
        return None

    if name:
        entry = next((e for e in registry.entries if e.name == name), None)
        if entry is None:
            return None
        if _write_lock(registry.lock, entry.name, entry.domain, claimed_by):
            return entry.name, entry.domain
        return None

    # Pick first free slot
    for entry in registry.entries:
        if _write_lock(registry.lock, entry.name, entry.domain, claimed_by):
            return entry.name, entry.domain
    return None


def release_ingress_lock(name: str) -> bool:
    """Release a claimed ingress slot."""
    registry = load_ingress_registry()
    return _delete_lock(registry.lock, name)


def get_ingress_status() -> list[tuple]:
    """Return (IngressEntry, lock_info_or_None) for every registered entry."""
    registry = load_ingress_registry()
    result = []
    for entry in registry.entries:
        lock = _read_lock(registry.lock, entry.name)
        result.append((entry, lock))
    return result
