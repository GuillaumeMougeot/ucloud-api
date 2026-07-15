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
