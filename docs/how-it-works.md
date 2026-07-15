# How it works

`ucloud-api` is a thin client over UCloud's public HTTP API — the same API the
web frontend uses. Nothing is scraped or automated through a browser.

## Architecture

```
          ┌──────────────┐
          │  CLI (typer) │   ucloud login / jobs / ssh-keys / apps / products
          └──────┬───────┘
                 │
      ┌──────────▼───────────┐
      │  Jobs / Catalog /    │   high-level operations
      │  SSHKeys / SSHRunner │
      └──────────┬───────────┘
                 │
        ┌────────▼─────────┐
        │  UCloudClient    │   authenticated httpx, retries on 401
        └────────┬─────────┘
                 │
        ┌────────▼─────────┐
        │  Authenticator   │   refresh token -> cached access tokens (JWT exp)
        └──────────────────┘
```

## Modules

| Module | Role |
| --- | --- |
| `auth.py` | Exchange a refresh token for access tokens; cache until JWT `exp`. |
| `client.py` | Authenticated `httpx` wrapper; injects the bearer token, retries once on `401`. |
| `jobs.py` | Create / retrieve / wait / terminate jobs; parse the SSH endpoint; export a job to a spec. |
| `catalog.py` | Search apps; list app parameters; list compute products. |
| `files.py` | Browse drives and folders; stat/mkdir/trash. |
| `transfer.py` | Upload (WEBSOCKET_V2) and download (GET), many files concurrently. |
| `models.py` | Typed `JobSpecification` and the `AppParameterValue` union (serialize to camelCase). |
| `params.py` | Ergonomic factories for parameter values. |
| `ssh.py` | Run commands / open a shell on a job via the system `ssh`. |
| `cli.py` | The `ucloud` command surface. |

## Endpoint map

| Step | UCloud endpoint |
| --- | --- |
| Auth | `POST /auth/refresh` with `Authorization: Bearer <refreshToken>` → `{accessToken}` |
| Create job | `POST /api/jobs` with `{items: [JobSpecification]}` |
| Wait / status | `GET /api/jobs/retrieve` (poll until `status.state == RUNNING`) |
| List jobs | `GET /api/jobs/browse` |
| Terminate | `POST /api/jobs/terminate` |
| SSH endpoint | parsed from the `ssh … -p <port>` line in the job's updates |
| App search | `POST /api/hpc/apps/search` |
| App parameters | `GET /api/hpc/apps/byNameAndVersion` |
| Products | `GET /api/jobs/retrieveProducts` |
| SSH keys | `POST /api/ssh` (create), `GET /api/ssh/browse` (list) |
| Drives | `GET /api/files/collections/browse` |
| Files | `GET /api/files/browse?path=…` (retrieve/upload/download/folder use `id`) |
| Upload | `POST /api/files/upload` → `WEBSOCKET_V2` framed stream |
| Download | `POST /api/files/download` → HTTPS `GET` |
| Projects | `GET /api/projects/v2/browse`; project context via the `Project` header |

## The access-token lifecycle

1. First API call → no valid cached token → `POST /auth/refresh`.
2. The response's access token is a JWT; its `exp` claim is decoded (without
   signature verification — we only read the expiry) and cached to disk.
3. Subsequent calls reuse the cached token until shortly before `exp`.
4. If a token expires mid-flight and a call returns `401`, the client refreshes
   once and retries.

## Why SSH instead of the web terminal

SSH-enabled jobs advertise a real `ssh ucloud@ssh.cloud.sdu.dk -p <port>`
endpoint. Driving that with the system `ssh` client is robust and gives you
scp/rsync, agent forwarding, and non-interactive command execution for free —
far simpler and sturdier than reverse-engineering the browser terminal or a
Jupyter server.
