---
description: Update all project docs (CLAUDE.md, README, CHANGELOG), bump version, commit and push
user_invocable: true
---

# update-docs-and-commit

Update all project documentation, bump the version, and push changes.

## Steps

1. **Analyze recent changes**: Read git log since the last version tag/bump to understand what changed.

2. **Determine version bump**: Read the current version from `src/iblai_infra/__init__.py`. Decide the new version:
   - **Patch** (0.x.Y) — bug fixes, minor tweaks
   - **Minor** (0.X.0) — new features, commands, or significant changes
   - Ask the user only if the scope is ambiguous.

3. **Update version**: Edit `src/iblai_infra/__init__.py` to set the new `__version__`.

4. **Update CHANGELOG.md**: Add a new version entry at the top with sections:
   - `### Added` — new features, commands, files
   - `### Changed` — modifications to existing behavior
   - `### Fixed` — bug fixes
   - Only include sections that have entries. Use the same style as existing entries.

5. **Update CLAUDE.md**: Reflect the current state of the project:
   - Update the version number in the project structure section
   - Add/update sections for any new commands, architecture changes, or conventions
   - Keep the file accurate as a development guide — it should describe what exists NOW
   - Do NOT bloat the file — remove outdated information

6. **Update README.md**: Update user-facing documentation:
   - Add new commands with usage examples
   - Update feature descriptions if changed
   - Keep it concise — README is for users, not developers

7. **Commit and push**:
   - Stage: `CHANGELOG.md`, `CLAUDE.md`, `README.md`, `src/iblai_infra/__init__.py`, and `uv.lock` if changed
   - Commit message format: `chore: bump version to X.Y.Z, update docs and changelog`
   - Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`
   - Push to the current branch

## Key files

- `src/iblai_infra/__init__.py` — version source of truth
- `CHANGELOG.md` — version history
- `CLAUDE.md` — developer/Claude context guide
- `README.md` — user-facing docs

## Rules

- NEVER include secrets, credentials, or sensitive values in any docs
- Keep CLAUDE.md under ~200 lines of meaningful content
- CHANGELOG entries should describe the "why" not just the "what"
- Match the existing writing style in each file
