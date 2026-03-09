# iblai-infra — Claude Development Guide

## Overview

Interactive CLI tool for provisioning ibl.ai platform infrastructure on AWS. Built with Python, Typer, Rich, and questionary. Uses Terraform for resource management.

**Command pattern:** `iblai infra <command>`

## Project Structure

```
iblai-infra/
├── pyproject.toml                          # uv/hatch config, entry point: iblai = iblai_infra.cli:app
├── src/iblai_infra/
│   ├── __init__.py                         # __version__ = "0.2.0"
│   ├── __main__.py                         # python -m iblai_infra support
│   ├── cli.py                              # Typer app: root `iblai` + `infra` subgroup
│   ├── app.py                              # Wizard orchestrator (5-step flow)
│   ├── models.py                           # Pydantic models — contract between wizard & Terraform
│   ├── ui.py                               # Rich console, ibl.ai branding, progress helpers
│   ├── prompts/
│   │   ├── credentials.py                  # Step 1: AWS auth (profile/keys/env)
│   │   ├── infrastructure.py               # Steps 2-3: project, compute, network, SSH
│   │   ├── dns_certs.py                    # Step 4: domain, Route53, certificates
│   │   └── review.py                       # Step 5: summary + confirm
│   ├── providers/
│   │   └── aws.py                          # AWS helpers: STS validation, Route53, key pairs, IP detect, permission checks
│   ├── terraform/
│   │   ├── runner.py                       # TerraformRunner: setup/init/plan/apply/destroy with JSON streaming
│   │   ├── state.py                        # ProjectState persistence (~/.iblai-infra/projects/)
│   │   └── templates/aws/single-server/
│   │       ├── main.tf                     # VPC, subnets, ALB, EC2, S3, certs, DNS
│   │       ├── variables.tf                # All Terraform variables
│   │       ├── outputs.tf                  # IPs, ALB DNS, S3 buckets, SSH command
│   │       └── user_data.sh               # Docker, AWS CLI, UFW, systemd setup
│   └── ansible/
│       └── __init__.py                     # Phase 2 placeholder
```

## Architecture

### CLI Structure (Typer)

- **Root app** (`iblai`): `--version`, `--help`
- **Subgroup** (`iblai infra`): `provision`, `destroy`, `status <name>`, `list`, `permissions`
- Running `iblai infra` with no arguments shows branded landing screen with all commands and getting-started steps
- Entry point in `pyproject.toml`: `iblai = "iblai_infra.cli:app"`

### Wizard Flow (app.py)

5 interactive steps, each in its own prompt module:
1. **Credentials** — AWS profile / access keys / env vars, validated via STS
2. **Project & Compute** — name, environment (dev/staging/prod), instance type, volume
3. **Network & SSH** — VPC CIDR, VPN IP (auto-detected), SSH key (generate/import/AWS keypair)
4. **DNS & Certs** — domain, Route53 zone detection, cert method (ACM/upload/none)
5. **Review** — full summary panel, confirm

After confirmation: `TerraformRunner.setup()` → `init()` → `plan()` → `apply()` → show results.

### Models (Pydantic)

`InfraConfig` is the **single contract** between the wizard prompts and Terraform execution:
- `AWSCredentials` (method, profile, keys, region, account_id)
- `NetworkConfig` (vpc_cidr, vpn_ip with IP validation)
- `ComputeConfig` (instance_type, volume_size ≥20GB, volume_type)
- `SSHConfig` (method, key_name, public_key, private_key_path)
- `CertificateConfig` (method: acm/upload/none, zone_id, cert files)
- `DNSConfig` (base_domain, use_route53, hosted_zone_id, 16 subdomains)

`ProjectState` tracks lifecycle: initialized → created → failed → destroyed.

### Terraform Runner

- Uses `terraform apply -json` for structured event streaming
- Parses `apply_start`, `apply_progress`, `apply_complete`, `apply_errored` events
- `terraform show -json tfplan` for accurate resource count before apply
- Rich Live display: resource status table + progress bar, `transient=True`
- `_generate_tfvars()` converts InfraConfig → terraform.tfvars
- `RESOURCE_LABELS` maps AWS resource types to human-friendly names

### IAM Permission Checks

- `iblai infra permissions` — displays the minimum IAM policy JSON required for provisioning
- `iblai infra permissions --check` — dry-run verification against active credentials
- Checks 7 services: EC2, ELB, S3, ACM, Route 53, IAM, STS
- Uses harmless read-only API calls (e.g., `DryRun=True` for EC2, `list_*` for others)
- `REQUIRED_IAM_POLICY` dict and `check_permissions()` live in `providers/aws.py`
- Accepts `--profile` and `--region` flags for targeting specific credentials

### State Management

- Workspace root: `~/.iblai-infra/projects/<name>/`
- State file: `state.json` (Pydantic `ProjectState` serialized)
- Terraform files copied to workspace from templates

### Certificate Modes (in Terraform)

| Mode | DNS | HTTPS | Implementation |
|------|-----|-------|----------------|
| ACM | Route53 auto-managed | Yes | ACM certs + DNS validation + HTTPS listener |
| Upload | External (user-managed) | Yes | IAM server cert + HTTPS listener |
| None | External (user-managed) | No | HTTP only (user warned) |

Uses `locals` with `use_acm`, `use_upload`, `use_https` booleans and conditional `count`.

## Brand & UI

- **Primary color:** `#2175C5` (ibl.ai blue)
- **Palette:** `#5BA3E0` (light), `#A8D0F2` (pale), `#174E87` (dark), `#0E3259` (navy)
- Rich theme applied globally via `IBL_THEME`
- questionary styled via `PROMPT_STYLE`
- ASCII art logo banner in `ui.banner()`
- Step progress breadcrumb bar in `ui.step_header()`

## Dependencies

- `typer>=0.12` — CLI framework
- `rich>=13.7` — Terminal UI
- `questionary>=2.0` — Interactive prompts
- `pydantic>=2.5` — Data validation
- `boto3>=1.34` — AWS SDK

## Conventions

- Python 3.11+, `from __future__ import annotations`
- `src/` layout with hatchling build
- Package manager: `uv`
- All prompts return Pydantic models, never raw dicts
- UI helpers (`ui.success()`, `ui.error()`, etc.) for all terminal output
- Terraform templates use standard HCL, not Jinja2
- State persisted as JSON via Pydantic's `.model_dump_json()`

## Phase 2 (Future)

Ansible-based environment state setup — placeholder at `src/iblai_infra/ansible/`.
