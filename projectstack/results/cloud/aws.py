"""
AWS: CloudWatch metrics (GetMetricData) and resource discovery.

Credentials: access keys in Settings when given, otherwise the AWS CLI profile in
Settings, otherwise boto3's default chain (environment, ~/.aws, SSO, instance or task
role). Read-only permissions are enough: cloudwatch:GetMetricData and, for discovery,
the Describe/List calls of each service (the ReadOnlyAccess or CloudWatchReadOnlyAccess
plus ViewOnlyAccess managed policies cover them).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from projectstack.config import IN_DOCKER
from projectstack.results.cloud import CloudError, FetchResult, Resource, Series, period_for
from projectstack.results.cloud.presets import AWS, MetricDef


class AwsMonitor:
    name = "aws"

    def __init__(self, settings: dict[str, str]):
        try:
            import boto3  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise CloudError("The AWS SDK is not installed — pip install boto3") from exc
        self.settings = settings
        self._sessions: dict[str, Any] = {}

    def session(self, region: str = ""):
        import boto3
        from botocore.exceptions import ProfileNotFound
        region = region or self.settings.get("aws_region") or ""
        if region not in self._sessions:
            kw: dict[str, Any] = {"region_name": region or None}
            if self.settings.get("aws_access_key_id") and self.settings.get("aws_secret_access_key"):
                kw.update(aws_access_key_id=self.settings["aws_access_key_id"],
                          aws_secret_access_key=self.settings["aws_secret_access_key"],
                          aws_session_token=self.settings.get("aws_session_token") or None)
            elif self.settings.get("aws_profile"):
                kw["profile_name"] = self.settings["aws_profile"]
            try:
                self._sessions[region] = boto3.Session(**kw)
            except ProfileNotFound as exc:
                raise CloudError(f"AWS profile {self.settings.get('aws_profile')!r} was not found in ~/.aws") from exc
        return self._sessions[region]

    def client(self, service: str, region: str = ""):
        sess = self.session(region)
        if not sess.region_name:
            raise CloudError("No AWS region — set one in Settings (aws_region) or in your AWS profile")
        return sess.client(service)

    @staticmethod
    def _explain(exc: Exception, what: str) -> CloudError:
        from botocore.exceptions import ClientError, NoCredentialsError, NoRegionError, TokenRetrievalError
        if isinstance(exc, (NoCredentialsError, TokenRetrievalError)):
            if IN_DOCKER:
                return CloudError("No AWS credentials — ProjectStack is running in Docker: add read-only keys in Settings, or "
                                  "start it with docker-compose.aws.yml to use your `aws configure` / `aws sso login` sign-in "
                                  "(README → Run with Docker)")
            return CloudError("No AWS credentials — run `aws configure` or `aws sso login` on this machine, or add read-only keys in Settings")
        if isinstance(exc, NoRegionError):
            return CloudError("No AWS region — set one in Settings (aws_region)")
        if isinstance(exc, ClientError):
            code = exc.response.get("Error", {}).get("Code", "")
            msg = exc.response.get("Error", {}).get("Message", str(exc))
            if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
                return CloudError(f"AWS denied {what}: {msg}")
            if code in ("ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "SignatureDoesNotMatch"):
                return CloudError(f"AWS rejected the credentials ({code}) — refresh them (aws sso login) or check the keys in Settings")
            return CloudError(f"AWS error during {what}: {code} {msg}")
        return CloudError(f"AWS error during {what}: {exc}")

    # ------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """Who we are signed in as (sts:GetCallerIdentity needs no permissions)."""
        try:
            ident = self.client("sts").get_caller_identity()
        except CloudError:
            raise
        except Exception as exc:
            raise self._explain(exc, "the identity check") from exc
        return {"provider": "aws", "account": ident.get("Account"), "identity": ident.get("Arn"),
                "region": self.session().region_name}

    # ------------------------------------------------------------------

    def discover(self, types: list[str] | None = None, region: str = "") -> tuple[list[Resource], list[str]]:
        """Resources of the given types in a region; services we may not list are reported, not fatal."""
        region = region or self.session().region_name or ""
        wanted = types or list(AWS)
        found: list[Resource] = []
        warnings: list[str] = []
        listers = {"ec2": self._ec2, "asg": self._asg, "ecs": self._ecs, "eks": self._eks, "rds": self._rds,
                   "aurora": self._aurora, "elasticache": self._elasticache, "alb": self._elb, "nlb": self._elb,
                   "apigw": self._apigw, "lambda": self._lambda}
        done_elb = False
        for t in wanted:
            if t not in listers:
                continue
            if t in ("alb", "nlb"):
                if done_elb:
                    continue
                done_elb = True
            try:
                found += [r for r in listers[t](region) if r.type in wanted]
            except CloudError as exc:
                warnings.append(str(exc))
            except Exception as exc:
                warnings.append(str(self._explain(exc, f"listing {AWS[t].title}s")))
        return found, warnings

    def _pages(self, client, op: str, key: str, **kw) -> list[dict]:
        out: list[dict] = []
        if client.can_paginate(op):
            for page in client.get_paginator(op).paginate(**kw):
                out += page.get(key, [])
        else:
            out = getattr(client, op)(**kw).get(key, [])
        return out

    def _ec2(self, region: str) -> list[Resource]:
        out = []
        for res in self._pages(self.client("ec2", region), "describe_instances", "Reservations",
                               Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]):
            for i in res.get("Instances", []):
                name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "")
                out.append(Resource("aws", "ec2", i["InstanceId"], name or i["InstanceId"], region))
        return out

    def _asg(self, region: str) -> list[Resource]:
        return [Resource("aws", "asg", g["AutoScalingGroupName"], g["AutoScalingGroupName"], region)
                for g in self._pages(self.client("autoscaling", region), "describe_auto_scaling_groups", "AutoScalingGroups")]

    def _ecs(self, region: str) -> list[Resource]:
        ecs = self.client("ecs", region)
        out = []
        for arn in self._pages(ecs, "list_clusters", "clusterArns"):
            cluster = arn.rsplit("/", 1)[-1]
            for sarn in self._pages(ecs, "list_services", "serviceArns", cluster=arn):
                service = sarn.rsplit("/", 1)[-1]
                out.append(Resource("aws", "ecs", f"{cluster}/{service}", f"{service} ({cluster})", region))
        return out

    def _eks(self, region: str) -> list[Resource]:
        return [Resource("aws", "eks", c, c, region) for c in self._pages(self.client("eks", region), "list_clusters", "clusters")]

    def _rds(self, region: str) -> list[Resource]:
        return [Resource("aws", "rds", d["DBInstanceIdentifier"], f"{d['DBInstanceIdentifier']} ({d.get('Engine', '')})", region)
                for d in self._pages(self.client("rds", region), "describe_db_instances", "DBInstances")]

    def _aurora(self, region: str) -> list[Resource]:
        return [Resource("aws", "aurora", c["DBClusterIdentifier"], c["DBClusterIdentifier"], region)
                for c in self._pages(self.client("rds", region), "describe_db_clusters", "DBClusters")]

    def _elasticache(self, region: str) -> list[Resource]:
        return [Resource("aws", "elasticache", c["CacheClusterId"], f"{c['CacheClusterId']} ({c.get('Engine', '')})", region)
                for c in self._pages(self.client("elasticache", region), "describe_cache_clusters", "CacheClusters")]

    def _elb(self, region: str) -> list[Resource]:
        out = []
        for lb in self._pages(self.client("elbv2", region), "describe_load_balancers", "LoadBalancers"):
            kind = {"application": "alb", "network": "nlb"}.get(lb.get("Type", ""))
            if kind:
                # The CloudWatch dimension is the end of the ARN: app/<name>/<id> or net/<name>/<id>
                dim = lb["LoadBalancerArn"].split(":loadbalancer/", 1)[-1]
                out.append(Resource("aws", kind, dim, lb["LoadBalancerName"], region))
        return out

    def _apigw(self, region: str) -> list[Resource]:
        return [Resource("aws", "apigw", a["name"], a["name"], region)
                for a in self._pages(self.client("apigateway", region), "get_rest_apis", "items")]

    def _lambda(self, region: str) -> list[Resource]:
        return [Resource("aws", "lambda", f["FunctionName"], f["FunctionName"], region)
                for f in self._pages(self.client("lambda", region), "list_functions", "Functions")]

    # ------------------------------------------------------------------

    def fetch(self, resources: list[Resource], start_ms: int, end_ms: int) -> FetchResult:
        period = period_for(start_ms)
        series: list[Series] = []
        missing: list[dict[str, str]] = []
        warnings: list[str] = []
        if period > 60:
            warnings.append(f"This run is older than CloudWatch keeps 1-minute data, so AWS metrics are at "
                            f"{period // 60}-minute resolution")
        by_region: dict[str, list[Resource]] = defaultdict(list)
        for r in resources:
            by_region[r.region or self.session().region_name or ""].append(r)
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        for region, group in by_region.items():
            queries: list[dict[str, Any]] = []
            index: dict[str, tuple[Resource, MetricDef]] = {}
            for r in group:
                rt = AWS[r.type]
                values = r.id.split("/", len(rt.dims) - 1) if len(rt.dims) > 1 else [r.id]
                if len(values) != len(rt.dims):
                    warnings.append(f"{r.display}: expected an id like {'/'.join(rt.dims)}")
                    continue
                dims = [{"Name": n, "Value": v} for n, v in zip(rt.dims, values)]
                for m in rt.metrics:
                    qid = f"m{len(index)}"
                    index[qid] = (r, m)
                    queries.append({"Id": qid, "ReturnData": True, "MetricStat": {
                        "Metric": {"Namespace": m.namespace, "MetricName": m.name, "Dimensions": dims},
                        "Period": period, "Stat": m.stat}})
            if not queries:
                continue
            try:
                cw = self.client("cloudwatch", region)
                results: dict[str, dict[str, Any]] = {}
                for i in range(0, len(queries), 500):
                    for page in cw.get_paginator("get_metric_data").paginate(
                            MetricDataQueries=queries[i:i + 500], StartTime=start, EndTime=end, ScanBy="TimestampAscending"):
                        for res in page.get("MetricDataResults", []):
                            acc = results.setdefault(res["Id"], {"Timestamps": [], "Values": [], "Messages": []})
                            acc["Timestamps"] += res.get("Timestamps", [])
                            acc["Values"] += res.get("Values", [])
                            acc["Messages"] += [m.get("Value", "") for m in res.get("Messages", [])]
            except CloudError:
                raise
            except Exception as exc:
                raise self._explain(exc, "reading CloudWatch metrics") from exc
            for qid, (r, m) in index.items():
                res = results.get(qid) or {"Timestamps": [], "Values": []}
                pts = sorted((int(t.timestamp() * 1000), _convert(v, m, period)) for t, v in zip(res["Timestamps"], res["Values"]))
                if pts:
                    series.append(Series(r, m, pts, period))
                else:
                    missing.append({"resource": r.display, "metric": m.label,
                                    "why": m.note or "CloudWatch returned no data for this window"})
        return FetchResult(series, missing, warnings)


def _convert(value: float, m: MetricDef, period: int) -> float:
    v = float(value) * m.scale
    if m.rate == "min":
        v = v * 60 / period
    elif m.rate == "s":
        v = v / period
    return v
