# Tutorial 1: your first GPU job

Goal: start a PyTorch GPU job from the terminal, wait for it to run, and execute
`nvidia-smi` on it over SSH — without opening the web GUI.

This assumes you've finished [Getting started](../getting-started.md) (installed,
`ucloud login` works, and an SSH key is registered).

## Step 1 — find the application

```bash
uv run ucloud apps search pytorch
```

```
┏━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Name       ┃ Version ┃ Title   ┃
┡━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ pytorch-te │ 26.05   │ PyTorch │
└────────────┴─────────┴─────────┘
```

Note the **Name** and **Version** — here `pytorch-te` and `26.05`.

## Step 2 — find a GPU product

```bash
uv run ucloud products
```

You'll see rows like:

```
┃ Provider   ┃ ID                    ┃ Category        ┃ vCPU ┃ Mem (GB) ┃ GPU ┃
│ aau        │ uc-a100-1-h           │ uc-a100-h       │ …    │ …        │ 1   │
│ ucloud     │ gpu-nvidia-b200-1-gpu │ gpu-nvidia-b200 │ 48   │ 288      │ 1   │
```

Pick one and note its **ID**, **Category**, and **Provider**. Filter to one
provider with `--provider aau` if the list is long.

## Step 3 — write a spec file

Create `pytorch.toml`. In TOML, scalar keys must come before any `[table]`:

```toml
name = "pytorch-run"
replicas = 1
ssh_enabled = true          # required so you can SSH in

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
```

!!! info "Application parameters"
    Some apps require extra parameters. If `create` complains that a parameter is
    missing, see [Tutorial 2](02-from-existing-job.md) — exporting a previous run
    shows you the exact parameter names and how to encode them.

## Step 4 — create the job and wait

```bash
uv run ucloud jobs create pytorch.toml --wait
```

```
Submitted job 5470001
state -> IN_QUEUE
state -> RUNNING
Job 5470001 is RUNNING.
Connect with: ssh ucloud@ssh.cloud.sdu.dk -p 3421
```

`--wait` polls until the job is `RUNNING` (or fails / times out). Use
`--timeout <seconds>` to change how long it waits, or `--no-wait` to return
immediately.

## Step 5 — run commands on it

```bash
uv run ucloud jobs ssh 5470001 -c "nvidia-smi"     # one-off command
uv run ucloud jobs ssh 5470001                      # interactive shell
```

If you need a specific key: `-i ~/.ssh/id_ed25519`.

## Step 6 — clean up

```bash
uv run ucloud jobs status 5470001
uv run ucloud jobs terminate 5470001
```

## What you learned

- `apps search` and `products` replace hunting for magic strings in the GUI.
- A job is fully described by a small TOML spec.
- `jobs create --wait` + `jobs ssh` gives you a hands-off launch-and-connect loop
  you can put in a script.

Next: [re-run an existing job](02-from-existing-job.md) to skip writing specs by
hand.
