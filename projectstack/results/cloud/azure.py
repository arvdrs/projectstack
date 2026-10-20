"""
Azure: Azure Monitor platform metrics (the Resource Manager metrics API) and
resource discovery.

Credentials: a service principal in Settings (tenant, client id, secret) when given,
otherwise DefaultAzureCredential (environment variables, managed identity, `az login`,
VS Code …). The Monitoring Reader role is enough for metrics, Reader for discovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from projectstack.config import IN_DOCKER
from projectstack.results.cloud import CloudError, FetchResult, Resource, Series
from projectstack.results.cloud.presets import AZURE, MetricDef

RETENTION_DAYS = 93
_TYPE_BY_AZ = {t.az_type.lower(): t.key for t in AZURE.values()}


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _subscription_of(resource_id: str) -> str:
    parts = resource_id.strip("/").split("/")
    try:
        return parts[parts.index("subscriptions") + 1]
    except (ValueError, IndexError) as exc:
        raise CloudError(f"Not an Azure resource id: {resource_id!r}") from exc


class AzureMonitor:
    name = "azure"

    def __init__(self, settings: dict[str, str]):
        try:
            import azure.identity  # noqa: F401
            import azure.mgmt.monitor  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise CloudError("The Azure SDK is not installed — pip install azure-identity azure-mgmt-monitor azure-mgmt-resource") from exc
        self.settings = settings
        self._credential = None
        self._monitors: dict[str, Any] = {}

    @property
    def credential(self):
        if self._credential is None:
            from azure.identity import ClientSecretCredential, DefaultAzureCredential
            s = self.settings
            if s.get("azure_tenant_id") and s.get("azure_client_id") and s.get("azure_client_secret"):
                self._credential = ClientSecretCredential(s["azure_tenant_id"], s["azure_client_id"], s["azure_client_secret"])
            else:
                self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        return self._credential

    def monitor(self, subscription: str):
        if subscription not in self._monitors:
            from azure.mgmt.monitor import MonitorManagementClient
            self._monitors[subscription] = MonitorManagementClient(self.credential, subscription)
        return self._monitors[subscription]

    def resources_client(self):
        from azure.mgmt.resource.resources import ResourceManagementClient
        sub = self.settings.get("azure_subscription_id")
        if not sub:
            raise CloudError("No Azure subscription — set azure_subscription_id in Settings")
        return ResourceManagementClient(self.credential, sub)

    @staticmethod
    def _explain(exc: Exception, what: str) -> CloudError:
        from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
        if isinstance(exc, ClientAuthenticationError):
            first = str(exc).strip().splitlines()[0][:300]
            if IN_DOCKER:
                return CloudError("Azure sign-in failed — ProjectStack is running in Docker, where `az login` is not available: "
                                  f"add a service principal in Settings ({first})")
            return CloudError(f"Azure sign-in failed — run `az login` on this machine or add a service principal in Settings ({first})")
        if isinstance(exc, ResourceNotFoundError):
            return CloudError(f"Azure could not find the resource during {what}")
        if isinstance(exc, HttpResponseError):
            code = getattr(getattr(exc, "error", None), "code", "") or getattr(exc, "status_code", "")
            if str(code) in ("AuthorizationFailed", "403"):
                return CloudError(f"Azure denied {what} — the identity needs the Monitoring Reader (metrics) or Reader (discovery) role")
            return CloudError(f"Azure error during {what}: {exc.message if hasattr(exc, 'message') else exc}")
        return CloudError(f"Azure error during {what}: {exc}")

    # ------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        try:
            token = self.credential.get_token("https://management.azure.com/.default")
        except Exception as exc:
            raise self._explain(exc, "sign-in") from exc
        info: dict[str, Any] = {"provider": "azure", "signed_in": bool(token.token),
                                "subscription": self.settings.get("azure_subscription_id") or None}
        if info["subscription"]:
            try:
                next(iter(self.resources_client().resource_groups.list()), None)
            except CloudError:
                raise
            except Exception as exc:
                raise self._explain(exc, "reading the subscription") from exc
        return info

    def discover(self, types: list[str] | None = None, region: str = "") -> tuple[list[Resource], list[str]]:
        wanted = [t for t in (types or list(AZURE)) if t in AZURE]
        if not wanted:
            return [], []
        flt = " or ".join(f"resourceType eq '{AZURE[t].az_type}'" for t in wanted)
        try:
            items = list(self.resources_client().resources.list(filter=flt))
        except CloudError:
            raise
        except Exception as exc:
            raise self._explain(exc, "listing resources") from exc
        out = []
        for it in items:
            key = _TYPE_BY_AZ.get((it.type or "").lower())
            if key and (not region or (it.location or "").lower() == region.lower()):
                group = it.id.split("/resourceGroups/", 1)[-1].split("/", 1)[0] if "/resourceGroups/" in it.id else ""
                out.append(Resource("azure", key, it.id, f"{it.name} ({group})" if group else it.name, it.location or ""))
        return out, []

    # ------------------------------------------------------------------

    def fetch(self, resources: list[Resource], start_ms: int, end_ms: int) -> FetchResult:
        series: list[Series] = []
        missing: list[dict[str, str]] = []
        warnings: list[str] = []
        age_days = (datetime.now(timezone.utc).timestamp() * 1000 - start_ms) / 86_400_000
        if age_days > RETENTION_DAYS:
            raise CloudError(f"This run is {age_days:.0f} days old; Azure Monitor keeps platform metrics for {RETENTION_DAYS} days")
        timespan = f"{_iso(start_ms)}/{_iso(end_ms)}"
        for r in resources:
            rt = AZURE[r.type]
            try:
                client = self.monitor(_subscription_of(r.id))
                got = self._query(client, r.id, list(rt.metrics), timespan)
            except CloudError as exc:
                warnings.append(f"{r.display}: {exc}")
                continue
            for m in rt.metrics:
                pts = got.get(m.key)
                if pts:
                    series.append(Series(r, m, pts, 60))
                else:
                    missing.append({"resource": r.display, "metric": m.label,
                                    "why": m.note or "Azure Monitor returned no data for this window"})
        return FetchResult(series, missing, warnings)

    def _query(self, client, resource_id: str, metrics: list[MetricDef], timespan: str) -> dict[str, list[tuple[int, float]]]:
        """All metrics in one call; when the resource rejects one (unsupported name), each on its own."""
        from azure.core.exceptions import HttpResponseError

        def call(defs: list[MetricDef]):
            aggs = sorted({m.az_agg for m in defs})
            return client.metrics.list(resource_id, timespan=timespan, interval="PT1M",
                                       metricnames=",".join(m.az_name for m in defs), aggregation=",".join(aggs))
        try:
            responses = [(metrics, call(metrics))]
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) in (401, 403):
                raise self._explain(exc, "reading metrics") from exc
            responses = []
            for m in metrics:
                try:
                    responses.append(([m], call([m])))
                except HttpResponseError:
                    continue          # not available on this resource (tier, SKU or kind)
        except Exception as exc:
            raise self._explain(exc, "reading metrics") from exc
        out: dict[str, list[tuple[int, float]]] = {}
        for defs, resp in responses:
            by_name = {m.az_name.lower(): m for m in defs}
            for metric in resp.value or []:
                m = by_name.get((metric.name.value or "").lower())
                if not m:
                    continue
                pts = []
                for ts in metric.timeseries or []:
                    for d in ts.data or []:
                        raw = getattr(d, m.az_agg.lower(), None)
                        if raw is None:
                            continue
                        v = float(raw) * m.scale
                        if m.rate == "s":
                            v /= 60
                        pts.append((int(d.time_stamp.timestamp() * 1000), v))
                if pts:
                    out[m.key] = sorted(pts)
        return out
