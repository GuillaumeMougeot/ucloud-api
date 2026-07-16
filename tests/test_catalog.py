"""Catalog parsing and job-spec export."""

from __future__ import annotations

import tomllib

import tomli_w

from ucloud_api.catalog import Catalog
from ucloud_api.jobs import specification_to_spec_dict
from ucloud_api.models import JobSpecification


class _FakeClient:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def get(self, path, params=None):
        return self._payload

    def post(self, path, json=None):
        return self._payload


def test_products_flattens_by_provider() -> None:
    payload = {
        "productsByProvider": {
            "aau": [
                {
                    "product": {
                        "name": "u1-gpu-1",
                        "category": {"name": "u1-gpu", "provider": "aau"},
                        "cpu": 16,
                        "memoryInGigs": 128,
                        "gpu": 1,
                        "description": "One GPU",
                    }
                }
            ]
        }
    }
    products = Catalog(_FakeClient(payload)).products()  # type: ignore[arg-type]
    assert len(products) == 1
    p = products[0]
    assert (p.id, p.category, p.provider, p.gpu) == ("u1-gpu-1", "u1-gpu", "aau", 1)


def test_products_provider_filter() -> None:
    payload = {
        "productsByProvider": {
            "aau": [{"product": {"name": "a", "category": {"name": "c", "provider": "aau"}}}],
            "ucloud": [{"product": {"name": "b", "category": {"name": "d", "provider": "ucloud"}}}],
        }
    }
    products = Catalog(_FakeClient(payload)).products(provider="aau")  # type: ignore[arg-type]
    assert [p.provider for p in products] == ["aau"]


_WALLETS_PAYLOAD = {
    "items": [
        {
            "paysFor": {
                "name": "gpu-nvidia-b200",
                "provider": "ucloud",
                "productType": "COMPUTE",
                "accountingUnit": {"name": "GPU"},
                "accountingFrequency": "PERIODIC_MINUTE",
            },
            "quota": 660000,
            "totalUsage": 3000,
            "maxUsable": 657000,
        },
        {
            "paysFor": {
                "name": "uc-a100-h",
                "provider": "aau",
                "productType": "COMPUTE",
                "accountingUnit": {"name": "GPU"},
                "accountingFrequency": "PERIODIC_HOUR",
            },
            "quota": 50,
            "totalUsage": 50,
            "maxUsable": 0,
        },
        {
            "paysFor": {
                "name": "storage",
                "provider": "ucloud",
                "productType": "STORAGE",
                "accountingUnit": {"name": "GB"},
                "accountingFrequency": "ONCE",
            },
            "quota": 6144,
            "totalUsage": 722,
            "maxUsable": 5422,
        },
    ]
}


def test_wallets_normalize_periodic_minutes_to_hours() -> None:
    wallets = Catalog(_FakeClient(_WALLETS_PAYLOAD)).wallets()  # type: ignore[arg-type]
    gpu = next(w for w in wallets if w.category == "gpu-nvidia-b200")
    assert (gpu.quota, gpu.usage, gpu.max_usable) == (11000, 50, 10950)  # minutes -> hours
    assert gpu.unit == "GPU-hours"
    assert gpu.usable
    exhausted = next(w for w in wallets if w.category == "uc-a100-h")
    assert not exhausted.usable
    storage = next(w for w in wallets if w.category == "storage")
    assert (storage.quota, storage.unit) == (6144, "GB")  # absolute quota untouched


def test_products_usable_only_filters_by_wallet_category() -> None:
    class _Client:
        def get(self, path, params=None):
            if path.endswith("browseWallets"):
                return _WALLETS_PAYLOAD
            return {
                "productsByProvider": {
                    "ucloud": [
                        {
                            "product": {
                                "name": "gpu-b200-1",
                                "category": {"name": "gpu-nvidia-b200", "provider": "ucloud"},
                            }
                        },
                        {
                            "product": {
                                "name": "u1-standard-1",
                                "category": {"name": "u1-standard", "provider": "ucloud"},
                            }
                        },
                    ],
                    "aau": [
                        {
                            "product": {
                                "name": "a100-1",
                                "category": {"name": "uc-a100-h", "provider": "aau"},
                            }
                        }
                    ],
                }
            }

    catalog = Catalog(_Client())  # type: ignore[arg-type]
    # Only the category with remaining quota survives: no wallet -> out,
    # exhausted wallet -> out.
    assert [p.id for p in catalog.products(usable_only=True)] == ["gpu-b200-1"]
    assert len(catalog.products(usable_only=False)) == 3


