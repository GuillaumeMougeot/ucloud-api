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
| `ucloud products` | List compute products you can launch |
| `ucloud jobs …` | Create, inspect and connect to jobs |
| `ucloud ssh-keys …` | Manage SSH public keys |
| `ucloud apps …` | Discover applications |

## `ucloud login`

Store a refresh token (see [Authentication](authentication.md)).

```bash
echo 'TOKEN' | uv run ucloud login          # from stdin (recommended)
uv run ucloud login                          # hidden interactive prompt
uv run ucloud login --token 'TOKEN'          # explicit (avoid: visible in history)
uv run ucloud login --base-url https://cloud.sdu.dk
```

## `ucloud whoami`

Verify authentication and print the deployment + credentials path.

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

## `ucloud jobs create`

Submit a job from a TOML spec file.

```bash
uv run ucloud jobs create my-job.toml               # waits for RUNNING by default
uv run ucloud jobs create my-job.toml --no-wait     # return immediately
uv run ucloud jobs create my-job.toml --timeout 1800
uv run ucloud jobs create my-job.toml --no-ssh      # don't print the SSH command
```

See [Configuration → spec file format](configuration.md#job-spec-files) for the
TOML schema.

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
