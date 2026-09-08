# Migration to ibl-cli-ops 6.x (`iblai_config`)

Touchpoint inventory for migrating this tool off the legacy config system
(`defaults.yml`/`auth.yml`, `ibl config save`, implicit rendering) to the 6.x
`iblai_config` model (`config.yml`/`services.yml`/`secrets.yml`, explicit
`ibl render`, registry-validated keys).

> Naming note: the new config model is called "6.x" throughout this doc, but
> the release ships as **7.x** (upstream bumped major for the breaking
> change). The version floor is **≥7.0.0** wherever a tag is checked.

Command mapping (verified against `feat-prepare-for-infra-migration` @ 6.3.0):

| Legacy | New |
|---|---|
| `ibl config save` (bare, implicit render) | `ibl render` |
| `ibl config save --set K=V` | `ibl config set K=V [K2=V2 ...]` + explicit `ibl render`. Multiple pairs, all-or-nothing. List/dict-typed keys take JSON values (`K='["a","b"]'`); dicts deep-merge. |
| `ibl config printvalue K` | `ibl config get K [--json]` — exit 1 when missing; `--json` gives `{key, found, value, source}` (source: secrets/config/services/env/images/default) |
| `ibl config rotate-secrets -f --include-auth` | `ibl secrets rotate --all-generated -y` (every gate-active generator-backed secret; also fills missing ones) |
| `ibl config rotate-secrets --only K` | `ibl secrets rotate K -y` |
| `ibl config environment <preset>` | `ibl init [--from <preset>]` + `ibl services enable/disable KEY` |
| `ibl tutor config save` | folded into `ibl render` — delete |
| secrets written via `--set` / PyYAML into config.yml | `ibl secrets set KEY=VALUE` / `--from-env KEY=VAR` / `--from-stdin KEY` (validated, coerced, backed up, 0600; prefer env/stdin forms — argv is visible in `ps`) |
| `echo y \| ibl fix` | `ibl fix --yes` |
| — (new requirements) | `export NODE_ID=...` before render; `ibl secrets generate`; `ibl secrets check [--json]` (`{generatable, user_supplied}`, exit 1 if any missing) |

New bootstrap contract (fresh server):

```bash
export IBL_ROOT=/ibl NODE_ID=<node-id>
ibl init --from single-server           # or bare init + services enable ...
ibl config set BASE_DOMAIN=... # ENVIRONMENT_CONFIG removed upstream — preset owns the service list
ibl secrets set KEY --from-env VAR ...  # operator-supplied secrets (env/stdin forms — argv shows in ps)
ibl secrets generate
ibl secrets check --json                # fail-fast gate (user_supplied list is prompt/fail material)
ibl render
# start services; ibl global-proxy reads rendered output only — always render first
```

---

## 0. Upstream status (ibl-cli-ops)

**Core semantics complete — `fix-prioritize-required-for-in-secrets` merged
as PR #2042 (on top of PR #2041 `feat-prepare-for-infra-migration`)** —
verified empirically from this repo. A second batch of upstream asks from
the review pass is listed under "Remaining upstream" below.

- `ibl secrets set` (validated, stdin/env-safe), `ibl secrets rotate
  --all-generated`, `ibl config set` (multi-pair, JSON for list/dict types),
  `ibl config get --json` (provenance + exit 1 on missing), `ibl secrets
  check --json` (`{generatable, user_supplied}`), `ibl fix --yes`.
- **`user_supplied` fix landed**: generator-less gated secrets now surface in
  `secrets check` and block `ibl render`; a secret with no generator AND no
  gate is always-optional. `secrets generate` ends by listing still-missing
  user-supplied keys.
- **`generate_for`** (producer/consumer split): a secret generated on one
  root (e.g. `IBL_CALL.RUN_LIVEKIT`, `RUN_MYSQL`) reports as `user_supplied`
  ("copy from the producer") on consumer-only roots. `required_for` now
  accepts any config key, not just toggles (e.g. `IBL_DM.FLOWISE_API_KEY`
  gates on `FLOWISE_BASE_URL` being set — resolves the chicken-and-egg
  without `required_at_render=False`). `required_if` is a global veto taking
  a flat dotted-key view (`_ai_enabled` gates LiveKit/Flowise/Langfuse
  consumer secrets on `ENABLE_IBL_AI(_PLUS)`).
