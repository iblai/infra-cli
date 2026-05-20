"""Post-provision runtime IAM helper.

The platform server bakes a single AWS access key into `/ibl/config.yml` for
two ongoing purposes:

  1. **ECR pulls** — `aws ecr get-login-password` against IBL's image
     registry. Cross-account; works because IBL's ECR repositories have a
     repository policy granting pulls from the operator's AWS account.
  2. **S3 access** — read / write the dm-media, dm-static, and backups
     buckets Terraform just created in the operator's own account.

Rather than reusing the operator's admin keys (full provisioning scope,
massive blast radius) or asking IBL ops to mint a separate user, this
module prints a **scoped IAM policy** the operator pastes into their own
IAM console after `provision-env` / `provision` succeeds. The resulting
access key is minimum-privilege:

  * S3: only the three buckets Terraform created, only the verbs the
    platform actually uses (no `s3:*`, no bucket-policy mutation).
  * ECR: only the auth + pull verbs, scoped to IBL's ECR repos.

The policy JSON is also written to the project workspace
(`<workspace>/runtime-iam-policy.json`) so the operator can pipe it
directly into the CLI:

    aws iam put-user-policy \\
        --user-name <name>-runtime \\
        --policy-name iblai-runtime \\
        --policy-document file://<workspace>/runtime-iam-policy.json
"""

from __future__ import annotations

import json
from pathlib import Path

from iblai_infra import ui
from iblai_infra.models import DeploymentType, InfraConfig

# IBL's image registry account / region — the ECR cross-account pull target.
# Centralized here so the rendered policy stays consistent with the actual
# `docker login` target hardcoded across the ansible roles.
IBLAI_ECR_ACCOUNT_ID = "765174860755"
IBLAI_ECR_REGION = "us-east-1"

# Tight S3 verbs the platform actually uses at runtime. Notably excludes
# bucket-policy / ACL mutations, lifecycle config, encryption config — all
# of which Terraform set up at provision time and the platform never
# revisits.
_S3_OBJECT_ACTIONS = [
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:GetObjectAcl",
    "s3:PutObjectAcl",
]
_S3_BUCKET_ACTIONS = [
    "s3:ListBucket",
    "s3:GetBucketLocation",
]
_ECR_AUTH_ACTIONS = ["ecr:GetAuthorizationToken"]
_ECR_PULL_ACTIONS = [
    "ecr:BatchCheckLayerAvailability",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer",
]

POLICY_FILENAME = "runtime-iam-policy.json"


def build_runtime_iam_policy(bucket_names: list[str]) -> dict:
    """Build the IAM policy JSON document for the runtime user.

    `bucket_names` must be the literal S3 bucket names Terraform created
    (the values of `s3_bucket_*` outputs). Returns a dict ready to
    `json.dumps()` — no formatting opinions baked in here.
    """
    if not bucket_names:
        raise ValueError("at least one S3 bucket name is required")

    bucket_arns = [f"arn:aws:s3:::{b}" for b in bucket_names]
    object_arns = [f"arn:aws:s3:::{b}/*" for b in bucket_names]
    ecr_repo_arn = (
        f"arn:aws:ecr:{IBLAI_ECR_REGION}:{IBLAI_ECR_ACCOUNT_ID}:repository/*"
    )

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PlatformBucketObjects",
                "Effect": "Allow",
                "Action": _S3_OBJECT_ACTIONS,
                "Resource": object_arns,
            },
            {
                "Sid": "PlatformBucketList",
                "Effect": "Allow",
                "Action": _S3_BUCKET_ACTIONS,
                "Resource": bucket_arns,
            },
            {
                "Sid": "ECRAuth",
                "Effect": "Allow",
                "Action": _ECR_AUTH_ACTIONS,
                "Resource": "*",
            },
            {
                "Sid": "ECRPullPlatformImages",
                "Effect": "Allow",
                "Action": _ECR_PULL_ACTIONS,
                "Resource": ecr_repo_arn,
            },
        ],
    }


def extract_bucket_names(outputs: dict) -> list[str]:
    """Pull bucket names out of a terraform outputs dict.

    Reads the three `s3_bucket_{backups,media,static}` outputs that the
    single-server template emits. Returns an empty list when none are
    present (e.g. call-server, which has no buckets).
    """
    keys = ("s3_bucket_backups", "s3_bucket_media", "s3_bucket_static")
    return [outputs[k] for k in keys if outputs.get(k)]


def render_runtime_access_instructions(
    config: InfraConfig,
    outputs: dict,
    ws: Path,
) -> None:
    """Print post-provision IAM-user setup instructions to the operator.

    Skips silently for `DeploymentType.CALL` (no S3 buckets and the call
    stack uses its own credentials flow). Writes the policy JSON to the
    workspace at `runtime-iam-policy.json` so the operator can pipe it
    into `aws iam put-user-policy --policy-document file://...`.
    """
    if config.deployment_type == DeploymentType.CALL:
        return

    bucket_names = extract_bucket_names(outputs)
    if not bucket_names:
        # No buckets in outputs — terraform template might not have run S3
        # creation, or the operator pointed at a deployment shape we don't
        # cover. Surface a soft note instead of printing a half-policy.
        ui.muted(
            "Skipping runtime IAM instructions: no S3 buckets in terraform "
            "outputs."
        )
        return

    policy = build_runtime_iam_policy(bucket_names)
    policy_path = ws / POLICY_FILENAME
    policy_path.write_text(json.dumps(policy, indent=2) + "\n")

    user_name = f"{config.project_name}-{config.environment.value}-runtime"

    ui.newline()
    ui.console.rule("[bold yellow]Next: create the runtime IAM user[/]")
    ui.console.print(
        "The platform server needs minimum-privilege AWS credentials baked\n"
        "into [highlight]/ibl/config.yml[/highlight] for [bold]ECR pulls[/bold] (IBL's image registry)\n"
        "and [bold]S3 access[/bold] to the three buckets Terraform just created.\n"
    )
    ui.console.print(
        "  [muted]The policy below has already been saved to:[/muted]\n"
        f"  [highlight]{policy_path}[/highlight]\n"
    )

    # Show the policy verbatim so the operator can sanity-check before
    # creating anything. Indented blob renders monospace via the IBL theme.
    ui.console.rule("[muted]runtime-iam-policy.json[/muted]")
    ui.console.print(json.dumps(policy, indent=2))
    ui.console.rule()
    ui.newline()

    ui.console.print("  [bold]One-time setup — copy/paste into your shell:[/]\n")
    ui.console.print(
        f"  [highlight]aws iam create-user --user-name {user_name}[/highlight]\n"
        f"  [highlight]aws iam put-user-policy \\\n"
        f"      --user-name {user_name} \\\n"
        f"      --policy-name iblai-runtime \\\n"
        f"      --policy-document file://{policy_path}[/highlight]\n"
        f"  [highlight]aws iam create-access-key --user-name {user_name}[/highlight]\n"
    )
    ui.console.print(
        "  Copy the [bold]AccessKeyId[/bold] + [bold]SecretAccessKey[/bold] from the last command into your\n"
        "  [highlight].env.setup[/highlight] as [highlight]AWS_ACCESS_KEY_ID[/highlight] and [highlight]AWS_SECRET_ACCESS_KEY[/highlight], then run:\n"
    )
    ui.console.print(
        f"  [brand]iblai infra setup-env {config.project_name} -f .env.setup[/brand]\n"
    )
    ui.muted(
        "  These runtime keys are minimum-privilege — safe to commit to a "
        "vault or password manager, but never to git."
    )
    ui.newline()
