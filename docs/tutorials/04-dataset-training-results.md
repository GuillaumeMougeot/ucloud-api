# Tutorial 4: dataset → training → results

A complete workflow entirely from the terminal: push a dataset to UCloud, run a
GPU training job against it, and pull the results back — no web GUI.

Assumes you've finished [Getting started](../getting-started.md) (logged in, SSH
key registered).

## Step 0 — pick your project

GPU allocations and storage live in a **project**, not your personal "My
workspace". Set the one that has your allocation:

```bash
uv run ucloud projects
# Active   ID                                     Title
#          (none)                                 My workspace (personal — no allocation)
#  *       6a9d3c0b-…                             DeiC-AU-N1-2026195
uv run ucloud login --project 6a9d3c0b-52bc-4652-94a6-1411d59b958e
```

From here, `/<driveId>/…` paths refer to that project's drives
(`ucloud files drives` lists them).

## Step 1 — upload the dataset

```bash
uv run ucloud files mkdir /12347837/experiments
uv run ucloud files upload ./data /12347837/experiments/data
```

Directories upload recursively, many files at once. Check it landed:

```bash
uv run ucloud files ls /12347837/experiments/data
```

## Step 2 — write the job spec

`train.toml` — a PyTorch GPU job with the experiment folder **mounted** so the
job can read the dataset and write results back to UCloud storage:

```toml
name = "train-run"
replicas = 1
ssh_enabled = true

[application]
name = "pytorch-te"
version = "26.05"

[product]
id = "uc-a100-1-h"
category = "uc-a100-h"
provider = "aau"

[time_allocation]
hours = 8

# Mount the whole experiment folder (dataset in, results out).
[[resources]]
type = "file"
path = "/12347837/experiments"
read_only = false
```

(You can also mount from the command line with `-m /12347837/experiments`
instead of the `[[resources]]` block.)

## Step 3 — launch and connect

```bash
uv run ucloud jobs create train.toml --wait
# Job 5470123 is RUNNING.
# Connect with: ssh ucloud@ssh.cloud.sdu.dk -p 3421
```

The mounted folder appears inside the job under `/work/experiments`.

## Step 4 — run the training

Kick off training over SSH. For a quick run you can block until it finishes:

```bash
uv run ucloud jobs ssh 5470123 -c "cd /work/experiments && python /work/experiments/data/train.py \
    --data /work/experiments/data --out /work/experiments/results"
```

For a long run, start it detached and poll:

```bash
uv run ucloud jobs ssh 5470123 -c "cd /work/experiments && nohup python train.py \
    --data data --out results > results/train.log 2>&1 &"

# later, watch progress:
uv run ucloud jobs ssh 5470123 -c "tail -n 20 /work/experiments/results/train.log"
```

Because `results/` is on the mounted drive, everything the job writes there is
immediately in UCloud storage.

## Step 5 — pull the results back

```bash
uv run ucloud files download /12347837/experiments/results ./results
```

Or browse around first with the interactive shell (tab-completes paths):

```bash
uv run ucloud files shell /12347837/experiments
# ucloud:/12347837/experiments$ ls
# ucloud:/12347837/experiments$ cd results
# ucloud:/12347837/experiments/results$ get model.pt ./model.pt
```

## Step 6 — clean up

```bash
uv run ucloud jobs terminate 5470123
```

## Make it a script

Every step is one command, so the whole loop drops into a shell script or CI job:

```bash
#!/usr/bin/env bash
set -euo pipefail
JOB_DIR=/12347837/experiments
uv run ucloud files upload ./data "$JOB_DIR/data"
JOB=$(uv run ucloud jobs create train.toml --no-wait | awk '/Submitted/{print $3}')
uv run ucloud jobs status "$JOB"           # or wait, then ssh to run training
# ... run training over ssh ...
uv run ucloud files download "$JOB_DIR/results" ./results
uv run ucloud jobs terminate "$JOB"
```
