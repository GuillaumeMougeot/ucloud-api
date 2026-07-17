# CLI reference

Every command supports `--help`. Examples below use `uv run ucloud …`; drop the
prefix if you installed with `uv tool install`.

## Global

```
ucloud --help
```

| Command | Description |
| --- | --- |
| `ucloud login` | Store and verify a refresh token |
| `ucloud whoami` | Confirm the stored credentials can authenticate |
| `ucloud projects` | List projects; pick one with `login --project` |
| `ucloud products` | List compute products you can launch |
| `ucloud jobs …` | Create, inspect and connect to jobs |
| `ucloud q …` | Queue jobs: dependencies, auto-extend, quota gating |
| `ucloud sync …` | Push a working tree to a drive (incremental) |
| `ucloud ssh-keys …` | Manage SSH public keys |
| `ucloud apps …` | Discover applications |
| `ucloud files …` | Browse drives and folders |
| `ucloud quota` | Show the workspace's allocations |

## `ucloud login`

Store a refresh token (see [Authentication](authentication.md)).

```bash
echo 'TOKEN' | uv run ucloud login          # from stdin (recommended)
uv run ucloud login                          # first time: hidden interactive prompt
uv run ucloud login --token 'TOKEN'          # explicit (avoid: visible in history)
uv run ucloud login --base-url https://cloud.sdu.dk
uv run ucloud login --project <PROJECT_ID>   # switch project — reuses your token
uv run ucloud login --reauth                 # force entering a new token
```

Only the settings you pass change; the rest are kept. A token is obtained in
this order: `--token`, piped stdin, your already-stored token, then a prompt — so
`login --project <id>` does **not** re-ask for your token. If a reused token has
expired, you're prompted for a fresh one; `--reauth` forces that prompt.

## `ucloud whoami`

Verify authentication and print the deployment + credentials path.

## `ucloud init`

Scaffold `ucloud/job.toml` + `ucloud/setup.sh` for the project in the current
directory. Values are read from your workspace, not left as placeholders: your
drive, a single-GPU product you have quota for, the newest PyTorch version, which
script parameter the app takes, and whether it will accept `ssh_enabled`.

```bash
uv run ucloud init                              # scaffold for the cwd
uv run ucloud init --product cpu-amd-zen5-8-vcpu --app pytorch-te
uv run ucloud init --drive 12347837 --force     # overwrite existing files
```

Then point `run` at your training command and `ucloud q submit ucloud/job.toml`.

