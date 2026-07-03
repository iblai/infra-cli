# Provisioning ibl.ai on Google Cloud (GCP)

This guide walks you through standing up a **single-server** ibl.ai deployment on
GCP, start to finish. If you've used the AWS flow, this is the same experience —
just point it at a GCP project.

> **Scope:** single-server only. Multi-server and call-server remain AWS-only for now.
> **Storage note:** GCP creates **no** object storage. The platform keeps using
> **AWS S3** — you supply AWS credentials at the setup step (details in
> [Storage](#5-storage-uses-aws-s3)).

---

## What you'll end up with

```
                 Internet
                    │
      ┌─────────────▼──────────────┐
      │  Global external HTTPS LB   │   ← one static IP, Google-managed TLS cert
      └─────────────┬──────────────┘
                    │ HTTP :80
      ┌─────────────▼──────────────┐
      │  1 VM (Ubuntu 22.04, Docker) │   ← external IP for SSH (locked to your IP)
      └─────────────────────────────┘
   VPC · 1 subnet · 2 firewall rules · Cloud DNS A-records → the LB IP
```

---

## 1. Prerequisites (one-time)

| # | What | How |
|---|------|-----|
| 1 | **A GCP project with billing on** | You'll pass its **project ID** (e.g. `my-proj-123456`, *not* the display name). |
| 2 | **Local tools** | `terraform` (≥1.0) and `gcloud` installed. Plus this CLI: `uv sync` (add `--extra gcp` — see step 4). |
| 3 | **Authentication** | Pick one:<br>• **Local:** `gcloud auth application-default login`<br>• **CI / automation:** a service-account key JSON file |
| 4 | **GCP Python libraries** | `uv sync --extra gcp` (installs the Google SDKs this CLI needs). |
| 5 | **IAM roles + APIs** | Run `iblai infra permissions --provider gcp` to print them. Short version below. |
| 6 | **A domain + Cloud DNS zone** *(for HTTPS)* | Either already have a Cloud DNS zone for your domain, or let the wizard create one and you delegate it (see [DNS](#4-dns--https)). Skip this only if you want HTTP-only. |
| 7 | **Three AWS S3 buckets + AWS keys** | The platform stores media/static/backups on S3. See [Storage](#5-storage-uses-aws-s3). |

**Roles + APIs** (what `permissions` prints):

```bash
# Grant to your user or service account:
#   roles/compute.admin   roles/dns.admin   roles/iam.serviceAccountUser

# Enable the APIs:
gcloud services enable compute.googleapis.com dns.googleapis.com --project <PROJECT_ID>
```

### Authentication — pick one

**Option A — your Google account** (easiest for local use):

```bash
gcloud auth application-default login
```

> `gcloud auth login` alone is **not** enough — Terraform and the CLI read
> *Application Default Credentials*, which only the command above writes.

**Option B — service-account key** (for CI, or when your Workspace org blocks
Option A's browser flow with an "Access blocked / admin needs to review" page):

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

Use it by choosing **"Service-account key file"** in the wizard, or setting
`GCP_CREDENTIALS_FILE=~/infra-cli-key.json` in your `.env`. Keep the key file
out of git; delete the service account when you no longer need it.

Verify you're ready (works with either option):

```bash
iblai infra permissions --provider gcp --check --project <PROJECT_ID>
```

---

## 2. Provision — the easy way (interactive)

```bash
iblai infra provision
```

1. **Choose cloud** → select **GCP**.
2. **Authentication** → project ID, region/zone, and ADC or a key file. *(Validated on the spot.)*
3. **Project & compute** → a name, environment, machine type (default `e2-standard-8`), disk.
4. **Network & access** → it auto-detects your public IP and locks SSH to it.
5. **Domain & certificates** → pick your Cloud DNS zone (or create one), or skip HTTPS.
6. **Review** → confirm, and it provisions.

When it finishes you'll see the VM IP, the app URL, and an SSH command.

> **AI heads-up:** `e2-standard-8` has 32 GB RAM. If you'll enable AI features at
> setup, choose a 64 GB machine (`e2-standard-16` or `n2-highmem-8`).

---

## 3. Provision — the automated way (non-interactive)

Best for CI or repeatable runs.

```bash
cp .env.provision.gcp.example .env
# edit .env — set PROVIDER=gcp, GCP_PROJECT_ID, DOMAIN, etc.
iblai infra provision-env -f .env
```

Minimum `.env`:

```ini
PROVIDER=gcp
GCP_PROJECT_ID=my-proj-123456
# GCP_CREDENTIALS_FILE=/path/to/key.json   # omit to use `gcloud` ADC
PROJECT_NAME=mydeploy
DOMAIN=platform.example.com
VPN_IP=auto                                 # your current IP; or a literal IP
CERT_METHOD=auto                            # managed cert if a Cloud DNS zone matches, else HTTP-only
DNS_ZONE_NAME=my-zone                        # the zone's resource name (managed cert)
```

See `.env.provision.gcp.example` for every option (machine type, disk, upload certs, create-zone, etc.).

---

## 4. DNS & HTTPS

The certificate is a **classic Google-managed SSL cert** covering your base domain
+ 19 ibl.ai subdomains. Two things to know:

1. **It validates asynchronously.** `apply` finishes while the cert is still
   `PROVISIONING`. HTTPS goes live **10–60 minutes later**, once DNS resolves to the
   load balancer. This is normal — the CLI tells you so.
2. **Your domain must be delegated to Cloud DNS.** If you let the CLI **create** the
   zone (`CREATE_DNS_ZONE=true` or the wizard's "create" option), it prints the
   nameservers — set those at your registrar. The cert can't validate until
   delegation is live.

Check cert status any time:

```bash
gcloud compute ssl-certificates describe <project>-<env>-cert --global \
  --format="value(managed.status)"        # PROVISIONING → ACTIVE when ready
```

**No domain yet?** Choose `CERT_METHOD=none` (or "Skip HTTPS" in the wizard) to get an
HTTP-only load balancer, and add TLS later.

---

## 5. Storage uses AWS S3

GCP provisions no buckets. Before (or during) setup, make sure you have **three S3
buckets** in an AWS account, named on the standard convention:

```
<project>-<environment>-<domain-with-dots-as-dashes>-backups
<project>-<environment>-<domain-with-dots-as-dashes>-dm-media
<project>-<environment>-<domain-with-dots-as-dashes>-dm-static   # public-read
```

Example, for `PROJECT_NAME=mydeploy`, `ENVIRONMENT=prod`, `DOMAIN=platform.example.com`:

```
mydeploy-prod-platform-example-com-backups
mydeploy-prod-platform-example-com-dm-media
mydeploy-prod-platform-example-com-dm-static   (public-read)
```

You provide the **AWS access key + secret** at the `setup` step (below); the app on
the GCP box uses them to reach S3 — exactly as the AWS deployment does today.

---

## 6. Set up the platform

Provisioning creates the infrastructure; **setup** installs and configures ibl.ai on
the VM (over SSH, via Ansible):

```bash
iblai infra setup <PROJECT_NAME>
```

It reads the VM's IP and SSH key from the saved state and prompts for the AWS
credentials (for S3), a GitHub token, and admin details. This step is identical to
the AWS flow.

---

## 7. Inspect and tear down

```bash
iblai infra list                 # all environments (GCP rows are marked "gcp · single")
iblai infra status <PROJECT_NAME> # details for one
iblai infra destroy <PROJECT_NAME>
```

`destroy` removes everything this tool created (VM, LB, firewall, DNS records, cert).
A Cloud DNS zone **you** created outside the tool is left alone; a zone the tool
created is removed.

---

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `No Application Default Credentials found` | Run `gcloud auth application-default login` (`gcloud auth login` alone is not enough), or set `GCP_CREDENTIALS_FILE` to a service-account key. |
| "Access blocked: … admin needs to review" during ADC login | Your Workspace org blocks the ADC browser flow. Use a service-account key instead — [Authentication, Option B](#authentication--pick-one). |
| `API not enabled for this project` | `gcloud services enable compute.googleapis.com dns.googleapis.com --project <ID>`. |
| HTTPS not working right after apply | Expected — the managed cert takes 10–60 min. Confirm DNS is **delegated** and A-records resolve to the LB IP. |
| Cert stuck in `PROVISIONING` / `FAILED_NOT_VISIBLE` | A domain isn't resolving to the LB. Check registrar delegation and that every A-record points at the LB IP. |
| **503 "no healthy upstream"** | Expected **before** `iblai infra setup` completes — the LB health check probes the LMS heartbeat (`learn.<domain>/heartbeat` on `:80`), which only exists once the platform is installed. The backend flips healthy ~1–2 min after setup finishes. If it persists: SSH in and check `curl -H "Host: learn.<domain>" http://localhost/heartbeat` returns 200, then `gcloud compute backend-services get-health <project>-<env>-backend --global`. |
| `apply` fails assigning an external IP, or SSH keys ignored | Your project enforces an org policy (no external IPs, or OS Login). This build targets loose projects (external IP + metadata SSH). Tell the maintainers — IAP + OS Login is a documented alternative. |
| Can't SSH to the VM | SSH is locked to the IP you provided (`VPN_IP`). If your IP changed, re-provision or update the firewall rule. |

---

## How it maps to AWS (for reference)

| AWS | GCP |
|-----|-----|
| VPC + 2 AZ subnets | VPC + 1 regional subnet |
| Security groups | Firewall rules (by network tag) |
| EC2 + key pair | Compute instance + metadata SSH key |
| Application Load Balancer | Global external ALB (`EXTERNAL_MANAGED`) |
| Target group | Unmanaged instance group + backend service |
| ACM cert (Route53-validated) | Google-managed cert (Cloud DNS, async) |
| Route53 A-alias records | Cloud DNS A-records → static LB IP |
| S3 buckets | *(none — reuses AWS S3)* |

Deeper internals live in `CLAUDE.md` under **GCP Provider (single-server)**.
