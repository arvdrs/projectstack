/**
 * ProjectStack API client
 * Typed wrappers around every backend endpoint.
 */

const API_BASE = '/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RequestPreview {
  index: number
  method: string
  url: string
  status_code: number
}

export interface SessionInfo {
  session_id: string
  filename: string
  request_count: number
  requests: RequestPreview[]
  source_format?: string
  domains: string[]
  transaction_names?: Record<string, string>   // stored in recordings made with the proxy recorder
}

export interface CorrelatedValue {
  var_name: string
  source_request_index: number
  extract_from: string
  extract_key: string
  extract_value: string
  used_in_requests: number[]
  detection_pass: string
  reason: string
  extract_value_2?: string        // HAR2 value (only present in compare endpoint)
  is_confirmed_dynamic?: boolean  // true if confirmed by HAR comparison
  extractor?: string              // json | css | header | header_regex | boundary | regex | jwt_claim | cookie_header | function
  extractor_config?: Record<string, unknown>
  confidence_score?: number
  confidence_label?: 'High' | 'Medium' | 'Low'
}

export interface ReplayRequestResult {
  index: number
  method: string
  path: string
  recorded_status: number
  replay_status: string | null
  ok: boolean
  reason: string
  hints?: string[]
  response_snippet?: string
}

export interface ReplayAdded {
  var_name: string
  source_request_index: number
  used_in_requests: number[]
  recorded: string
  replayed: string
}

export interface ReplayIteration {
  iteration: number
  passed: number
  failed: number
  added: ReplayAdded[]
  repaired: { var_name: string; from: string; to: string }[]
  broken: { var_name: string; source_request_index: number; reason: string }[]
  verified_count: number
  static_count: number
  rolled_back?: boolean
}

export interface ReplayFixResult {
  values: CorrelatedValue[]
  iterations: ReplayIteration[]
  unverified_changes: boolean
  final: {
    results: ReplayRequestResult[]
    passed: number
    failed: number
    verified: string[]
    static: string[]
    repaired: { var_name: string; from: string; to: string }[]
    broken: { var_name: string; source_request_index: number; reason: string }[]
    added: ReplayAdded[]
  }
}

export interface RulePack {
  id: string
  name: string
  description: string
  source: 'builtin' | 'user'
  rule_count: number
  rules: { id?: string; name?: string; type: string; var: string }[]
}

/** How far to trust an auto-correlation result before replaying it. */
export interface CorrelationAssessment {
  score: number
  label: 'High' | 'Medium' | 'Low'
  headline: string
  factors: { impact: 'positive' | 'negative' | 'neutral'; text: string }[]
  counts: {
    correlations: number; high: number; medium: number; low: number; confirmed: number
    switched_off: number; same_tokens: number; untraced: number; missing_bodies: number
  }
}

export interface CorrelateResult {
  count: number
  values: CorrelatedValue[]
  assessment?: CorrelationAssessment
  comparison?: { paired: number; total: number; second_total: number; static_in_both: number }
}

export type AssertionType = 'status_code' | 'response_contains' | 'response_regex' | 'response_time_ms'

export interface AssertionConfig {
  id: string
  type: AssertionType
  value: string
  enabled: boolean
}

export interface DynamicValue {
  var_name: string
  extract_value: string
  role: 'used' | 'extracted'
}

export interface RequestDetail {
  index: number
  method: string
  url: string
  path: string
  status_code: number
  headers: Record<string, string>
  body: string | null
  body_mime_type: string | null
  response_status: number
  response_headers: Record<string, string>
  response_body: string | null
  response_mime_type: string | null
  dynamic_values: DynamicValue[]
}

export type ThreadGroupType = 'standard' | 'stepping' | 'concurrency' | 'ultimate'

export interface UltimateRow {
  threads: number
  initial_delay: number
  startup: number
  hold: number
  shutdown: number
}

/** Thread group of the generated script. Only the fields of the chosen type are used. */
export interface ThreadGroupConfig {
  type: ThreadGroupType
  // stepping
  threads: number
  initial_delay: number
  start_burst: number
  step_users: number
  step_period: number
  step_rampup: number
  stop_users: number
  stop_period: number
  hold: number            // stepping + concurrency
  // concurrency
  target: number
  ramp_up: number
  steps: number
  iterations: number      // 0 = unlimited
  // ultimate
  rows: UltimateRow[]
}

export type SlaMetric = 'avg' | 'p90' | 'p95' | 'p99'

/** User-defined SLA. There is none by default — runs are then never judged pass/fail. */
export interface SlaConfig {
  response_time_metric: SlaMetric
  response_time_ms: number | null
  transaction_targets: Record<string, number | null>
  error_rate_pct: number | null
  throughput_rps: number | null
}

export interface SlaCheck {
  kind: 'response_time' | 'error_rate' | 'throughput'
  label: string | null
  metric: string
  unit: 'ms' | '%' | 'req/s'
  target: number
  actual: number | null
  passed: boolean
  note?: string
}

