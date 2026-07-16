# Queue & batch workflows

This is the "Slurm feeling" on UCloud: submit jobs from spec files, chain them
with dependencies, let a scheduler watch quota and time — with two improvements
over Slurm: specs are TOML (not `#SBATCH` comments), and running jobs can gain
time instead of dying at the estimate.

## The extended spec file

Three optional sections turn a plain job spec into a full workflow. They are
consumed by the tool and never sent to the API:

```toml
name = "train-unet"
replicas = 1

[application]
name = "pytorch-te"
version = "26.05"

[product]
id = "gpu-nvidia-b200-1-gpu"
category = "gpu-nvidia-b200"
provider = "ucloud"

[time_allocation]
hours = 4                       # ask honestly; auto_extend covers the overrun

[sync]                          # push your working tree, mount it into the job
local = "."                     # relative to this spec file
remote = "/12347837/repos/unet" # drive folder; mounted at /work/unet

[setup]                         # prepare the machine at job start
python = "uv"                   # install uv + `uv sync` the synced repo
run = "uv run python train.py"  # batch mode: job ENDS when this exits

[schedule]                      # queue policy
auto_extend = "1h"              # +1h whenever <15 min remain...
max_time = "24h"                # ...but never past 24h total
```

What each section does:

- **`[sync]`** — incrementally uploads `local` to `remote` (only new/changed
  files travel; `.gitignore` respected via `git ls-files` when the folder is a
  git repo) and mounts `remote` into the job. Your code appears at
  `/work/<folder-name>`. No commits, no tokens inside the job.
- **`[setup]`** — generates a shell script, uploads it beside your code, and
  wires it to the app's script parameter. With `run` set it uses
  **`batchScript`**: UCloud itself terminates the job when the command exits —
  no idle GPU burn even if nothing is watching. Without `run` it uses
  **`initScript`**: the environment is prepared and the job stays up for you to
  `ssh` in. The run's output lands in `<remote>/.ucloud/run-<name>.log` and its
  exit code in `.ucloud/exit-<name>` (used for dependency checks).
- **`[schedule]`** — read by the queue daemon: when a monitored job has less
  than 15 minutes left and is still running, it is extended by `auto_extend`,
  up to `max_time` total (default cap 24h).

`ucloud jobs create spec.toml` runs the same sync + setup pipeline for a single
job — the queue is only needed for dependencies, parallel gating, and
auto-extend.

## The queue

```bash
ucloud q submit train.toml --name unet-base
ucloud q submit eval.toml  --after unet-base   # Slurm's afterok
ucloud q submit sweep-lr1.toml                 # independent: runs when quota allows
ucloud q ls                                    # QUEUED/SUBMITTED/RUNNING/DONE/...
ucloud q logs unet-base                        # the batch run's output
ucloud q rm unet-base --terminate              # cancel (and stop the job)
ucloud q clear                                 # sweep finished records
```

The queue lives in local files (one JSON per job) and is advanced by **ticks**:

```bash
ucloud q tick                     # advance once — cron-able
ucloud q daemon                   # tick every 60s until Ctrl-C
ucloud q daemon --interval 30 --until-idle   # exit when nothing is left to do
```

A tick reconciles every record against the live jobs API, extends jobs that are
low on time, and submits queued jobs whose dependencies are all `DONE` and whose
product category still has quota (`ucloud quota`). Failed dependencies mark
their dependents `BLOCKED` (like `afterok`). Sync + setup run at *launch* time,
so dependent jobs pick up the code as it is when they start.

**Stopping the daemon is always safe.** Running jobs are ordinary UCloud jobs —
watch them in the web GUI; queued specs wait on disk until something ticks
again. Batch jobs still auto-terminate on completion because that is enforced
by UCloud, not the daemon. The only thing that stops is auto-extend and the
submission of queued jobs.

Run the daemon on any always-on machine (a lab server is perfect) — `tmux new -s
ucloud 'ucloud q daemon'` survives your SSH session — or put `ucloud q tick` in
cron. Cron has no PATH to speak of, so use the absolute path that
`uv tool install` gives you (`which ucloud` to find it, usually
`~/.local/bin/ucloud`):

```cron
*/5 * * * * ~/.local/bin/ucloud q tick >> ~/.ucloud-tick.log 2>&1
```

## Syncing code

Two situations, two tools:

- **Before a job starts** — `[sync]` in the spec, or standalone:

  ```bash
  ucloud sync push train.toml                     # use the spec's [sync]
  ucloud sync push . /12347837/repos/unet         # explicit local + remote
  ```

  Incremental (server skips unchanged files), `.gitignore`-aware, and it works
  with queued jobs since no job needs to be running. Deletions are not
  propagated — remove stale remote files with `ucloud files rm` if needed.

- **Iterating inside a running job** — real rsync over the job's SSH:

  ```bash
  ucloud jobs rsync 5471234 ./src/ /work/unet/src/       # push changes in
  ucloud jobs rsync 5471234 /work/unet/results/ ./out --pull
  ```

  Requires `ssh_enabled = true` and a registered key, plus `rsync` locally.

## Failure semantics

| Situation | What happens |
| --- | --- |
| run command exits 0 | job terminates; record `DONE`; dependents launch |
| run command exits non-zero | job terminates; record `FAILED` (exit code in `q ls`); dependents `BLOCKED` |
| job hits its time limit | `EXPIRED` → record `FAILED` (raise `max_time` or `time_allocation`) |
| daemon offline at that moment | nothing is lost — the next tick reconciles from the API |
| no quota left in the category | job waits as `QUEUED` ("waiting for quota") |

## Limits & notes

- The queue is per-machine (records under your user data directory). Two
  machines ticking the same queue don't coordinate.
- `auto_extend` extends *allocations*; whether extension is permitted is up to
  the provider (the same rule as the GUI's +1h/+8h buttons).
- Batch mode wraps your command; interactive apps (Jupyter, VS Code) make more
  sense with `[setup]` *without* `run`, then `ucloud jobs ssh` in.
