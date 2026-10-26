"""Make a root's enabled service toggles exactly a given set.

`ibl services set-from <preset>` can't express a node of a split topology
(app-scalable also drops the notification worker and Grafana, and turns AI
calls on), so a node's toggles are set one by one from its own list.

    python3 ibl_services_exact.py ON_KEYS [KEEP_KEYS]

Both arguments are comma-separated. KEEP_KEYS are left as they are (e.g.
IBL_DM.RUN_AI_CALL, which `iblai infra call connect` owns). Prints one JSON
line: {"enabled": [...], "disabled": [...], "on": [...]}.
"""
import json
import subprocess
import sys


def ibl(*args):
    p = subprocess.run(["ibl", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        sys.exit(f"ibl {' '.join(args)} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def current():
    out = ibl("services", "list", "--all", "--json")
    # Notices can precede the JSON document on stdout.
    return json.loads(out[out.index("{"):])


def main():
    want = {k for k in sys.argv[1].split(",") if k}
    keep = {k for k in (sys.argv[2] if len(sys.argv) > 2 else "").split(",") if k}
    state = current()
    unknown = sorted((want | keep) - set(state))
    if unknown:
        sys.exit(f"unknown service keys: {unknown}")
    disabled = sorted(k for k, v in state.items() if v.get("value") and k not in want and k not in keep)
    enabled = sorted(k for k in want if not state[k].get("value"))
    for k in disabled:
        ibl("services", "disable", k)
    for k in enabled:
        ibl("services", "enable", k)
    on = sorted(k for k, v in current().items() if v.get("value"))
    print(json.dumps({"enabled": enabled, "disabled": disabled, "on": on}))


if __name__ == "__main__":
    main()