export type SlaResult =
  | { defined: false }
  | {
      defined: true
      config: SlaConfig
      targets: string[]
      checks: SlaCheck[]
      passed: boolean
      passed_count: number
      failed_count: number
    }

export interface JmeterInfo {
  found: boolean
  path: string | null
  home: string | null
  configured_path: string | null
  configured_valid: boolean
  custom_thread_groups: boolean
}

export interface GenerateParams {
  test_name: string
  threads: number
  rampup: number
  loops: number
  base_url?: string
  base_urls?: string[]
  test_vars?: { name: string; value: string }[]
  test_data_files?: { filename: string; data: string; accessMethod: string; variables?: string[] }[]
  include_view_tree?: boolean
  grafana_url?: string
  no_transactions?: boolean
  selected_var_names?: string[] | null
  selected_request_indices?: number[] | null
  custom_assertions?: Record<string, AssertionConfig[]> | null
  custom_transaction_names?: Record<string, string> | null
  request_order?: number[] | null
  include_think_times?: boolean
  load_profile?: 'smoke' | 'soak' | 'spike' | 'stress' | 'custom'
  infinite_loops?: boolean
  duration_seconds?: number
  thread_group?: ThreadGroupConfig
}

// HAR Analysis
export interface AuthFlow {
  has_auth: boolean
  auth_endpoints: { index: number; url: string; method: string }[]
  bearer_request_count: number
  api_key_request_count: number
}

export interface HarAnalysis {
  total: number
  methods: Record<string, number>
  statuses: Record<string, number>
  protocols: Record<string, number>
  domains: Record<string, number>
  avg_response_ms: number
  max_response_ms: number
  error_count: number
  auth_flow: AuthFlow
  duplicates: { index: number; duplicate_of: number; method: string; url: string }[]
  rate_limited: { index: number; url: string; retry_after: string }[]
  sensitive_findings: SensitiveFinding[]
  missing_response_bodies?: number[]
}

export interface SensitiveFinding {
  request_index: number
  url: string
  field: string
  type: string
  count: number
  sample: string
}

export interface GenerateResult {
  load_config?: { threads: number; rampup: number; loops: number; duration_seconds: number }
  thread_group?: Partial<ThreadGroupConfig> & { type: ThreadGroupType }
  transactions?: { name: string; request_count: number }[]
  jmx: string
  length: number
  sampler_count?: number
  transaction_count?: number
  validation_errors?: { code: string; message: string }[]
}

export interface RunParams {
  // Overrides apply to the standard Thread Group only
  threads?: number
  rampup?: number
  loops?: number              // -1 = loop until the duration ends
  duration_seconds?: number   // 0 = run by loop count
  sla?: SlaConfig | null      // defined in the Generate step; null = no SLA
}

export interface TimelinePoint {
  second: number
  requests: number
  errors: number
  avg_ms: number
}

export interface LabelMetrics {
  total: number
  passed: number
  failed: number
  error_rate: number
  avg_ms: number
  p90_ms?: number
  p95_ms: number
  p99_ms?: number
}

export interface TestResults {
  total_requests: number
  passed: number
  failed: number
  error_rate: number
  throughput: number
  avg_ms: number
  min_ms: number
  max_ms: number
  p90_ms: number
  p95_ms: number
  duration_sec: number
  by_label: Record<string, LabelMetrics>
  timeline: TimelinePoint[]
  html_report_url?: string | null
  sla?: SlaResult
}

export interface Settings {
  ai_provider: string
  anthropic_api_key: string
  openai_api_key: string
  groq_api_key: string
  gemini_api_key: string
  ollama_url: string
  ollama_model: string
  jmeter_path: string
  default_threads: string
  default_rampup: string
  default_loops: string
  grafana_url: string
  gmail_address: string
  gmail_app_password: string
  aws_profile?: string
  aws_region?: string
  aws_access_key_id?: string
  aws_secret_access_key?: string
  aws_session_token?: string
  azure_subscription_id?: string
  azure_tenant_id?: string
  azure_client_id?: string
  azure_client_secret?: string
  cloud_auto_fetch?: string
}

export interface ProviderInfo {
  label: string
  subtitle: string
  tier: string
  description: string
  key_field: string | null
  key_placeholder: string | null
  model: string
}

