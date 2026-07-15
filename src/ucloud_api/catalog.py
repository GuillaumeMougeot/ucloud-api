"""Discover applications and compute products available on the deployment.

These back the ``ucloud apps search`` and ``ucloud products`` commands, which turn
"find the magic strings in the GUI's DevTools" into a couple of CLI calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import UCloudClient

_APPS_BASE = "/api/hpc/apps"
_JOBS_BASE = "/api/jobs"


@dataclass(slots=True)
class AppSummary:
    name: str
    version: str
    title: str
    description: str


@dataclass(slots=True)
class AppParameter:
    """A single parameter an application accepts."""

    name: str
    type: str
    optional: bool
    title: str
    description: str
    default: Any

    @property
    def spec_type(self) -> str:
        """The AppParameterValue ``type`` to use for this parameter in a spec."""
        return _APP_PARAM_TO_SPEC_TYPE.get(self.type, "text")


#: Maps an application-parameter type (from the catalog) to the tagged
#: AppParameterValue ``type`` you write in a spec file.
_APP_PARAM_TO_SPEC_TYPE = {
    "input_file": "file",
    "input_directory": "file",
    "text": "text",
    "textarea": "textarea",
    "integer": "integer",
    "floating_point": "floating_point",
    "boolean": "boolean",
    "enumeration": "text",
    "peer": "peer",
    "ingress": "ingress",
    "license_server": "license_server",
    "network_ip": "network",
}


@dataclass(slots=True)
class ComputeProductInfo:
    """A product plus the fields needed to reference it in a job spec."""

    id: str
    category: str
    provider: str
    cpu: int | None
    memory_gb: int | None
    gpu: int | None
    description: str


class Catalog:
    def __init__(self, client: UCloudClient) -> None:
        self._client = client

    def search_apps(self, query: str, *, limit: int = 25) -> list[AppSummary]:
        """Full-text search the application store (POST /api/hpc/apps/search)."""
        data = self._client.post(
            f"{_APPS_BASE}/search", json={"query": query, "itemsPerPage": limit}
        )
        items = data.get("items", []) if isinstance(data, dict) else []
        results: list[AppSummary] = []
        for item in items:
            meta = (item or {}).get("metadata", {})
            results.append(
                AppSummary(
                    name=str(meta.get("name", "?")),
                    version=str(meta.get("version", "?")),
                    title=str(meta.get("title", "")),
                    description=str(meta.get("description", "")),
                )
            )
        return results

    def app_parameters(self, name: str, version: str) -> list[AppParameter]:
        """List the parameters an application accepts.

        GET /api/hpc/apps/byNameAndVersion; parameters live under
        ``invocation.parameters``.
        """
        data = self._client.get(
            f"{_APPS_BASE}/byNameAndVersion",
            params={"appName": name, "appVersion": version},
        )
        invocation = data.get("invocation", {}) if isinstance(data, dict) else {}
        params = invocation.get("parameters", []) or []
        results: list[AppParameter] = []
        for p in params:
            results.append(
                AppParameter(
                    name=str(p.get("name", "?")),
                    type=str(p.get("type", "?")),
                    optional=bool(p.get("optional", False)),
                    title=str(p.get("title", "") or ""),
                    description=str(p.get("description", "") or ""),
                    default=p.get("defaultValue"),
                )
            )
        return results

    def products(self, *, provider: str | None = None) -> list[ComputeProductInfo]:
        """List compute products you can launch (GET /api/jobs/retrieveProducts)."""
        data = self._client.get(f"{_JOBS_BASE}/retrieveProducts")
        by_provider = data.get("productsByProvider", {}) if isinstance(data, dict) else {}
        results: list[ComputeProductInfo] = []
        for prov, entries in by_provider.items():
            if provider is not None and prov != provider:
                continue
            for entry in entries or []:
                product = (entry or {}).get("product", {})
                category = product.get("category", {})
                results.append(
                    ComputeProductInfo(
                        id=str(product.get("name", "?")),
                        category=str(category.get("name", "?")),
                        provider=str(category.get("provider", prov)),
                        cpu=_as_int(product.get("cpu")),
                        memory_gb=_as_int(product.get("memoryInGigs")),
                        gpu=_as_int(product.get("gpu")),
                        description=str(product.get("description", "")),
                    )
                )
        return results


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
