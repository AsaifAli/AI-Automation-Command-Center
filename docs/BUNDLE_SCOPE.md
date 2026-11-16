# Bundle Scope and Application Instructions

This bundle is a **release-hardening execution pack**. It corrects the FlowPilot public README/readiness document and adds reusable pre-release, repository-hygiene, and non-destructive API acceptance scripts, plus implementation specifications and cross-project audit evidence.

It deliberately does not silently replace the FlowPilot API, repository, UI, worker, or deployment code with untested rewrites. The P0/P1 application changes in `PRODUCTION_GAP_REGISTER.md` require schema and service changes, tenant-bound access tests, failure-injection tests, and live infrastructure acceptance. They are specified in this bundle but are not falsely marked implemented.

## Apply docs/scripts overlay

From the FlowPilot repo root, extract the ZIP contents, then run:

```bash
python bundle/apply_bundle.py --repo /path/to/AI-Automation-Command-Center
```

If you extracted the ZIP directly into the repository root, use `--repo .`. The script backs up any destination files it replaces under `.bundle-backups/<UTC timestamp>/` before copying. It only installs README/docs/scripts; it does not change app source or deploy anything.

Then review:

```bash
git status --short
git diff --check
```

Run:

```bash
bash scripts/pre_release_check.sh
```

For a deployed non-destructive API check:

```bash
export FLOWPILOT_API_URL='https://<api-host>'
export FLOWPILOT_OPERATOR_KEY='<operator-key>'
python scripts/production_smoke.py
```

Do not pass API/provider secrets as command-line arguments or paste them into chat.