export interface SessionStatus {
  session_id: string
  filename: string
  request_count: number
  correlated_count: number
  has_jmx: boolean
  has_results: boolean
  steps_complete: Record<string, boolean>
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      // ignore
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// API Functions
// ---------------------------------------------------------------------------

export async function uploadHar(file: File): Promise<SessionInfo> {
  const form = new FormData()
  form.append('file', file)
  return fetchJson<SessionInfo>(`${API_BASE}/upload`, {
    method: 'POST',
    body: form,
  })
}

export async function uploadJmx(file: File): Promise<SessionInfo> {
  const form = new FormData()
  form.append('file', file)
  return fetchJson<SessionInfo>(`${API_BASE}/upload-jmx`, {
    method: 'POST',
    body: form,
  })
}

export async function removeNoise(sessionId: string): Promise<{ noise_indices: number[]; reasons: Record<string, string> }> {
  return fetchJson(`${API_BASE}/remove-noise/${sessionId}`, { method: 'POST' })
}

export async function filterByBaseUrl(
  sessionId: string,
  baseUrls: string | string[]
): Promise<{ request_count: number; requests: RequestPreview[]; transaction_names?: Record<string, string> }> {
  const urls = Array.isArray(baseUrls) ? baseUrls : [baseUrls]
  const q = new URLSearchParams()
  for (const u of urls) q.append('base_url', u)
  return fetchJson(`${API_BASE}/filter/${sessionId}?${q}`, {
    method: 'POST',
  })
}

/** Auto-correlation: the 5-step rule-based engine. Never calls an AI provider. */
export async function correlate(sessionId: string): Promise<CorrelateResult> {
  return fetchJson<CorrelateResult>(`${API_BASE}/correlate/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ use_ai: false }),
  })
}

/** Auto-correlation with a second recording: values that differ between the two are confirmed dynamic. */
export async function correlateCompare(sessionId: string, secondSessionId: string): Promise<CorrelateResult> {
  return fetchJson<CorrelateResult>(`${API_BASE}/correlate-compare/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ second_session_id: secondSessionId, use_ai: false }),
  })
}

/** AI auto-correlation: adds what the AI finds on top of the session's saved correlations. */
export async function aiCorrelate(
  sessionId: string,
  provider?: string,
): Promise<CorrelateResult & { added: CorrelatedValue[]; provider: string }> {
  return fetchJson(`${API_BASE}/ai-correlate/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: provider ?? null }),
  })
}

export interface CorrelationRuleRequest {
  var_name: string
  source_request_index: number
  extract_from: 'body' | 'header'
  extract_type: 'json' | 'regex' | 'begin_end'
  extract_key?: string
  extract_pattern?: string
  extract_begin?: string
  extract_end?: string
}

export async function applyRule(
  sessionId: string,
  rule: CorrelationRuleRequest
): Promise<CorrelatedValue> {
  return fetchJson<CorrelatedValue>(`${API_BASE}/apply-rule/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rule),
  })
}

/** Persist the edited correlation list (renames, removals, custom rules) to the session. */
export async function saveCorrelations(sessionId: string, values: CorrelatedValue[]): Promise<CorrelateResult> {
  return fetchJson<CorrelateResult>(`${API_BASE}/correlations/${sessionId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  })
}

/** Replay the script with JMeter (1 user × 1 iteration), auto-fix correlations, repeat. */
export async function replayFix(
  sessionId: string,
  opts: { max_iterations?: number; selected_request_indices?: number[] | null } = {},
): Promise<ReplayFixResult> {
  return fetchJson<ReplayFixResult>(`${API_BASE}/replay-fix/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_iterations: opts.max_iterations ?? 5, selected_request_indices: opts.selected_request_indices ?? null }),
  })
}

export async function getRulePacks(): Promise<{ packs: RulePack[] }> {
  return fetchJson(`${API_BASE}/rule-packs`)
}

