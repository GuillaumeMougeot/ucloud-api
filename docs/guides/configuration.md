# Configuration

## Credential resolution order

When it needs credentials, `ucloud-api` looks in this order (first match wins):

1. Explicit arguments in code (`UCloudClient(credentials=…)`).
2. Environment variables `UCLOUD_REFRESH_TOKEN` / `UCLOUD_BASE_URL`.
3. The on-disk credentials file (written by `ucloud login`).

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `UCLOUD_REFRESH_TOKEN` | Your refresh token | — |
| `UCLOUD_BASE_URL` | Deployment URL | `https://cloud.sdu.dk` |
| `UCLOUD_PROJECT` | Active project id (sent as the `Project` header) | — |
| `UCLOUD_CONFIG_DIR` | Where credentials/cache live | OS config dir |

## Projects

Most drives and GPU allocations belong to a **project**. Set your active project
so requests carry the `Project` header:

```bash
uv run ucloud projects                        # list ids + titles
uv run ucloud login --project <PROJECT_ID>    # persist it
# or, per-shell:
export UCLOUD_PROJECT=<PROJECT_ID>
```

Without it, requests target your personal workspace and project drives return
`403 "Write permission is required"`.

## The `.env` file

The **CLI** auto-loads a `.env` file from the current directory (searching
upward). Copy the template and fill it in:

```bash
cp .env.example .env
```

```dotenv
UCLOUD_BASE_URL=https://cloud.sdu.dk
UCLOUD_REFRESH_TOKEN=your-token-here
```

`.env` is git-ignored. Real environment variables still take precedence over it.

!!! warning "Library users"
    The Python **library** does not auto-load `.env`. Call
    `dotenv.load_dotenv()` yourself, or set the environment variables, before
    constructing `UCloudClient()`.

## Files on disk

| File | Contents | Permissions |
| --- | --- | --- |
| `~/.config/ucloud-api/credentials.json` | refresh token + base URL | `0600` |
| `~/.config/ucloud-api/token_cache.json` | cached access token + expiry | `0600` |

Both respect `UCLOUD_CONFIG_DIR`. Delete them to fully "log out".

## Job spec files

Moved: the spec format has its own page — [Job specs](job-specs.md).
