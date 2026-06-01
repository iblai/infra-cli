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

Ansible-touching features (SMTP, Stripe, SSO) will call a future
``AnsibleRunner.run_partial(tags=[...])`` helper that runs ``ansible-playbook
--tags <role>`` against the existing inventory — runs only the role(s)
relevant to the feature, leaving other roles untouched. That helper is not
delivered yet.

To add a new feature subgroup:

1. Create ``src/iblai_infra/features/<feature>.py`` with a
   ``<feature>_app = typer.Typer(name="<feature>", ...)`` instance and the
   four commands above.
2. In ``cli.py`` add ``infra_app.add_typer(<feature>_app, name="<feature>")``
   next to the other ``add_typer`` calls.
3. Add ``tests/features/test_<feature>.py`` mirroring the WAF test layout.

Currently registered subgroups:
    - ``waf`` — see :mod:`iblai_infra.features.waf`
"""

from __future__ import annotations