export async function uploadRulePack(pack: unknown): Promise<{ ok: boolean; id: string }> {
  return fetchJson(`${API_BASE}/rule-packs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pack),
  })
}

export async function deleteRulePack(id: string): Promise<{ ok: boolean }> {
  return fetchJson(`${API_BASE}/rule-packs/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function getProviders(): Promise<Record<string, ProviderInfo>> {
  return fetchJson<Record<string, ProviderInfo>>(`${API_BASE}/providers`)
}

export async function generate(
  sessionId: string,
  params: GenerateParams
): Promise<GenerateResult> {
  return fetchJson<GenerateResult>(`${API_BASE}/generate/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
}

export async function downloadJmx(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/download/${sessionId}`)
  if (!res.ok) throw new Error('Download failed')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : 'test.jmx'
  a.href = url
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export interface LivePoint {
  t: number
  rps: number
  errors: number
  avg_ms: number | null
  p95_ms: number | null
  users: number | null
}

export interface LiveSnapshot {
  status: 'starting' | 'running' | 'stopping' | 'finishing' | 'done' | 'failed'
  error: string
  elapsed_s: number
  test_elapsed_s: number
  config: { threads: number; rampup: number; loops: number; duration_seconds: number }
  expected_requests: number | null
  totals: {
    requests: number
    errors: number
    error_pct: number
    avg_ms: number
    p95_ms: number
    rps_now: number
    avg_ms_now: number
    active_users: number
  }
  timeline: LivePoint[]
  labels: { label: string; requests: number; errors: number; avg_ms: number; p95_ms: number; max_ms: number }[]
  recent: { t: number; label: string; code: string; ms: number; ok: boolean; thread: string }[]
}

export async function getRunLive(sessionId: string): Promise<LiveSnapshot> {
  return fetchJson<LiveSnapshot>(`${API_BASE}/run-live/${sessionId}`)
}

export async function stopRun(sessionId: string, graceful = true): Promise<{ ok: boolean }> {
  return fetchJson(`${API_BASE}/run-stop/${sessionId}?graceful=${graceful}`, { method: 'POST' })
}

export async function runTest(
  sessionId: string,
  params: RunParams
): Promise<TestResults> {
  return fetchJson<TestResults>(`${API_BASE}/run/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
}

export async function getSettings(): Promise<Settings> {
  return fetchJson<Settings>(`${API_BASE}/settings`)
}

export async function updateSettings(
  settings: Partial<Settings>
): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`${API_BASE}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
}

export async function getSession(sessionId: string): Promise<SessionStatus> {
  return fetchJson<SessionStatus>(`${API_BASE}/session/${sessionId}`)
}

export async function downloadReport(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/report/${sessionId}`)
  if (!res.ok) throw new Error('Report not available — run a test first')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : 'report.html'
  a.href = url
  document.body.appendChild(a); a.click()
  document.body.removeChild(a); URL.revokeObjectURL(url)
}

export async function getReportStatus(sessionId: string): Promise<{ available: boolean; url: string | null }> {
  return fetchJson(`${API_BASE}/report-status/${sessionId}`)
}

export async function analyzeReport(sessionId: string): Promise<{ summary: string }> {
  return fetchJson(`${API_BASE}/analyze-report/${sessionId}`)
}

export type InsightSeverity = 'critical' | 'warning' | 'info' | 'good'

export interface ReportInsights {
  verdict: 'pass' | 'at_risk' | 'fail' | 'no_sla'   // judged only against a user-defined SLA
  score: number | null
  sla: SlaResult
  headline: string
  source: 'jmeter_html' | 'jtl_summary'
  note?: string
  window: { start: string | null; end: string | null }
  kpis: {
    samples: number; error_pct: number; throughput: number | null
    mean_ms: number | null; median_ms: number | null; p90_ms: number | null; p95_ms: number | null; p99_ms: number | null
    min_ms: number | null; max_ms: number | null
    apdex: number | null; apdex_rating: string | null; apdex_t_ms: number | null; apdex_f_ms: number | null
    peak_threads: number | null
  }
  findings: { severity: InsightSeverity; title: string; detail: string; metric: string | null }[]
  transactions: {
    name: string; samples: number; errors: number; error_pct: number
    mean_ms: number | null; median_ms: number | null; p90_ms: number | null; p95_ms: number | null; p99_ms: number | null
    max_ms: number | null; throughput: number | null; status: 'ok' | 'warn' | 'fail' | 'none'
  }[]
  errors: { type: string; count: number; pct_of_errors: number; pct_of_samples: number }[]
  ai_summary?: string
  ai_error?: string
}

export async function getReportInsights(sessionId: string, ai = false): Promise<ReportInsights> {
  return fetchJson(`${API_BASE}/report-insights/${sessionId}${ai ? '?ai=true' : ''}`)
}

export async function downloadInsightsReport(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/insights-report/${sessionId}`)
  if (!res.ok) throw new Error('Insights report not available — run a test first')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const match = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)
  a.download = match ? match[1] : 'insights.html'
  a.href = url
  document.body.appendChild(a); a.click()
  document.body.removeChild(a); URL.revokeObjectURL(url)
}

export async function getGmailStatus(): Promise<{ configured: boolean; address: string | null }> {
  return fetchJson(`${API_BASE}/gmail/status`)
}

export async function verifyGmail(): Promise<{ ok: boolean; address: string }> {
  return fetchJson(`${API_BASE}/gmail/verify`, { method: 'POST' })
}

export interface ShareReportParams {
  to: string
  cc?: string
  subject?: string
  note?: string
  include_ai?: boolean
  attach_jmeter_report?: boolean
  attach_insights_html?: boolean
}

export interface ShareReportResult {
  ok: boolean; from: string; to: string[]; cc: string[]; subject: string; attachments: string[]; skipped: string[]
}

export async function shareReport(sessionId: string, params: ShareReportParams): Promise<ShareReportResult> {
  return fetchJson(`${API_BASE}/share-report/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
}

export interface ParamSuggestion {
  field: string
  value: string
  var_name: string
  occurrences: number
}

export async function suggestParams(sessionId: string): Promise<{ suggestions: ParamSuggestion[] }> {
  return fetchJson(`${API_BASE}/suggest-params/${sessionId}`)
}

export async function getRequestDetail(sessionId: string, index: number): Promise<RequestDetail> {
  return fetchJson<RequestDetail>(`${API_BASE}/request-detail/${sessionId}/${index}`)
}

// ---------------------------------------------------------------------------
// Proxy recorder
// ---------------------------------------------------------------------------

export interface ProxyStartResult {
  ok: boolean
  port?: number
  error?: string
  already_running?: boolean
  chromium_launched?: boolean
  chromium_bin?: string | null
  cert_trusted?: boolean
}

export interface ProxyStartParams {
  start_url?: string
  transaction?: string        // name of the first transaction
  launch_browser?: boolean
}

export interface ProxyStopResult {
  ok: boolean
  error?: string
  session_id?: string
  filename?: string
  request_count?: number
  requests?: RequestPreview[]
  source_format?: string
  domains?: string[]
  transaction_names?: Record<string, string>
}

export interface ProxyStatusResult {
  running: boolean
  port: number
  request_count: number        // everything the browser sent
  kept_count: number           // what will be imported (static files are left out)
  current_transaction: string
  transactions: { name: string; request_count: number }[]
  browser_open: boolean
}

export async function proxyStart(params: ProxyStartParams = {}): Promise<ProxyStartResult> {
  return fetchJson<ProxyStartResult>(`${API_BASE}/proxy/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
}

/** Start a new transaction while recording: requests sent from now on are grouped under it. */
export async function proxyTransaction(name: string): Promise<ProxyStatusResult> {
  return fetchJson<ProxyStatusResult>(`${API_BASE}/proxy/transaction`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export async function downloadHar(sessionId: string): Promise<void> {
  return downloadUrl(`${API_BASE}/har/${sessionId}`, 'recording.har')
}

export async function proxyStop(): Promise<ProxyStopResult> {
  return fetchJson<ProxyStopResult>(`${API_BASE}/proxy/stop`, { method: 'POST' })
}

export async function proxyStatus(): Promise<ProxyStatusResult> {
  return fetchJson<ProxyStatusResult>(`${API_BASE}/proxy/status`)
}

// ---------------------------------------------------------------------------
// Analysis + exports
// ---------------------------------------------------------------------------

export interface ResponseSummary {
  index: number
  method: string
  url: string
  path: string
  status_code: number
  response_mime_type: string | null
  response_body_preview: string | null
  response_body_length: number
  response_headers: Record<string, string>
}

export async function getResponses(
  sessionId: string
): Promise<{ total: number; responses: ResponseSummary[] }> {
  return fetchJson(`${API_BASE}/responses/${sessionId}`)
}

export async function getHarAnalysis(sessionId: string): Promise<HarAnalysis> {
  return fetchJson<HarAnalysis>(`${API_BASE}/har-analysis/${sessionId}`)
}

export async function scanSensitive(sessionId: string): Promise<{ findings: SensitiveFinding[]; total: number }> {
  return fetchJson(`${API_BASE}/scan-sensitive/${sessionId}`)
}

export async function getDuplicates(sessionId: string): Promise<{ duplicates: { index: number; duplicate_of: number; method: string; url: string }[]; total: number }> {
  return fetchJson(`${API_BASE}/duplicates/${sessionId}`)
}

/** Trigger a browser download for a URL that returns a file. */
async function downloadUrl(url: string, fallbackName: string): Promise<void> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Download failed: ${res.statusText}`)
  const blob = await res.blob()
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : fallbackName
  a.href = href
  document.body.appendChild(a); a.click()
  document.body.removeChild(a); URL.revokeObjectURL(href)
}

export async function downloadK6(sessionId: string): Promise<void> {
  return downloadUrl(`${API_BASE}/export-k6/${sessionId}`, 'test_k6.js')
}

export async function downloadCiYaml(sessionId: string, platform: 'github' | 'jenkins' = 'github'): Promise<void> {
  return downloadUrl(`${API_BASE}/export-ci/${sessionId}?platform=${platform}`, platform === 'github' ? 'jmeter-test.yml' : 'Jenkinsfile')
}

export async function downloadZip(sessionId: string): Promise<void> {
  return downloadUrl(`${API_BASE}/export-zip/${sessionId}`, 'projectstack_export.zip')
}

export interface TransactionScope {
  request_order: number[] | null
  selected_request_indices: number[] | null
}
export type TransactionGrouping = { transaction_names: Record<string, string>; group_count: number }

/** Rule-based grouping (time gaps, URL modules, action keywords). */
export async function autoTransactions(sessionId: string, scope: TransactionScope): Promise<TransactionGrouping> {
  return fetchJson(`${API_BASE}/auto-transactions/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scope),
  })
}

/** AI grouping with the provider configured in Settings. */
export async function sortTransactions(sessionId: string, scope: TransactionScope): Promise<TransactionGrouping> {
  return fetchJson(`${API_BASE}/sort-transactions/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scope),
  })
}

export async function getJmeterInfo(): Promise<JmeterInfo> {
  return fetchJson<JmeterInfo>(`${API_BASE}/jmeter-info`)
}

export async function rawConvertHar(file: File, testName?: string): Promise<void> {
  const form = new FormData()
  form.append('file', file)
  const q = new URLSearchParams()
  if (testName) q.set('test_name', testName)
  const res = await fetch(`${API_BASE}/raw-convert?${q}`, { method: 'POST', body: form })
  if (!res.ok) {
    let detail = res.statusText
    try { const body = await res.json(); detail = body.detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : `${file.name.replace(/\.har$/, '')}_raw.jmx`
  a.href = url
  document.body.appendChild(a); a.click()
  document.body.removeChild(a); URL.revokeObjectURL(url)
}

export async function downloadGrafanaDashboard(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/grafana-dashboard/${sessionId}`)
  if (!res.ok) throw new Error('Could not generate Grafana dashboard')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : 'grafana_dashboard.json'
  a.href = url
  document.body.appendChild(a); a.click()
  document.body.removeChild(a); URL.revokeObjectURL(url)
}

// ---------------------------------------------------------------------------
// Results analysis (JTL → ClickHouse → findings, reports, compare, ask)
// ---------------------------------------------------------------------------

export interface ResultsStatus {
  available: boolean
  version?: string
  server?: string
  runs?: number
  message?: string
  hint?: string
}

export type AnalysisJobStatus = 'queued' | 'running' | 'done' | 'failed'

export interface AnalysisJob {
  id: string
  source: string
  status: AnalysisJobStatus
  progress: string[]
  run_id: string | null
  error: string | null
  session_id?: string | null
}

export type RunVerdict = 'pass' | 'at_risk' | 'fail' | 'no_sla'
export type FindingSeverity = 'critical' | 'warning' | 'info'

export interface ResultsRunRow {
  run_id: string
  created_at: string
  scenario: string
  tag: string
  samples: number
  peak_threads: number
  verdict: RunVerdict | 'unknown' | string
  summary: { p95_ms?: number; error_pct?: number; tps?: number; samples?: number }
  start_ms: number
  end_ms: number
}

export interface LabelStats {
  label: string
  samples: number
  avg_ms: number
  p50_ms: number
  p90_ms: number
  p95_ms: number
  p99_ms: number
  max_ms: number
  error_pct: number
  tps: number
}

export interface OverallStats extends Omit<LabelStats, 'label'> {
  min_ms: number
  errors: number
}

export interface TimelinePoint {
  t_offset_s: number
  users: number
  tps: number
  p95_ms: number
  samples: number
  errors: number
  error_pct: number
}

export interface AnalysisFinding {
  n: number
  type: string
  severity: FindingSeverity
  title: string
  detail: string
  start_offset_s?: number
  end_offset_s?: number
  labels: string[]
  evidence: Record<string, unknown>
}

export interface Observations {
  headline: string
  key_points: string[]
  business_impact: string
  recommended_action: string
}

export interface NumberCheckResult { checked: number; unverified: { field: string; text: string }[]; ok: boolean }

export interface AiObservations {
  content: Observations
  provider?: string | null
  prompt?: string | null
  numbers?: NumberCheckResult | null
  source?: string
  note?: string
  warnings?: string[]
}

export interface SlaCheckRow {
  kind: 'response_time' | 'error_rate' | 'throughput'
  label: string | null
  metric: string
  unit: string
  target: number
  actual: number
  passed: boolean
}

export interface BaselineInfo {
  requested: string
  found: boolean
  note?: string
  run_id?: string
  tag?: string
  created_at?: string
  gating?: boolean
  load?: { baseline_users: number; current_users: number; baseline_tps: number; current_tps: number; kind: string; comparable: boolean; reason: string }
  summary?: { compared: number; regressed: number; improved: number; too_few_samples: number }
  thresholds?: { p95_pct: number; p95_ms: number; err_pp: number; min_samples: number; load_pct: number }
}

export interface RunAnalysis {
  run: {
    run_id: string; scenario: string; tag: string; start: string; end: string; duration_s: number
    samples: number; peak_users: number; labels: { request: number; transaction: number; subresult: number }
  }
  steady: { start_offset_s: number; end_offset_s: number; duration_s: number; source: string; note?: string; method?: string }
  verdict: RunVerdict
  sla: { defined: boolean; targets?: string[]; checks?: SlaCheckRow[]; passed?: boolean; failed_count?: number }
  baseline?: BaselineInfo
  overall: { request?: OverallStats; transaction?: OverallStats }
  labels: { request?: LabelStats[]; transaction?: LabelStats[]; subresult?: LabelStats[] }
  timeline: { bucket_s: number; points: TimelinePoint[] }
  errors: { label: string; response_code: string; response_message: string; failure: string; count: number; first_offset_s: number; last_offset_s: number }[]
  findings: AnalysisFinding[]
  skipped: { detector: string; reason: string }[]
  warnings?: string[]
  truncated: Record<string, unknown>
  created_at?: string
  source_file?: string
  has_plan: boolean
  observations: Observations
  observations_ai: AiObservations | null
}

export interface AskQuery { n: number; sql: string; columns: string[]; rows: string[][]; truncated: boolean; seconds: number; error: string }

export interface AskAnswer {
  question: string
  answer: string
  queries: AskQuery[]
  provider: string
  prompt: string
  numbers: NumberCheckResult | null
  stopped: string
  warning?: string | null
}

export interface AnalyzeOptions {
  scenario?: string
  tag?: string
  sla?: Partial<SlaConfig> | null
  steady?: string
  baseline?: string
}

export async function getResultsStatus(): Promise<ResultsStatus> {
  return fetchJson(`${API_BASE}/results/status`)
}

export function analyzeJtl(file: File, opts: AnalyzeOptions = {},
  onProgress?: (sent: number, total: number) => void): Promise<AnalysisJob> {
  const form = new FormData()
  form.append('file', file)
  form.append('scenario', opts.scenario ?? '')
  form.append('tag', opts.tag ?? '')
  form.append('sla', opts.sla ? JSON.stringify(opts.sla) : '')
  form.append('steady', opts.steady ?? '')
  form.append('baseline', opts.baseline ?? '')
  // XMLHttpRequest rather than fetch: result files can be several GB, and only XHR reports upload progress
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/results/analyze`)
    xhr.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total) }
    xhr.onload = () => {
      let body: { detail?: string } & Partial<AnalysisJob> = {}
      try { body = JSON.parse(xhr.responseText) } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as AnalysisJob)
      else reject(new Error(body.detail || `Upload failed (${xhr.status} ${xhr.statusText})`))
    }
    xhr.onerror = () => reject(new Error('Upload failed: the server could not be reached'))
    xhr.onabort = () => reject(new Error('Upload cancelled'))
    xhr.send(form)
  })
}

export async function analyzeSessionRun(sessionId: string, baseline = 'last-green'): Promise<AnalysisJob> {
  return fetchJson(`${API_BASE}/results/analyze-session/${sessionId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ baseline }),
  })
}

