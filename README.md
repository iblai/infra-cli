<div align="center">

<a href="https://ibl.ai"><img src="https://ibl.ai/images/iblai-logo.png" alt="ibl.ai" width="300"></a>

# Infra CLI

Interactive CLI for provisioning and configuring the [ibl.ai](https://ibl.ai) platform on **AWS** or **GCP** (single-server). End-to-end infrastructure with Terraform, full application setup with Ansible. Can also bootstrap existing servers (any cloud or bare metal) without Terraform.

[![AWS](https://img.shields.io/badge/AWS-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com)
[![GCP](https://img.shields.io/badge/GCP-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com)
[![Terraform](https://img.shields.io/badge/Terraform-7B42BC?logo=terraform&logoColor=white)](https://www.terraform.io)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

> **Note:** This repository contains the installation and infrastructure provisioning tooling. Access to the ibl.ai Docker images and platform codebase requires a license. To get started, reach out at [ibl.ai/contact](https://ibl.ai/contact).

---

## Quick start

Five steps from zero to a running platform:

```bash
# 1. Install (needs Python 3.11+, Terraform on PATH)
git clone git@github.com:iblai/iblai-infra-ops.git && cd iblai-infra-ops
uv sync                        # AWS only
uv sync --extra gcp            # AWS + GCP support

# 2. Verify your cloud credentials have what's needed
uv run iblai infra permissions --check                                  # AWS
uv run iblai infra permissions --provider gcp --check --project <ID>    # GCP

# 3. Provision the infrastructure (interactive wizard — pick AWS or GCP)
uv run iblai infra provision

# 4. Install the platform on the new VM (Ansible over SSH)
uv run iblai infra setup <project-name>

# 5. Open the app
#    https://learn.<your-domain>
```

Prefer no prompts? Steps 3–4 also run from a `.env` file — see [Non-interactive](#4-non-interactive-provision--setup-env-file).

> **Deploying on Google Cloud?** The flow above is identical; read the short **[GCP guide](docs/GCP.md)** first for GCP prerequisites (project, APIs, auth) and cert/DNS behavior.

## Prerequisites

| | Requirement |
|---|---|
| **Always** | Python 3.11+ · [uv](https://docs.astral.sh/uv/) (or pip) · [Terraform](https://developer.hashicorp.com/terraform/install) on PATH · a domain you control · GitHub access to the private ibl.ai packages (licensed) |
| **AWS** | An account with EC2, ELB, S3, ACM, Route53, IAM, STS permissions (`iblai infra permissions` prints the exact policy) |
| **GCP** | A project with billing; `compute` + `dns` APIs enabled; roles `compute.admin`, `dns.admin`, `iam.serviceAccountUser`; auth via `gcloud auth application-default login` or a service-account key. AWS S3 is still used for object storage — see the [GCP guide](docs/GCP.md) |

**Installed automatically** as Python dependencies: `ansible-core` (runs the setup), `boto3` (AWS SDK), and — with `--extra gcp` — the Google Cloud SDKs. Terraform is called as a subprocess and must be installed separately.

**What setup installs on the target server:** Docker Engine + compose, pyenv + Python 3.11.8, AWS CLI v2, and [iblai-cli-ops](https://github.com/iblai/iblai-cli-ops) (the platform management CLI — private repository; unauthenticated requests see a 404).

## Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # install uv if needed

git clone git@github.com:iblai/iblai-infra-ops.git
cd iblai-infra-ops
uv sync --extra gcp                                  # or plain `uv sync` for AWS only
```

Verify:

```bash
uv run iblai --version
terraform --version
```

Every command below can be run as `uv run iblai ...`, or just `iblai ...` after `source .venv/bin/activate`.

## Usage

Run `iblai infra` with no arguments for an interactive menu of all commands.

### 1. Check cloud permissions

```bash
iblai infra permissions [--check]                                  # AWS: IAM policy JSON + dry-run
iblai infra permissions --provider gcp [--check --project <ID>]    # GCP: roles + APIs + probe
```

### 2. Provision infrastructure

```bash
iblai infra provision
```

The wizard walks you through, validating as you go:

1. **Cloud** — AWS or GCP
2. **Credentials** — AWS: profile / keys / env vars · GCP: project ID, region/zone, ADC or key file
3. **Topology** — AWS: single-server, multi-server, or call-server (LiveKit) · GCP: single-server
4. **Project & compute** — name, environment, machine/instance type, disk (min **100 GB**; a 32 GB-RAM pick prints a heads-up — 64 GB recommended when AI features are on)
5. **Network & SSH** — CIDR, your IP for SSH access (auto-detected), SSH key (generate or provide)
6. **Domain & certificates** — AWS: Route53 + ACM / upload / none · GCP: Cloud DNS + Google-managed cert (validates asynchronously, 10–60 min) / upload / none
7. **WAF (optional, AWS single-server)** — AWS WAFv2 on the ALB; skippable, add later with `iblai infra waf enable`
8. **Review** — full summary before anything is created

Terraform then runs with live per-resource progress. The results panel shows the VM IP, SSH command, and app URL.

### 3. Set up the platform

```bash
iblai infra setup <name>       # a Terraform-provisioned environment (IP/domain/key auto-filled)
iblai infra setup              # any existing server (bare metal / other cloud) — prompts for everything
```

Prompts for: release tag of [iblai-prod-images](https://github.com/iblai/iblai-prod-images) (the one version knob — the matching `iblai-cli-ops` version is resolved automatically from its pin), tenant platform name (blank = default), enable-AI toggle, optional integrations (SMTP / Stripe / Google SSO / Microsoft SSO — each off unless configured), GitHub PAT, AWS credentials (ECR + S3), OpenAI key (optional), and super admin credentials.

The playbook runs 16 roles in phases:

| Phase | Roles | What it does |
|---|---|---|
| Host setup | `docker`, `awscli`, `python` | Docker + compose, AWS CLI v2, pyenv + Python 3.11.8 |
| Platform install | `ibl_cli_ops`, `ibl_platform` | Installs the pinned platform packages; configures domain, gateway, defaults |
| Core services | `ibl_dm`, `ibl_edx`, `ibl_spa` | Data Manager (Django/Postgres/Redis/Celery), Open edX (LMS/CMS/MySQL/Mongo/ES), and the Auth/Mentor/Skills SPAs |
| Finalization | `integrations`, `admin_setup`, `data_seeding`, `ibl_tenant_platform` | OAuth/OIDC, edX↔DM sync, super admin, data seeding, optional tenant launch |
| Optional | `smtp_config`, `stripe_config`, `google_sso_config`, `microsoft_sso_config` | Each no-ops unless its trigger key is set |

### 4. Non-interactive provision + setup (`.env` file)

Same Terraform + Ansible, zero prompts. **Single-server only.**

```bash
# Provision — AWS
cp .env.provision.example .env && $EDITOR .env
iblai infra provision-env -f .env

# Provision — GCP (sets PROVIDER=gcp)
cp .env.provision.gcp.example .env && $EDITOR .env
iblai infra provision-env -f .env

# Set up the provisioned VM
cp .env.setup.example .env.setup && $EDITOR .env.setup
iblai infra setup-env <project-name> -f .env.setup
```

**Free-standing server** (no Terraform): omit the project name and add `TARGET_HOST`, `SSH_PRIVATE_KEY_PATH`, `BASE_DOMAIN`, `PROJECT_NAME` to `.env.setup`.

**Sample `.env` files** (every key documented inline — copy, edit, run):

| Sample | Used by |
|---|---|
| `.env.provision.example` | `provision-env` — fresh AWS single-server |
| `.env.provision.gcp.example` | `provision-env` — fresh GCP single-server |
| `.env.setup.example` | `setup-env` — platform install on a provisioned or free-standing VM |
| `.env.example` | `launch-env` — one-shot AMI launch (AWS) |

Populated `.env` files are gitignored; secrets are never persisted to `state.json` — they ride `--extra-vars` into Ansible at run time only.

### 5. Re-setup an existing environment

```bash
iblai infra resetup <name>
```

Points a running environment at a new domain with fresh secrets: prompts for the new base domain, prod-images release tag, and credentials; rotates all secrets, syncs DB passwords, restarts services. No Terraform changes.

### 6. Optional feature toggles (post-provision)

```bash
iblai infra waf enable [<name>]              # AWS single-server only — WAFv2 on the ALB
iblai infra waf enable-env [<name>] -f .env  # non-interactive (WAF_ALLOWED_IPS)
iblai infra waf disable <name> [--yes]
iblai infra waf status [<name>]
```

### 7. Launch from AMI (AWS, CI/CD)

One-shot Terraform + Ansible from a pre-built AMI:

```bash
iblai infra launch-env                       # .env-driven (cp .env.example .env first)

iblai infra launch \                         # fully flag-driven
  --ami-id $AMI_ID --domain $DOMAIN --hosted-zone-id $HOSTED_ZONE_ID \
  --aws-key-id $AWS_ACCESS_KEY_ID --aws-secret-key $AWS_SECRET_ACCESS_KEY \
  --ssh-public-key "$SSH_PUBLIC_KEY" --ssh-key $SSH_KEY_PATH \
  --git-token $GIT_TOKEN --vpn-ip $VPN_IP \
  --admin-email $ADMIN_EMAIL --admin-password $ADMIN_PASSWORD
```

See `iblai infra launch --help` for all options (instance type, `--platform-name`, SMTP/Stripe/SSO, `--enable-ai`, ...).

### 8. Service update (image updates, CI/CD)

Update container images and restart services — no provisioning, no secret rotation:

```bash
iblai infra service-update --host <ip> --ssh-key ~/.ssh/key.pem --git-token $GIT_TOKEN
```

Or launch a fresh AMI and swap it into an ALB target group — see `iblai infra service-update --help`.

### 9. Manage environments

```bash
iblai infra list                # all environments (cloud, type, status)
iblai infra status <name>       # details + outputs for one
iblai infra auth                # switch AWS credentials
iblai infra destroy <name>      # tear everything down
```

## Authentication

The CLI never silently auto-detects credentials — you always choose, interactively on first use, and the session is reused across commands.

**AWS** — pick one in the wizard: profiles from `~/.aws/` (type to filter), environment variables, or manual access keys.

**GCP** — two options; the wizard asks which:

*Option A — your Google account (local use):*

```bash
gcloud auth application-default login    # note: `gcloud auth login` alone is NOT enough
```

*Option B — service-account key (CI, or if your org blocks the browser flow above):*

```bash
PROJECT=<your-project-id>
gcloud iam service-accounts create infra-cli --project=$PROJECT
for R in roles/compute.admin roles/dns.admin roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:infra-cli@$PROJECT.iam.gserviceaccount.com" --role=$R --condition=None
done
gcloud iam service-accounts keys create ~/infra-cli-key.json \
  --iam-account="infra-cli@$PROJECT.iam.gserviceaccount.com"
```

Then choose **"Service-account key file"** in the wizard (or set `GCP_CREDENTIALS_FILE=~/infra-cli-key.json` in your `.env`). Keep the key file out of git; delete the service account when done.

## What gets created

**AWS (Terraform):** VPC with 2 public subnets · EC2 (Ubuntu 22.04, encrypted EBS) · Application Load Balancer (TLS 1.2/1.3) · ACM certificates (DNS-validated, auto-renewed) · security groups (SSH restricted to your IP) · 3 S3 buckets (backups, media, static) · Route53 A-records for all subdomains · optional WAFv2.

**GCP (Terraform):** VPC + regional subnet · Compute Engine VM (Ubuntu 22.04) · global external Application Load Balancer with a static IP · Google-managed SSL certificate (all subdomains, async validation) · firewall rules (SSH restricted to your IP, health-check ranges) · Cloud DNS A-records. Object storage stays on AWS S3 — details in the [GCP guide](docs/GCP.md).

**Subdomains (DNS A-records + TLS certificate coverage), all under your base domain:**

| Group | Subdomains |
|-------|------------|
| Learning platform | `learn` · `preview.learn` · `studio.learn` · `apps.learn` · `meilisearch.learn` |
| Backend / data | `api` · `api.data` · `asgi.data` · `llm.data` · `base.manager` |
| Apps & services | `auth` · `os` · `lms` · `platform` · `monitor` · `flowise` · `prometheus` |

**Platform services (Ansible):** Open edX (LMS/CMS + MySQL/Redis/MongoDB/Elasticsearch/Forum) · Data Manager (Django/ASGI/Celery + PostgreSQL 16/Redis/Flowise) · Auth/Mentor/Skills SPAs · monitoring (Prometheus/Grafana) · nginx reverse proxy.

## Workspace

Terraform state, generated SSH keys, and project configuration live at `~/.iblai-infra/projects/<project-name>/`.

## Development

```bash
uv sync --extra dev --extra gcp
uv run pytest tests/ -v                                          # 749 tests, ~2s
uv run pytest tests/ --cov=iblai_infra --cov-report=term-missing
```

```
iblai-infra-ops/
├── src/iblai_infra/
│   ├── cli.py                  # Typer CLI commands
│   ├── app.py                  # Provision wizard orchestrator
│   ├── models.py               # Pydantic models (the wizard ↔ Terraform contract)
│   ├── env_provision.py        # .env → InfraConfig (provision-env, AWS)
│   ├── gcp_env_provision.py    # .env → InfraConfig (provision-env, GCP)
│   ├── env_setup.py            # .env → SetupConfig  (setup-env)
│   ├── prompts/                # Interactive questionary prompts
│   ├── providers/              # aws.py + gcp.py — cloud SDK helpers
│   ├── features/               # Post-provision toggles (waf.py)
│   ├── terraform/              # Runner + templates
│   │   └── templates/
│   │       ├── aws/            # single-server (+ waf.tf), multi-server, call-server
│   │       └── gcp/            # single-server
│   └── ansible/                # Runner + playbooks + 16 roles
├── docs/                       # GCP guide, development notes
├── tests/                      # 749 tests
└── pyproject.toml
```

## License

Released under the [MIT License](LICENSE).
