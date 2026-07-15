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
| `ucloud ssh-keys …` | Manage SSH public keys |
| `ucloud apps …` | Discover applications |
| `ucloud files …` | Browse drives and folders |

## `ucloud login`

Store a refresh token (see [Authentication](authentication.md)).

```bash
echo 'TOKEN' | uv run ucloud login          # from stdin (recommended)
uv run ucloud login                          # hidden interactive prompt
uv run ucloud login --token 'TOKEN'          # explicit (avoid: visible in history)
uv run ucloud login --base-url https://cloud.sdu.dk
uv run ucloud login --project <PROJECT_ID>   # set the active project
```

## `ucloud whoami`

Verify authentication and print the deployment + credentials path.

## `ucloud projects`

List the projects you belong to (id + title), marking the active one. Set the
active project with `ucloud login --project <id>` or `UCLOUD_PROJECT` in `.env`.
Most drives and GPU allocations live in a project.

## `ucloud products`

List compute products (id / category / provider + cpu / memory / gpu).

```bash
uv run ucloud products
uv run ucloud products --provider aau        # filter to one provider
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

## `ucloud jobs terminate <id>`

Terminate a running job.

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