export async function getAnalysisJob(jobId: string): Promise<AnalysisJob> {
  return fetchJson(`${API_BASE}/results/jobs/${jobId}`)
}

export async function listResultRuns(limit = 100): Promise<{ runs: ResultsRunRow[] }> {
  return fetchJson(`${API_BASE}/results/runs?limit=${limit}`)
}

export async function getResultRun(runId: string): Promise<RunAnalysis> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}`)
}

export async function aiObservations(runId: string, fresh = false): Promise<AiObservations> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}/observations`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fresh }),
  })
}

export function resultReportUrl(runId: string, audience: 'tech' | 'exec', ai = false): string {
  return `${API_BASE}/results/runs/${encodeURIComponent(runId)}/report?audience=${audience}${ai ? '&ai=true' : ''}`
}

export async function downloadResultReport(runId: string, audience: 'tech' | 'exec', format: 'html' | 'md', ai = false): Promise<void> {
  return downloadUrl(`${resultReportUrl(runId, audience, ai)}&format=${format}&download=true`, `${runId}_${audience}.${format}`)
}

export async function askRun(runId: string, question: string): Promise<AskAnswer> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}/ask`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }),
  })
}

export async function deleteResultRun(runId: string): Promise<{ deleted: string }> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}`, { method: 'DELETE' })
}

