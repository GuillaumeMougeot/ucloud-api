"""Pydantic models mirroring the subset of UCloud's API we use.

Field names are pythonic ``snake_case``; they serialize to the ``camelCase`` JSON
UCloud expects via an alias generator. Always dump with
``model_dump(by_alias=True, exclude_none=True)`` before sending.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


# --------------------------------------------------------------------------- #
# AppParameterValue: the tagged union used for both parameters and resources.
# Tag values (e.g. "floating_point") are literal API contract strings.
# --------------------------------------------------------------------------- #


class FileParam(CamelModel):
    type: Literal["file"] = "file"
    path: str
    read_only: bool = False


class BoolParam(CamelModel):
    type: Literal["boolean"] = "boolean"
    value: bool


class TextParam(CamelModel):
    type: Literal["text"] = "text"
    value: str


class TextAreaParam(CamelModel):
    type: Literal["textarea"] = "textarea"
    value: str


class IntegerParam(CamelModel):
    type: Literal["integer"] = "integer"
    value: int


class FloatingPointParam(CamelModel):
    type: Literal["floating_point"] = "floating_point"
    value: float


class PeerParam(CamelModel):
    type: Literal["peer"] = "peer"
    hostname: str
    job_id: str


class LicenseParam(CamelModel):
    type: Literal["license_server"] = "license_server"
    id: str


class BlockStorageParam(CamelModel):
    type: Literal["block_storage"] = "block_storage"
    id: str


class NetworkParam(CamelModel):
    type: Literal["network"] = "network"
    id: str


class IngressParam(CamelModel):
    type: Literal["ingress"] = "ingress"
    id: str


AppParameterValue = Annotated[
    FileParam
    | BoolParam
    | TextParam
    | TextAreaParam
    | IntegerParam
    | FloatingPointParam
    | PeerParam
    | LicenseParam
    | BlockStorageParam
    | NetworkParam
    | IngressParam,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- #
# Job specification (the create payload) and status.
# --------------------------------------------------------------------------- #


class NameAndVersion(CamelModel):
    name: str
    version: str


class ComputeProduct(CamelModel):
    id: str
    category: str
    provider: str


class SimpleDuration(CamelModel):
    hours: int = 0
    minutes: int = 0
    seconds: int = 0


class JobSpecification(CamelModel):
    application: NameAndVersion
    product: ComputeProduct
    name: str | None = None
    replicas: int = 1
    parameters: dict[str, AppParameterValue] = Field(default_factory=dict)
    resources: list[AppParameterValue] | None = None
    time_allocation: SimpleDuration | None = None
    opened_file: str | None = None
    ssh_enabled: bool | None = None


class JobState(StrEnum):
    IN_QUEUE = "IN_QUEUE"
    RUNNING = "RUNNING"
    CANCELING = "CANCELING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            JobState.SUCCESS,
            JobState.FAILURE,
            JobState.EXPIRED,
        }
