# Portable runtime

The runtime needs Python 3.10+. Core execution and HTML/Markdown exports use the standard library. Styled PDF exports use ReportLab, pinned in `requirements.txt`. The container installs this dependency; no machine-specific packages, Node modules, provider credentials, or author's computer paths are bundled. Node and jsdom are used only for frontend development tests.

## Run locally from a clean checkout

```sh
python3 -m pip install -r requirements.txt
python3 app.py --execution-limit 4
```

Without PDF export, `python3 -S app.py --execution-limit 4` still runs the core with site packages disabled. Local mode binds to loopback and
retains local project discovery and optional official provider CLI sign-in.
Provider CLIs are independently installed optional integrations, not bundled app
dependencies. Credentials stay with those tools.

## Run a portable container

Copy `.env.example` to `.env`, fill in a private password of at least 16 characters,
and choose the execution limit. Then run:

```sh
docker compose up --build -d
```

Open `http://127.0.0.1:8765`. The username is `workspace`; the password is your
`ACCESS_PASSWORD`. No example password is supplied. Docker/Compose is required
on the machine running this command; Python is inside the container.

The named `workspace-data` volume holds SQLite state, artifacts, and the private
per-process API token. The image excludes that data and all credentials. Keep
backups of the volume if the workspace matters. Removing the volume deletes its
contents; rebuilding the image does not.

## Optional hosted operation

This repository can be uploaded without deploying it. To deploy later, use the
Dockerfile behind an HTTPS reverse proxy with:

- `APP_MODE=hosted` and `HOST=0.0.0.0` (container defaults).
- `PUBLIC_ORIGIN` set to the exact public HTTPS origin, with no path.
- A private `ACCESS_PASSWORD` of at least 16 characters.
- An explicit positive `EXECUTION_LIMIT`.
- `PORT` set to the platform's listening port.
- `DATA_DIR` pointing to a writable persistent volume (container default `/data`).

Hosted mode is a password-protected, single shared workspace. The HTML containing
the session credential and static assets require authentication; API mutations
also require the per-process bearer token and the expected origin. `/healthz`
returns only a health status and is public for health probes. Do not place an
HTTP cache in front of authenticated responses.

Local filesystem scanning and Terminal/provider sign-in are disabled in hosted
mode. A remote server cannot discover projects or launch provider tools on a
visitor's computer. The UI omits those actions in this mode. Core project, team,
task, appearance, and API-based model execution remain available. Configure API
keys through Model connections or server environment variables; local CLI
subscription sessions are not used for API execution.

Multiple replicas must not share one SQLite volume. The runtime deliberately
refuses to start a second engine against a locked data directory.

## What belongs in GitHub

Source, CSS, examples, tests, Dockerfile, and configuration templates belong in
the repository. `.runtime/`, `node_modules/`, `.env`, database files, local
provider binaries, and account credentials are excluded. Installing a provider
CLI on a development machine does not add it to this repository or image.
