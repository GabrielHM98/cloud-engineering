# Shared pipeline contract

This app participates in an `app-releases/apps/<name>/` monorepo. `app.yaml` is the app-specific input to a root reusable workflow. The app folder contains no standalone GitHub workflow, registry credentials, or cluster credentials.

## Inputs

- Build context: this app directory.
- Dockerfile: `Dockerfile`, with targets `web`, `catalog`, `predictor`.
- Runtime asset: GitHub Release tag/asset/checksums from committed `runtime/lock.json`.
- `{repository}` substitution: the actual GitHub `OWNER/REPO` containing this app, supplied by the shared workflow.
- `{url:web}` substitution: the host-accessible URL of the web container started by the integration runner.

Hooks are argument arrays, not shell command strings. Execute each command with this folder as its current working directory. Preparation downloads/verifies the approved runtime and installs the app's host test dependencies. Checks run the Python test suite. Integration checks execute the existing smoke test against the running web container.

## Container integration

Start all components on one isolated Docker network, with aliases exactly `web`, `catalog`, and `predictor`. All listen on container port 8080. Set the web environment variables from `app.yaml`, including `http://catalog:8080` and `http://predictor:8080`. Poll each `/readyz` endpoint before calling the integration hook. Allow model/catalog startup time; the existing Compose file supplies an alternative local orchestration path.

Images declare non-root users and support a read-only root filesystem with writable `/tmp`. Their embedded model/catalog is read-only. The manifest declares CPU/memory limits for the shared integration runner. The release runner must clean up only the containers/network it owns.

## Release outputs

Publish tested images as:

```text
<registry-prefix>/chemical-functions-web@sha256:...
<registry-prefix>/chemical-functions-catalog@sha256:...
<registry-prefix>/chemical-functions-predictor@sha256:...
```

Record the source commit, runtime lock, and all three registry digests. Treat them as one tested release set when preparing a promotion PR in `paas-releases`. The folder's `app.yaml` does not itself implement workflow discovery, publishing, or cross-repo promotion; those belong in the root shared pipeline.

Docker sets `PYTHONPATH=/app/engine` for the preserved engine module imports. Host tests use `python scripts/run_tests.py`, which adds the same engine import path. Model-specific dependency pins stay beside the frozen benchmark; HTTP/dev entry points live in `requirements/`.
