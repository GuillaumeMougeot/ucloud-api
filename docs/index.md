# ucloud-api

Launch and control GPU jobs on **[UCloud](https://cloud.sdu.dk)** (the SDU
eScience Center platform in Denmark) from a terminal — no web GUI required.

UCloud's web app is just a single-page frontend over a documented REST API.
`ucloud-api` talks to that API directly, so you can start a job, wait for it to
run, and collect its output from a **headless server**, a script, or CI — plus
the things the API alone doesn't give you: a job queue with dependencies,
incremental code sync, and automatic time extension.

!!! note "Unofficial"
    Not affiliated with SDU eScience Center. It uses the same public API the web
    frontend uses. This is a different platform from the unrelated Chinese
    "UCloud" (`github.com/ucloud`).

## The idea in one picture

```
 laptop browser  ──(one time)──►  refresh token string  ──►  headless server
                                                              │
                       ucloud login / q submit / q logs   ◄──┘
```

You extract **one long-lived refresh token** from a browser session *once* (on
any laptop), copy that string to your server, and from then on everything is
API calls. No browser ever runs on the machine that launches jobs.

## Quick taste

A training job that syncs your code, builds its environment, runs, and
terminates itself:

```bash
uv run ucloud q submit train.toml --name base    # sync + setup + submit
uv run ucloud q submit eval.toml --after base    # Slurm's afterok
uv run ucloud q daemon                           # auto-extend, launch deps
uv run ucloud q logs base                        # watch the run, no SSH needed
```

## Where to go next

**Learning** (step by step):

| | |
| --- | --- |
| Install and authenticate | [Getting started](getting-started.md) |
| Run your first GPU job (~5 min) | [Tutorial 1](tutorials/01-first-gpu-job.md) |
| A training run that manages itself | [Tutorial 2](tutorials/02-training-run.md) |
| Drive it from Python | [Tutorial 3](tutorials/03-python-library.md) |

**Guides** (task-oriented):

| | |
| --- | --- |
| Write or generate a job spec | [Job specs](guides/job-specs.md) |
| Queue, dependencies, auto-extend, code sync | [Queue & batch workflows](guides/queue-and-batch.md) |
| Browse, upload, download, mount files | [Files and storage](guides/files-and-storage.md) |
| The token model, rotation, security | [Authentication](guides/authentication.md) |
| Env vars, config files, precedence | [Configuration](guides/configuration.md) |

**Reference & background:**

| | |
| --- | --- |
| Look up a command | [CLI reference](cli-reference.md) |
| What it does under the hood | [How it works](how-it-works.md) |
| Fix an error | [Troubleshooting](troubleshooting.md) |
| Hack on it | [Contributing](contributing.md) |