- `IBL_CALL.RUN_LIVEKIT` toggle + conditional call-template rendering landed
  (§6 unblocked). SCORM AWS keys mirror top-level `AWS_ACCESS_KEY_ID`/
  `AWS_SECRET_ACCESS_KEY` + `required_at_render=False`; social-auth passwords
  are ungated/optional.

**Verified post-`secrets generate` user-supplied render blockers per preset
(re-run 2026-08-27 against merged develop, renamed presets):**

| Root | `user_supplied` after generate |
|---|---|
| bare `ibl init` (call-server shape) | none |
| `single-server` | **none** (`IBL_SPA.VIDEOAI.OPENAI_API_KEY` dropped upstream) |
| `app-scalable` | `IBL_DM.REDIS_PASSWORD`, `IBL_EDX.MYSQL_ROOT_PASSWORD`, `IBL_EDX.MONGODB_PASSWORD`, `IBL_EDX.REDIS_PASSWORD` (copy from the node running the stores — correct cross-node semantics) |
| `app-single` | none |
| `all-services` | none |

Playbook consequences: the standard single-server path has **zero**
user-supplied secrets — `secrets check` passes right after `generate`. The
old `all-services`-enables-everything problem is RESOLVED by the upstream
`single-server` preset: the playbook runs `ibl init --from single-server`
and upstream owns the canonical service list — updating the preset updates
deployments, no disable list in this repo.

**Breaking key change for our roles:** `IBL_SMTP_SYSTEM_PASSWORD` was removed
(DM mirrors compute from `IBL_SMTP_PASSWORD`); the other `IBL_SMTP_SYSTEM_*`
config keys remain. The smtp_config role must write only `IBL_SMTP_PASSWORD`
(via `ibl secrets set`) — writing the removed key is now a hard error.

**All review-pass asks MERGED into develop** (PRs #2043
`cleanup-init-and-config`, #2044 `single-server-preset`, #2045
`exporter-placement`) and **verified empirically 2026-08-27 against merged
develop** — scratch roots per preset, full bootstrap contract exercised
(`init --from single-server` → `config set BASE_DOMAIN` → `secrets generate`
→ `secrets check` rc 0 → `NODE_ID=… ibl render` → 110 app files, clean):

- `ibl init` idempotent ✅ — re-init on an existing root → rc 0 with "use
  `ibl services set-from` to change presets"; `--force` removed entirely.
- `ibl services set-from <preset> [-y]` ✅ — no-change short-circuit
  ("already match"), switchover prints final state with `(changed)` markers,
  hard-replaces `services.yml` with a provenance header (`# preset: …`).
  Old preset names rejected by `--from`/`set-from` choice validation.
- `ENVIRONMENT_CONFIG` gone ✅ (registry key, `call_task.py` pop, template
  gates). Per-exporter `ENABLED` gating verified by render: single-server
  with monitoring on gets cadvisor + node_exporter + celery_exporter
  (default `IBL_DM.RUN_BEAT`) + postgres_exporter (default `IBL_DM.RUN_DB`);
  **no nginx_exporter** (gate requires `IBL_NGINX.RUN_NGINX`, enabled
  nowhere — exporter is deprecated, unused, removal to follow) and **no
  redis_exporter** (default `False` — still under test, not deployed; flip
  to a `RUN_REDIS`-computed default when it ships). The whole exporters
  stack is gated on `IBL_UTILITY.PROMETHEUS.ENABLE_PROMETHEUS_MONITORING`
  (default `False`, operator opt-in).
- `single-server` preset ✅; presets renamed to match the dep model:
  `app-scalable` / `app-single`. The preset drops elasticsearch (meilisearch
  instead, now tutor-managed), flowise, forum/notes/caddy/edx-smtp; enables
  the langfuse stack + grafana.
- SPA direction confirmed by render ✅: all 10 SPA compose dirs under
  `app/ibl-spa/` render **unconditionally**; `RUN_*_SPA` toggles gate only
  the global-proxy server blocks. So the `ibl_spa` role loops over enabled
  toggles (§7) — **the preset is authoritative** for the SPA set; accepted
  consequence: fresh single-servers switch from today's hardcoded
  auth/mentor/skills to the preset's `AUTH`/`LMS`/`OS`.

