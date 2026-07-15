# Tutorial 2: from an existing job

The fastest way to get a correct spec — including all the fiddly application
parameters — is to copy one from a job you've **already run** (in the GUI or via
the CLI). That's what `ucloud jobs show` is for.

## Step 1 — find the job id

```bash
uv run ucloud jobs list
```

```
┃ ID      ┃ Application                   ┃ State     ┃ Created          ┃
│ 5466088 │ cuda-jupyter-ubuntu-aau@24.04 │ SUCCESS   │ 2025-11-26 15:26 │
```

## Step 2 — export it as a spec

```bash
uv run ucloud jobs show 5466088 -o my-job.toml
```

This writes a clean, ready-to-run TOML file. Under the hood it strips UCloud's
server-managed noise and reduces each parameter to its minimal form, so the
output loads straight back into `jobs create`.

Without `-o` it prints to stdout, so you can redirect or inspect it:

```bash
uv run ucloud jobs show 5466088 > my-job.toml
```

## Step 3 — tweak what you need

Open `my-job.toml` and change whatever differs for the new run — commonly the
application and/or the product:

```toml
[application]
name = "pytorch-te"     # was cuda-jupyter-ubuntu-aau
version = "26.05"

[product]
id = "uc-a100-1-h"
category = "uc-a100-h"
provider = "aau"
```

Leave the `[parameters.*]` tables as-is if the new app expects the same ones
(for example a `pubKey` parameter). This is exactly the detail that's painful to
reconstruct from scratch — reusing an existing job hands it to you.

## Step 4 — run it

```bash
uv run ucloud jobs create my-job.toml --wait
```

## Tip: keep a library of specs

Because specs are plain files, you can version them in your own repo:

```
specs/
├── pytorch-a100.toml
├── pytorch-b200.toml
└── jupyter-cpu.toml
```

Then launching is a one-liner and every run is reproducible.
