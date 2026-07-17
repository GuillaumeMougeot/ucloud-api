# Tutorial 1: your first GPU job

Goal: run `nvidia-smi` on a UCloud GPU from your terminal and read its output
back — proving your token, project, product, and app all work. About five
minutes, and the job cleans up after itself.

This assumes you've finished [Getting started](../getting-started.md)
(installed, `ucloud login` works, project set).

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

Note the **Name** and **Version**. Your deployment may show different versions —
use what it prints, not what this page prints.

## Step 2 — find a GPU product you have quota for

```bash
uv run ucloud products
```

`products` only lists machines your workspace can actually pay for
(`ucloud quota` shows the allowances). You'll see rows like:

```
┃ Provider ┃ ID                       ┃ Category        ┃ vCPU ┃ Mem (GB) ┃ GPU ┃
│ ucloud   │ gpu-nvidia-b200-1-gpu    │ gpu-nvidia-b200 │ 48   │ 288      │ 1   │
│ ucloud   │ gpu-nvidia-b200-1-mig.1g │ gpu-nvidia-b200 │ 6    │ 36       │ 1   │
```

Pick the **smallest GPU** on offer for this hello-world — here the `mig.1g`
slice (a hardware partition of a B200). Note its **ID**, **Category**, and
**Provider**.

## Step 3 — find your drive

The job will write its output to a folder on your UCloud drive, so you need the
drive's id:

```bash
uv run ucloud files drives
```

```
┃ Path      ┃ Title                               ┃ Provider ┃
│ /12347837 │ Member Files: GuillaumeMougeot#5298 │ ucloud   │
```

Use your own path (the `/12347837` below) in the next step.

## Step 4 — write the spec

Make an empty folder and put `hello.toml` in it:

```bash
mkdir hello-ucloud && cd hello-ucloud
```

```toml
name = "hello-gpu"
replicas = 1

[application]
name = "pytorch-te"
version = "26.05"

[product]
id = "gpu-nvidia-b200-1-mig.1g"
category = "gpu-nvidia-b200"
provider = "ucloud"

[time_allocation]
hours = 1                 # an upper bound — the job ends itself much sooner

[sync]
local = "."               # this folder, pushed to your drive and mounted
remote = "/12347837/repos/hello-ucloud"

[setup]
run = "nvidia-smi"        # batch mode: the job TERMINATES when this exits
```

Two things make this a **batch job**: `[sync]` puts a folder from your machine
on the drive and mounts it into the job, and `[setup] run` is a command whose
exit *ends the job*. No idle GPU if you walk away, nothing to remember to
terminate.

!!! note "Why not SSH in and type `nvidia-smi` myself?"
    Not every app allows SSH — `pytorch-te` doesn't (`ucloud apps show
    pytorch-te 26.05` tells you). Batch mode works everywhere, and for real
    training runs it's what you want anyway.

## Step 5 — create it

```bash
uv run ucloud jobs create hello.toml --wait
```

```
synced 1 file(s) to /12347837/repos/hello-ucloud (0 unchanged)
setup script -> /12347837/repos/hello-ucloud/.ucloud/hello-gpu-setup.sh (wired to 'batchScript')
Submitted job 12356702
follow the run: ucloud jobs logs hello.toml
state -> IN_QUEUE
state -> RUNNING
Job 12356702 is RUNNING.
```

## Step 6 — read the output

The run's output is written to the drive as it happens:

```bash
uv run ucloud jobs logs hello.toml
```

```
+ nvidia-smi
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 580.xx    Driver Version: 580.xx    CUDA Version: 13.0           |
| ...                                                                         |
|   0  NVIDIA B200 MIG 1g.23gb  ...                                          |
+-----------------------------------------------------------------------------+
```

There's your GPU. And because `nvidia-smi` exited, **the job is already
terminating itself** — check with `ucloud jobs list` if you like.

## Step 7 — clean up (optional)

The job is gone on its own. The only leftover is the synced folder on the drive:

```bash
uv run ucloud files rm /12347837/repos/hello-ucloud
```

## What you learned

- `apps search`, `products`, and `files drives` replace hunting for magic
  strings in the GUI.
- A job is fully described by a small TOML spec — see the
  [Job specs guide](../guides/job-specs.md) for every field.
- `[sync]` + `[setup] run` make a **batch job**: code goes in, output comes
  back via `jobs logs`, and the job ends itself.

Next: [a training run that manages itself](02-training-run.md) — the same
ideas, on a real project, with a queue watching the clock for you.
