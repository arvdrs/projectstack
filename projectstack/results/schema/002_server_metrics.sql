-- Server metrics from AWS CloudWatch / Azure Monitor for the window of an analysed run.
-- One row per resource, metric and minute (or the coarser period the cloud still keeps).
CREATE TABLE IF NOT EXISTS {db}.server_metrics
(
    run_id        LowCardinality(String),
    provider      LowCardinality(String),      -- aws | azure
    resource_type LowCardinality(String),      -- ec2, rds, alb … / vm, sqldb, appgw …
    resource_id   String,
    resource_name String,
    role          LowCardinality(String),
    metric        LowCardinality(String),      -- preset key: cpu, connections, response_time …
    label         String,
    kind          LowCardinality(String),      -- cpu_pct, mem_pct, latency …
    unit          LowCardinality(String),
    period_s      UInt32,
    ts            DateTime64(3, 'UTC'),
    value         Float64,
    fetched_at    DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(fetched_at)
ORDER BY (run_id, resource_id, metric, ts)
TTL fetched_at + INTERVAL {retention_days} DAY;