// -- per-label detail and synthesis report -----------------------------------

export type LabelStatus = 'critical' | 'attention' | 'healthy' | 'idle'

export interface LabelPoint {
  t_offset_s: number; samples: number; errors: number; tps: number
  p50_ms: number; p95_ms: number; p99_ms: number; avg_ms: number; error_pct: number
}

export interface LabelDetail {
  run_id: string
  label: string
  kind: 'request' | 'transaction' | 'subresult'
  status: LabelStatus
  synthesis: string[]
  steady: OverallStats
  whole_run: OverallStats
  ladder: { p: number; ms: number }[]
  histogram: { bin_ms: number; bins: { from_ms: number; to_ms: number; samples: number }[]; overflow: number }
  share_pct: number
  stddev_ms: number
  timeline: { bucket_s: number; points: LabelPoint[] }
  codes: { code: string; samples: number; failed: number; share_pct: number }[]
  errors: { code: string; message: string; count: number; first_offset_s: number; last_offset_s: number }[]
  slowest: { t_offset_s: number; elapsed_ms: number; latency_ms: number; code: string; success: boolean; thread: string }[]
  breakdown: { connect_ms: number; server_ms: number; download_ms: number; avg_ms: number; bytes: number; sent_bytes: number } | null
  findings: { n: number; type: string; severity: FindingSeverity; title: string }[]
  sla: SlaCheckRow[]
  baseline: {
    run_id: string; tag?: string; samples: number; p50_ms?: number; p95_ms: number; p99_ms?: number
    error_pct: number; tps?: number; p95_change_pct: number | null; err_change_pp: number
  } | null
  window: { start_offset_s: number; end_offset_s: number }
}

