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
_WALLETS_BASE = "/api/accounting/v2"


@dataclass(slots=True)
class AppSummary:
    name: str
    version: str
    title: str
    description: str


@dataclass(slots=True)
class AppGroup:
    """An application group as shown on the catalog landing page.

    ``name`` is the group's default flavor — the launchable application name you
    pass to ``apps show`` / use in a spec. A few grouping-only entries have no
    default flavor, in which case ``name`` is empty.
    """

    name: str
    title: str
    category: str
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


@dataclass(slots=True)
class AppDetails:
    """One application version: its parameters and whether it can do SSH."""

    name: str
    version: str
    parameters: list[AppParameter]
    #: ``OPTIONAL`` / ``MANDATORY`` / ``DISABLED``; ``None`` when the app has no
    #: SSH support at all (most batch apps, e.g. pytorch-te).
    ssh_mode: str | None

    @property
    def supports_ssh(self) -> bool:
        """Whether ``ssh_enabled = true`` is accepted; otherwise create returns 400."""
        return self.ssh_mode is not None and self.ssh_mode != "DISABLED"

    @property
    def script_param(self) -> str | None:
        """The parameter to hand a generated setup script to, if the app takes one.

        ``batchScript`` first: with it UCloud ends the job when the script exits,
        which is what makes a batch run self-terminating.
        """
        names = {p.name for p in self.parameters if p.spec_type == "file"}
        for preferred in ("batchScript", "initScript"):
            if preferred in names:
                return preferred
        return None


