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

A job is described by a TOML file that mirrors UCloud's `JobSpecification`.
Field names may be `snake_case` (as below) or the API's `camelCase` — both load.

```toml
# Top-level scalars MUST come before any [table] (TOML rule).
name = "pytorch-run"       # optional job name
replicas = 1               # default 1
ssh_enabled = true         # enable SSH access
opened_file = "/123/notebook.ipynb"   # optional

[application]
name = "pytorch-te"
version = "26.05"

[product]
id = "uc-a100-1-h"
category = "uc-a100-h"
provider = "aau"

[time_allocation]
hours = 4
minutes = 0
seconds = 0

# Application parameters: each is a tagged value.
[parameters.someText]
type = "text"
value = "hello"

[parameters.workingDirectory]
type = "file"
path = "/1234567/project"
read_only = false

# Extra resources (public links, networks, ...) as a list:
# [[resources]]
# type = "ingress"
# id = "my-public-link-id"
```

### Parameter types

| `type` | Fields | Meaning |
| --- | --- | --- |
| `text` | `value` | a string |
| `textarea` | `value` | a multi-line string |
| `boolean` | `value` | true/false |
| `integer` | `value` | whole number |
| `floating_point` | `value` | decimal number |
| `file` | `path`, `read_only` | a file or directory from your UCloud drive |
| `peer` | `hostname`, `job_id` | link to another running job |
| `ingress` | `id` | a public link |
| `network` | `id` | a network |
| `block_storage` | `id` | a block-storage volume |
| `license_server` | `id` | a license |

Don't know which parameters an app needs? Export a previous run with
[`ucloud jobs show`](cli-reference.md#ucloud-jobs-show-id).