Deliberately narrow: it scaffolds **Python + uv batch jobs** and refuses anything
else rather than emitting a template that cannot work. For other app types, copy
[`examples/pytorch.toml`](https://github.com/GuillaumeMougeot/ucloud-api/blob/main/examples/pytorch.toml)
or seed from a job you already ran with `ucloud jobs show <id> -o spec.toml`.

## `ucloud projects`

List the projects you belong to (id + title), marking the active one. Set the
active project with `ucloud login --project <id>` or `UCLOUD_PROJECT` in `.env`.
Most drives and GPU allocations live in a project.

## `ucloud products`

List compute products (id / category / provider + cpu / memory / gpu). The
product *catalog* is the same for every workspace, so by default this filters
to categories your **active workspace has remaining quota for** — switch
projects and the list changes with your allocations.

```bash
uv run ucloud products                       # what you can actually launch here
uv run ucloud products --all                 # the whole deployment catalog
uv run ucloud products --provider aau        # filter to one provider
```

## `ucloud quota`

Show the active workspace's allocations — compute and storage quotas, usage,
and what's left. This is what `products` filters on. Exhausted allocations are
dimmed.

```bash
uv run ucloud quota
```

## `ucloud apps list`

List every application in the catalog, grouped by category. The catalog is
per-deployment (not per-project), so this is the same set the GUI shows. Use a
listed name with `apps show <name> <version>`; find a version via `apps search`.

```bash
uv run ucloud apps list
uv run ucloud apps list -c bio          # filter to a category (substring match)
```

## `ucloud apps search`

Search the application catalog.

```bash
uv run ucloud apps search pytorch
uv run ucloud apps search jupyter --limit 50
```

## `ucloud apps show <name> <version>`

Show the parameters an application accepts, including which are required and the
spec `type` to use for each.

```bash
uv run ucloud apps show pytorch-te 26.05
```

## `ucloud jobs create`

Submit a job from a TOML spec file.

```bash
uv run ucloud jobs create my-job.toml               # waits for RUNNING by default
uv run ucloud jobs create my-job.toml --no-wait     # return immediately
uv run ucloud jobs create my-job.toml --timeout 1800
uv run ucloud jobs create my-job.toml --no-ssh      # don't print the SSH command
uv run ucloud jobs create my-job.toml -m /959294/data          # mount a folder
uv run ucloud jobs create my-job.toml -m /959294/ref:ro        # read-only mount
```

`--mount` / `-m` is repeatable; append `:ro` for read-only. See
[Files and storage](files-and-storage.md). For the TOML schema see
[Configuration → spec file format](configuration.md#job-spec-files).

Specs may also carry `[sync]`, `[setup]` and `[schedule]` sections — then
`jobs create` pushes your working tree, prepares the environment, and (with
`run`) submits a batch job that ends when the command exits. See
[Queue & batch workflows](queue-and-batch.md).

## `ucloud jobs list`

List your recent jobs (id, application, state, created).

## `ucloud jobs status <id>`

Show a single job's current state.

## `ucloud jobs show <id>`

Export an existing job as a spec TOML you can re-run.

```bash
uv run ucloud jobs show 5466088               # print to stdout
uv run ucloud jobs show 5466088 -o my-job.toml
```

## `ucloud jobs ssh <id>`

SSH into a running job.

```bash
uv run ucloud jobs ssh 5470001                       # interactive shell
uv run ucloud jobs ssh 5470001 -c "nvidia-smi"       # run one command
uv run ucloud jobs ssh 5470001 -i ~/.ssh/id_ed25519  # pick a private key
```

## `ucloud jobs extend <id>`

Add time to a running job's allocation — the CLI version of the GUI's +1h/+8h
buttons. Useful when a training run needs longer than you estimated.

```bash
uv run ucloud jobs extend 5471234              # +1 hour
uv run ucloud jobs extend 5471234 -H 8         # +8 hours
uv run ucloud jobs extend 5471234 -H 0 -M 30   # +30 minutes
```

## `ucloud jobs terminate <id>`

Terminate a running job.

## `ucloud jobs logs <spec.toml>`

Print the setup + run log of a job submitted with `jobs create` — the same log
`q logs` reads, so it works while the job is running. Pass `--name` if you
submitted under a custom tag.

```bash
uv run ucloud jobs logs train.toml
```

## `ucloud jobs rsync <id> <src> <dst>`

Delta-sync files into (or out of) a running job over its SSH endpoint — ideal
for iterating on code inside a live job. Needs `ssh_enabled = true`, a
registered key, and `rsync` installed locally.

```bash
uv run ucloud jobs rsync 5471234 ./src/ /work/repo/src/
uv run ucloud jobs rsync 5471234 /work/repo/results/ ./out --pull
uv run ucloud jobs rsync 5471234 ./src/ /work/repo/src/ --delete
```

## `ucloud sync push`

Incrementally push a working tree to a drive folder: only new/changed files
travel, `.gitignore` is respected in git repos, junk dirs (`.venv`,
`__pycache__`, …) are excluded elsewhere. Deletions are not propagated.

```bash
uv run ucloud sync push train.toml              # use the spec's [sync] section
uv run ucloud sync push . /12347837/repos/unet  # explicit local + remote
```

## `ucloud q …` — the job queue

Queue jobs with dependencies, quota gating, and auto-extend. Full guide:
[Queue & batch workflows](queue-and-batch.md).

```bash
uv run ucloud q submit train.toml --name base       # launches when quota allows
uv run ucloud q submit eval.toml --after base       # afterok dependency
uv run ucloud q submit train.toml -m /12345/data:ro # extra mount, like jobs create
uv run ucloud q ls                                  # statuses
uv run ucloud q logs base                           # setup + run output
uv run ucloud q tick                                # advance once (cron-able)
uv run ucloud q daemon --interval 30                # keep advancing
uv run ucloud q rm base --terminate                 # cancel
uv run ucloud q clear                               # sweep finished records
```

When to use which: `q submit` when anything should happen *after* submission
(dependencies, auto-extend, waiting for quota); `jobs create` for one-shot jobs
you watch yourself. Same spec file, same pipeline, same log location — see
[jobs create vs q submit](queue-and-batch.md#jobs-create-vs-q-submit).

## `ucloud ssh-keys add <public_key_file>`

Register an SSH public key so SSH-enabled jobs accept it.

```bash
uv run ucloud ssh-keys add ~/.ssh/id_ed25519.pub --title my-laptop
```

## `ucloud ssh-keys list`

List registered SSH public keys.

## `ucloud files drives`

List the drives (file collections) you can access, with the path to browse each.

## `ucloud files ls <path>`

List the contents of a UCloud folder.

```bash
uv run ucloud files ls /12347837
uv run ucloud files ls /12347837/project
```

## `ucloud files upload <local> <remote>`

Upload a file or directory tree (many files in parallel).

```bash
uv run ucloud files upload ./dataset /12347837/dataset
uv run ucloud files upload ./model.pt /12347837/models/ -j 16
uv run ucloud files upload ./data /12347837/data --no-overwrite --chunk-mb 16
```

## `ucloud files download <remote> <local>`

Download a file or directory tree (many files in parallel).

```bash
uv run ucloud files download /12347837/results ./results
uv run ucloud files download /12347837/model.pt ./model.pt -j 16
```

## `ucloud files mkdir <path>`

Create a folder on UCloud.

## `ucloud files rm <path>`

Move a file or folder to the trash (asks for confirmation; `-y` to skip).

## `ucloud files shell [start]`

Open an interactive browser with `cd` / `ls` / `pwd` / `get` / `put` / `mkdir` /
`rm` and **tab-completion** of remote paths. Starts at the root (which lists your
drives) unless you pass a starting path.
