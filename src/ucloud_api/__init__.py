"""ucloud-api: launch and control UCloud (SDU eScience) GPU jobs from the CLI.

Quick start::

    from ucloud_api import UCloudClient, Jobs, JobSpecification, NameAndVersion, ComputeProduct

    with UCloudClient() as client:
        jobs = Jobs(client)
        job_id = jobs.create(JobSpecification(
            application=NameAndVersion(name="pytorch-te", version="..."),
            product=ComputeProduct(id="...", category="...", provider="..."),
            ssh_enabled=True,
        ))
        jobs.wait_until_running(job_id)
        endpoint = jobs.ssh_endpoint(job_id)
"""

from __future__ import annotations

from . import params
from .client import UCloudClient
from .config import Credentials, load_credentials
from .exceptions import (
    APIError,
    AuthError,
    ConfigError,
    JobFailedError,
    JobTimeoutError,
    UCloudError,
)
from .jobs import Jobs, SSHEndpoint, SSHKeys
from .models import (
    AppParameterValue,
    ComputeProduct,
    JobSpecification,
    JobState,
    NameAndVersion,
    SimpleDuration,
)
from .ssh import SSHRunner

__version__ = "0.1.0"

__all__ = [
    "APIError",
    "AppParameterValue",
    "AuthError",
    "ComputeProduct",
    "ConfigError",
    "Credentials",
    "JobFailedError",
    "JobSpecification",
    "JobState",
    "JobTimeoutError",
    "Jobs",
    "NameAndVersion",
    "SSHEndpoint",
    "SSHKeys",
    "SSHRunner",
    "SimpleDuration",
    "UCloudClient",
    "UCloudError",
    "__version__",
    "load_credentials",
    "params",
]
