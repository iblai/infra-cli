# Changelog

## [0.2.0] — 2026-03-09

### Added
- `iblai infra permissions` command — displays minimum IAM policy JSON required for provisioning
- `--check` flag for dry-run permission verification against active AWS credentials (EC2, ELB, S3, ACM, Route 53, IAM, STS)
- `--profile` and `--region` flags for targeting specific credentials during permission checks
- Branded landing screen when running `iblai infra` with no arguments — shows all available commands and a getting-started guide

## [0.1.0] — 2026-03-09

### Added
- Interactive provisioning wizard with 5-step flow (credentials, compute, network, DNS, review)
- AWS authentication: profile, access keys, or environment variables with STS validation
- EC2 single-server provisioning with configurable instance type and volume
- VPC, public subnets (multi-AZ), internet gateway, and route tables
- Application Load Balancer with security groups
- Three certificate modes: ACM (auto-managed via Route53), upload (IAM server cert), or none (HTTP only)
- Three SSH key modes: generate Ed25519 keypair, provide existing public key, or use AWS key pair
- SSH access restricted to user-provided VPN IP
- S3 buckets for backups, media, and static files
- 16 ibl.ai platform subdomain records (when using Route53)
- Real-time Terraform progress with JSON event streaming and Rich Live display
- `iblai infra provision` — interactive provisioning wizard
- `iblai infra destroy` — destroy infrastructure with double-confirmation for production
- `iblai infra status <name>` — show infrastructure details and workspace info
- `iblai infra list` — list all managed environments
- ibl.ai branded terminal UI with Rich theme and questionary styling
- Project state persistence at `~/.iblai-infra/projects/`
- Workspace visibility showing Terraform files during and after provisioning
