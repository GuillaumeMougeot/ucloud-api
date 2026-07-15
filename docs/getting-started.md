# Getting started

This page gets you from nothing to an authenticated CLI in a few minutes.

## Prerequisites

- A UCloud account you can log into at <https://cloud.sdu.dk> (via WAYF).
- [uv](https://docs.astral.sh/uv/) installed.
- An `ssh` client on the machine you'll run jobs from (standard on Linux/macOS).

## Install

```bash
git clone https://github.com/GuillaumeMougeot/ucloud-api
cd ucloud-api
uv sync                 # create the venv and install dependencies
uv run ucloud --help
```

Prefer a standalone command on your `PATH`?

```bash
uv tool install .
ucloud --help           # now available everywhere
```

!!! tip
    The rest of the docs write `uv run ucloud …`. If you used `uv tool install`,
    just drop the `uv run` prefix.

## Authenticate

`ucloud-api` needs a **refresh token** — a long-lived string it exchanges for
short-lived access tokens. You grab it once from a browser (on any machine) and
hand it to the CLI. Full details and the security model are in
[Authentication](authentication.md); the short version:

1. Log in to <https://cloud.sdu.dk> in your browser.
2. Open DevTools → **Application** → **Cookies** → `https://cloud.sdu.dk`, and
   copy the value of the **`refreshToken`** cookie.
3. Give it to the CLI on your server:

   ```bash
   echo 'PASTE_THE_TOKEN_HERE' | uv run ucloud login
   ```

The token is verified before being saved to
`~/.config/ucloud-api/credentials.json` (mode `0600`).

Check it worked:

```bash
uv run ucloud whoami
# Authenticated against https://cloud.sdu.dk
```

## Register an SSH key (once)

So that SSH-enabled jobs will accept you:

```bash
uv run ucloud ssh-keys add ~/.ssh/id_ed25519.pub --title my-laptop
uv run ucloud ssh-keys list
```

(You can also do this in the GUI under *Resources → SSH Keys* — it's the same
underlying list.)

## Next

You're ready to launch something. Continue to
[Tutorial 1: your first GPU job](tutorials/01-first-gpu-job.md).
