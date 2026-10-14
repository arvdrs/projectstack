import { create } from 'zustand'
import type {
  CorrelatedValue, TestResults, RequestPreview, AssertionConfig, ThreadGroupConfig, SlaConfig,
  CorrelationAssessment,
} from '../api/client'

export interface CsvConfig {
  id: string
  varNames: string
  data: string
  delimiter: string
}

export interface TestVar {
  id: string
  name: string
  value: string
}

export interface TestDataFile {
  id: string
  filename: string
  data: string          // raw CSV text
  accessMethod: 'unique' | 'shared'
  variables: string[]   // column names from CSV header row
}

export interface LoadConfig {
  threads: number
  rampup: number
  loops: number              // -1 = loop until the duration ends
  duration_seconds: number   // 0 = run by loop count
}

export const DEFAULT_THREAD_GROUP: ThreadGroupConfig = {
  type: 'standard',
  threads: 10, initial_delay: 0, start_burst: 0, step_users: 2, step_period: 30, step_rampup: 5,
  stop_users: 5, stop_period: 1, hold: 60,
  target: 10, ramp_up: 60, steps: 5, iterations: 0,
  rows: [
    { threads: 10, initial_delay: 0, startup: 30, hold: 60, shutdown: 10 },
    { threads: 10, initial_delay: 60, startup: 30, hold: 60, shutdown: 10 },
  ],
}

export const EMPTY_SLA: SlaConfig = {
  response_time_metric: 'p90', response_time_ms: null, transaction_targets: {},
  error_rate_pct: null, throughput_rps: null,
}

/** The SLA to send with a run: null unless at least one target is set. */
export function activeSla(sla: SlaConfig | null): SlaConfig | null {
  if (!sla) return null
  const hasTarget = sla.response_time_ms != null || sla.error_rate_pct != null || sla.throughput_rps != null ||
    Object.values(sla.transaction_targets).some(v => v != null)
  return hasTarget ? sla : null
}

interface AppState {
  sessionId: string | null
  filename: string | null
  requestCount: number
  requests: RequestPreview[]
  sourceFormat: string
  domains: string[]
  baseUrl: string
  baseUrls: string[]
  correlatedValues: CorrelatedValue[]
  // Confidence in the last auto-correlation (or AI auto-correlation) result
  correlationAssessment: CorrelationAssessment | null
  jmx: string | null
  results: TestResults | null
  currentStep: number
  maxReachedStep: number
  reportUrl: string | null
  selectedRequestIndices: number[] | null
  customAssertions: Record<string, AssertionConfig[]>
  requestOrder: number[] | null
  transactionNames: Record<string, string>
  csvConfigs: Record<string, CsvConfig[]>
  lastRunConfig: LoadConfig | null
  // Load settings baked into the most recently generated script (pre-fills the Run step)
  generatedLoadConfig: LoadConfig | null
  // Thread group chosen in the Generate step, and the one in the last generated script
  threadGroup: ThreadGroupConfig
  generatedThreadGroup: (Partial<ThreadGroupConfig> & { type: ThreadGroupConfig['type'] }) | null
  // SLA defined in the Generate step — null = no SLA (the default)
  sla: SlaConfig | null

  // Test Data
  testVars: TestVar[]
  testDataFiles: TestDataFile[]

  // Second HAR for dynamic variable comparison
  secondSessionId: string | null
  secondFilename: string | null
  secondRequests: RequestPreview[]

  setSession: (
    sessionId: string,
    filename: string,
    requestCount: number,
    requests: RequestPreview[],
    sourceFormat?: string,
    domains?: string[]
  ) => void
  setSecondSession: (sessionId: string, requests: RequestPreview[], filename: string) => void
  setBaseUrl: (url: string) => void
  setBaseUrls: (urls: string[]) => void
  setRequestCount: (count: number, requests: RequestPreview[]) => void
  setCorrelated: (values: CorrelatedValue[]) => void
  setCorrelationAssessment: (a: CorrelationAssessment | null) => void
  setJmx: (jmx: string) => void
  setResults: (results: TestResults) => void
  setStep: (step: number) => void
  setReportUrl: (url: string | null) => void
  setSelectedRequests: (indices: number[] | null) => void
  updateRequestAssertions: (reqIdx: number, assertions: AssertionConfig[]) => void
  reset: () => void
  setRequestOrder: (order: number[] | null) => void
  setTransactionName: (reqIdx: number, name: string) => void
  setTransactionNames: (names: Record<string, string>) => void
  updateCsvConfigs: (reqIdx: number, configs: CsvConfig[]) => void
  setLastRunConfig: (config: LoadConfig) => void
  setGeneratedLoadConfig: (config: LoadConfig | null) => void
  setThreadGroup: (tg: ThreadGroupConfig) => void
  setGeneratedThreadGroup: (tg: AppState['generatedThreadGroup']) => void
  setSla: (sla: SlaConfig | null) => void
  setTestVars: (vars: TestVar[]) => void
  setTestDataFiles: (files: TestDataFile[]) => void
}

