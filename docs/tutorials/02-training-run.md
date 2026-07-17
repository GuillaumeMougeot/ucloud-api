# Tutorial 2: a training run that manages itself

Goal: take a Python project on your machine and a dataset on a UCloud drive,
and get a training run that **syncs its own code, builds its own environment,
extends its own time allocation, terminates itself, and queues a follow-up job**
— so you never babysit a GPU again.

This is the workflow this tool exists for. It assumes
[Tutorial 1](01-first-gpu-job.md) (you've run a batch job and know your drive
id, app, and products).

## The starting point

A normal project — nothing UCloud-specific in it:

```
my-trainer/
├── pyproject.toml        # declares torch, your deps
├── train.py
└── eval.py
```

and a dataset already on the drive, say `/12347837/datasets/imagenet-mini`
(upload one with `ucloud files upload` — see
[Files and storage](../guides/files-and-storage.md)).

## Step 1 — the spec, with three workflow sections

`train.toml`, next to your code:

```toml
name = "train-base"
replicas = 1

[application]
name = "pytorch-te"
version = "26.05"

[product]
id = "gpu-nvidia-b200-1-gpu"        # a full GPU this time — real work
category = "gpu-nvidia-b200"
provider = "ucloud"

[time_allocation]
hours = 4                           # honest guess; [schedule] covers the overrun

[sync]
local = "."                         # this project, relative to the spec file
remote = "/12347837/repos/my-trainer"

[setup]
python = "uv"                       # install uv + `uv sync` your declared deps
run = "python train.py --data /work/imagenet-mini --out results"

[schedule]
auto_extend = "1h"                  # +1h whenever <15 min remain...
max_time = "24h"                    # ...but never past 24h total

# The dataset, mounted read-only at /work/imagenet-mini:
[[resources]]
type = "file"
path = "/12347837/datasets/imagenet-mini"
read_only = true
```

What each section buys you:

- **`[sync]`** — your working tree is pushed to the drive (incremental: only
  changed files travel, `.gitignore` respected) and mounted at
  `/work/my-trainer`, which is the job's working directory. No commits, no
  tokens inside the job.
- **`[setup]`** — the machine prepares itself at start: install uv, build the
  venv from your `pyproject.toml`, then run your command. Because `run` is set,
  **the job terminates when training exits** — success or crash, the GPU never
  idles.
- **`[schedule]`** — the queue's policy: extend the allocation by an hour
  whenever less than 15 minutes remain, up to a hard cap. This is UCloud's
  GUI "+1h" button, pressed for you.
- **`[[resources]]`** — data that already lives on the drive is *mounted*, never
  copied. Results written under the synced folder (`results/`) land on the
  drive as they're written.

## Step 2 — submit to the queue

```bash
uv run ucloud q submit train.toml --name base
```

```
queued base
14:32:04 base: synced 42 file(s) to /12347837/repos/my-trainer (0 unchanged)
14:32:04 base: setup script -> /12347837/repos/my-trainer/.ucloud/base-setup.sh (wired to 'batchScript')
14:32:04 base: submitted job 12356683
```

`q submit` (rather than `jobs create`) records the job locally under the name
`base` — that record is what dependencies, auto-extend, and `q logs` hang off.
[When to use which →](../guides/queue-and-batch.md#jobs-create-vs-q-submit)

## Step 3 — chain the evaluation

```bash
uv run ucloud q submit eval.toml --after base
```

`--after` is Slurm's `afterok`: `eval` launches only if `base` finishes with
exit code 0 — a crash or a manual terminate marks it `BLOCKED` instead. The
code is synced again at launch time, so `eval` picks up any fixes you made
while `base` ran.

## Step 4 — let something watch the clock

Auto-extend and dependency launches happen on **ticks**. Run the daemon in tmux
on any always-on machine, or put `q tick` in cron:

```bash
uv run ucloud q daemon                 # tick every 60s until Ctrl-C
# or: */5 * * * * ~/.local/bin/ucloud q tick >> ~/.ucloud-tick.log 2>&1
```

A real daemon session, ticking a run exactly like this one:

```
14:39:34 base: job 12356683 is RUNNING
15:54:35 base: extended job 12356683 by 1h (low on time)
17:21:36 base: DONE (job state SUCCESS, run exit code 0)
17:21:41 eval: submitted job 12356686
```

**Stopping the daemon is always safe.** Batch jobs self-terminate because
UCloud enforces it, not the daemon — the only things that pause are extensions
and new launches, and the next tick reconciles everything from the live API.

## Step 5 — watch, without SSH

```bash
uv run ucloud q ls                     # QUEUED / RUNNING / DONE / FAILED ...
uv run ucloud q logs base              # setup + training output, live
```

`q ls` shows the *run's* exit code, not just the job state — UCloud reports
`SUCCESS` for any batch script that ran to completion, including one whose
training crashed; the queue reads the recorded exit code and tells you the
truth.

## Step 6 — collect results

They're already on the drive (the synced folder is a mount, so `results/` was
written straight to it):

```bash
uv run ucloud files download /12347837/repos/my-trainer/results ./results
```

## Two performance rules worth knowing

Measured on a real 480 GB / 5.7M-image training run — details in
[Queue & batch workflows](../guides/queue-and-batch.md):

1. **Raise your dataloader's worker count well past the core count** when
   reading many small files from a mount. The mount is latency-bound (~90 ms
   per file): at PyTorch's/fastai's default 16 workers it delivers ~170
   images/s; at 256 workers, ~1000. Workers sit blocked on the network, not on
   CPU, so 256 workers on 48 cores is fine. This one setting was the
   difference between 8.7 h and 1.4 h per epoch.
2. **Build the venv on the job's local disk** (`[setup] python = "uv"` already
   does) — never on `/work`, which is network storage and metered.

## What you learned

- Three TOML sections turn a job into a workflow: sync in, set up, run,
  self-terminate.
- `q submit` + `--after` + the daemon give you `sbatch`, `afterok`, and an
  auto-extend Slurm doesn't have.
- Nothing here required SSH, a browser, or your attention at 3 a.m.

Next: [drive it all from Python](03-python-library.md).
