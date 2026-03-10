# iblai-infra

Interactive CLI for provisioning [ibl.ai](https://ibl.ai) platform infrastructure on AWS.

## Requirements

- Python 3.11+
- [Terraform](https://developer.hashicorp.com/terraform/install) installed and on PATH
- AWS account with appropriate permissions

## Install

```bash
uv pip install .
```

Or for development:

```bash
uv pip install -e .
```

## Usage

Run `iblai infra` to see all available commands and a getting-started guide.

### Authentication

The CLI always lets you choose how to authenticate — it never silently auto-detects credentials. On first use, it walks you through authentication interactively.

The tool supports:
- **AWS profiles** from `~/.aws/config` and `~/.aws/credentials` (type to filter)
- **Environment variables** (`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`)
- **Manual entry** — access key + secret key (masked input)

Your session is saved after authentication and reused across all subsequent commands until you switch credentials or the session expires.

### Switch credentials

```bash
iblai infra auth
```

Clears the saved session and re-prompts for authentication. Use this to switch AWS profiles or accounts.

### Check IAM permissions

```bash
iblai infra permissions              # Show required IAM policy
iblai infra permissions --check      # Verify your credentials have the right permissions
iblai infra permissions --check --profile myprofile --region eu-west-1
```

### Provision infrastructure

```bash
iblai infra provision
```

Launches an interactive wizard that walks you through:

1. **AWS credentials** — profile, access keys, or environment variables
2. **Project & compute** — name, environment (dev/staging/prod), instance type, volume size
3. **Network & SSH** — VPC CIDR, VPN IP for SSH access, SSH key setup
4. **Domain & certificates** — base domain, Route53 integration, certificate method (ACM, upload, or none)
5. **Review** — full summary before proceeding

Terraform runs with real-time progress showing each resource as it's created.

### List environments

```bash
iblai infra list
```

### Check status

```bash
iblai infra status <name>
```

Shows infrastructure details, workspace location, and Terraform outputs.

### Destroy infrastructure

```bash
iblai infra destroy <name>
```

Prompts for confirmation. Production environments require typing the project name to confirm.

### Version

```bash
iblai --version
```

## Development

### Running tests

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

With coverage:

```bash
uv run pytest tests/ --cov=iblai_infra --cov-report=term-missing
```

## What gets created

- VPC with 2 public subnets (multi-AZ)
- EC2 instance (Ubuntu 22.04) with Docker pre-installed
- Application Load Balancer (internet-facing)
- Security groups (SSH restricted to your VPN IP)
- 3 S3 buckets (backups, media, static)
- SSL certificates and DNS records (if using Route53)
- SSH key pair (if generating new)

## Workspace

All Terraform files and state are stored at:

```
~/.iblai-infra/projects/<project-name>/
```

## License

Proprietary — ibl.ai