**`ibl services list --json` LANDED** (composes with `-p`/`--all`;
enabled-only by default; returns `{"<dotted key>": {value, description}}`
with native-typed values). The `ibl_spa` deploy loop enumerates
`RUN_*_SPA` toggles through it and validates the remaining naming
convention (`RUN_<NAME>_SPA` ↔ `<NAME>.PORT` ↔ `app/ibl-spa/<name>/`)
with loud failures.

**Remaining upstream:** the release tag only — version is cut as **7.1.0**
(`ibl/__about__.py`, changelog) but the newest pushed tag is still 7.0.0;
pin the 7.1.0 tag once pushed. (Local-env note for anyone re-validating:
`ibl render` shells out to `tutor` by name — the venv's bin dir must be on
`PATH`, which the playbook's pyenv-activate blocks already guarantee.)

Manifest (`deployment_manifest.json`): non-issue for this tool —
CI-generated deploy marker; fresh setups never have one.

## 1. CLI install + version pinning

| Site | Change |
|---|---|
| `single-server/roles/ibl_cli_ops/tasks/main.yml:39-47` | Rewrite stale comment (references `defaults.yml` missing from PyPI `ibl-cli`). Add a **≥7.0.0** floor check after install (`ibl version` / `ibl --help` probe) so new playbook templates never run against an old CLI (release ships as 7.x, see §0). |
| `call-server/roles/ibl_cli_ops/tasks/main.yml:39-43` | Same. |
| `src/iblai_infra/env_utils.py:54-104` (`resolve_pinned_cli_ops_tag`) | Works as-is; consider warning/failing when the resolved tag is <7.0.0 once playbooks are cut over. |
| `tests/conftest.py:127,145` | Fixtures pin `cli_ops_release_tag="3.19.0"` — bump to a 7.x tag. |

## 2. Root scaffolding, `NODE_ID`, `IBL_ROOT`

- **New task, every playbook, right after CLI install:** `ibl init` (+ preset /
  `services enable` per topology, see per-playbook sections). `ibl config set`
  errors on an uninitialized root. Single-server uses `ibl init --from
  single-server` (upstream preset, §0). Init is idempotent (§0: rc 0 no-op on
  an existing root, `--force` removed) — run it unconditionally, no `creates:`
  guard needed. Changing services on a live root is `ibl services set-from` —
  an explicit operator action with diff + confirm, never something a playbook
  does.
- **`NODE_ID` is required in the process env for `ibl render`** (cannot live in
  a config file). Add it to every `environment:` block that exports `IBL_ROOT`
  (~60 sites, see inventory below) and persist next to the existing bashrc
  `IBL_ROOT` lines:
  - `single-server/roles/python/tasks/main.yml:71`
  - `call-server/roles/python/tasks/main.yml:71`
  - `call-server/roles/ibl_call/tasks/main.yml:25-35`
  - RESOLVED: collected as a prompt / `--node-id` flag **defaulting to the
    project name** (service-update's `--name` is optional/auto-generated and
    AMI-baked values may differ, so the operator can always override).
    Stakes are low — `NODE_ID` only feeds CloudWatch log-group names
    (`{{BASE_DOMAIN}}-node-{{NODE_ID}}`, tutor-plugins template) and the
    Sentry env prefix; a mismatch means new log groups, nothing functional.
    Playbooks overwrite the persisted bashrc line with the supplied value.
- `IBL_ROOT` env-block inventory (add `NODE_ID` alongside):
  `playbook.yml:92`; `launch_playbook.yml:64`; `ibl_platform` ×14;
  `ibl_launch` ×4; `ibl_launch_services` ×7; `ibl_service_update` ×10;
  `ibl_spa` ×4; `smtp_config` ×2; `microsoft_sso_config` ×4; `ibl_dm:74`;
  `ibl_edx:15`; `data_seeding:56`; `ibl_tenant_platform:61`;
  `playwright_test_platforms:101`; `call-server/ibl_call` ×4.
- **New preflight task** (all playbooks, before first render): `ibl secrets
  check --json` — `failed_when` keyed on non-empty `user_supplied` (not bare
  rc), before anything starts.
- **Legacy-layout guard** (playbooks that touch existing platforms:
  `playbook.yml` [covers resetup + feature `run_partial` tags],
  `launch_playbook.yml`, `service_update_playbook.yml`): a `tags: always`
  pre_task that stats `{{ ibl_root }}/auth.yml` and fails with "legacy 5.x
  layout — run the 6.x migration (ibl-cli-ops docs/CONFIG_MIGRATION.md)
  first". Fresh servers (no auth.yml, no config.yml) and migrated servers
  (config.yml + services.yml, no auth.yml) pass; only unmigrated 5.x roots
  fail.

## 3. `ibl_platform` (setup / resetup) — `single-server/roles/ibl_platform/tasks/main.yml`

> **Ordering rule (applies to every converted role):** secret generation is
> gated on config state (`required_for`/`required_if` in the upstream
> registry — Langfuse/Flowise secrets generate only when
> `ENABLE_IBL_AI(_PLUS)` is on [registry default False], meilisearch keys
> only on `VERSION=sumac`). So every gate-affecting `ibl config set` must
> precede `ibl secrets generate`, which must precede the render that needs
> the secrets. In ibl_platform this pulled the whole scalar batch (edX
> flags, AI, RBAC) into the pre-proxy set; only the non-gate list merges
> (CSRF URLs, PLUGINS) stay after the proxy launch.

| Line(s) | Current | Change |
|---|---|---|
| 59 | `ibl config rotate-secrets -f --include-auth` (resetup) | `ibl secrets rotate --all-generated -y` — 1:1 replacement (rotates every gate-active generator-backed secret, OAuth creds included; also fills missing ones). |
| 75-80, 100-111 | PyYAML reads of rotated passwords from `/ibl/config.yml` | Read from `/ibl/secrets.yml` instead (`IBL_DM.DB_PASSWORD`, `IBL_EDX.MYSQL_ROOT_PASSWORD`, `IBL_EDX.OPENEDX_MYSQL_PASSWORD`). |
| 139-156, 187 | Fernet placeholder guard + `rotate-secrets --only ...IBL_FERNET_KEY` | Read guard value from `secrets.yml`; rotate maps 1:1 → `ibl secrets rotate IBL_EDX.IBL_EDX_SSO_BACKEND_APP.IBL_FERNET_KEY -y`. |
| 171-178 | `app/backups` dir precondition ("rotate-secrets requires it") | **Delete** — verified: `backup_secrets()` (`ibl/context.py`) mkdirs the backups dir itself (`parents=True, exist_ok=True`) and no-ops when `secrets.yml` doesn't exist. |
| 214, 229, 244, 281, 296-297, 352-354, 369-372 | 12 scalar `--set` calls | Batch into `ibl config set` blocks with **two renders** (see next row): one before the proxy launch at 259, one at the end of the config phase. Add the `ibl init`/preset step before them. **Drop the `ENVIRONMENT_CONFIG` set (281)** — the key was removed upstream (§0: registry key gone, exporters re-gated on service toggles); setting it now fails registry validation. |
| 259 | `ibl global-proxy launch-without-security` | **RESOLVED: render twice** — one `ibl render` after the sets at 214/229/244 (immediately before the proxy launch), a second after the remaining config phase — keeping the role's ordering as it is today. Context: under 5.x every `--set` implicitly rendered, so 259 always saw fresh output; with batching, each render must be explicit. (`ibl_launch_services:249-262` and `ibl_service_update:580-593` don't have this problem — save/render immediately precedes the reload there.) |
| 305-341 | PyYAML dual-file patch (CORS whitelist + list `IBL_CSRF_EXEMPT_URLS`), relies on a later implicit render | Single file now (staging copy gone). Lists are settable via `ibl config set 'K=["a","b"]'` (JSON). The *append* semantics become read-modify-write: `ibl config get K --json` + jq + `ibl config set`. Follow with explicit `ibl render`. |
| 380-404 | PyYAML append `ibl-edx-uwsgi` to `IBL_EDX.PLUGINS` (runtime copy only) | Same treatment; drift risk disappears with the single file. |
| 412-429 | Shell-generates `IBL_DM.LANGFUSE_NEXTAUTH_SECRET`/`LANGFUSE_SALT`, writes via `--set` | **Delete the whole block** — both are auto-generated by `ibl secrets generate`. (A `--set` on these would now hard-fail anyway: secret keys are rejected.) |

