# Tutorial 3: the Python library

The CLI is a thin layer over a small Python API. Use the library directly when
you want to launch jobs from your own scripts, notebooks, or automation.

## A complete launch-and-run script

```python
from ucloud_api import (
    UCloudClient, Jobs, JobSpecification,
    NameAndVersion, ComputeProduct, SimpleDuration, params,
)

with UCloudClient() as client:            # reads credentials from env / config
    jobs = Jobs(client)

    spec = JobSpecification(
        application=NameAndVersion(name="pytorch-te", version="26.05"),
        product=ComputeProduct(id="uc-a100-1-h", category="uc-a100-h", provider="aau"),
        name="pytorch-run",
        ssh_enabled=True,
        time_allocation=SimpleDuration(hours=4),
        parameters={
            # only if the app needs them; see params helpers below
            # "workingDirectory": params.directory("/1234567/project"),
        },
    )

    job_id = jobs.create(spec)
    print("submitted", job_id)

    jobs.wait_until_running(job_id, timeout=900)
    endpoint = jobs.ssh_endpoint(job_id)
    print("ssh:", endpoint.command if endpoint else "not advertised yet")
```

!!! note "Credentials in scripts"
    `UCloudClient()` resolves credentials from `UCLOUD_REFRESH_TOKEN` /
    `UCLOUD_BASE_URL`, then the config file. Unlike the CLI, the **library does
    not auto-load `.env`** — call `dotenv.load_dotenv()` yourself first if you
    rely on a `.env` file.

## Running commands over SSH

```python
from ucloud_api import SSHRunner

runner = SSHRunner(endpoint)                       # from jobs.ssh_endpoint(...)
result = runner.run("nvidia-smi", capture_output=True)
print(result.stdout)
```

## Building parameters

The `params` module has factories for every `AppParameterValue` type:

```python
from ucloud_api import params

params.text("hello")                 # {"type": "text", "value": "hello"}
params.boolean(True)
params.integer(4)
params.floating_point(0.5)
params.file("/1234567/data", read_only=True)
params.directory("/1234567/project") # same wire type as file
params.ingress("my-public-link-id")
params.peer("hostname", "job-id")
```

## Discovering apps and products

```python
from ucloud_api import UCloudClient
from ucloud_api.catalog import Catalog

with UCloudClient() as client:
    cat = Catalog(client)
    for app in cat.search_apps("pytorch"):
        print(app.name, app.version, app.title)
    for p in cat.products(provider="aau"):
        print(p.provider, p.id, p.category, "gpu=", p.gpu)
```

## Seeding a spec from an existing job

```python
from ucloud_api import UCloudClient, Jobs
from ucloud_api.jobs import specification_to_spec_dict
import tomli_w

with UCloudClient() as client:
    job = Jobs(client).retrieve("5466088")

spec_dict = specification_to_spec_dict(job)         # clean, minimal dict
print(tomli_w.dumps(spec_dict))                      # valid spec TOML
```

## Error handling

All errors derive from `UCloudError`:

```python
from ucloud_api import UCloudError, JobFailedError, JobTimeoutError

try:
    jobs.wait_until_running(job_id, timeout=600)
except JobFailedError as e:
    print("job entered a terminal state:", e.state)
except JobTimeoutError:
    print("still not running after the timeout")
except UCloudError as e:
    print("something else went wrong:", e)
```
