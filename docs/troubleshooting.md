# Troubleshooting

## "No UCloud refresh token found"

The CLI can't find credentials. Either run `ucloud login`, set
`UCLOUD_REFRESH_TOKEN`, or create a `.env` file. See
[Configuration](guides/configuration.md).

## "UCloud rejected the refresh token (expired or invalid)"

Your refresh token has expired or is wrong. Repeat the browser step in
[Authentication](guides/authentication.md) and run `ucloud login` with a fresh token.

## `whoami` works, but a job command returns an API error

- **Invalid job specification** — a field in your TOML is wrong. Check the
  `application`, `product`, and any `parameters`. The easiest fix is to export a
  known-good job with `ucloud jobs show <id>` and diff against yours.
- **Missing required parameter** — the app needs a parameter you didn't provide.
  Export a previous run of *that same app* to see the exact parameter names.
- **Product not available** — confirm the `id`/`category`/`provider` against
  `ucloud products`. Provider matters (e.g. `aau` vs `ucloud`).

## The job never reaches RUNNING

- `JobTimeoutError` — it's still queued after the timeout. GPU nodes can be
  busy; raise `--timeout`, or use `--no-wait` and poll with `ucloud jobs status`.
- `JobFailedError` — it entered a terminal state (e.g. `FAILURE`, `EXPIRED`)
  before running. Check the job in the GUI for the failure reason.

## "This application does not support SSH but it is required"

The spec sets `ssh_enabled = true` on an app that has no SSH support at all
(many batch apps, e.g. `pytorch-te`). Remove the line; `ucloud apps show
<name> <version>` tells you whether an app supports SSH. To watch a run without
SSH, make it a batch job and use `jobs logs` / `q logs`.

## "No SSH endpoint for this job"

- Make sure the spec has `ssh_enabled = true`.
- Make sure you've registered your key: `ucloud ssh-keys add ~/.ssh/id_*.pub`.
- The endpoint is advertised shortly after the job starts — give it a moment and
  retry, or re-run `ucloud jobs create … --wait` which polls for you.
- Only some apps support SSH (Terminal, Coder, RStudio, Ubuntu/AlmaLinux Xfce,
  Rsync, and similar). A pure batch app may not expose it.

## `ssh` fails to connect

- Confirm the private key matching your registered public key is available
  (`-i ~/.ssh/id_ed25519`, or loaded in your SSH agent).
- Host keys change every launch; `ucloud-api` uses
  `StrictHostKeyChecking=accept-new`, which is expected for ephemeral jobs.

## "The `ssh` command was not found"

Install an SSH client (`openssh-client` on Debian/Ubuntu). `ucloud-api` shells
out to the system `ssh`.

## Still stuck?

Open an issue with the command you ran and the full error:
<https://github.com/GuillaumeMougeot/ucloud-api/issues>
