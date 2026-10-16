"""
Server metrics from AWS CloudWatch and Azure Monitor, for the time window of an
analysed run.

ProjectStack reads the metrics of the resources a scenario runs on (instances,
containers, databases, caches, load balancers, app platforms) for the run's
window, stores them per minute next to the samples, and the detectors relate them
to what the test saw. Credentials come from the machine (an AWS CLI profile or SSO
session, `az login`, environment variables, an instance role or managed identity)
or from read-only keys in Settings; ProjectStack only ever reads.

The resources to read are saved per scenario (cloud_targets.json next to the run
folders), so runs of the same scenario are fetched the same way.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from projectstack.results.cloud.presets import MetricDef, resource_type

PROVIDERS = ("aws", "azure")


class CloudError(RuntimeError):
    """A cloud problem explained for the user (no credentials, access denied, unknown resource …)."""


@dataclass
class Resource:
    provider: str            # aws | azure
    type: str                # preset key: ec2, rds, alb … / vm, sqldb, appgw …
    id: str                  # AWS: the dimension value(s), "cluster/service" for two; Azure: the full resource id
    name: str = ""
    region: str = ""
    role: str = ""           # app | database | cache | edge … (free text, shown in charts)

    @property
    def display(self) -> str:
        return self.name or self.id.rsplit("/", 1)[-1]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Resource":
        known = {k: str(d.get(k) or "") for k in ("provider", "type", "id", "name", "region", "role")}
        if known["provider"] not in PROVIDERS:
            raise CloudError(f"Unknown cloud provider {known['provider']!r}")
        resource_type(known["provider"], known["type"])          # validates the type
        if not known["id"]:
            raise CloudError("A resource needs an id")
        return cls(**known)


@dataclass
class Series:
    resource: Resource
    metric: MetricDef
    points: list[tuple[int, float]] = field(default_factory=list)    # (epoch ms, value) in display units
    period_s: int = 60

    def as_dict(self, start_ms: int = 0) -> dict[str, Any]:
        return {"provider": self.resource.provider, "resource_type": self.resource.type,
                "resource_id": self.resource.id, "resource": self.resource.display, "role": self.resource.role,
                "region": self.resource.region, "metric": self.metric.key, "label": self.metric.label,
                "kind": self.metric.kind, "unit": self.metric.unit, "period_s": self.period_s,
                "points": [{"t_offset_s": round((t - start_ms) / 1000), "value": round(v, 4)} for t, v in self.points]}


@dataclass
class FetchResult:
    series: list[Series]
    missing: list[dict[str, str]]        # metrics without data, with why
    warnings: list[str]


def period_for(start_ms: int, now: datetime | None = None) -> int:
    """CloudWatch keeps 1-minute data for 15 days, 5-minute for 63 days, then hourly."""
    age = (now or datetime.now(timezone.utc)) - datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    if age <= timedelta(days=15):
        return 60
    if age <= timedelta(days=63):
        return 300
    return 3600


def get_provider(name: str, settings: dict[str, str]):
    if name == "aws":
        from projectstack.results.cloud.aws import AwsMonitor
        return AwsMonitor(settings)
    if name == "azure":
        from projectstack.results.cloud.azure import AzureMonitor
        return AzureMonitor(settings)
    raise CloudError(f"Unknown cloud provider {name!r} — use aws or azure")


def fetch(resources: list[Resource], start_ms: int, end_ms: int, settings: dict[str, str],
          padding_s: int = 300) -> FetchResult:
    """Metrics of every resource for [start - padding, end + padding], from each resource's provider."""
    series: list[Series] = []
    missing: list[dict[str, str]] = []
    warnings: list[str] = []
    for provider in PROVIDERS:
        mine = [r for r in resources if r.provider == provider]
        if not mine:
            continue
        result = get_provider(provider, settings).fetch(mine, start_ms - padding_s * 1000, end_ms + padding_s * 1000)
        series += result.series
        missing += result.missing
        warnings += result.warnings
    return FetchResult(series, missing, warnings)


# ---------------------------------------------------------------------------
# Resources saved per scenario
# ---------------------------------------------------------------------------

def targets_path(settings: dict[str, str]) -> Path:
    return Path(settings["runs_dir"]).parent / "cloud_targets.json"


def load_targets(settings: dict[str, str], scenario: str | None = None) -> list[Resource] | dict[str, list[Resource]]:
    path = targets_path(settings)
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    scenarios = {k: [Resource.from_dict(r) for r in v] for k, v in (data.get("scenarios") or {}).items()}
    if scenario is None:
        return scenarios
    return scenarios.get(scenario, [])


def save_targets(settings: dict[str, str], scenario: str, resources: list[Resource]) -> None:
    path = targets_path(settings)
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("scenarios", {})[scenario] = [asdict(r) for r in resources]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def configured(settings: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Which providers have an SDK installed and how they would sign in (no network calls)."""
    out: dict[str, dict[str, Any]] = {}
    try:
        import boto3  # noqa: F401
        aws_sdk = True
    except ImportError:
        aws_sdk = False
    keys = bool(settings.get("aws_access_key_id") and settings.get("aws_secret_access_key"))
    out["aws"] = {"sdk": aws_sdk, "auth": "keys in Settings" if keys else
                  (f"profile {settings['aws_profile']}" if settings.get("aws_profile") else "default credentials on this machine"),
                  "region": settings.get("aws_region") or ""}
    try:
        import azure.identity  # noqa: F401
        import azure.mgmt.monitor  # noqa: F401
        az_sdk = True
    except ImportError:
        az_sdk = False
    sp = bool(settings.get("azure_tenant_id") and settings.get("azure_client_id") and settings.get("azure_client_secret"))
    out["azure"] = {"sdk": az_sdk, "auth": "service principal in Settings" if sp else "az login / managed identity on this machine",
                    "subscription": settings.get("azure_subscription_id") or ""}
    return out
