# ucloud-api

Launch and control GPU jobs on **[UCloud](https://cloud.sdu.dk)** (the SDU
eScience Center platform in Denmark) from a terminal — no web GUI required.

UCloud's web app is just a single-page frontend over a fully documented REST
API. This project talks to that API directly, so you can start a job, wait for
it to run, and `ssh` into it from a headless server, a script, or CI.

> ⚠️ **Unofficial.** Not affiliated with SDU eScience Center. It uses the same
> public API the web frontend uses. This is a different platform from the
> unrelated Chinese "UCloud" (`github.com/ucloud`).

📖 **Documentation:** browse [`docs/`](docs/index.md) on GitHub, or the hosted
site at <https://guillaumemougeot.github.io/ucloud-api/> (once GitHub Pages is
enabled — see [below](#documentation-site)).

---

## Why this exists

UCloud only exposes a GUI for starting jobs. That is painful when you want to
automate runs or drive a GPU box from a machine that has no desktop/browser.
The good news: **you never need a browser on the machine that runs jobs.** You
extract one long-lived refresh token from a browser *once* (on any laptop), copy
that string to your server, and from then on everything is API + SSH.

```
 laptop browser  ──(one time)──►  refresh token string  ──►  headless server
                                                              │
                     ucloud login / jobs create / jobs ssh  ◄─┘
```

## Install

Uses [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/GuillaumeMougeot/ucloud-api
cd ucloud-api
uv sync            # create the venv and install deps
uv run ucloud --help
```

Or install the CLI as a standalone tool:

```bash
uv tool install .
ucloud --help
```

## Getting your refresh token (the one browser step)

Do this once, on any machine that has a browser — your laptop is fine. The
resulting token is just a string; it is **not** tied to that machine.

1. Log in to <https://cloud.sdu.dk> in your browser (via WAYF as usual).
2. Open DevTools → **Application** → **Cookies** → `https://cloud.sdu.dk`.
3. Copy the value of the **`refreshToken`** cookie.
   - Alternatively: DevTools → **Network**, find the `POST /auth/refresh/web`
     request, and copy the `refreshToken` from its request cookies.

Then hand it to the CLI on your server (piping keeps it out of your shell
history):

```bash
echo 'PASTE_THE_TOKEN_HERE' | uv run ucloud login
# or interactively (hidden input):
uv run ucloud login
# or non-interactively via env var:
export UCLOUD_REFRESH_TOKEN='...'
```

`ucloud login` verifies the token by minting an access token before saving it to
`~/.config/ucloud-api/credentials.json` (mode `0600`).

> Refresh tokens expire eventually. When `ucloud whoami` starts failing, repeat
> the browser step to grab a fresh one.

## Usage

### Register an SSH key (once)

So SSH-enabled jobs will accept you:

```bash
uv run ucloud ssh-keys add ~/.ssh/id_ed25519.pub --title my-laptop
uv run ucloud ssh-keys list
```

(You can also do this in the GUI under *Resources → SSH Keys* — same thing.)

### Find the app + product you want (no DevTools needed)

```bash
uv run ucloud apps search pytorch          # -> name/version, e.g. pytorch-te 26.05
uv run ucloud products                     # -> id/category/provider + cpu/mem/gpu
uv run ucloud products --provider aau       # filter to one provider
```

Already run a similar job in the GUI? Export it straight to a spec file:

```bash
uv run ucloud jobs show 5466088 -o my-job.toml   # seed from an existing job
# then edit the app name/version and product, e.g. to pytorch-te + a GPU product
```

### Start a job and connect

1. Copy `examples/pytorch.toml` (or a `jobs show` export) and fill in the real
   `application.version` and `product` values from the commands above.
2. Create the job, wait for it, and get the SSH command:

```bash
uv run ucloud jobs create my-job.toml --wait
# ...
# Job xxxx is RUNNING.
# Connect with: ssh ucloud@ssh.cloud.sdu.dk -p 3421
```

3. Run commands on it:

```bash
uv run ucloud jobs ssh xxxx                      # interactive shell
uv run ucloud jobs ssh xxxx -c "nvidia-smi"      # one-off command
```

### Manage jobs

```bash
uv run ucloud jobs list
uv run ucloud jobs status xxxx
uv run ucloud jobs terminate xxxx
```

## Use as a library

```python
from ucloud_api import (
    UCloudClient, Jobs, JobSpecification, NameAndVersion, ComputeProduct, params,
)

with UCloudClient() as client:
    jobs = Jobs(client)
    job_id = jobs.create(JobSpecification(
        application=NameAndVersion(name="pytorch-te", version="2.3.0"),
        product=ComputeProduct(id="u1-gpu-1", category="u1-gpu", provider="ucloud"),
        ssh_enabled=True,
        parameters={"workingDirectory": params.directory("/1234567/project")},
    ))
    jobs.wait_until_running(job_id)
    print(jobs.ssh_endpoint(job_id).command)
```

## How it works

| Step        | UCloud endpoint                                            |
| ----------- | --------------------------------------------------------- |
| Auth        | `POST /auth/refresh` with `Authorization: Bearer <token>` → `{accessToken}` (a short-lived JWT, cached until its `exp`) |
| Create job  | `POST /api/jobs` with `{items: [JobSpecification]}`        |
| Wait        | poll `GET /api/jobs/retrieve` until `status.state == RUNNING` |
| SSH         | read the `ssh ... -p <port>` line from the job's updates   |
| Terminate   | `POST /api/jobs/terminate`                                 |
| SSH keys    | `POST /api/ssh`                                            |
| App search  | `POST /api/hpc/apps/search`                                |
| Products    | `GET /api/jobs/retrieveProducts`                           |

## Status & caveats

- The auth flow, job payload, and endpoints were derived from the open-source
  UCloud frontend and the official docs. Exact `product`/`application.version`
  values are deployment-specific — grab them from the GUI's create-job network
  request (see `examples/pytorch.toml`).
- If `POST /auth/refresh` ever rejects a user refresh token on your deployment,
  open an issue: the fallback is the browser flow `POST /auth/refresh/web` with
  the cookie + `X-CSRFToken`, which we can add.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy
pre-commit install     # optional
```

## Documentation site

Docs live as Markdown in [`docs/`](docs/index.md) and build into a website with
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/):

```bash
uv run --group docs mkdocs serve     # live preview at http://127.0.0.1:8000
uv run --group docs mkdocs build     # static site into ./site
```

A GitHub Actions workflow (`.github/workflows/docs.yml`) publishes the site to
GitHub Pages on every push to `main`. **One-time setup:** in the repo, go to
**Settings → Pages → Build and deployment → Source** and select
**GitHub Actions**. After that it deploys automatically to
`https://guillaumemougeot.github.io/ucloud-api/`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
