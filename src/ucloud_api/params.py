"""Convenience factories for building ``AppParameterValue`` objects.

These keep call sites readable::

    from ucloud_api import params
    spec.parameters = {
        "jupyterVersion": params.text("3.11"),
        "workingDir": params.directory("/home/ucloud/project"),
    }
"""

from __future__ import annotations

from .models import (
    BlockStorageParam,
    BoolParam,
    FileParam,
    FloatingPointParam,
    IngressParam,
    IntegerParam,
    LicenseParam,
    NetworkParam,
    PeerParam,
    TextAreaParam,
    TextParam,
)


def text(value: str) -> TextParam:
    return TextParam(value=value)


def textarea(value: str) -> TextAreaParam:
    return TextAreaParam(value=value)


def boolean(value: bool) -> BoolParam:
    return BoolParam(value=value)


def integer(value: int) -> IntegerParam:
    return IntegerParam(value=value)


def floating_point(value: float) -> FloatingPointParam:
    return FloatingPointParam(value=value)


def file(path: str, *, read_only: bool = False) -> FileParam:
    """A file or directory mount from the user's UCloud drive."""
    return FileParam(path=path, read_only=read_only)


# ``directory`` reads better than ``file`` for folder mounts; same wire type.
directory = file


def peer(hostname: str, job_id: str) -> PeerParam:
    return PeerParam(hostname=hostname, job_id=job_id)


def ingress(id: str) -> IngressParam:
    return IngressParam(id=id)


def network(id: str) -> NetworkParam:
    return NetworkParam(id=id)


def block_storage(id: str) -> BlockStorageParam:
    return BlockStorageParam(id=id)


def license_server(id: str) -> LicenseParam:
    return LicenseParam(id=id)
