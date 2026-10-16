"""
Which metrics to read for each kind of cloud resource.

Each resource type lists the metrics a performance engineer looks at during a
load test, with how to read them (namespace / name / statistic on AWS, name /
aggregation on Azure) and what they mean to the detectors (`kind`):

  cpu_pct        processor utilisation, %             mem_pct        memory in use, %
  mem_free       memory still available (bytes)       limit_pct      % of a service limit (DTU, sessions, workers)
  credits        CPU credits left on burstable VMs    connections    open connections / clients
  latency        server-side latency, ms              errors         error responses per minute
  requests       requests per minute                  throttles      throttled calls per minute
  capacity       instances / tasks / replicas         network        bytes per second
  iops           I/O operations per second            queue          queued work
  evictions      cache evictions per minute
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    kind: str
    unit: str                    # %, ms, bytes, bytes/s, /min, count, ops/s, cores
    # AWS CloudWatch
    namespace: str = ""
    name: str = ""
    stat: str = "Average"        # Average | Sum | Maximum | Minimum | p95 …
    # Azure Monitor
    az_name: str = ""
    az_agg: str = "Average"      # Average | Total | Maximum | Minimum
    scale: float = 1.0           # multiply the raw value (seconds → ms: 1000)
    rate: str = ""               # "min": a per-period sum shown per minute; "s": per second
    note: str = ""               # shown when the metric has no data


@dataclass(frozen=True)
class ResourceType:
    key: str
    provider: str                # aws | azure
    title: str
    category: str                # vm | containers | data | edge
    metrics: tuple[MetricDef, ...]
    dims: tuple[str, ...] = ()   # AWS dimension names, filled from the resource id ("a/b" for two)
    az_type: str = ""            # Azure resource type, for discovery
    extra: dict = field(default_factory=dict)


def _a(key, label, kind, unit, namespace, name, stat="Average", scale=1.0, rate="", note=""):
    return MetricDef(key, label, kind, unit, namespace=namespace, name=name, stat=stat, scale=scale, rate=rate, note=note)


def _z(key, label, kind, unit, name, agg="Average", scale=1.0, rate="", note=""):
    return MetricDef(key, label, kind, unit, az_name=name, az_agg=agg, scale=scale, rate=rate, note=note)


CW_AGENT = "Needs the CloudWatch agent on the instance"

AWS: dict[str, ResourceType] = {t.key: t for t in [
    ResourceType("ec2", "aws", "EC2 instance", "vm", (
        _a("cpu", "CPU", "cpu_pct", "%", "AWS/EC2", "CPUUtilization"),
        _a("memory", "Memory used", "mem_pct", "%", "CWAgent", "mem_used_percent", note=CW_AGENT),
        _a("credits", "CPU credit balance", "credits", "count", "AWS/EC2", "CPUCreditBalance", note="Burstable (t*) instances only"),
        _a("net_in", "Network in", "network", "bytes/s", "AWS/EC2", "NetworkIn", "Sum", rate="s"),
        _a("net_out", "Network out", "network", "bytes/s", "AWS/EC2", "NetworkOut", "Sum", rate="s"),
        _a("status", "Status check failed", "errors", "count", "AWS/EC2", "StatusCheckFailed", "Maximum"),
    ), dims=("InstanceId",)),
    ResourceType("asg", "aws", "Auto Scaling group", "vm", (
        _a("cpu", "CPU (group average)", "cpu_pct", "%", "AWS/EC2", "CPUUtilization"),
        _a("in_service", "Instances in service", "capacity", "count", "AWS/AutoScaling", "GroupInServiceInstances",
           note="Turn on group metrics collection for the Auto Scaling group"),
        _a("desired", "Desired capacity", "capacity", "count", "AWS/AutoScaling", "GroupDesiredCapacity"),
    ), dims=("AutoScalingGroupName",)),
    ResourceType("ecs", "aws", "ECS service", "containers", (
        _a("cpu", "CPU", "cpu_pct", "%", "AWS/ECS", "CPUUtilization"),
        _a("memory", "Memory used", "mem_pct", "%", "AWS/ECS", "MemoryUtilization"),
        _a("tasks", "Running tasks", "capacity", "count", "ECS/ContainerInsights", "RunningTaskCount",
           note="Needs Container Insights on the cluster"),
    ), dims=("ClusterName", "ServiceName")),
    ResourceType("eks", "aws", "EKS cluster", "containers", (
        _a("cpu", "Node CPU", "cpu_pct", "%", "ContainerInsights", "node_cpu_utilization", note="Needs Container Insights"),
        _a("memory", "Node memory", "mem_pct", "%", "ContainerInsights", "node_memory_utilization", note="Needs Container Insights"),
        _a("nodes", "Nodes", "capacity", "count", "ContainerInsights", "cluster_node_count", note="Needs Container Insights"),
        _a("pod_cpu", "Pod CPU", "cpu_pct", "%", "ContainerInsights", "pod_cpu_utilization", note="Needs Container Insights"),
    ), dims=("ClusterName",)),
    ResourceType("rds", "aws", "RDS database instance", "data", (
        _a("cpu", "CPU", "cpu_pct", "%", "AWS/RDS", "CPUUtilization"),
        _a("connections", "Connections", "connections", "count", "AWS/RDS", "DatabaseConnections"),
        _a("free_memory", "Freeable memory", "mem_free", "bytes", "AWS/RDS", "FreeableMemory"),
        _a("read_latency", "Read latency", "latency", "ms", "AWS/RDS", "ReadLatency", scale=1000),
        _a("write_latency", "Write latency", "latency", "ms", "AWS/RDS", "WriteLatency", scale=1000),
        _a("read_iops", "Read IOPS", "iops", "ops/s", "AWS/RDS", "ReadIOPS"),
        _a("write_iops", "Write IOPS", "iops", "ops/s", "AWS/RDS", "WriteIOPS"),
        _a("queue", "Disk queue depth", "queue", "count", "AWS/RDS", "DiskQueueDepth"),
        _a("credits", "CPU credit balance", "credits", "count", "AWS/RDS", "CPUCreditBalance", note="Burstable (db.t*) instances only"),
    ), dims=("DBInstanceIdentifier",)),
    ResourceType("aurora", "aws", "Aurora cluster", "data", (
        _a("cpu", "CPU", "cpu_pct", "%", "AWS/RDS", "CPUUtilization"),
        _a("connections", "Connections", "connections", "count", "AWS/RDS", "DatabaseConnections"),
        _a("free_memory", "Freeable memory", "mem_free", "bytes", "AWS/RDS", "FreeableMemory"),
        _a("commit_latency", "Commit latency", "latency", "ms", "AWS/RDS", "CommitLatency"),
        _a("deadlocks", "Deadlocks", "errors", "/s", "AWS/RDS", "Deadlocks"),
    ), dims=("DBClusterIdentifier",)),
    ResourceType("elasticache", "aws", "ElastiCache node", "data", (
        _a("cpu", "CPU", "cpu_pct", "%", "AWS/ElastiCache", "CPUUtilization"),
        _a("engine_cpu", "Engine CPU", "cpu_pct", "%", "AWS/ElastiCache", "EngineCPUUtilization"),
        _a("memory", "Memory used", "mem_pct", "%", "AWS/ElastiCache", "DatabaseMemoryUsagePercentage"),
        _a("connections", "Connections", "connections", "count", "AWS/ElastiCache", "CurrConnections"),
        _a("evictions", "Evictions", "evictions", "/min", "AWS/ElastiCache", "Evictions", "Sum", rate="min"),
    ), dims=("CacheClusterId",)),
    ResourceType("alb", "aws", "Application Load Balancer", "edge", (
        _a("requests", "Requests", "requests", "/min", "AWS/ApplicationELB", "RequestCount", "Sum", rate="min"),
        _a("response_time", "Target response time", "latency", "ms", "AWS/ApplicationELB", "TargetResponseTime", scale=1000),
        _a("response_p95", "Target response time p95", "latency", "ms", "AWS/ApplicationELB", "TargetResponseTime", "p95", scale=1000),
        _a("target_5xx", "Target 5xx", "errors", "/min", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "Sum", rate="min"),
        _a("elb_5xx", "Load balancer 5xx", "errors", "/min", "AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", "Sum", rate="min"),
        _a("connections", "Active connections", "connections", "count", "AWS/ApplicationELB", "ActiveConnectionCount", "Sum"),
        _a("rejected", "Rejected connections", "errors", "/min", "AWS/ApplicationELB", "RejectedConnectionCount", "Sum", rate="min"),
    ), dims=("LoadBalancer",)),
    ResourceType("nlb", "aws", "Network Load Balancer", "edge", (
        _a("flows", "Active flows", "connections", "count", "AWS/NetworkELB", "ActiveFlowCount"),
        _a("new_flows", "New flows", "requests", "/min", "AWS/NetworkELB", "NewFlowCount", "Sum", rate="min"),
        _a("resets", "Target resets", "errors", "/min", "AWS/NetworkELB", "TCP_Target_Reset_Count", "Sum", rate="min"),
    ), dims=("LoadBalancer",)),
    ResourceType("apigw", "aws", "API Gateway (REST)", "edge", (
        _a("requests", "Requests", "requests", "/min", "AWS/ApiGateway", "Count", "Sum", rate="min"),
        _a("latency", "Latency", "latency", "ms", "AWS/ApiGateway", "Latency"),
        _a("latency_p95", "Latency p95", "latency", "ms", "AWS/ApiGateway", "Latency", "p95"),
        _a("err5", "5xx errors", "errors", "/min", "AWS/ApiGateway", "5XXError", "Sum", rate="min"),
        _a("err4", "4xx errors", "errors", "/min", "AWS/ApiGateway", "4XXError", "Sum", rate="min"),
    ), dims=("ApiName",)),
    ResourceType("lambda", "aws", "Lambda function", "edge", (
        _a("invocations", "Invocations", "requests", "/min", "AWS/Lambda", "Invocations", "Sum", rate="min"),
        _a("duration", "Duration", "latency", "ms", "AWS/Lambda", "Duration"),
        _a("duration_p95", "Duration p95", "latency", "ms", "AWS/Lambda", "Duration", "p95"),
        _a("errors", "Errors", "errors", "/min", "AWS/Lambda", "Errors", "Sum", rate="min"),
        _a("throttles", "Throttles", "throttles", "/min", "AWS/Lambda", "Throttles", "Sum", rate="min"),
        _a("concurrency", "Concurrent executions", "capacity", "count", "AWS/Lambda", "ConcurrentExecutions", "Maximum"),
    ), dims=("FunctionName",)),
]}

_VM_AZ = (
    _z("cpu", "CPU", "cpu_pct", "%", "Percentage CPU"),
    _z("free_memory", "Available memory", "mem_free", "bytes", "Available Memory Bytes"),
    _z("credits", "CPU credits remaining", "credits", "count", "CPU Credits Remaining", note="Burstable (B-series) VMs only"),
    _z("net_in", "Network in", "network", "bytes/s", "Network In Total", "Total", rate="s"),
    _z("net_out", "Network out", "network", "bytes/s", "Network Out Total", "Total", rate="s"),
    _z("disk_read", "Disk reads", "iops", "ops/s", "Disk Read Operations/Sec"),
    _z("disk_write", "Disk writes", "iops", "ops/s", "Disk Write Operations/Sec"),
)

AZURE: dict[str, ResourceType] = {t.key: t for t in [
    ResourceType("vm", "azure", "Virtual machine", "vm", _VM_AZ, az_type="Microsoft.Compute/virtualMachines"),
    ResourceType("vmss", "azure", "VM scale set", "vm", _VM_AZ, az_type="Microsoft.Compute/virtualMachineScaleSets"),
    ResourceType("aks", "azure", "AKS cluster", "containers", (
        _z("cpu", "Node CPU", "cpu_pct", "%", "node_cpu_usage_percentage"),
        _z("memory", "Node memory (working set)", "mem_pct", "%", "node_memory_working_set_percentage"),
    ), az_type="Microsoft.ContainerService/managedClusters"),
    ResourceType("containerapp", "azure", "Container App", "containers", (
        _z("cpu", "CPU", "cpu_cores", "cores", "UsageNanoCores", scale=1e-9),
        _z("memory", "Memory (working set)", "mem_bytes", "bytes", "WorkingSetBytes"),
        _z("requests", "Requests", "requests", "/min", "Requests", "Total", rate="min"),
        _z("replicas", "Replicas", "capacity", "count", "Replicas", "Maximum"),
    ), az_type="Microsoft.App/containerApps"),
    ResourceType("appservice", "azure", "App Service app", "edge", (
        _z("requests", "Requests", "requests", "/min", "Requests", "Total", rate="min"),
        _z("http5xx", "Server errors (5xx)", "errors", "/min", "Http5xx", "Total", rate="min"),
        _z("response_time", "Response time", "latency", "ms", "HttpResponseTime", scale=1000),
        _z("memory", "Memory (working set)", "mem_bytes", "bytes", "MemoryWorkingSet"),
        _z("connections", "Connections", "connections", "count", "AppConnections"),
    ), az_type="Microsoft.Web/sites"),
    ResourceType("appplan", "azure", "App Service plan", "edge", (
        _z("cpu", "CPU", "cpu_pct", "%", "CpuPercentage"),
        _z("memory", "Memory used", "mem_pct", "%", "MemoryPercentage"),
        _z("queue", "HTTP queue length", "queue", "count", "HttpQueueLength"),
    ), az_type="Microsoft.Web/serverfarms"),
    ResourceType("sqldb", "azure", "Azure SQL database", "data", (
        _z("cpu", "CPU", "cpu_pct", "%", "cpu_percent"),
        _z("dtu", "DTU used", "limit_pct", "%", "dtu_consumption_percent", note="DTU-based databases only"),
        _z("data_io", "Data I/O", "limit_pct", "%", "physical_data_read_percent"),
        _z("log_io", "Log I/O", "limit_pct", "%", "log_write_percent"),
        _z("sessions", "Sessions", "limit_pct", "%", "sessions_percent"),
        _z("workers", "Workers", "limit_pct", "%", "workers_percent"),
        _z("deadlocks", "Deadlocks", "errors", "/min", "deadlock", "Total", rate="min"),
        _z("conn_failed", "Failed connections", "errors", "/min", "connection_failed", "Total", rate="min"),
    ), az_type="Microsoft.Sql/servers/databases"),
    ResourceType("postgres", "azure", "PostgreSQL flexible server", "data", (
        _z("cpu", "CPU", "cpu_pct", "%", "cpu_percent"),
        _z("memory", "Memory used", "mem_pct", "%", "memory_percent"),
        _z("connections", "Active connections", "connections", "count", "active_connections"),
        _z("iops", "IOPS used", "limit_pct", "%", "disk_iops_consumed_percentage"),
    ), az_type="Microsoft.DBforPostgreSQL/flexibleServers"),
    ResourceType("mysql", "azure", "MySQL flexible server", "data", (
        _z("cpu", "CPU", "cpu_pct", "%", "cpu_percent"),
        _z("memory", "Memory used", "mem_pct", "%", "memory_percent"),
        _z("connections", "Active connections", "connections", "count", "active_connections"),
    ), az_type="Microsoft.DBforMySQL/flexibleServers"),
    ResourceType("redis", "azure", "Azure Cache for Redis", "data", (
        _z("cpu", "CPU", "cpu_pct", "%", "percentProcessorTime"),
        _z("server_load", "Server load", "limit_pct", "%", "serverLoad"),
        _z("memory", "Memory used", "mem_pct", "%", "usedmemorypercentage"),
        _z("connections", "Connected clients", "connections", "count", "connectedclients", "Maximum"),
        _z("evictions", "Evicted keys", "evictions", "/min", "evictedkeys", "Total", rate="min"),
    ), az_type="Microsoft.Cache/redis"),
    ResourceType("appgw", "azure", "Application Gateway", "edge", (
        _z("requests", "Requests", "requests", "/min", "TotalRequests", "Total", rate="min"),
        _z("failed", "Failed requests", "errors", "/min", "FailedRequests", "Total", rate="min"),
        _z("total_time", "Total time", "latency", "ms", "ApplicationGatewayTotalTime"),
        _z("connections", "Current connections", "connections", "count", "CurrentConnections", "Total"),
        _z("unhealthy", "Unhealthy hosts", "errors", "count", "UnhealthyHostCount"),
    ), az_type="Microsoft.Network/applicationGateways"),
    ResourceType("lb", "azure", "Load Balancer", "edge", (
        _z("snat", "SNAT connections", "connections", "count", "SnatConnectionCount", "Total"),
        _z("health", "Backend availability", "health_pct", "%", "DipAvailability"),
    ), az_type="Microsoft.Network/loadBalancers"),
]}

TYPES = {**{f"aws:{k}": v for k, v in AWS.items()}, **{f"azure:{k}": v for k, v in AZURE.items()}}
CATEGORIES = {"vm": "Virtual machines", "containers": "Containers", "data": "Databases and caches",
              "edge": "Load balancers and app platforms"}


def resource_type(provider: str, key: str) -> ResourceType:
    t = TYPES.get(f"{provider}:{key}")
    if not t:
        raise KeyError(f"Unknown {provider} resource type {key!r}")
    return t


def catalog() -> list[dict]:
    """What the UI offers: every resource type with its metrics."""
    return [{"provider": t.provider, "type": t.key, "title": t.title, "category": t.category,
             "metrics": [{"key": m.key, "label": m.label, "unit": m.unit, "note": m.note} for m in t.metrics]}
            for t in TYPES.values()]
