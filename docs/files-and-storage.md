# Files and storage

UCloud storage is organised into **drives** (also called *file collections*). A
path looks like:

```
/<driveId>/folder/subfolder
```

where `<driveId>` is the numeric id of a drive. `ucloud-api` can browse, upload,
download, and **mount folders into jobs** from the terminal.

!!! important "Set your project first"
    Most storage (and GPU allocations) live inside a **project**, not your
    personal workspace. Operations on project drives require an active project.
    List yours and set it once:

    ```bash
    uv run ucloud projects                       # shows ids + titles
    uv run ucloud login --project <PROJECT_ID>   # or set UCLOUD_PROJECT in .env
    ```

    Without a project set you'll see `403 "Write permission is required"` on
    project drives, because the request defaults to your personal space.

## Browse your drives

```bash
uv run ucloud files drives
```

```
┃ Path    ┃ Title ┃ Provider ┃
│ /959294 │ Home  │ ucloud   │
```

The **Path** column (`/959294`) is the root you pass to `files ls`.

## List a folder

```bash
uv run ucloud files ls /959294
uv run ucloud files ls /959294/project
```

```
┃ Type ┃ Name       ┃ Size ┃ Modified         ┃
│ dir  │ Jobs/      │      │ 2025-10-01 12:30 │
│ dir  │ project/   │      │ 2025-11-26 15:26 │
│ file │ notes.md   │ 2 KB │ 2025-11-20 09:03 │
```

## Upload and download

Transfer files or whole directory trees. Many files move in parallel, and a
progress bar shows throughput.

```bash
# Upload a file or a directory
uv run ucloud files upload ./dataset /12347837/dataset
uv run ucloud files upload ./model.pt /12347837/models/

# Download a file or a directory
uv run ucloud files download /12347837/results ./results
uv run ucloud files download /12347837/models/model.pt ./model.pt
```

Options:

- `-j / --concurrency N` — number of files transferred in parallel (default 8).
- `--chunk-mb N` — upload chunk size (default 8).
- `--overwrite / --no-overwrite` — replace vs. keep-and-rename on conflict.

Housekeeping:

```bash
uv run ucloud files mkdir /12347837/newdir
uv run ucloud files rm /12347837/oldstuff        # moves to trash (asks first; -y to skip)
```

!!! note "How it works"
    Uploads use the provider's `WEBSOCKET_V2` streaming protocol; downloads are a
    direct HTTPS GET. Transfers are network-bound, so `ucloud-api` runs many
    files concurrently rather than using threads/processes — that's where large
    datasets (thousands of files) get their speed.

## Mount a folder into a job

In the GUI you attach folders on the job-create page. With `ucloud-api` you do
the same thing by adding the folder to the job's **`resources`** as a `file`
entry. There are two ways:

### Option A — the `--mount` flag (easiest)

```bash
uv run ucloud jobs create pytorch.toml -m /959294/project
uv run ucloud jobs create pytorch.toml -m /959294/data -m /959294/ref:ro
```

- Repeat `-m` / `--mount` for each folder.
- Append `:ro` to mount read-only.
- Paths must be absolute (`/driveId/...`), exactly as shown by `files ls`.

Inside the job the folder appears under `/work/<folderName>` (the standard
UCloud mount location).

### Option B — declare it in the spec file

Add a `[[resources]]` block per folder:

```toml
[[resources]]
type = "file"
path = "/959294/project"
read_only = false

[[resources]]
type = "file"
path = "/959294/reference-data"
read_only = true
```

This is handy when you want the mounts version-controlled alongside the rest of
the spec.

## Passing a specific file as a parameter

Some applications take a *file* or *directory* as an input **parameter** (not a
mount) — for example an init script. Check with
[`ucloud apps show`](cli-reference.md#ucloud-apps-show-name-version), then set it
in `[parameters.*]`:

```toml
[parameters.initScript]
type = "file"
path = "/959294/project/setup.sh"
read_only = true
```

## From Python

```python
from ucloud_api import UCloudClient, Files

with UCloudClient() as client:
    files = Files(client)
    for d in files.list_drives():
        print(d.path, d.title)
    for e in files.list_path("/959294"):
        print("dir " if e.is_dir else "file", e.path)
```

## From Python

```python
from ucloud_api import UCloudClient, Transfer

with UCloudClient() as client:          # project comes from config/env
    tx = Transfer(client)
    tx.upload("./dataset", "/12347837/dataset", concurrency=16)
    tx.download("/12347837/results", "./results")
```
