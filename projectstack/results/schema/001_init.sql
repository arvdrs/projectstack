-- ProjectStack results analysis — initial schema.
-- {db} and {retention_days} are filled in by projectstack.results.db when the migration runs.

-- One row per JMeter sample of every analysed run
CREATE TABLE IF NOT EXISTS {db}.samples
(
    run_id           LowCardinality(String),
    ts               DateTime64(3, 'UTC') CODEC(Delta, ZSTD),   -- sample start (JTL timeStamp, epoch ms)
    elapsed          UInt32 CODEC(T64, ZSTD),                   -- response time, ms
    latency          UInt32 CODEC(T64, ZSTD),
    connect          UInt32 CODEC(T64, ZSTD),
    idle_time        UInt32 CODEC(T64, ZSTD),
    label            LowCardinality(String),
    response_code    LowCardinality(String),
    response_message String CODEC(ZSTD),
    failure_message  String CODEC(ZSTD),
    thread_name      LowCardinality(String),
    data_type        LowCardinality(String),
    success          Bool,
    bytes            UInt64 CODEC(T64, ZSTD),
    sent_bytes       UInt64 CODEC(T64, ZSTD),
    grp_threads      UInt32 CODEC(T64, ZSTD),
    all_threads      UInt32 CODEC(T64, ZSTD),
    url              String CODEC(ZSTD),
    hostname         LowCardinality(String),
    -- request: an HTTP (or other) sampler; transaction: a Transaction Controller parent
    -- sample; subresult: a redirect / embedded-resource child of a request
    kind             Enum8('request' = 1, 'transaction' = 2, 'subresult' = 3),
    ingested_at      DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ingested_at)
ORDER BY (run_id, label, ts)
TTL ingested_at + INTERVAL {retention_days} DAY;

-- Run registry: one row per analysed run (latest version wins)
CREATE TABLE IF NOT EXISTS {db}.runs
(
    run_id        String,
    created_at    DateTime64(3, 'UTC'),
    scenario      LowCardinality(String),
    tag           String,
    source_file   String,
    source_format LowCardinality(String),
    started_at    DateTime64(3, 'UTC'),
    ended_at      DateTime64(3, 'UTC'),
    samples       UInt64,
    peak_threads  UInt32,
    status        LowCardinality(String),   -- ingesting | ready | failed
    verdict       LowCardinality(String),   -- unknown until SLA detectors run
    summary       String,                   -- JSON: overall request stats, for listings
    meta          String,                   -- JSON: same content as meta.json
    version       UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY run_id;

-- Kind of each label in a run (decided once per label, so stats never double count)
CREATE TABLE IF NOT EXISTS {db}.run_labels
(
    run_id   String,
    label    String,
    kind     Enum8('request' = 1, 'transaction' = 2, 'subresult' = 3),
    samples  UInt64,
    first_ts DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree
ORDER BY (run_id, label);