def _read_parameters(invocation: dict[str, Any]) -> list[AppParameter]:
    """Parse ``invocation.parameters`` from GET /api/hpc/apps/byNameAndVersion."""
    results: list[AppParameter] = []
    for p in invocation.get("parameters", []) or []:
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
class Wallet:
    """An allocation (quota) the active workspace can spend from.

    Wallets are what actually differ between projects — the product *catalog* is
    deployment-wide, but you can only launch products whose category has a wallet
    with remaining quota in the active workspace.
    """

    category: str
    provider: str
    product_type: str  # COMPUTE | STORAGE | ...
    unit: str  # display unit, e.g. "Core-hours", "GPU-hours", "GB"
    quota: float
    usage: float
    max_usable: float

    @property
    def usable(self) -> bool:
        return self.max_usable > 0


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

    def list_apps(self, *, category: str | None = None) -> list[AppGroup]:
        """List every application in the store, grouped by category.

        The catalog is per-deployment (not per-project), so this mirrors what the
        GUI's landing page shows. It walks the landing page's categories and each
        category's groups (GET /api/hpc/apps/retrieveLandingPage then
        /api/hpc/apps/retrieveCategory).
        """
        landing = self._client.get(f"{_APPS_BASE}/retrieveLandingPage")
        categories = landing.get("categories", []) if isinstance(landing, dict) else []
        results: list[AppGroup] = []
        seen: set[str] = set()
        for cat in categories:
            ctitle = str((cat.get("specification") or {}).get("title", "") or "")
            if category is not None and category.lower() not in ctitle.lower():
                continue
            cid = (cat.get("metadata") or {}).get("id")
            if cid is None:
                continue
            detail = self._client.get(f"{_APPS_BASE}/retrieveCategory", params={"id": cid})
            status = (detail.get("status") or {}) if isinstance(detail, dict) else {}
            for group in status.get("groups") or []:
                spec = group.get("specification") or {}
                name = str(spec.get("defaultFlavor") or "")
                title = str(spec.get("title", "") or "")
                key = f"{ctitle}\0{name}\0{title}"
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    AppGroup(
                        name=name,
                        title=title,
                        category=ctitle,
                        description=str(spec.get("description", "") or ""),
                    )
                )
        results.sort(key=lambda g: (g.category.lower(), g.title.lower()))
        return results

    def app_details(self, name: str, version: str) -> AppDetails:
        """Everything a spec needs to know about one application version."""
        data = self._client.get(
            f"{_APPS_BASE}/byNameAndVersion",
            params={"appName": name, "appVersion": version},
        )
        invocation = data.get("invocation", {}) if isinstance(data, dict) else {}
        ssh = invocation.get("ssh")
        return AppDetails(
            name=name,
            version=version,
            parameters=_read_parameters(invocation),
            # Absent/null means the app has no SSH support at all — passing
            # ssh_enabled to one of those is rejected outright at create time.
            ssh_mode=str(ssh.get("mode")) if isinstance(ssh, dict) and ssh.get("mode") else None,
        )

    def app_parameters(self, name: str, version: str) -> list[AppParameter]:
        """List the parameters an application accepts."""
        return self.app_details(name, version).parameters

    def wallets(self) -> list[Wallet]:
        """List the active workspace's allocations (GET …/accounting/v2/browseWallets).

        The ``Project`` header decides whose wallets you see: the active
        project's, or your personal ones in "My workspace".
        """
        data = self._client.get(f"{_WALLETS_BASE}/browseWallets", params={"itemsPerPage": 250})
        items = data.get("items", []) if isinstance(data, dict) else []
        results: list[Wallet] = []
        for item in items:
            pays_for = (item or {}).get("paysFor", {})
            quota, usage, max_usable, unit = _normalize_accounting(
                float(item.get("quota", 0) or 0),
                float(item.get("totalUsage", 0) or 0),
                float(item.get("maxUsable", 0) or 0),
                pays_for,
            )
            results.append(
                Wallet(
                    category=str(pays_for.get("name", "?")),
                    provider=str(pays_for.get("provider", "?")),
                    product_type=str(pays_for.get("productType", "?")),
                    unit=unit,
                    quota=quota,
                    usage=usage,
                    max_usable=max_usable,
                )
            )
        return results

    def products(
        self, *, provider: str | None = None, usable_only: bool = False
    ) -> list[ComputeProductInfo]:
        """List compute products you can launch (GET /api/jobs/retrieveProducts).

        The catalog itself is deployment-wide; pass ``usable_only=True`` to keep
        only products whose category has remaining quota in the active workspace
        (see :meth:`wallets`).
        """
        usable_categories: set[tuple[str, str]] | None = None
        if usable_only:
            usable_categories = {(w.provider, w.category) for w in self.wallets() if w.usable}
        data = self._client.get(f"{_JOBS_BASE}/retrieveProducts")
        by_provider = data.get("productsByProvider", {}) if isinstance(data, dict) else {}
        results: list[ComputeProductInfo] = []
        for prov, entries in by_provider.items():
            if provider is not None and prov != provider:
                continue
            for entry in entries or []:
                product = (entry or {}).get("product", {})
                category = product.get("category", {})
                if usable_categories is not None:
                    key = (str(category.get("provider", prov)), str(category.get("name", "?")))
                    if key not in usable_categories:
                        continue
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


def _normalize_accounting(
    quota: float, usage: float, max_usable: float, pays_for: dict[str, Any]
) -> tuple[float, float, float, str]:
    """Convert raw accounting numbers into human units.

    Periodic wallets are metered per minute/hour/day; we normalise everything
    periodic to hours (e.g. ``Core-hours``), and leave absolute quotas (storage
    ``GB``) untouched.
    """
    unit_name = str((pays_for.get("accountingUnit") or {}).get("name", "") or "unit")
    frequency = str(pays_for.get("accountingFrequency", "ONCE") or "ONCE")
    per_hour = {"PERIODIC_MINUTE": 60.0, "PERIODIC_HOUR": 1.0, "PERIODIC_DAY": 1.0 / 24.0}
    if frequency in per_hour:
        f = per_hour[frequency]
        return quota / f, usage / f, max_usable / f, f"{unit_name}-hours"
    return quota, usage, max_usable, unit_name
