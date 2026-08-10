"""Post-provision "optional feature" sub-commands.

Each optional feature on an ibl.ai stack (WAF, SMTP, Stripe, SSO providers,
…) has a dedicated ``iblai infra <feature>`` Typer subgroup defined in its
own module here. The subgroups are registered against ``infra_app`` in
``cli.py``.

Common shape per feature:

    iblai infra <feature> enable      [<name>]            # interactive
    iblai infra <feature> enable-env  [<name>] -f .env    # non-interactive
    iblai infra <feature> disable     <name>  [--yes]
    iblai infra <feature> status      [<name>]

Terraform-touching features (WAF today; future managed-services on
multi-server) call :meth:`TerraformRunner.reapply`, which re-emits
``terraform.tfvars`` from the mutated ``state.config``, re-copies templates,
and runs ``init`` → ``plan`` → ``apply`` on the existing workspace with the
original ``bucket_suffix`` pinned.

Ansible-touching features (SMTP, Stripe, SSO) call
:meth:`AnsibleRunner.run_partial`, which runs ``ansible-playbook --tags
<role>`` against the existing inventory — only the role(s) relevant to the
feature, leaving the rest untouched. The tagged roles are individually gated
and idempotent, so re-applying one is safe.

Two things these features have to get right:

* **Credentials.** The tagged roles only write config and talk to local
  containers; none of them read the GitHub token or the AWS keys. Build the
  config with :meth:`SetupConfig.for_feature`, which recovers host, SSH key
  and domain from the project state and leaves the credential fields empty,
  rather than making an operator re-enter four secrets to turn on email.

* **Applying the change.** During a full setup the services start *after*
  these roles, so they read the new values on first boot. Added later, the
  containers are already running with the old environment and have to be
  recreated. Roles whose values are read at boot gate a restart task on
  ``restart_services``; the command asks before setting it, since on a live
  environment that means a brief outage.

To add a new feature subgroup:

1. Create ``src/iblai_infra/features/<feature>.py`` with a
   ``<feature>_app = typer.Typer(name="<feature>", ...)`` instance and the
   four commands above.
2. In ``cli.py`` add ``infra_app.add_typer(<feature>_app, name="<feature>")``
   next to the other ``add_typer`` calls.
3. Add ``tests/features/test_<feature>.py`` mirroring the WAF test layout.

Currently registered subgroups:
    - ``waf`` — see :mod:`iblai_infra.features.waf` (Terraform)
    - ``smtp`` — see :mod:`iblai_infra.features.smtp` (Ansible)
"""

from __future__ import annotations
