# FlowPilot lint correction

This patch resolves the six Ruff findings shown by the current local and Docker test runs. It changes only three files:

- `app/api/main.py`: keeps the intentional exception boundary around the in-process background task and documents the narrowly scoped BLE001 suppression. `execute_run` already records the failed run before re-raising; the background-task wrapper must prevent a post-response exception from surfacing as a failed request.
- `app/integrations/llm.py`: uses Python 3.11+'s `datetime.UTC` alias for parsing and comparing `Retry-After` dates.
- `ui/api_resilience.py`: imports `Callable` from `collections.abc` and uses `datetime.UTC`.

No application behaviour, retry limits, BYOK handling, provider credentials, Render settings, or tests are changed by this lint-only patch.

## Apply

From the FlowPilot repository root, extract this ZIP to a temporary directory and overlay its contents:

```bash
unzip -o ~/Downloads/flowpilot-lint-fix.zip -d /tmp/flowpilot-lint-fix
rsync -av /tmp/flowpilot-lint-fix/flowpilot-lint-fix/ ./
```

Then run the complete validation sequence:

```bash
ruff check app ui tests scripts
python -m compileall -q app ui tests scripts
pytest -q

docker build --no-cache --target test -t flowpilot-test .
docker run --rm flowpilot-test
```

Run `docker run` only after the build succeeds. The previous `pull access denied` message occurred because the image was never created after the Docker build's Ruff step failed.