## 4. Launch — `ibl_launch` + `ibl_launch_services`

| Site | Change |
|---|---|
| `ibl_launch/tasks/main.yml:63` | `--set BASE_DOMAIN` → `ibl config set` + render. AMI caveat below. |
| `ibl_launch/tasks/main.yml:86` | Unconditional `rotate-secrets -f --include-auth` → same redesign as ibl_platform:59. |
| `ibl_launch/tasks/main.yml:105-140` | Password-sync PyYAML reads → `secrets.yml`. |
| `ibl_launch_services/tasks/main.yml:29` | `ibl config save && ibl dm update` → `ibl render && ibl dm update`. |
| `ibl_launch_services/tasks/main.yml:78-91` | `ibl config save && ibl tutor config save` → single `ibl render` (tutor save folded in). |
| `ibl_launch_services/tasks/main.yml:120-152` | PyYAML quoted-boolean SPA patch → plain `ibl config set` (str-typed keys; 'true'/'false' strings coerce exactly); explicit render after. |
| `ibl_launch_services/tasks/main.yml:154-167` | "Re-render" save → `ibl render`. |
| `ibl_launch_services/tasks/main.yml:249-262` | `ibl config save && ibl global-proxy reload` → `ibl render && ibl global-proxy reload`. |
| — | **AMI story (RESOLVED, and likely moot):** old 5.x AMIs are unsupported — the legacy-layout guard (§2) fails them fast at launch. AMI flows may be retired entirely (§12.8); if kept, new AMIs get baked from a 6.x-provisioned server (ops task, outside this repo); no in-role migration. |

