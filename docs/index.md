# ucloud-api

Launch and control GPU jobs on **[UCloud](https://cloud.sdu.dk)** (the SDU
eScience Center platform in Denmark) from a terminal — no web GUI required.

UCloud's web app is just a single-page frontend over a documented REST API.
`ucloud-api` talks to that API directly, so you can start a job, wait for it to
run, and `ssh` into it from a **headless server**, a script, or CI.

!!! note "Unofficial"
    Not affiliated with SDU eScience Center. It uses the same public API the web
    frontend uses. This is a different platform from the unrelated Chinese
    "UCloud" (`github.com/ucloud`).

## The idea in one picture

```
 laptop browser  ──(one time)──►  refresh token string  ──►  headless server
                                                              │
                     ucloud login / jobs create / jobs ssh  ◄─┘
```

You extract **one long-lived refresh token** from a browser session *once* (on
any laptop), copy that string to your server, and from then on everything is API
+ SSH. No browser ever runs on the machine that launches jobs.

## Where to go next

| I want to… | Read |
| --- | --- |
| Install it and authenticate | [Getting started](getting-started.md) |
| Understand the token / headless login | [Authentication](authentication.md) |
| Launch my first GPU job end to end | [Tutorial 1: your first GPU job](tutorials/01-first-gpu-job.md) |
| Re-run a job I already started in the GUI | [Tutorial 2: from an existing job](tutorials/02-from-existing-job.md) |
| Automate jobs from Python | [Tutorial 3: the Python library](tutorials/03-python-library.md) |
| Look up a command | [CLI reference](cli-reference.md) |
| Configure tokens / env vars | [Configuration](configuration.md) |
| Understand what it does under the hood | [How it works](how-it-works.md) |
| Fix an error | [Troubleshooting](troubleshooting.md) |

## Quick taste

```bash
uv run ucloud apps search pytorch      # find the app name + version
uv run ucloud products --provider aau  # find a GPU product
uv run ucloud jobs create my-job.toml --wait
uv run ucloud jobs ssh <id> -c "nvidia-smi"
```
