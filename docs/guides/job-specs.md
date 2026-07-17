# Job specs

Everything a job is, in one TOML file. This page is the reference for that
file: the fields UCloud's API defines, the parameter types, the three sections
this tool adds on top, and the fastest ways to get a correct spec without
writing one from scratch.

## Anatomy

A spec mirrors UCloud's `JobSpecification` 1:1, plus up to three tool-only
sections (`[sync]`, `[setup]`, `[schedule]`) that are consumed locally and
never sent to the API. Field names may be `snake_case` (as below) or the API's
`camelCase` — both load.

```toml
# TOML rule: top-level scalars MUST come before any [table].
name = "train-run"         # optional job name (also the default log/queue tag)
replicas = 1               # default 1
ssh_enabled = true         # ONLY for apps that support SSH — see below
opened_file = "/123/notebook.ipynb"   # optional

[application]
name = "pytorch-te"        # find with: ucloud apps search / apps list
version = "26.05"

[product]
id = "gpu-nvidia-b200-1-gpu"          # find with: ucloud products
category = "gpu-nvidia-b200"
provider = "ucloud"

[time_allocation]
hours = 4                  # an honest estimate; the queue can extend it later
minutes = 0
seconds = 0

# --- Application parameters: each is a tagged value -------------------------
[parameters.someText]
type = "text"
value = "hello"

[parameters.workingDirectory]
type = "file"
path = "/1234567/project"
read_only = false

# --- Extra resources: drive mounts, public links, networks ------------------
[[resources]]
type = "file"
path = "/1234567/datasets/my-dataset"   # appears at /work/my-dataset
read_only = true

# --- Tool sections (never sent to the API) ----------------------------------
[sync]
local = "."                             # relative to this spec file
remote = "/1234567/repos/my-project"    # pushed incrementally, mounted at /work/my-project

[setup]
python = "uv"                           # install uv + `uv sync` the synced project
script = "extra-setup.sh"               # optional: your own lines, embedded too
run = "python train.py"                 # batch mode: the job ENDS when this exits

[schedule]
auto_extend = "1h"                      # queue only: +1h whenever <15 min remain
max_time = "24h"                        # ...never past this total
```

The tool sections' *behavior* — what sync pushes, what the generated setup
script does, how auto-extend decides — is documented in
[Queue & batch workflows](queue-and-batch.md).

## Parameter types

| `type` | Fields | Meaning |
| --- | --- | --- |
| `text` | `value` | a string |
| `textarea` | `value` | a multi-line string |
| `boolean` | `value` | true/false |
| `integer` | `value` | whole number |
| `floating_point` | `value` | decimal number |
| `file` | `path`, `read_only` | a file or directory from your UCloud drive |
| `peer` | `hostname`, `job_id` | link to another running job |
| `ingress` | `id` | a public link |
| `network` | `id` | a network |
| `block_storage` | `id` | a block-storage volume |
| `license_server` | `id` | a license |

Which parameters does an app accept? Ask it:

```bash
uv run ucloud apps show pytorch-te 26.05
```

This also prints whether the app **supports SSH** — `ssh_enabled = true` on an
app without SSH support is rejected outright at create time
("This application does not support SSH but it is required"). Batch apps like
`pytorch-te` typically have none; watch them with `jobs logs` / `q logs`
instead.

## Mounting drive folders

Anything already on a drive is *mounted*, never copied. A folder mounted at
`/1234567/datasets/x` appears inside the job at `/work/x`. Three equivalent
ways:

```toml
[[resources]]              # in the spec — versioned with your code
type = "file"
path = "/1234567/datasets/x"
read_only = true
```

```bash
ucloud jobs create spec.toml -m /1234567/datasets/x:ro   # ad hoc
ucloud q submit spec.toml -m /1234567/datasets/x:ro      # same flag, queued
```

The `--mount` flag is for one-off additions; put anything permanent in the spec.

## Seeding a spec from a job you already ran

The fastest way to a correct spec — including fiddly application parameters —
is to export one from a job that already worked (GUI-launched jobs count):

```bash
uv run ucloud jobs list
# │ 5466088 │ cuda-jupyter-ubuntu@24.04 │ SUCCESS │ ...
uv run ucloud jobs show 5466088 -o my-job.toml
```

The export strips UCloud's server-managed noise and reduces each parameter to
its minimal form, so it loads straight back into `jobs create` / `q submit`.
Then edit what differs — typically the app, product, and time. Parameters you
don't understand are exactly the ones worth keeping as-is.

Without `-o` it prints to stdout.

## Or scaffold one from your workspace

In a Python project, `ucloud init` writes a `ucloud/job.toml` + `ucloud/setup.sh`
with **real values read from your account** — your drive, a GPU product with
quota, the newest PyTorch, whether the app takes SSH and which script parameter
it accepts:

```bash
uv run ucloud init
```

See [`ucloud init`](../cli-reference.md#ucloud-init).

## Keep a library of specs

Specs are plain files — version them with the code they launch:

```
my-project/
├── train.py
└── ucloud/
    ├── train.toml
    ├── eval.toml
    └── setup.sh
```

Then every run is reproducible, and `[sync] local = ".."` ships the specs to
the drive along with everything else.

!!! warning "Name the folder `ucloud/`, not `.ucloud/`"
    `.ucloud/` is the tool's own runtime directory on the drive (generated
    setup scripts, run logs, exit codes) and is excluded from sync.