## 5. Service update — `ibl_service_update/tasks/main.yml`

| Line(s) | Change |
|---|---|
| 38-55 | Fernet read from config.yml → `secrets.yml`. |
| 70-77 | Backups-dir precondition — **delete** (same as ibl_platform:171-178; rotate's backup mkdirs itself). |
| 86 | `rotate-secrets --only <fernet>` → `ibl secrets rotate <fernet> -y`. |
| 106-137 | Dual-file CORS/CSRF PyYAML patch → `ibl config set` with JSON list (read-modify-write via `config get --json` for the append); render follows (next row). |
| 141-161 | "Save platform config" (the load-bearing implicit render) → `ibl render`. |
| 163-176 | `ibl tutor config save` → delete (folded into render). |
| 488-544 | SSO cred sync: `--set IBL_SPA.EDX_SSO_CLIENT_ID` ok as `ibl config set`; **`--set IBL_SPA.EDX_SSO_CLIENT_SECRET` (516) now hard-fails** — use `ibl secrets set IBL_SPA.EDX_SSO_CLIENT_SECRET --from-env SSO_CLIENT_SECRET` (value read from edX; env form keeps it out of `ps`). Bare save at 534 → `ibl render`. |
| 580-593 | `global-proxy reload` — ensure a render precedes it. |
| 602-608 | Greps `BASE_DOMAIN` from rendered `app/ibl-dm/ibl-dm-pro/config.yml` — **broken under 6.x**: verified the rendered `ibl-dm-pro` tree no longer contains a `config.yml` (compose + env templates only). Must become `ibl config get BASE_DOMAIN`. |
| — | **Existing-server compat (RESOLVED):** migrated-servers-only. The legacy-layout guard (§2) fails unmigrated roots with a pointer to the operator runbook (`docs/CONFIG_MIGRATION.md` in ibl-cli-ops). No `ibl migrate` driving from this repo. |

## 6. Call server — `call-server/roles/ibl_call/tasks/main.yml`

| Line(s) | Change |
|---|---|
| 37-50 | `--set BASE_DOMAIN` → init first, then `ibl config set BASE_DOMAIN` (parent domain convention unchanged). |
| 52-68 | `ibl config environment call-only` → **preset gone.** New: `ibl init` (all services off) + `ibl services enable IBL_CALL.RUN_LIVEKIT` + `ibl secrets generate` + `ibl render` (LiveKit key/secret are generator-backed on the call root). Toggle + conditional template rendering landed upstream. |
| 93-113 | `ibl call up` — now preceded by render; LiveKit key/secret are generated into `secrets.yml` by `secrets generate` (changes the `show-call-secrets` story: values exist pre-boot). |
| Python: `app.py:508-509`, `cli.py:1567,2410-2412` | **Drop `env_config` entirely** (`SetupConfig`, `_build_extra_vars`, prompts): call path keys off `services enable`, single-server off the `--from single-server` preset, and the `ENVIRONMENT_CONFIG` set task is gone (§3) — nothing consumes it. |

## 7. SPA / integrations / tenant roles

| Site | Change |
|---|---|
| `ibl_spa/tasks/main.yml:123-130` | 8 scalar `--set` → batch `ibl config set` block. |
| `ibl_spa/tasks/main.yml:138-170` | PyYAML quoted-boolean writes → plain `ibl config set` (str-typed keys coerce exactly). |
| `ibl_spa/tasks/main.yml:172-185` | "Re-render templates with SPA configuration" → `ibl render`. |
| `ibl_spa/tasks/main.yml:202,232,258` | Hardcoded start tasks for exactly auth/mentor/skills → **loop over enabled `IBL_SPA.RUN_*_SPA` toggles** (read via `ibl services list -p IBL_SPA` or `ibl config get K --json`), `docker compose up` each. services.yml becomes the single source of truth for which SPAs deploy — the preset decides, the role follows (§0 gap 2). Compose dirs render unconditionally, so the loop can't hit a missing dir. |
| `ibl_spa/tasks/main.yml:281-294` | `save && global-proxy reload` → `render && reload`. |
| `integrations/tasks/main.yml:9-12` | Chain head `ibl config save && ...` → `ibl render && ...` (oauth/oidc/edx-manager launch + dm auth-setup unchanged). |
| `ibl_tenant_platform/tasks/main.yml:41-61` | `--set PLATFORM_NAME` → `ibl config set` + render. |
| `spa_clone` / `spa_clone_remove` | **No change** — deliberately copies rendered `.env` (bypasses config system); custom_domains nginx survival unaffected. Keep the bypass. |

## 8. Post-setup feature roles

| Feature | Change |
|---|---|
| `smtp_config/tasks/main.yml:21-59` | Dual-file PyYAML write collapses to single `/ibl/config.yml`; **`IBL_SMTP_PASSWORD` moves to `ibl secrets set --from-env`; `IBL_SMTP_SYSTEM_PASSWORD` no longer exists (removed upstream — writing it is now an error; DM mirrors compute from `IBL_SMTP_PASSWORD`)**; remaining scalars via `ibl config set`. `:61-75` save → `ibl render`. Header comment (two-copies rationale, 6-10) obsolete. |
| `microsoft_sso_config/tasks/main.yml:49-98, 296-362` | Nested/list PyYAML patches → batched `ibl config set` on **leaf keys** (the registry registers each `IBL_EDX_BASE_OAUTH_SSO_BACKEND.*` leaf individually — there is no parent dict key to deep-merge into; list/dict-typed leaves take JSON). `LEARNER_PORTAL_URL_ROOT` dropped: unregistered + unconsumed upstream. Idempotency (the edX-restart gate) preserved by comparing each leaf's `ibl config get --json` resolved value first. Reads of `IBL_SPA.EDX_SSO_CLIENT_ID` → `ibl config get`. Saves at 108, 372 → `ibl render`. Header comments updated. |
| google_sso / stripe / llm (admin_setup) | No config-system touchpoints (Django ORM / DM DB) — **no change**. |
| Future: prompt derivation | `ibl secrets check --json` (landed) lets feature `enable` flows and the setup wizard derive operator-secret prompts from `user_supplied` instead of hardcoding (unblocked — classification verified working). |

## 9. Python side (`src/iblai_infra/`)

| Site | Change |
|---|---|
| `ansible/runner.py:197-246` (`read_config_values`) | `printvalue` → `ibl config get K --json` per key: `found` + exit 1 handle missing keys, `source` distinguishes explicitly-configured from registry default, and JSON parsing replaces the repr quote-strip hack (:244-245). Verified: `config get` works **without** `NODE_ID` in the environment — the SSH preamble (:214) needs no change beyond the command swap. |
| `features/smtp.py:37` (`STATUS_KEYS`) | Fix latent mismatch: role writes `IBL_SMTP_SYSTEM_PORT`, status reads `IBL_SMTP_PORT`. |
| `ansible/runner.py:604-624` (`_build_extra_vars`) | Add `node_id` extra-var (feeds the new env blocks); remove `env_config` (§6). |
| `models.py` (`SetupConfig`) | Add `node_id` (default = project name); remove `env_config` (§6). |
| `prompts/setup.py`, `env_setup.py`, `cli.py` | No structural change for pinning; add node-id prompt / `--node-id` flag with project-name default (§2). Later: registry-derived secret prompts (§8). |

## 10. Tests

- `tests/features/test_smtp.py:213-246` — `TestReadConfigValues` encodes `printvalue` repr format; rewrite for `--get` output.
- `tests/ansible/test_runner.py:288,312,340,351,411-422,575-586` — role-set and extra-var assertions; update for any role changes + `node_id`.
- `tests/conftest.py:127,145`, `tests/test_env_setup.py`, `tests/test_env_utils.py`, `tests/prompts/test_setup.py` — tag fixtures / pin-resolution stubs to 7.x tags.
- `tests/features/test_post_setup_features.py:146-149` — extra-vars round-trip.

## 11. Docs (stale after cutover)

`CLAUDE.md:152,154,186-187,193-195,378-379,439`; `README.md:287`;
`docs/develoment.md:52`. (CHANGELOG history: leave.)

## 12. Decisions

1. **`NODE_ID` source** — RESOLVED: prompt / `--node-id` flag defaulting to
   the project name; operator can override (baked/auto-generated names don't
   matter — mismatch only renames CloudWatch log groups, see §2).
2. **Existing 5.x servers** — RESOLVED: migrated-only; legacy-layout guard
   (`auth.yml` present → fail with "migrate to 6.x first", see §2).
3. **Launch AMIs** — RESOLVED: falls out of #2 — the guard fails old AMIs
   fast; new AMIs get baked from a 6.x-provisioned server (ops task). See
   also #8 — the AMI paths may be retired entirely.
4. **Operator-secret collection** — RESOLVED by the gate semantics pass (§0):
   standard single-server needs only the top-level AWS keys (already
   collected); the last blocker (`IBL_SPA.VIDEOAI.OPENAI_API_KEY`) was
   dropped upstream (done, verified). LiveKit hand-off (call → app node) and
   Flowise two-phase apply only when those services are enabled. Preflight
   keys off `secrets check --json` `user_supplied` as the drift guard
   (working — verified).
5. **Release tag** — RESOLVED: version cut as **7.1.0** (all migration work
   merged to develop); version floor ≥7.0.0 (§1). Pin the 7.1.0 tag once
   pushed (newest pushed tag is still 7.0.0).
6. **Single-server service list** — RESOLVED: upstream `single-server` preset
   (`ibl init --from single-server`, §0). Upstream owns the canonical list;
   updating the preset updates deployments — no disable list in this repo.
7. **Proxy-launch ordering in `ibl_platform`** — RESOLVED: render twice,
   keeping today's task order (§3, line 259 row).
8. **AMI flows likely retired** — word is "we don't need to worry about the
   AMI"; the launch-from-AMI and service-update `--ami-id` paths may be going
   away. Deprioritize the §4/§5 AMI-specific work (rebake story, `--ami-id`
   mode) pending confirmation; the legacy-layout guard covers them either
   way.

Resolved by upstream `feat-prepare-for-infra-migration`: rotate-all
(`--all-generated`), list/dict writes (`ibl config set` JSON), secret writes
(`ibl secrets set`), machine-readable reads (`config get --json`),
non-interactive fix (`--yes`). Manifest interplay: non-issue for this tool
(CI-only deploy marker).

## 13. Implementation order & validation

Phasing — each phase is an independently **developable and testable**
increment (a reviewable PR onto a migration branch, validated end-to-end via
tag overrides before anything ships); later phases reuse the patterns of
earlier ones. **Release is atomic** — see the rollout constraint below:

- The 7.x CLI removed the legacy commands and 5.x lacks the new ones, so a
  playbook is entirely legacy or entirely new — and the installed CLI tag is
  one prod-images pin shared by every playbook. The pin flip converts all
  flows at once.
- Phases 2+3 are one shipping unit regardless: setup/resetup and all feature
  roles share `playbook.yml`, and a `--tags smtp` partial run of a
  half-converted playbook executes legacy tasks against a 7.x CLI.
- Phase 1 mostly can't ship early either: the `ibl init` task and version
  floor fail on 5.x, and the legacy-layout guard would brick
  resetup/service-update against today's unmigrated servers. Only the inert
  plumbing may merge ahead: `NODE_ID` in env blocks (5.x ignores it),
  `node_id` extra-var + `SetupConfig` field, test fixtures.
- Python-side reads (§9 `read_config_values`) flip with the cutover too —
  `config get` doesn't exist on 5.x servers.
- (Dual-path roles branching on CLI version would make phases individually
  shippable, but double every task + test surface for a one-time transition
  — rejected.)

1. **Cross-cutting plumbing** (§1, §2): version floor in `ibl_cli_ops` roles;
   `ibl init` task; `NODE_ID` in env blocks + bashrc + `_build_extra_vars`/
   `SetupConfig`; `secrets check --json` preflight (Ansible `failed_when`
   keyed on non-empty `user_supplied`, not bare rc — rc 1 also fires for
   unfilled generatable secrets if sequencing is wrong).
2. **Setup path** (§3, §7, §8-adjacent roles in `playbook.yml` order):
   `ibl_platform` → `ibl_spa` → `integrations` → `ibl_tenant_platform`.
   Everything else copies these patterns.
3. **Post-setup features** (§8) + status reads (§9 `read_config_values`).
4. **Call server** (§6).
5. **Launch + service-update** (§4, §5) — ungated (legacy-layout guard
   replaces the migration/AMI decisions); real-world testing needs a
   migrated server. AMI-specific work deprioritized (§12.8 — flows may be
   retired); confirm before investing there.
6. **Tests + docs** (§10, §11) alongside each phase.

Suggested PR blocks onto the migration branch (each internally complete —
role + its tests — with unit tests green; half-converted branch state is
fine, nothing executes the templates until release):

1. Python plumbing: `node_id` on `SetupConfig` + `_build_extra_vars` +
   prompt/flag + fixtures.
2. Mechanical `NODE_ID` env-block sweep (~60 sites + bashrc) — isolated so
   the trivial-but-large diff never hides semantic changes. Inert under
   5.x; may even land on main ahead of the branch.
3. Cross-cutting Ansible semantics: version floor, `ibl init` task,
   legacy-layout guard, `secrets check` preflight.
4. `ibl_platform` (§3) — the pattern-setting PR (batch `config set` + two
   renders, `secrets.yml` reads, rotate mapping). Everything after copies it.
5. `ibl_spa` (§7) incl. the toggle-driven deploy loop.
6. `integrations` + `ibl_tenant_platform`.
7. Feature roles: `smtp_config` + `microsoft_sso_config` (§8).
8. Python reads: `read_config_values` → `config get --json`, `STATUS_KEYS`
   fix, test rewrites (§9).
9. Call server (§6) + `env_config` removal.
10. `ibl_launch`/`ibl_launch_services`, then `ibl_service_update` (§4, §5).

Ordering constraint: only 4-before-5..10. Blocks 1-3 land in any order.

Rollout constraint: playbook templates ship inside iblai-infra, and the
`ibl_cli_ops` role installs whatever tag prod-images pins — so ALL converted
playbooks and a ≥7.x CLI tag land in the **same** iblai-infra release, with
the version floor (§1) catching mismatches in both directions. Pre-release
validation uses the existing tag overrides (`setup-env`/`launch` accept a
prod-images tag; resetup resolves the pin from whatever prod-images ref it's
pointed at). Ops prerequisite at cutover: live 5.x environments are migrated
(operator runbook, `docs/CONFIG_MIGRATION.md` in ibl-cli-ops) before the new
resetup/service-update/features touch them — the legacy-layout guard (§2)
fails them fast otherwise.

Validation per phase (goal-driven):

- Unit: `uv run pytest tests/` green after each phase's test updates (§10).
- Phase 2 end-to-end: `provision-env` + `setup-env` against a throwaway AWS
  project with a 7.x CLI tag; success = playbook completes, `ibl render
  status` clean, `ibl secrets check` exits 0, platform URLs healthy
  (`iblai infra dns check`), no `config save`/`printvalue` strings remain in
  `src/` (`grep -rn "config save\|printvalue\|rotate-secrets\|tutor config
  save\|config environment" src/` returns nothing).
- Phase 3: `smtp enable` + `smtp status` round-trip on that environment.
- Phase 4: call-server provision; `ibl call up` reaches `:7880`; secrets
  visible in the call root's `secrets.yml`.
- Phase 5: service-update against a 6.x-provisioned server; launch-from-AMI
  validation only if the AMI flows survive (§12.8).