export async function getLabelDetail(runId: string, label: string): Promise<LabelDetail> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}/label?name=${encodeURIComponent(label)}`)
}

export async function listRunLabels(runId: string): Promise<{ labels: (LabelStats & { kind: string })[] }> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}/labels`)
}

export function labelsReportUrl(runId: string): string {
  return `${API_BASE}/results/runs/${encodeURIComponent(runId)}/labels-report`
}

export async function downloadLabelsReport(runId: string, format: 'html' | 'md'): Promise<void> {
  return downloadUrl(`${labelsReportUrl(runId)}?format=${format}&download=true`, `${runId}_requests_transactions.${format}`)
}

// -- comparing two analysed runs ---------------------------------------------

export interface CompareRow {
  kind: string; label: string
  baseline: { samples: number; p95_ms: number; error_pct: number; tps: number }
  current: { samples: number; p95_ms: number; error_pct: number; tps: number }
  p95_change_ms: number | null; p95_change_pct: number | null; err_change_pp: number | null; tps_change_pct: number | null
  status: 'regressed' | 'improved' | 'same' | 'too_few_samples'
  regressed: string[]; improved: string[]; severity?: FindingSeverity
}

export interface ComparedRun {
  run: RunAnalysis['run']
  verdict: RunVerdict
  overall: RunAnalysis['overall']
  steady: RunAnalysis['steady']
  timeline: RunAnalysis['timeline']
  sla: RunAnalysis['sla']
  findings: { n: number; type: string; severity: FindingSeverity; title: string; labels: string[] }[]
}

