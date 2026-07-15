# Files and storage

UCloud storage is organised into **drives** (also called *file collections*). A
path looks like:

```
/<driveId>/folder/subfolder
```

where `<driveId>` is the numeric id of a drive (your personal drive is usually
titled *Home*). `ucloud-api` can browse this from the terminal, and — crucially —
**mount a folder into a job** so it's available inside the running container.

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

## What's not (yet) supported

Uploading and downloading files over the API (UCloud uses a separate chunked
upload/download protocol) isn't wrapped yet. For now, move data with the GUI, or
`rsync`/`scp` over SSH into a running job (see
[Tutorial 1](tutorials/01-first-gpu-job.md)). If you'd find upload/download
useful, [open an issue](https://github.com/GuillaumeMougeot/ucloud-api/issues).