const initialState = {
  sessionId: null,
  filename: null,
  requestCount: 0,
  requests: [],
  sourceFormat: 'har',
  domains: [],
  baseUrl: '',
  baseUrls: [],
  correlatedValues: [],
  correlationAssessment: null,
  jmx: null,
  results: null,
  currentStep: 0,
  maxReachedStep: 0,
  reportUrl: null,
  selectedRequestIndices: null,
  customAssertions: {},
  requestOrder: null,
  transactionNames: {},
  csvConfigs: {},
  lastRunConfig: null,
  generatedLoadConfig: null,
  threadGroup: DEFAULT_THREAD_GROUP,
  generatedThreadGroup: null,
  sla: null,
  testVars: [] as TestVar[],
  testDataFiles: [] as TestDataFile[],
  secondSessionId: null,
  secondFilename: null,
  secondRequests: [],
}

// Everything keyed by request index — invalid once the request list is re-parsed
const perRequestState = {
  selectedRequestIndices: null, customAssertions: {}, requestOrder: null, transactionNames: {}, csvConfigs: {},
  correlatedValues: [], correlationAssessment: null, jmx: null, results: null, reportUrl: null,
  lastRunConfig: null, generatedLoadConfig: null, generatedThreadGroup: null,
}

export const useAppStore = create<AppState>((set) => ({
  ...initialState,

  setSession: (sessionId, filename, requestCount, requests, sourceFormat = 'har', domains = []) =>
    set({
      ...perRequestState,
      sessionId, filename, requestCount, requests, sourceFormat, domains,
      baseUrl: '', baseUrls: [],
      currentStep: 0, maxReachedStep: 0,
      testVars: [], testDataFiles: [],
      secondSessionId: null, secondFilename: null, secondRequests: [],
    }),

  setSecondSession: (secondSessionId, secondRequests, secondFilename) => set({ secondSessionId, secondRequests, secondFilename }),

  setBaseUrl: (url) => set({ baseUrl: url }),
  setBaseUrls: (urls) => set({ baseUrls: urls, baseUrl: urls[0] ?? '' }),

  // The domain filter re-parses the recording, so request indices change and the
  // server drops its correlations: reset everything that refers to old indices.
  setRequestCount: (count, requests) => set({
    ...perRequestState,
    requestCount: count, requests,
    maxReachedStep: 0,
  }),

  setCorrelated: (values) => set((state) => ({
    correlatedValues: values,
    maxReachedStep: Math.max(state.maxReachedStep, 1),
  })),

  setCorrelationAssessment: (a) => set({ correlationAssessment: a }),

  setJmx: (jmx) => set((state) => ({
    jmx,
    maxReachedStep: Math.max(state.maxReachedStep, 2),
  })),

  setResults: (results) => set((state) => ({
    results,
    reportUrl: results.html_report_url ?? null,
    maxReachedStep: Math.max(state.maxReachedStep, 4),
  })),

  setStep: (step) => set((state) => ({
    currentStep: step,
    maxReachedStep: Math.max(state.maxReachedStep, step),
  })),

  setReportUrl: (url) => set({ reportUrl: url }),

  setSelectedRequests: (indices) => set({ selectedRequestIndices: indices }),

  updateRequestAssertions: (reqIdx, assertions) => set((state) => ({
    customAssertions: { ...state.customAssertions, [String(reqIdx)]: assertions },
  })),

  reset: () => set(initialState),

  setRequestOrder: (order) => set({ requestOrder: order }),

  setTransactionName: (reqIdx, name) => set((state) => ({
    transactionNames: { ...state.transactionNames, [String(reqIdx)]: name },
  })),

  // Replaces the whole grouping, so names from an earlier grouping do not linger
  setTransactionNames: (names) => set({ transactionNames: names }),

  updateCsvConfigs: (reqIdx, configs) => set((state) => ({
    csvConfigs: { ...state.csvConfigs, [String(reqIdx)]: configs },
  })),

  setLastRunConfig: (config) => set({ lastRunConfig: config }),
  setGeneratedLoadConfig: (config) => set({ generatedLoadConfig: config }),
  setThreadGroup: (tg) => set({ threadGroup: tg }),
  setGeneratedThreadGroup: (tg) => set({ generatedThreadGroup: tg }),
  setSla: (sla) => set({ sla }),

  setTestVars: (vars) => set({ testVars: vars }),
  setTestDataFiles: (files) => set({ testDataFiles: files }),
}))