def test_search_apps_reads_metadata() -> None:
    payload = {
        "items": [
            {"metadata": {"name": "pytorch-te", "version": "2.3.0", "title": "PyTorch"}},
        ]
    }
    results = Catalog(_FakeClient(payload)).search_apps("pytorch")  # type: ignore[arg-type]
    assert results[0].name == "pytorch-te"
    assert results[0].version == "2.3.0"


def test_list_apps_walks_categories_and_groups() -> None:
    landing = {"categories": [{"metadata": {"id": 210}, "specification": {"title": "AI"}}]}
    category = {
        "status": {
            "groups": [
                {"specification": {"defaultFlavor": "pytorch-te", "title": "PyTorch"}},
                {"specification": {"title": "AlphaFold 3"}},  # no defaultFlavor
            ]
        }
    }

    class _SeqClient:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, path, params=None):
            self.calls += 1
            return landing if path.endswith("retrieveLandingPage") else category

    groups = Catalog(_SeqClient()).list_apps()  # type: ignore[arg-type]
    assert [(g.name, g.title, g.category) for g in groups] == [
        ("", "AlphaFold 3", "AI"),  # sorted by title; empty name allowed
        ("pytorch-te", "PyTorch", "AI"),
    ]


def test_list_apps_category_filter_is_substring() -> None:
    landing = {
        "categories": [
            {"metadata": {"id": 1}, "specification": {"title": "Artificial Intelligence"}},
            {"metadata": {"id": 2}, "specification": {"title": "Bioinformatics"}},
        ]
    }

    class _Client:
        def get(self, path, params=None):
            if path.endswith("retrieveLandingPage"):
                return landing
            return {"status": {"groups": [{"specification": {"defaultFlavor": "x", "title": "X"}}]}}

    groups = Catalog(_Client()).list_apps(category="intelli")  # type: ignore[arg-type]
    assert {g.category for g in groups} == {"Artificial Intelligence"}


def test_app_parameters_parse_and_map_spec_type() -> None:
    payload = {
        "invocation": {
            "parameters": [
                {"name": "initScript", "type": "input_file", "optional": True, "title": "Init"},
                {"name": "count", "type": "integer", "optional": False, "title": "Count"},
                {"name": "mode", "type": "enumeration", "optional": True, "title": "Mode"},
            ]
        }
    }
    params_ = Catalog(_FakeClient(payload)).app_parameters("pytorch-te", "26.05")  # type: ignore[arg-type]
    assert params_[0].spec_type == "file"  # input_file -> file
    assert params_[1].spec_type == "integer"
    assert params_[1].optional is False
    assert params_[2].spec_type == "text"  # enumeration -> text


def test_spec_export_roundtrips_to_toml_and_back() -> None:
    job = {
        "id": "123",
        "specification": {
            "application": {"name": "pytorch-te", "version": "2.3.0"},
            "product": {"id": "u1-gpu-1", "category": "u1-gpu", "provider": "aau"},
            "replicas": 1,
            "sshEnabled": True,
            "timeAllocation": {"hours": 4, "minutes": 0, "seconds": 0},
            "parameters": {"folder": {"type": "file", "path": "/1/p", "readOnly": False}},
            "resolvedProduct": {"should": "be dropped"},
        },
    }
    spec_dict = specification_to_spec_dict(job)
    assert spec_dict["ssh_enabled"] is True
    assert "resolvedProduct" not in spec_dict

    # It must be valid TOML that loads back into a JobSpecification.
    reparsed = tomllib.loads(tomli_w.dumps(spec_dict))
    spec = JobSpecification.model_validate(reparsed)
    assert spec.application.name == "pytorch-te"
    assert spec.parameters["folder"].type == "file"
    assert spec.time_allocation is not None
    assert spec.time_allocation.hours == 4