export interface RunComparison {
  baseline: { run_id: string; scenario: string; tag: string; created_at: string; verdict: string; users: number }
  current: { run_id: string; scenario: string; tag: string; created_at: string; verdict: string; users: number }
  load: { baseline_users: number; current_users: number; users_change_pct: number | null; baseline_tps: number
          current_tps: number; tps_change_pct: number | null; kind: string; comparable: boolean; reason: string }
  thresholds: { p95_pct: number; p95_ms: number; err_pp: number; min_samples: number; load_pct: number }
  kinds: Record<string, { labels: CompareRow[]; overall: CompareRow | null }>
  only_in_baseline: Record<string, string[]>
  only_in_current: Record<string, string[]>
  regressions: unknown[]
  summary: { compared: number; regressed: number; improved: number; too_few_samples: number }
  gating: boolean
  runs: { baseline: ComparedRun | null; current: ComparedRun | null }
  findings_diff: { new: ComparedRun['findings']; resolved: ComparedRun['findings']; persisting: ComparedRun['findings'] }
}

export async function compareRuns(base: string, current: string): Promise<RunComparison> {
  return fetchJson(`${API_BASE}/results/compare?base=${encodeURIComponent(base)}&current=${encodeURIComponent(current)}`)
}

export async function downloadComparison(base: string, current: string): Promise<void> {
  return downloadUrl(`${API_BASE}/results/compare/report?base=${encodeURIComponent(base)}&current=${encodeURIComponent(current)}`,
    `compare_${base}_vs_${current}.md`)
}

// -- server metrics from AWS CloudWatch / Azure Monitor -----------------------

export type CloudProvider = 'aws' | 'azure'

export interface CloudResource { provider: CloudProvider; type: string; id: string; name: string; region: string; role: string }

export interface CloudCatalog {
  types: { provider: CloudProvider; type: string; title: string; category: string
           metrics: { key: string; label: string; unit: string; note: string }[] }[]
  categories: Record<string, string>
}

export interface ServerSeries {
  provider: CloudProvider; resource_type: string; resource_id: string; resource: string; role: string; region: string
  metric: string; label: string; kind: string; unit: string; period_s: number
  points: { t_offset_s: number; value: number }[]
}

export interface ServerMetrics {
  run_id: string; scenario: string; available: boolean
  summary: { fetched_at: string; period_s: number
             resources: { name: string; provider: string; type: string; role: string
                          metrics: { metric: string; label: string; unit: string; steady_avg: number | null; max: number; max_offset_s: number }[] }[]
             missing: { resource: string; metric: string; why: string }[]; warnings: string[] } | null
  series: ServerSeries[]
  resources: CloudResource[]
  missing: { resource: string; metric: string; why: string }[]
  warnings: string[]
  fetched_at: string | null
  steady: RunAnalysis['steady']
  duration_s: number
  findings: (AnalysisFinding & { evidence: Record<string, unknown> })[]
}

export async function getCloudCatalog(): Promise<CloudCatalog> {
  return fetchJson(`${API_BASE}/results/cloud/catalog`)
}

export async function getCloudStatus(): Promise<Record<CloudProvider, { sdk: boolean; auth: string; region?: string; subscription?: string }>> {
  return fetchJson(`${API_BASE}/results/cloud/status`)
}

export async function checkCloud(provider: CloudProvider): Promise<Record<string, string | boolean | null>> {
  return fetchJson(`${API_BASE}/results/cloud/check`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider }),
  })
}

export async function discoverCloud(provider: CloudProvider, types: string[], region = ''): Promise<{ resources: CloudResource[]; warnings: string[] }> {
  const q = new URLSearchParams({ provider, types: types.join(','), region })
  return fetchJson(`${API_BASE}/results/cloud/discover?${q}`)
}

export async function getCloudTargets(scenario: string): Promise<{ resources: CloudResource[] }> {
  return fetchJson(`${API_BASE}/results/cloud/targets?scenario=${encodeURIComponent(scenario)}`)
}

export async function getServerMetrics(runId: string): Promise<ServerMetrics> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}/server-metrics`)
}

export async function fetchServerMetrics(runId: string, resources: CloudResource[]): Promise<{
  series: number; findings: number; verdict: string; missing: { resource: string; metric: string; why: string }[]; warnings: string[]
}> {
  return fetchJson(`${API_BASE}/results/runs/${encodeURIComponent(runId)}/server-metrics`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ resources, save: true }),
  })
}
