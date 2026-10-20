import { useState, useRef, useCallback, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Upload, CheckCircle, Loader2, AlertTriangle,
  FileText, Download, Play, BarChart2, ChevronRight,
  Code, Zap, RefreshCw, Info, Globe, Eye, X, ChevronDown, ChevronUp,
  Search, Shield, Cpu, FileDown, RotateCcw, XCircle, Wrench, Layers, Scissors, Sparkles, Target, Plus,
  Square, Video, Copy, Check,
} from 'lucide-react'

import ReportAnalyzer from '../components/ReportAnalyzer'
import AnalyzeRunPrompt from '../components/results/AnalyzeRunPrompt'
import LiveRunMonitor from '../components/LiveRunMonitor'
import {
  useAppStore, activeSla, EMPTY_SLA, DEFAULT_THREAD_GROUP,
  type CsvConfig, type TestVar, type TestDataFile,
} from '../store/useAppStore'
import {
  uploadHar, filterByBaseUrl, correlate, correlateCompare, aiCorrelate, generate,
  downloadJmx, runTest, getSettings, updateSettings, downloadReport,
  getReportStatus, getRequestDetail, suggestParams,
  getHarAnalysis, scanSensitive, downloadK6, downloadCiYaml, downloadZip,
  autoTransactions, sortTransactions, applyRule, rawConvertHar, getResponses, saveCorrelations, replayFix,
  getRunLive, stopRun, getJmeterInfo,
  proxyStart, proxyStop, proxyStatus, proxyTransaction, downloadHar,
  type ProxyStartResult, type ProxyStatusResult, type ProxyStopResult,
  type CorrelatedValue, type ReplayFixResult, type LiveSnapshot, type RequestDetail, type RequestPreview,
  type AssertionConfig, type AssertionType, type DynamicValue,
  type HarAnalysis, type SensitiveFinding, type CorrelationRuleRequest, type ParamSuggestion,
  type ResponseSummary, type ThreadGroupConfig, type ThreadGroupType, type SlaConfig, type SlaMetric,
  type SlaResult, type SlaCheck, type JmeterInfo, type UltimateRow, type CorrelationAssessment,
} from '../api/client'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STEPS = [
  { label: 'Upload', icon: Upload },
  { label: 'Correlate Values', icon: Zap },
  { label: 'Generate Script', icon: Code },
  { label: 'Run Test', icon: Play },
  { label: 'View Report', icon: BarChart2 },
]

const fadeSlide = {
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
  exit: { opacity: 0, y: -8, transition: { duration: 0.2 } },
}

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------

function Btn({
  children, onClick, disabled, variant = 'primary', fullWidth = false, small = false,
}: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean
  variant?: 'primary' | 'ghost' | 'danger'; fullWidth?: boolean; small?: boolean
}) {
  const base: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: '6px',
    cursor: disabled ? 'not-allowed' : 'pointer',
    border: 'none', borderRadius: '999px', fontWeight: 500,
    fontSize: small ? '13px' : '14px', padding: small ? '7px 16px' : '11px 24px',
    transition: 'all 0.2s ease', opacity: disabled ? 0.5 : 1,
    width: fullWidth ? '100%' : undefined, justifyContent: fullWidth ? 'center' : undefined,
  }
  const styles: Record<string, React.CSSProperties> = {
    primary: { ...base, background: 'var(--accent-gradient)', color: '#fff', boxShadow: '0 6px 20px -8px rgba(var(--accent-rgb),0.7)' },
    ghost: { ...base, background: 'rgba(255,255,255,0.05)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', backdropFilter: 'blur(10px)' },
    danger: { ...base, background: 'rgba(var(--danger-rgb),0.1)', color: 'var(--danger)', border: '1px solid rgba(var(--danger-rgb),0.3)' },
  }
  return (
    <button style={styles[variant]} onClick={onClick} disabled={disabled}
      onMouseEnter={e => { if (!disabled) { const el = e.currentTarget; el.style.transform = 'translateY(-1px)'; if (variant === 'primary') el.style.boxShadow = '0 10px 28px -8px rgba(var(--accent-rgb),0.85)'; else el.style.background = 'rgba(255,255,255,0.1)' } }}
      onMouseLeave={e => { const el = e.currentTarget; el.style.transform = 'translateY(0)'; el.style.boxShadow = styles[variant].boxShadow as string ?? ''; el.style.background = styles[variant].background as string }}
    >
      {children}
    </button>
  )
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ background: 'var(--glass), var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '20px', padding: '28px', boxShadow: '0 20px 40px -24px rgba(0,0,0,0.6)', ...style }}>
      {children}
    </div>
  )
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div style={{ background: 'var(--glass), var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '16px', padding: '18px 22px' }}>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500, marginBottom: '10px' }}>{label}</div>
      <div style={{ fontSize: '30px', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1, letterSpacing: '-0.02em' }}>{value}</div>
      {sub && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{sub}</div>}
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 style={{ fontSize: '32px', fontWeight: 600, lineHeight: 1.15, marginBottom: '24px', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '14px' }}>
      <span style={{ width: '4px', height: '28px', borderRadius: '4px', background: 'var(--accent-gradient)', flexShrink: 0 }} />
      {children}
    </h2>
  )
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div style={{ background: 'rgba(var(--danger-rgb),0.08)', border: '1px solid rgba(var(--danger-rgb),0.3)', borderRadius: '12px', padding: '12px 16px', color: 'var(--danger)', fontSize: '14px', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
      <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '1px' }} />
      <span>{msg}</span>
    </div>
  )
}

function InfoBox({ msg }: { msg: string }) {
  return (
    <div style={{ background: 'rgba(var(--info-rgb),0.08)', border: '1px solid rgba(var(--info-rgb),0.25)', borderRadius: '12px', padding: '12px 16px', color: 'var(--info)', fontSize: '14px', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
      <Info size={16} style={{ flexShrink: 0, marginTop: '1px' }} />
      <span>{msg}</span>
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px' }}>{children}</label>
}

function Input({ value, onChange, type = 'text', placeholder, min, disabled }: {
  value: string | number; onChange: (v: string) => void; type?: string; placeholder?: string; min?: number; disabled?: boolean
}) {
  return (
    <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} min={min} disabled={disabled}
      style={{ width: '100%', background: disabled ? 'var(--bg-elevated)' : 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px', padding: '10px 14px', color: disabled ? 'var(--text-muted)' : 'var(--text-primary)', fontSize: '14px', outline: 'none', transition: 'border-color 0.15s', boxSizing: 'border-box', opacity: disabled ? 0.6 : 1 }}
      onFocus={e => { if (!disabled) e.target.style.borderColor = 'var(--accent)' }}
      onBlur={e => { e.target.style.borderColor = 'var(--border)' }}
    />
  )
}

function MethodBadge({ method }: { method: string }) {
  const colors: Record<string, string> = { GET: '#58a6ff', POST: '#3ccf7a', PUT: '#d29922', DELETE: '#f85149', PATCH: '#d2a8ff' }
  const c = colors[method.toUpperCase()] || '#8b949e'
  return (
    <span style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'monospace', color: c, background: `${c}18`, borderRadius: '4px', padding: '2px 6px' }}>
      {method}
    </span>
  )
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', userSelect: 'none' }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
        style={{ width: '15px', height: '15px', accentColor: 'var(--accent)', cursor: 'pointer' }} />
      <span style={{ fontSize: '14px', color: 'var(--text-primary)' }}>{label}</span>
    </label>
  )
}

// ---------------------------------------------------------------------------
// Helpers for RequestDetailPanel
// ---------------------------------------------------------------------------

function formatJson(text: string): string {
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text }
}

function HighlightedText({ text, highlights }: {
  text: string
  highlights: DynamicValue[]
}) {
  if (!text || highlights.length === 0) return <>{text}</>
  type Seg = { text: string; varName?: string; role?: string }
  let segs: Seg[] = [{ text }]
  const sorted = [...highlights]
    .filter(h => h.extract_value && h.extract_value.length >= 3)
    .sort((a, b) => b.extract_value.length - a.extract_value.length)
  for (const h of sorted) {
    const next: Seg[] = []
    for (const seg of segs) {
      if (seg.varName) { next.push(seg); continue }
      const parts = seg.text.split(h.extract_value)
      parts.forEach((p, i) => {
        if (p) next.push({ text: p })
        if (i < parts.length - 1) next.push({ text: h.extract_value, varName: h.var_name, role: h.role })
      })
    }
    segs = next
  }
  return (
    <>
      {segs.map((s, i) =>
        s.varName
          ? <mark key={i} title={`${s.varName} (${s.role})`} style={{
              background: s.role === 'extracted' ? 'rgba(var(--accent-rgb),0.28)' : 'rgba(var(--info-rgb),0.28)',
              color: 'inherit', borderRadius: '2px', padding: '1px 2px',
              cursor: 'default', fontWeight: 600, outline: 'none', border: 'none',
            }}>{s.text}</mark>
          : <span key={i}>{s.text}</span>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Assertion Editor
// ---------------------------------------------------------------------------

const ASSERTION_LABELS: Record<AssertionType, string> = {
  status_code: 'HTTP Status =',
  response_contains: 'Body Contains',
  response_regex: 'Body Regex',
  response_time_ms: 'Response Time <',
}
const ASSERTION_PLACEHOLDERS: Record<AssertionType, string> = {
  status_code: '200',
  response_contains: 'expected text…',
  response_regex: '^\\{.*\\}$',
  response_time_ms: '2000',
}

function AssertionEditor({ assertions, onChange, defaultStatusCode }: {
  assertions: AssertionConfig[]
  onChange: (next: AssertionConfig[]) => void
  defaultStatusCode: number
}) {
  const [addType, setAddType] = useState<AssertionType>('response_contains')
  const [addValue, setAddValue] = useState('')
  const genId = () => Math.random().toString(36).slice(2, 9)

  const add = () => {
    const v = addType === 'status_code' && !addValue.trim() ? String(defaultStatusCode) : addValue.trim()
    if (!v) return
    onChange([...assertions, { id: genId(), type: addType, value: v, enabled: true }])
    setAddValue('')
  }

  return (
    <div>
      <div style={{ marginBottom: '12px', fontSize: '13px', color: 'var(--text-muted)' }}>
        Assertions run after each request. Custom assertions replace the auto-generated status check.
      </div>
      {assertions.length === 0 && (
        <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px', background: 'var(--bg-elevated)', borderRadius: '8px', marginBottom: '12px' }}>
          No custom assertions — auto status {defaultStatusCode} check will be used.
        </div>
      )}
      {assertions.map(a => (
        <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 10px', borderRadius: '7px', marginBottom: '5px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', opacity: a.enabled ? 1 : 0.45 }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', whiteSpace: 'nowrap', minWidth: '108px' }}>{ASSERTION_LABELS[a.type]}</span>
          <input value={a.value} onChange={e => onChange(assertions.map(x => x.id === a.id ? { ...x, value: e.target.value } : x))}
            style={{ flex: 1, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '5px', padding: '4px 7px', color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'monospace', outline: 'none' }} />
          <input type="checkbox" checked={a.enabled} onChange={() => onChange(assertions.map(x => x.id === a.id ? { ...x, enabled: !x.enabled } : x))}
            style={{ accentColor: 'var(--accent)', cursor: 'pointer', width: '14px', height: '14px' }} />
          <button onClick={() => onChange(assertions.filter(x => x.id !== a.id))} style={{ background: 'transparent', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: '17px', lineHeight: 1, padding: '2px 4px' }}>×</button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: '7px', marginTop: '12px', padding: '10px', background: 'var(--bg-elevated)', borderRadius: '8px', border: '1px dashed var(--border)' }}>
        <select value={addType} onChange={e => setAddType(e.target.value as AssertionType)}
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 8px', color: 'var(--text-primary)', fontSize: '12px', outline: 'none', cursor: 'pointer' }}>
          {(Object.keys(ASSERTION_LABELS) as AssertionType[]).map(t => <option key={t} value={t}>{ASSERTION_LABELS[t]}</option>)}
        </select>
        <input value={addValue} onChange={e => setAddValue(e.target.value)} placeholder={ASSERTION_PLACEHOLDERS[addType]}
          onKeyDown={e => e.key === 'Enter' && add()}
          style={{ flex: 1, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 10px', color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'monospace', outline: 'none' }} />
        <Btn small onClick={add}>+ Add</Btn>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Request Detail Panel (slide-in drawer)
// ---------------------------------------------------------------------------

function CollapsibleSection({ title, children, count }: { title: string; children: React.ReactNode; count?: number }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginBottom: '14px' }}>
      <button onClick={() => setOpen(!open)} style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '13px', fontWeight: 600, padding: 0 }}>
        <ChevronDown size={13} style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 0.15s' }} />
        {title}{count !== undefined ? ` (${count})` : ''}
      </button>
      {open && <div style={{ marginTop: '8px' }}>{children}</div>}
    </div>
  )
}

function BodyBlock({ body, mimeType, highlights, context }: { body: string | null; mimeType?: string | null; highlights: DynamicValue[]; context?: 'request' | 'response' }) {
  if (!body) {
    if (context === 'response') {
      return (
        <div style={{ padding: '16px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '8px' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '13px', fontStyle: 'italic', marginBottom: '10px' }}>No response body captured</div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.7 }}>
            Response bodies are only saved when you export with content. Re-export using:
            <br />
            <strong style={{ color: 'var(--text-secondary)' }}>Chrome DevTools</strong> → Network tab → Right-click any request → <code style={{ background: 'var(--bg-surface)', padding: '1px 6px', borderRadius: '4px', fontSize: '11px' }}>Save all as HAR with content</code>
            <br /><br />
            Or use <strong style={{ color: 'var(--text-secondary)' }}>Record in browser</strong> on the first step to capture traffic through ProjectStack's recorder — responses are always included.
          </div>
        </div>
      )
    }
    return <div style={{ color: 'var(--text-muted)', fontSize: '13px', fontStyle: 'italic' }}>No body</div>
  }
  const isJson = (mimeType || '').includes('json') || body.trimStart().startsWith('{') || body.trimStart().startsWith('[')
  const display = isJson ? formatJson(body) : body
  return (
    <pre style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '8px', padding: '14px', fontSize: '12px', fontFamily: 'monospace', overflowX: 'auto', maxHeight: '320px', overflowY: 'auto', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
      <HighlightedText text={display} highlights={highlights} />
    </pre>
  )
}

function CsvConfigEditor({ configs, onChange }: { configs: CsvConfig[]; onChange: (next: CsvConfig[]) => void }) {
  const genId = () => Math.random().toString(36).slice(2, 9)
  const add = () => onChange([...configs, { id: genId(), varNames: '', data: '', delimiter: ',' }])
  const remove = (id: string) => onChange(configs.filter(c => c.id !== id))
  const update = (id: string, patch: Partial<CsvConfig>) =>
    onChange(configs.map(c => c.id === id ? { ...c, ...patch } : c))

  return (
    <div>
      <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '12px' }}>
        Define CSV Data Set Config elements to parameterize request body fields. Each config generates a JMeter CSVDataSet element.
      </div>
      {configs.length === 0 && (
        <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px', background: 'var(--bg-elevated)', borderRadius: '8px', marginBottom: '12px' }}>
          No CSV configs — add one to parameterize body fields.
        </div>
      )}
      {configs.map(c => (
        <div key={c.id} style={{ marginBottom: '14px', padding: '12px', background: 'var(--bg-elevated)', borderRadius: '8px', border: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', gap: '8px', marginBottom: '8px', alignItems: 'center' }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block', marginBottom: '3px' }}>Variable Names (comma-separated)</label>
              <input value={c.varNames} onChange={e => update(c.id, { varNames: e.target.value })}
                placeholder="username,password,email"
                style={{ width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 10px', color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'monospace', outline: 'none', boxSizing: 'border-box' }} />
            </div>
            <div style={{ width: '80px' }}>
              <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block', marginBottom: '3px' }}>Delimiter</label>
              <input value={c.delimiter} onChange={e => update(c.id, { delimiter: e.target.value })}
                style={{ width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 8px', color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'monospace', outline: 'none', boxSizing: 'border-box' }} />
            </div>
            <button onClick={() => remove(c.id)} style={{ background: 'transparent', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: '18px', padding: '4px', alignSelf: 'flex-end' }}>×</button>
          </div>
          <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block', marginBottom: '3px' }}>Values (one row per line)</label>
          <textarea value={c.data} onChange={e => update(c.id, { data: e.target.value })}
            placeholder={`admin${c.delimiter}secret123\nuser1${c.delimiter}pass456`}
            rows={4}
            style={{ width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '8px 10px', color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'monospace', outline: 'none', resize: 'vertical', boxSizing: 'border-box' }} />
        </div>
      ))}
      <Btn small variant="ghost" onClick={add}>+ Add CSV Config</Btn>
    </div>
  )
}

function RequestDetailPanel({ sessionId, requestIndex, onClose, correlatedValues, assertions, onAssertionsChange, csvConfigs, onCsvConfigsChange }: {
  sessionId: string
  requestIndex: number | null
  onClose: () => void
  correlatedValues: CorrelatedValue[]
  assertions: AssertionConfig[]
  onAssertionsChange: (next: AssertionConfig[]) => void
  csvConfigs: CsvConfig[]
  onCsvConfigsChange: (next: CsvConfig[]) => void
}) {
  const [detail, setDetail] = useState<RequestDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<'request' | 'response' | 'assertions' | 'csv'>('request')

  useEffect(() => {
    if (requestIndex === null) return
    setLoading(true); setDetail(null); setTab('request')
    getRequestDetail(sessionId, requestIndex)
      .then(d => {
        setDetail(d)
        // Auto-seed status code assertion if none exist yet
        if (assertions.length === 0) {
          onAssertionsChange([{ id: Math.random().toString(36).slice(2, 9), type: 'status_code', value: String(d.status_code), enabled: true }])
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [requestIndex]) // eslint-disable-line react-hooks/exhaustive-deps

  if (requestIndex === null) return null

  const usedHighlights = (detail?.dynamic_values ?? []).filter(dv => dv.role === 'used')
  const extractedHighlights = (detail?.dynamic_values ?? []).filter(dv => dv.role === 'extracted')
  const allHighlights = detail?.dynamic_values ?? []

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 999 }} />
      <div style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: '640px', background: 'var(--bg-surface)', borderLeft: '1px solid var(--border)', zIndex: 1000, display: 'flex', flexDirection: 'column' }}>
        {/* Header */}
        <div style={{ padding: '18px 22px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
          {detail && <MethodBadge method={detail.method} />}
          <span style={{ flex: 1, fontFamily: 'monospace', fontSize: '13px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {detail?.path ?? '…'}
          </span>
          {detail && (
            <span style={{ fontSize: '12px', fontWeight: 700, color: detail.status_code < 400 ? 'var(--success)' : 'var(--danger)', background: detail.status_code < 400 ? 'rgba(var(--accent-rgb),0.12)' : 'rgba(var(--danger-rgb),0.12)', padding: '3px 8px', borderRadius: '6px' }}>
              {detail.status_code}
            </span>
          )}
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '4px', display: 'flex', alignItems: 'center' }}>
            <X size={17} />
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', paddingLeft: '22px', flexShrink: 0 }}>
          {(['request', 'response', 'assertions', 'csv'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)} style={{
              padding: '9px 16px', border: 'none', background: 'transparent',
              color: tab === t ? 'var(--accent)' : 'var(--text-muted)',
              borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
              marginBottom: '-1px', fontSize: '13px', fontWeight: tab === t ? 600 : 400, cursor: 'pointer', textTransform: 'capitalize',
            }}>
              {t === 'csv' ? `CSV Config${csvConfigs.length > 0 ? ` (${csvConfigs.length})` : ''}` : t}{t === 'assertions' ? ` (${assertions.length})` : ''}
            </button>
          ))}
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 22px' }}>
          {loading && (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '48px' }}>
              <Loader2 size={24} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
            </div>
          )}

          {!loading && detail && tab === 'request' && (
            <div>
              {/* Full URL */}
              <div style={{ marginBottom: '16px' }}>
                <Label>Full URL</Label>
                <div style={{ fontFamily: 'monospace', fontSize: '12px', wordBreak: 'break-all', color: 'var(--text-secondary)', background: 'var(--bg-elevated)', padding: '10px 12px', borderRadius: '8px' }}>
                  {detail.url}
                </div>
              </div>

              {/* Dynamic values used in this request */}
              {usedHighlights.length > 0 && (
                <div style={{ marginBottom: '14px', display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {usedHighlights.map(dv => (
                    <span key={dv.var_name} style={{ fontSize: '11px', padding: '2px 9px', borderRadius: '10px', background: 'rgba(var(--info-rgb),0.12)', border: '1px solid rgba(var(--info-rgb),0.3)', color: '#58a6ff', fontFamily: 'monospace' }}>
                      ${'{' + dv.var_name + '}'} ← used
                    </span>
                  ))}
                </div>
              )}

              {/* Headers */}
              <CollapsibleSection title="Request Headers" count={Object.keys(detail.headers).length}>
                <div style={{ background: 'var(--bg-elevated)', borderRadius: '8px', padding: '12px', fontSize: '12px', fontFamily: 'monospace' }}>
                  {Object.entries(detail.headers).map(([k, v]) => (
                    <div key={k} style={{ display: 'flex', gap: '8px', marginBottom: '4px', wordBreak: 'break-all' }}>
                      <span style={{ color: 'var(--accent)', flexShrink: 0, minWidth: '140px' }}>{k}:</span>
                      <span style={{ color: 'var(--text-secondary)' }}>{v}</span>
                    </div>
                  ))}
                </div>
              </CollapsibleSection>

              {/* Body */}
              <Label>Request Body</Label>
              <BodyBlock body={detail.body} mimeType={detail.body_mime_type} highlights={usedHighlights} />
            </div>
          )}

          {!loading && detail && tab === 'response' && (
            <div>
              {/* Extracted values */}
              {extractedHighlights.length > 0 && (
                <div style={{ marginBottom: '14px', display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {extractedHighlights.map(dv => (
                    <span key={dv.var_name} style={{ fontSize: '11px', padding: '2px 9px', borderRadius: '10px', background: 'rgba(var(--accent-rgb),0.12)', border: '1px solid rgba(var(--accent-rgb),0.3)', color: 'var(--accent)', fontFamily: 'monospace' }}>
                      {dv.var_name} ← extracted
                    </span>
                  ))}
                </div>
              )}

              {/* Response headers */}
              <CollapsibleSection title="Response Headers" count={Object.keys(detail.response_headers).length}>
                <div style={{ background: 'var(--bg-elevated)', borderRadius: '8px', padding: '12px', fontSize: '12px', fontFamily: 'monospace' }}>
                  {Object.entries(detail.response_headers).map(([k, v]) => (
                    <div key={k} style={{ display: 'flex', gap: '8px', marginBottom: '4px', wordBreak: 'break-all' }}>
                      <span style={{ color: 'var(--accent)', flexShrink: 0, minWidth: '140px' }}>{k}:</span>
                      <span style={{ color: 'var(--text-secondary)' }}>{v}</span>
                    </div>
                  ))}
                </div>
              </CollapsibleSection>

              {/* Response body */}
              <Label>Response Body</Label>
              <BodyBlock body={detail.response_body} mimeType={detail.response_mime_type} highlights={extractedHighlights} context="response" />
            </div>
          )}

          {!loading && detail && tab === 'assertions' && (
            <AssertionEditor
              assertions={assertions}
              onChange={onAssertionsChange}
              defaultStatusCode={detail.status_code}
            />
          )}

          {tab === 'csv' && (
            <CsvConfigEditor configs={csvConfigs} onChange={onCsvConfigsChange} />
          )}
        </div>
      </div>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </>
  )
}

// ---------------------------------------------------------------------------
// Transactions — group requests into JMeter Transaction Controllers
// ---------------------------------------------------------------------------

const TXN_COLORS = ['#58a6ff', '#3ccf7a', '#d29922', '#d2a8ff', '#39c5cf', '#f0883e']

/** Name as the generator writes it after TC_nn_ (mirrors clean_transaction_name on the server). */
function cleanTxnName(name: string): string {
  return name.trim().replace(/^(?:TC_?\d+|T\d+)_/i, '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
}

type TxnBlock = { name: string; indices: number[] }

/**
 * A Transaction Controller is a run of consecutive requests with the same name;
 * a request without a name joins the transaction before it — exactly how the
 * server builds the script. A leading unnamed run is "ungrouped" (name '').
 */
function computeBlocks(order: number[], names: Record<string, string>): TxnBlock[] {
  const blocks: TxnBlock[] = []
  for (const idx of order) {
    const raw = (names[String(idx)] ?? '').trim()
    const prev = blocks[blocks.length - 1]
    if (prev && (!raw || cleanTxnName(raw) === cleanTxnName(prev.name))) prev.indices.push(idx)
    else blocks.push({ name: raw, indices: [idx] })
  }
  return blocks
}

function blocksToNames(blocks: TxnBlock[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const b of blocks) if (b.name.trim()) for (const i of b.indices) out[String(i)] = b.name
  return out
}

function txnLabel(n: number, name: string): string {
  return `TC_${String(n + 1).padStart(2, '0')}_${cleanTxnName(name) || 'Flow'}`
}

function TxnNameInput({ value, color, onCommit }: { value: string; color: string; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  const commit = () => { if (draft.trim() && draft !== value) onCommit(draft.trim()); else setDraft(value) }
  return (
    <input value={draft} onChange={e => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setDraft(value) }}
      aria-label="Transaction name"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '8px', padding: '5px 10px', fontSize: '14px', fontWeight: 600, color, minWidth: 0, width: '220px', outline: 'none' }}
      onFocus={e => { e.target.style.borderColor = color }} />
  )
}

function TransactionsPanel({ sessionId, requests }: {
  sessionId: string
  requests: RequestPreview[]   // included requests, in execution order
}) {
  const { transactionNames, setTransactionNames } = useAppStore()
  const [busy, setBusy] = useState<'auto' | 'ai' | null>(null)
  const [error, setError] = useState('')
  const order = requests.map(r => r.index)
  const byIndex = new Map(requests.map(r => [r.index, r]))
  const blocks = computeBlocks(order, transactionNames)
  const grouped = blocks.some(b => b.name.trim())
  const update = (next: TxnBlock[]) => setTransactionNames(blocksToNames(next))

  const nameCounts = new Map<string, number>()
  blocks.forEach(b => { if (b.name.trim()) nameCounts.set(cleanTxnName(b.name), (nameCounts.get(cleanTxnName(b.name)) ?? 0) + 1) })
  const newName = () => {
    let n = blocks.filter(b => b.name.trim()).length + 1
    while (nameCounts.has(cleanTxnName(`Transaction ${n}`))) n++
    return `Transaction ${n}`
  }

  const rename = (b: number, name: string) => update(blocks.map((blk, i) => i === b ? { ...blk, name } : blk))
  const split = (b: number, pos: number) => {
    const blk = blocks[b]
    update([...blocks.slice(0, b), { ...blk, indices: blk.indices.slice(0, pos) },
      { name: newName(), indices: blk.indices.slice(pos) }, ...blocks.slice(b + 1)])
  }
  const mergeUp = (b: number) => rename(b, blocks[b - 1].name)

  const runGrouping = async (kind: 'auto' | 'ai') => {
    setBusy(kind); setError('')
    try {
      const scope = { request_order: order, selected_request_indices: null }
      const res = kind === 'auto' ? await autoTransactions(sessionId, scope) : await sortTransactions(sessionId, scope)
      setTransactionNames(res.transaction_names)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const pathOf = (r?: RequestPreview) => {
    if (!r) return ''
    try { return new URL(r.url, 'http://x').pathname } catch { return r.url }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '12px' }}>
        <Btn small onClick={() => runGrouping('auto')} disabled={!!busy || order.length === 0}>
          {busy === 'auto' ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> Grouping…</> : <><Layers size={13} /> Auto-group</>}
        </Btn>
        <Btn small variant="ghost" onClick={() => runGrouping('ai')} disabled={!!busy || order.length === 0}>
          {busy === 'ai' ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> Asking AI…</> : <><Sparkles size={13} /> AI group</>}
        </Btn>
        {grouped && <Btn small variant="ghost" onClick={() => setTransactionNames({})} disabled={!!busy}><X size={13} /> Clear</Btn>}
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Auto-group splits on time gaps, URL modules and action words · AI group uses the provider from Settings
        </span>
      </div>
      {error && <div style={{ marginBottom: '12px' }}><ErrorBox msg={error} /></div>}
      <div style={{ marginBottom: '14px' }}>
        <InfoBox msg="Each transaction becomes a JMeter Transaction Controller that times the user action as a whole. Name it, split it where a new action starts (✂), or merge it into the one above. A transaction is a block of requests in execution order — change the order in the Requests tab." />
      </div>

      {order.length === 0 && <div style={{ fontSize: '13px', color: 'var(--text-muted)', padding: '12px 0' }}>No requests are included — tick some in the Requests tab.</div>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {blocks.map((b, bi) => {
          const named = !!b.name.trim()
          const color = named ? TXN_COLORS[bi % TXN_COLORS.length] : 'var(--text-muted)'
          const dup = named && (nameCounts.get(cleanTxnName(b.name)) ?? 0) > 1
          return (
            <div key={`${bi}-${b.indices[0]}`} style={{ border: '1px solid var(--border)', borderLeft: `3px solid ${color}`, borderRadius: '12px', overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 14px', background: 'var(--bg-elevated)', flexWrap: 'wrap' }}>
                {named ? (
                  <>
                    <TxnNameInput value={b.name} color={color} onCommit={v => rename(bi, v)} />
                    <code style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{txnLabel(bi, b.name)}</code>
                  </>
                ) : (
                  <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                    Not in a transaction {grouped && <span style={{ fontWeight: 400, fontSize: '12px', color: 'var(--text-muted)' }}>· generated as {txnLabel(0, '')}</span>}
                  </span>
                )}
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{b.indices.length} request{b.indices.length !== 1 ? 's' : ''}</span>
                {dup && <span style={{ fontSize: '11px', color: 'var(--warning)' }}>Name used more than once — each block stays a separate controller</span>}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: '6px' }}>
                  {!named && <Btn small onClick={() => rename(bi, newName())}><Plus size={12} /> Make a transaction</Btn>}
                  {named && bi > 0 && <Btn small variant="ghost" onClick={() => mergeUp(bi)}><ChevronUp size={12} /> Merge into previous</Btn>}
                  {named && bi === 0 && <Btn small variant="ghost" onClick={() => rename(0, '')}><X size={12} /> Ungroup</Btn>}
                </div>
              </div>
              {b.indices.map((idx, pos) => {
                const r = byIndex.get(idx)
                return (
                  <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '6px 14px', borderTop: '1px solid var(--border)', fontSize: '12px' }}>
                    <span style={{ color: 'var(--text-muted)', width: '34px', flexShrink: 0 }}>#{idx + 1}</span>
                    <MethodBadge method={r?.method ?? '?'} />
                    <span style={{ flex: 1, minWidth: 0, fontFamily: 'monospace', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r?.url}>{pathOf(r)}</span>
                    <span style={{ color: (r?.status_code ?? 0) >= 400 ? 'var(--danger)' : 'var(--success)' }}>{r?.status_code}</span>
                    {pos > 0 ? (
                      <button onClick={() => split(bi, pos)} title="Start a new transaction at this request" aria-label="Start a new transaction here"
                        style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: '6px', color: 'var(--text-muted)', cursor: 'pointer', padding: '3px 6px', display: 'inline-flex', alignItems: 'center' }}>
                        <Scissors size={12} />
                      </button>
                    ) : <span style={{ width: '26px' }} />}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Proxy recorder — record a journey and name its transactions as you go
// ---------------------------------------------------------------------------

// In Docker the backend cannot open a window on the viewer's computer, so they open one with this
// command: a separate Chrome profile (the proxy flag only applies to a new Chrome process, even
// while Chrome is already open) whose traffic goes through the recorder
function recordingBrowserCommand(port: number, url: string): { how: string; cmd: string } {
  const flags = `--proxy-server=127.0.0.1:${port} --ignore-certificate-errors --no-first-run`
  const start = url ? ` "${url}"` : ''
  const ua = navigator.userAgent
  if (/Windows/.test(ua)) {
    return { how: 'Press Win + R, paste this and press Enter:', cmd: `chrome ${flags} --user-data-dir="%TEMP%\\pstack-recording"${start}` }
  }
  if (/Macintosh|Mac OS X/.test(ua)) {
    return { how: 'Paste this in Terminal:', cmd: `open -na "Google Chrome" --args ${flags} --user-data-dir=/tmp/pstack-recording${start}` }
  }
  return { how: 'Paste this in a terminal:', cmd: `google-chrome ${flags} --user-data-dir=/tmp/pstack-recording${start}` }
}

function DockerBrowserCommand({ port, url }: { port: number; url: string }) {
  const [copied, setCopied] = useState(false)
  const { how, cmd } = recordingBrowserCommand(port, url)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cmd)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard blocked — the command can still be selected */ }
  }
  return (
    <div style={{ background: 'rgba(var(--info-rgb),0.08)', border: '1px solid rgba(var(--info-rgb),0.25)', borderRadius: '12px', padding: '12px 16px', fontSize: '14px' }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', color: 'var(--info)' }}>
        <Info size={16} style={{ flexShrink: 0, marginTop: '2px' }} />
        <span>ProjectStack is running in Docker, so it can't open the recording window itself. {how}</span>
      </div>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '10px' }}>
        <code style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere', fontSize: '12px', lineHeight: 1.6, padding: '8px 10px', borderRadius: '8px', background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)', userSelect: 'all' }}>{cmd}</code>
        <button onClick={copy} aria-label="Copy the command"
          style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', flexShrink: 0, background: 'transparent', border: '1px solid var(--border)', borderRadius: '8px', color: 'var(--text-secondary)', cursor: 'pointer', padding: '7px 10px', fontSize: '12px' }}>
          {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px', lineHeight: 1.6 }}>
        It opens a separate Chrome window whose traffic goes through the recorder; record the journey there and close
        it when you're done. Chrome warns about an unsupported flag: that flag lets the recorder read HTTPS pages, and
        it applies to this window only.
      </div>
    </div>
  )
}

function RecorderPanel({ onRecorded }: { onRecorded: (res: ProxyStopResult) => void }) {
  const [status, setStatus] = useState<ProxyStatusResult | null>(null)
  const [startUrl, setStartUrl] = useState('')
  const [firstTxn, setFirstTxn] = useState('')
  const [nextTxn, setNextTxn] = useState('')
  const [busy, setBusy] = useState<'start' | 'stop' | 'txn' | null>(null)
  const [error, setError] = useState('')
  const [launch, setLaunch] = useState<ProxyStartResult | null>(null)
  const txnInputRef = useRef<HTMLInputElement>(null)
  const running = !!status?.running
  const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))

  const refresh = useCallback(async () => {
    try { setStatus(await proxyStatus()) } catch { /* backend restarting — keep the last state */ }
  }, [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (!running) return
    const id = setInterval(refresh, 1000)
    return () => clearInterval(id)
  }, [running, refresh])

  const start = async () => {
    setBusy('start'); setError('')
    try {
      const res = await proxyStart({ start_url: startUrl.trim() || undefined, transaction: firstTxn.trim() || undefined })
      if (!res.ok) { setError(res.error ?? 'Could not start the recorder'); return }
      setLaunch(res)
      await refresh()
      setTimeout(() => txnInputRef.current?.focus(), 50)
    } catch (e) {
      setError(msg(e))
    } finally {
      setBusy(null)
    }
  }

  const startTransaction = async () => {
    const name = nextTxn.trim()
    if (!name) return
    setBusy('txn'); setError('')
    try {
      setStatus(await proxyTransaction(name))
      setNextTxn('')
    } catch (e) {
      setError(msg(e))
    } finally {
      setBusy(null)
      txnInputRef.current?.focus()
    }
  }

  const stop = async () => {
    setBusy('stop'); setError('')
    try {
      const res = await proxyStop()
      await refresh()
      if (!res.ok) { setError(res.error ?? 'The recording could not be imported'); return }
      setLaunch(null)
      onRecorded(res)
    } catch (e) {
      setError(msg(e))
    } finally {
      setBusy(null)
    }
  }

  const port = status?.port ?? 8082
  const inputSt: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', background: 'var(--bg-surface)', border: '1px solid var(--border)',
    borderRadius: '12px', padding: '10px 14px', color: 'var(--text-primary)', fontSize: '14px', outline: 'none',
  }

  if (!running) {
    return (
      <Card>
        <div style={{ display: 'flex', gap: '14px', alignItems: 'flex-start', marginBottom: '18px' }}>
          <Video size={20} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: '2px' }} />
          <div>
            <div style={{ fontSize: '16px', fontWeight: 600 }}>Record a user journey</div>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '3px', lineHeight: 1.6 }}>
              {status?.in_docker
                ? "Records a separate Chrome window whose traffic goes through ProjectStack's recorder (ProjectStack runs in Docker here, so once you start, it shows the command that opens that window)."
                : "Opens a separate Chrome window whose traffic goes through ProjectStack's recorder."} Before each step of the
              journey, type its name and start the transaction — the requests that follow are grouped under it, just like
              naming Transaction Controllers in JMeter's recorder.
            </div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '14px', marginBottom: '16px' }}>
          <div>
            <Label>Start URL (optional)</Label>
            <input value={startUrl} onChange={e => setStartUrl(e.target.value)} placeholder="https://your-app.example.com" style={inputSt} />
          </div>
          <div>
            <Label>First transaction (optional)</Label>
            <input value={firstTxn} onChange={e => setFirstTxn(e.target.value)} placeholder="e.g. Open home page" style={inputSt}
              onKeyDown={e => { if (e.key === 'Enter') start() }} />
          </div>
        </div>
        <Btn onClick={start} disabled={busy !== null}>
          {busy === 'start'
            ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> Starting…</>
            : <><span style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#fff' }} /> Start recording</>}
        </Btn>
        {error && <div style={{ marginTop: '12px' }}><ErrorBox msg={error} /></div>}
        <div style={{ marginTop: '14px', fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
          Static files (JS, CSS, images) and the browser's own background traffic are left out of the script.
          Prefer another browser? Set its HTTP proxy to <code>127.0.0.1:{port}</code> and trust the mitmproxy certificate
          (open <code>http://mitm.it</code> through the proxy).
        </div>
      </Card>
    )
  }

  const txns = status?.transactions ?? []
  const current = status?.current_transaction ?? ''
  return (
    <Card>
      <style>{`@keyframes recpulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.25 } }`}</style>
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap', marginBottom: '18px' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontSize: '15px', fontWeight: 600 }}>
          <span style={{ width: '11px', height: '11px', borderRadius: '50%', background: 'var(--danger)', animation: 'recpulse 1.4s ease-in-out infinite' }} />
          Recording
        </span>
        <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--text-primary)' }}>{status?.kept_count ?? 0}</strong> requests
          <span style={{ color: 'var(--text-muted)' }}> · {status?.request_count ?? 0} including static files</span>
        </span>
        <div style={{ marginLeft: 'auto' }}>
          <Btn onClick={stop} disabled={busy !== null}>
            {busy === 'stop' ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> Importing…</> : <><Square size={13} /> Stop &amp; import</>}
          </Btn>
        </div>
      </div>

      {status?.in_docker ? (
        // also after a page reload, when the start response (launch) is gone
        <div style={{ marginBottom: '14px' }}>
          <DockerBrowserCommand port={port} url={startUrl.trim()} />
        </div>
      ) : launch && !launch.chromium_launched && (
        <div style={{ marginBottom: '14px' }}>
          <InfoBox msg={`Couldn't open Chrome automatically — set your browser's HTTP proxy to 127.0.0.1:${port} and record there.`} />
        </div>
      )}
      {launch?.chromium_launched && status && !status.browser_open && (
        <div style={{ marginBottom: '14px' }}>
          <InfoBox msg="The recording window was closed. Stop & import to use what was recorded so far." />
        </div>
      )}

      <div style={{ padding: '16px', borderRadius: '14px', background: 'var(--bg-surface)', border: '1px solid var(--border)', marginBottom: '16px' }}>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Current transaction</div>
        <div style={{ fontSize: '20px', fontWeight: 600, color: current ? 'var(--accent)' : 'var(--text-muted)', marginBottom: '14px' }}>
          {current || 'None — requests are not in a transaction yet'}
        </div>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          <input ref={txnInputRef} value={nextTxn} onChange={e => setNextTxn(e.target.value)} aria-label="Next transaction name"
            onKeyDown={e => { if (e.key === 'Enter') startTransaction() }}
            placeholder="Next step, e.g. Login · Search product · Add to cart"
            style={{ ...inputSt, flex: 1, minWidth: '220px', width: 'auto' }} />
          <Btn onClick={startTransaction} disabled={busy !== null || !nextTxn.trim()}>
            {busy === 'txn' ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Plus size={14} />} Start transaction
          </Btn>
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
          Name the step first, then do it in the recording window. A request belongs to the transaction that was current when it was sent.
        </div>
      </div>

      {error && <div style={{ marginBottom: '12px' }}><ErrorBox msg={error} /></div>}

      {txns.length > 0 && (
        <div>
          <Label>Transactions so far</Label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {txns.map((t, i) => {
              const isCurrent = t.name === current && i === txns.map(x => x.name).lastIndexOf(current)
              const color = t.name ? TXN_COLORS[i % TXN_COLORS.length] : 'var(--text-muted)'
              return (
                <div key={`${i}-${t.name}`} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '8px 12px', borderRadius: '10px', border: `1px solid ${isCurrent ? color : 'var(--border)'}`, background: isCurrent ? 'rgba(255,255,255,0.03)' : 'transparent' }}>
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: color, flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: '13px', fontWeight: t.name ? 600 : 400, color: t.name ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                    {t.name || 'Before the first transaction'}
                  </span>
                  {isCurrent && <span style={{ fontSize: '11px', color }}>recording</span>}
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{t.request_count} request{t.request_count !== 1 ? 's' : ''}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Step 0: Upload HAR
// ---------------------------------------------------------------------------

function StepUpload() {
  const {
    setSession, setSecondSession, setStep, setBaseUrls, setRequestCount,
    sessionId: storeSessionId, selectedRequestIndices, setSelectedRequests,
    customAssertions, updateRequestAssertions, correlatedValues,
    requestOrder, setRequestOrder, transactionNames, setTransactionNames,
    filename: storeFilename, requestCount: storeRequestCount, requests: storeRequests,
    domains: storeDomains, baseUrls: storeBaseUrls, secondSessionId, secondFilename, secondRequests,
  } = useAppStore()
  const [uploadTab, setUploadTab] = useState<'requests' | 'transactions' | 'analysis' | 'responses'>('requests')
  // Bring in traffic by uploading HAR files or recording it through the proxy — or
  // Quick Convert a HAR straight to a JMX, outside the 5-step workflow
  const [source, setSource] = useState<'upload' | 'record' | 'convert'>('upload')
  useEffect(() => { proxyStatus().then(s => { if (s.running) setSource('record') }).catch(() => {}) }, [])
  const [responses, setResponses] = useState<ResponseSummary[] | null>(null)
  const [responsesLoading, setResponsesLoading] = useState(false)
  const [responsesError, setResponsesError] = useState('')
  const [expandedResponse, setExpandedResponse] = useState<number | null>(null)
  const [responseSearch, setResponseSearch] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [harAnalysis, setHarAnalysis] = useState<HarAnalysis | null>(null)
  const [sensitiveFindings, setSensitiveFindings] = useState<SensitiveFinding[]>([])
  const [scanLoading, setScanLoading] = useState(false)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [filterLoading, setFilterLoading] = useState(false)
  const [error, setError] = useState('')
  // Restored from the store, so coming back to this step keeps the uploaded recording
  const [sessionIdRef, setSessionIdRef] = useState<string | null>(storeSessionId)
  const [domains, setDomains] = useState<string[]>(storeDomains)
  const [selectedDomains, setSelectedDomains] = useState<string[]>(storeBaseUrls)
  const [preview, setPreview] = useState<null | { filename: string; count: number; requests: RequestPreview[] }>(
    () => storeSessionId && storeFilename ? { filename: storeFilename, count: storeRequestCount, requests: storeRequests } : null,
  )
  const [detailRequestIndex, setDetailRequestIndex] = useState<number | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  // Second HAR state
  const [dragging2, setDragging2] = useState(false)
  const [loading2, setLoading2] = useState(false)
  const [error2, setError2] = useState('')
  const [secondPreview, setSecondPreview] = useState<{ filename: string; count: number } | null>(
    () => secondSessionId ? { filename: secondFilename ?? 'second recording', count: secondRequests.length } : null,
  )
  const input2Ref = useRef<HTMLInputElement>(null)

  // Raw-convert state
  const [draggingRaw, setDraggingRaw] = useState(false)
  const [loadingRaw, setLoadingRaw] = useState(false)
  const [errorRaw, setErrorRaw] = useState('')
  const [rawDone, setRawDone] = useState<string | null>(null)
  const inputRawRef = useRef<HTMLInputElement>(null)

  const isReqSelected = (idx: number) =>
    selectedRequestIndices === null || selectedRequestIndices.includes(idx)
  const allReqsSelected = preview !== null &&
    (selectedRequestIndices === null || selectedRequestIndices.length === preview.requests.length)
  const selectedCount = preview
    ? (selectedRequestIndices === null ? preview.requests.length : selectedRequestIndices.length)
    : 0
  const filteredCount = preview ? preview.requests.length - selectedCount : 0
  const toggleAll = () => setSelectedRequests(allReqsSelected ? [] : null)
  const toggleReq = (idx: number) => {
    if (selectedRequestIndices === null) {
      setSelectedRequests(preview!.requests.map(r => r.index).filter(i => i !== idx))
    } else {
      const next = isReqSelected(idx)
        ? selectedRequestIndices.filter(i => i !== idx)
        : [...selectedRequestIndices, idx]
      setSelectedRequests(preview && next.length === preview.requests.length ? null : next)
    }
  }

  const getOrderedIndices = () => {
    if (!preview) return []
    return requestOrder !== null ? [...requestOrder] : preview.requests.map(r => r.index)
  }
  const moveUp = (idx: number) => {
    const arr = getOrderedIndices()
    const pos = arr.indexOf(idx)
    if (pos <= 0) return
    const next = [...arr]
    ;[next[pos - 1], next[pos]] = [next[pos], next[pos - 1]]
    setRequestOrder(next)
  }
  const moveDown = (idx: number) => {
    const arr = getOrderedIndices()
    const pos = arr.indexOf(idx)
    if (pos < 0 || pos >= arr.length - 1) return
    const next = [...arr]
    ;[next[pos], next[pos + 1]] = [next[pos + 1], next[pos]]
    setRequestOrder(next)
  }
  const orderedRequests = preview ? (
    requestOrder !== null
      ? requestOrder.map(i => preview.requests.find(r => r.index === i)).filter(Boolean) as typeof preview.requests
      : preview.requests
  ) : []
  // Transactions cover the included requests, in execution order
  const includedRequests = orderedRequests.filter(r => isReqSelected(r.index))
  const txnBlocks = computeBlocks(includedRequests.map(r => r.index), transactionNames)
  const txnOf = new Map<number, { n: number; name: string }>()
  txnBlocks.forEach((b, n) => b.indices.forEach(i => txnOf.set(i, { n, name: b.name })))
  const txnCount = txnBlocks.filter(b => b.name.trim()).length

  const getCorrelationCount = (idx: number) => {
    return correlatedValues.filter(cv =>
      cv.source_request_index === idx || cv.used_in_requests.includes(idx)
    ).length
  }

  const handleFile = useCallback(async (file: File) => {
    if (!file.name.endsWith('.har')) {
      setError('Please upload a .har file (Chrome DevTools → Network → Save HAR (with sensitive data))')
      return
    }
    setLoading(true); setError(''); setSelectedDomains([]); setDomains([])
    try {
      const res = await uploadHar(file)
      setDomains(res.domains || [])
      setSessionIdRef(res.session_id)
      setPreview({ filename: res.filename ?? file.name, count: res.request_count, requests: res.requests })
      setSession(res.session_id, res.filename ?? file.name, res.request_count, res.requests, res.source_format, res.domains)
      // Recordings made with the proxy recorder carry their transaction names
      setTransactionNames(res.transaction_names ?? {})
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [setSession, setTransactionNames])

  const handleRecorded = useCallback((res: ProxyStopResult) => {
    if (!res.session_id) return
    const filename = res.filename ?? 'recording.har'
    const requests = res.requests ?? []
    setError(''); setSelectedDomains([]); setDomains(res.domains ?? [])
    setSessionIdRef(res.session_id)
    setPreview({ filename, count: res.request_count ?? requests.length, requests })
    setSession(res.session_id, filename, res.request_count ?? requests.length, requests, res.source_format, res.domains)
    setTransactionNames(res.transaction_names ?? {})
    setUploadTab(Object.keys(res.transaction_names ?? {}).length ? 'transactions' : 'requests')
  }, [setSession, setTransactionNames])

  const handleSecondFile = useCallback(async (file: File) => {
    if (!file.name.endsWith('.har')) {
      setError2('Please upload a .har file')
      return
    }
    setLoading2(true); setError2('')
    try {
      const res = await uploadHar(file)
      setSecondPreview({ filename: res.filename ?? file.name, count: res.request_count })
      setSecondSession(res.session_id, res.requests, res.filename ?? file.name)
    } catch (e) {
      setError2(String(e))
    } finally {
      setLoading2(false)
    }
  }, [setSecondSession])

  const handleRawConvert = useCallback(async (file: File) => {
    if (!file.name.endsWith('.har')) {
      setErrorRaw('Please upload a .har file')
      return
    }
    setLoadingRaw(true); setErrorRaw(''); setRawDone(null)
    try {
      await rawConvertHar(file, file.name.replace(/\.har$/, '') + ' Raw Test')
      setRawDone(file.name)
    } catch (e) {
      setErrorRaw(String(e))
    } finally {
      setLoadingRaw(false)
    }
  }, [])

  const handleDomainSelect = useCallback(async (domain: string) => {
    if (!sessionIdRef) return
    const next = selectedDomains.includes(domain)
      ? selectedDomains.filter(d => d !== domain)
      : [...selectedDomains, domain]
    setSelectedDomains(next)
    setBaseUrls(next)
    setFilterLoading(true)
    try {
      if (next.length === 0) {
        // No filter — show all
        const res = await filterByBaseUrl(sessionIdRef, [])
        setPreview(p => p ? { ...p, count: res.request_count, requests: res.requests } : p)
        setRequestCount(res.request_count, res.requests)
        setTransactionNames(res.transaction_names ?? {})
      } else {
        const res = await filterByBaseUrl(sessionIdRef, next)
        setPreview(p => p ? { ...p, count: res.request_count, requests: res.requests } : p)
        setRequestCount(res.request_count, res.requests)
        setTransactionNames(res.transaction_names ?? {})
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setFilterLoading(false)
    }
  }, [sessionIdRef, selectedDomains, setBaseUrls, setRequestCount, setTransactionNames])

  return (
    <motion.div {...fadeSlide}>
      <SectionTitle>Capture the User Flow</SectionTitle>

      <div role="tablist" aria-label="How to capture traffic" style={{ display: 'inline-flex', gap: '4px', padding: '4px', borderRadius: '999px', border: '1px solid var(--border)', background: 'var(--bg-elevated)', marginBottom: '20px' }}>
        {([['upload', 'Upload HAR files', Upload], ['record', 'Record in browser', Video], ['convert', 'Quick Convert', FileDown]] as const).map(([key, label, Icon]) => (
          <button key={key} role="tab" aria-selected={source === key} onClick={() => setSource(key)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '7px', padding: '8px 16px', borderRadius: '999px', border: 'none', cursor: 'pointer', fontSize: '13px', fontWeight: 600, background: source === key ? 'var(--accent-gradient)' : 'transparent', color: source === key ? '#fff' : 'var(--text-secondary)' }}>
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {source === 'record' && (
        <div style={{ marginBottom: '20px' }}>
          <RecorderPanel onRecorded={handleRecorded} />
        </div>
      )}

      {/* First and second recording side by side */}
      {source === 'upload' && (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px', marginBottom: '20px' }}>

        {/* HAR 1 */}
        <div>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '7px' }}>
            <span style={{ width: '20px', height: '20px', borderRadius: '50%', background: preview ? 'var(--success)' : 'var(--bg-elevated)', border: preview ? 'none' : '2px solid var(--border)', color: preview ? '#0a0c0f' : 'var(--text-muted)', fontSize: '11px', fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>1</span>
            First Recording <span style={{ fontSize: '11px', color: 'var(--danger)', fontWeight: 500 }}>required</span>
          </div>
          {preview ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '14px 16px', background: 'var(--bg-elevated)', border: '1px solid rgba(var(--success-rgb),0.35)', borderRadius: '16px', cursor: 'pointer' }}
              onClick={() => inputRef.current?.click()}>
              <input ref={inputRef} type="file" accept=".har" style={{ display: 'none' }} onChange={e => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }} />
              <CheckCircle size={17} style={{ color: 'var(--success)', flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: '14px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{preview.filename}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{preview.count} requests</div>
              </div>
              <span style={{ fontSize: '11px', color: 'var(--success)', background: 'rgba(var(--success-rgb),0.1)', padding: '3px 9px', borderRadius: '5px', flexShrink: 0 }}>Replace</span>
            </div>
          ) : (
            <div
              onClick={() => inputRef.current?.click()}
              onDragOver={e => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={e => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }}
              style={{ border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border)'}`, borderRadius: '20px', padding: '48px 20px', textAlign: 'center', cursor: 'pointer', transition: 'all 0.2s', background: dragging ? 'rgba(var(--accent-rgb),0.04)' : 'var(--bg-elevated)' }}
            >
              <input ref={inputRef} type="file" accept=".har" style={{ display: 'none' }} onChange={e => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }} />
              {loading ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                  <Loader2 size={28} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
                  <span style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>Parsing…</span>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                  <Upload size={30} style={{ color: 'var(--text-muted)' }} />
                  <div>
                    <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '3px' }}>Drop <code style={{ background: 'var(--bg-surface)', padding: '1px 5px', borderRadius: '4px' }}>.har</code> here</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>or click to browse</div>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Chrome DevTools → Network → Save HAR</div>
                </div>
              )}
            </div>
          )}
          {error && <div style={{ marginTop: '8px' }}><ErrorBox msg={error} /></div>}
        </div>

        {/* HAR 2 */}
        <div>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '7px' }}>
            <span style={{ width: '20px', height: '20px', borderRadius: '50%', background: secondPreview ? 'var(--success)' : 'var(--bg-elevated)', border: secondPreview ? 'none' : '2px solid var(--border)', color: secondPreview ? '#0a0c0f' : 'var(--text-muted)', fontSize: '11px', fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>2</span>
            Second Recording <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 400 }}>optional · detects dynamic values</span>
          </div>
          {secondPreview ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '14px 16px', background: 'var(--bg-elevated)', border: '1px solid rgba(var(--success-rgb),0.35)', borderRadius: '16px', cursor: 'pointer' }}
              onClick={() => input2Ref.current?.click()}>
              <input ref={input2Ref} type="file" accept=".har" style={{ display: 'none' }} onChange={e => { if (e.target.files?.[0]) handleSecondFile(e.target.files[0]) }} />
              <CheckCircle size={17} style={{ color: 'var(--success)', flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: '14px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{secondPreview.filename}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{secondPreview.count} requests</div>
              </div>
              <span style={{ fontSize: '11px', color: 'var(--success)', background: 'rgba(var(--success-rgb),0.1)', padding: '3px 9px', borderRadius: '5px', flexShrink: 0 }}>Replace</span>
            </div>
          ) : (
            <div
              onClick={() => input2Ref.current?.click()}
              onDragOver={e => { e.preventDefault(); setDragging2(true) }}
              onDragLeave={() => setDragging2(false)}
              onDrop={e => { e.preventDefault(); setDragging2(false); const f = e.dataTransfer.files[0]; if (f) handleSecondFile(f) }}
              style={{ border: `2px dashed ${dragging2 ? 'var(--accent)' : 'rgba(var(--accent-rgb),0.35)'}`, borderRadius: '20px', padding: '48px 20px', textAlign: 'center', cursor: 'pointer', transition: 'all 0.2s', background: dragging2 ? 'rgba(var(--accent-rgb),0.04)' : 'rgba(var(--accent-rgb),0.015)' }}
            >
              <input ref={input2Ref} type="file" accept=".har" style={{ display: 'none' }} onChange={e => { if (e.target.files?.[0]) handleSecondFile(e.target.files[0]) }} />
              {loading2 ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                  <Loader2 size={28} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
                  <span style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>Parsing…</span>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                  <Upload size={30} style={{ color: 'rgba(var(--accent-rgb),0.5)' }} />
                  <div>
                    <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '3px', color: 'var(--text-secondary)' }}>Drop second <code style={{ background: 'var(--bg-surface)', padding: '1px 5px', borderRadius: '4px' }}>.har</code> here</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>or click to browse · same flow, different run</div>
                  </div>
                  <div style={{ fontSize: '11px', color: 'rgba(var(--accent-rgb),0.6)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <Zap size={10} /> Values that differ between runs are flagged as dynamic
                  </div>
                </div>
              )}
            </div>
          )}
          {error2 && <div style={{ marginTop: '8px' }}><ErrorBox msg={error2} /></div>}
        </div>

      </div>
      )}

      {/* Quick Convert: HAR straight to JMX, outside the 5-step workflow */}
      {source === 'convert' && (
        <Card style={{ marginBottom: '20px' }}>
          <div style={{ display: 'flex', gap: '14px', alignItems: 'flex-start', marginBottom: '18px' }}>
            <FileDown size={20} style={{ color: '#d29922', flexShrink: 0, marginTop: '2px' }} />
            <div>
              <div style={{ fontSize: '16px', fontWeight: 600 }}>Quick Convert</div>
              <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '3px', lineHeight: 1.6 }}>
                Turns a HAR file straight into a JMeter script and downloads it. Every request is kept exactly as recorded —
                no correlation, parameterisation, transactions or SLA. It doesn't start the 5-step workflow; use
                Upload HAR files or Record in browser for a script that replays reliably under load.
              </div>
            </div>
          </div>
          {rawDone ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '14px 16px', background: 'var(--bg-elevated)', border: '1px solid rgba(var(--success-rgb),0.35)', borderRadius: '16px', cursor: 'pointer' }}
              onClick={() => { setRawDone(null); inputRawRef.current?.click() }}>
              <input ref={inputRawRef} type="file" accept=".har" style={{ display: 'none' }} onChange={e => { if (e.target.files?.[0]) handleRawConvert(e.target.files[0]) }} />
              <CheckCircle size={17} style={{ color: 'var(--success)', flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: '14px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{rawDone}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>JMX downloaded — click to convert another</div>
              </div>
              <FileDown size={15} style={{ color: 'var(--accent)', flexShrink: 0 }} />
            </div>
          ) : (
            <div
              onClick={() => inputRawRef.current?.click()}
              onDragOver={e => { e.preventDefault(); setDraggingRaw(true) }}
              onDragLeave={() => setDraggingRaw(false)}
              onDrop={e => { e.preventDefault(); setDraggingRaw(false); const f = e.dataTransfer.files[0]; if (f) handleRawConvert(f) }}
              style={{ border: `2px dashed ${draggingRaw ? '#d29922' : '#7a611555'}`, borderRadius: '20px', padding: '48px 20px', textAlign: 'center', cursor: 'pointer', transition: 'all 0.2s', background: draggingRaw ? 'rgba(var(--warning-rgb),0.06)' : 'rgba(var(--warning-rgb),0.02)' }}
            >
              <input ref={inputRawRef} type="file" accept=".har" style={{ display: 'none' }} onChange={e => { if (e.target.files?.[0]) handleRawConvert(e.target.files[0]) }} />
              {loadingRaw ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                  <Loader2 size={28} style={{ color: '#d29922', animation: 'spin 1s linear infinite' }} />
                  <span style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>Converting…</span>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
                  <FileDown size={30} style={{ color: 'rgba(var(--warning-rgb),0.6)' }} />
                  <div>
                    <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '3px', color: 'var(--text-secondary)' }}>Drop <code style={{ background: 'var(--bg-surface)', padding: '1px 5px', borderRadius: '4px' }}>.har</code> here to quick-convert</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>or click to browse · downloads JMX immediately</div>
                  </div>
                  <div style={{ fontSize: '11px', color: 'rgba(var(--warning-rgb),0.7)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    All requests retained as-is · no modifications
                  </div>
                </div>
              )}
            </div>
          )}
          {errorRaw && <div style={{ marginTop: '8px' }}><ErrorBox msg={errorRaw} /></div>}
        </Card>
      )}

      {preview && source !== 'convert' && (
        <motion.div {...fadeSlide}>
          {/* Domain selector */}
          {domains.length > 0 && (
            <Card style={{ marginBottom: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
                <Globe size={16} style={{ color: 'var(--accent)' }} />
                <span style={{ fontWeight: 600, fontSize: '14px' }}>Select Base URLs</span>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)', marginLeft: 'auto' }}>
                  Select one or more — each domain gets its own BASE_URL variable in JMX
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {domains.map(d => {
                  const isSelected = selectedDomains.includes(d)
                  return (
                    <button key={d} onClick={() => handleDomainSelect(d)} style={{
                      padding: '6px 14px', borderRadius: '20px',
                      border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
                      background: isSelected ? 'rgba(var(--accent-rgb),0.12)' : 'var(--bg-surface)',
                      color: isSelected ? 'var(--accent)' : 'var(--text-secondary)',
                      fontSize: '13px', cursor: 'pointer', fontFamily: 'monospace', transition: 'all 0.15s',
                    }}>
                      {isSelected && <span style={{ marginRight: '5px' }}>✓</span>}{d}
                    </button>
                  )
                })}
              </div>
              {filterLoading && (
                <div style={{ marginTop: '10px', fontSize: '13px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> Filtering requests…
                </div>
              )}
              {selectedDomains.length > 0 && !filterLoading && (
                <div style={{ marginTop: '10px', fontSize: '13px', color: 'var(--accent)' }}>
                  ✓ Showing {preview.count} requests from {selectedDomains.length} domain{selectedDomains.length > 1 ? 's' : ''} — BASE_URL{selectedDomains.length > 1 ? 's' : ''} will be parameterized in JMX
                </div>
              )}
            </Card>
          )}

          <Card style={{ padding: 0, overflow: 'hidden' }}>
            {/* Header: filename + stats */}
            <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
                <CheckCircle size={18} style={{ color: 'var(--success)' }} />
                <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{preview.filename}</span>
                <div style={{ marginLeft: 'auto' }} />
                {preview.filename.startsWith('recording_') && storeSessionId && (
                  <Btn small variant="ghost" onClick={() => downloadHar(storeSessionId).catch(e => setError(String(e)))}>
                    <Download size={13} /> Save recording (.har)
                  </Btn>
                )}
              </div>
              {/* Stats row */}
              <div style={{ display: 'flex', gap: '28px', flexWrap: 'wrap', alignItems: 'center' }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <span style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1 }}>{preview.count}</span>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: '3px' }}>Total</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <span style={{ fontSize: '22px', fontWeight: 700, color: 'var(--accent)', lineHeight: 1 }}>{selectedCount}</span>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: '3px' }}>Included</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <span style={{ fontSize: '22px', fontWeight: 700, color: filteredCount > 0 ? '#d29922' : 'var(--text-muted)', lineHeight: 1 }}>{filteredCount}</span>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: '3px' }}>Filtered</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <span style={{ fontSize: '22px', fontWeight: 700, color: '#58a6ff', lineHeight: 1 }}>{correlatedValues.length}</span>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: '3px' }}>Correlations</span>
                </div>
                {selectedDomains.length > 0 && (
                  <div style={{ marginLeft: 'auto', fontSize: '12px', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                    {selectedDomains.join(', ')}
                  </div>
                )}
              </div>
            </div>

            {/* Tab bar */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', paddingLeft: '24px', background: 'var(--bg-surface)' }}>
              {([
                { key: 'requests', label: `Requests (${preview.count})` },
                { key: 'transactions', label: `Transactions${txnCount ? ` (${txnCount})` : ''}` },
                { key: 'responses', label: 'Responses' },
                { key: 'analysis', label: 'Analysis' },
              ] as const).map(t => (
                <button key={t.key} onClick={() => {
                  setUploadTab(t.key)
                  if (t.key === 'responses' && !responses && storeSessionId && !responsesLoading) {
                    setResponsesLoading(true); setResponsesError('')
                    getResponses(storeSessionId)
                      .then(r => setResponses(r.responses))
                      .catch(e => setResponsesError(String(e)))
                      .finally(() => setResponsesLoading(false))
                  }
                }} style={{
                  padding: '9px 16px', border: 'none', background: 'transparent',
                  color: uploadTab === t.key ? 'var(--accent)' : 'var(--text-muted)',
                  borderBottom: uploadTab === t.key ? '2px solid var(--accent)' : '2px solid transparent',
                  marginBottom: '-1px', fontSize: '13px', fontWeight: uploadTab === t.key ? 600 : 400, cursor: 'pointer',
                }}>
                  {t.label}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div style={{ padding: '16px 24px' }}>

              {/* Requests tab */}
              {uploadTab === 'requests' && (
                <>
                  <div style={{ marginBottom: '12px', display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
                    <Btn onClick={() => setStep(1)} disabled={selectedCount === 0}>
                      Next: Correlate <ChevronRight size={14} />
                    </Btn>
                    <Btn variant="ghost" onClick={() => setUploadTab('transactions')}>
                      <Layers size={14} /> {txnCount ? `Edit transactions (${txnCount})` : 'Group into transactions'}
                    </Btn>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      Untick requests to leave them out · use the arrows to change execution order
                    </span>
                  </div>
                  {/* Search bar */}
                  <div style={{ position: 'relative', marginBottom: '12px' }}>
                    <Search size={14} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
                    <input
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                      placeholder="Filter by URL, method, or status…"
                      style={{ width: '100%', boxSizing: 'border-box', paddingLeft: '32px', paddingRight: '10px', paddingTop: '7px', paddingBottom: '7px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '7px', color: 'var(--text-primary)', fontSize: '13px', outline: 'none' }}
                    />
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border)' }}>
                          <th style={{ padding: '6px 10px', width: '36px' }}>
                            <input type="checkbox" checked={allReqsSelected} onChange={toggleAll}
                              style={{ accentColor: 'var(--accent)', cursor: 'pointer' }} />
                          </th>
                          {['#', 'Method', 'URL', 'Status', 'Corrs', 'Transaction', ''].map(h => (
                            <th key={h} style={{ textAlign: 'left', padding: '6px 10px', color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {orderedRequests.filter(r => {
                          if (!searchQuery.trim()) return true
                          const q = searchQuery.toLowerCase()
                          return r.url.toLowerCase().includes(q) ||
                            r.method.toLowerCase().includes(q) ||
                            String(r.status_code).includes(q)
                        }).map(r => (
                          <tr key={r.index} style={{ borderBottom: '1px solid var(--border)', opacity: isReqSelected(r.index) ? 1 : 0.4, background: r.status_code === 429 ? 'rgba(var(--warning-rgb),0.05)' : 'transparent' }}>
                            <td style={{ padding: '6px 10px' }}>
                              <input type="checkbox" checked={isReqSelected(r.index)} onChange={() => toggleReq(r.index)}
                                style={{ accentColor: 'var(--accent)', cursor: 'pointer' }} />
                            </td>
                            <td style={{ padding: '6px 10px', color: 'var(--text-muted)', fontSize: '12px' }}>{r.index + 1}</td>
                            <td style={{ padding: '6px 10px' }}><MethodBadge method={r.method} /></td>
                            <td style={{ padding: '6px 10px', maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)', fontFamily: 'monospace', fontSize: '12px' }}>{r.url}</td>
                            <td style={{ padding: '6px 10px', color: r.status_code >= 400 ? 'var(--danger)' : 'var(--success)', fontSize: '12px' }}>{r.status_code}</td>
                            <td style={{ padding: '4px 8px' }}>
                              {getCorrelationCount(r.index) > 0 && (
                                <span style={{ fontSize: '11px', fontWeight: 700, padding: '1px 6px', borderRadius: '10px', background: 'rgba(var(--info-rgb),0.12)', border: '1px solid rgba(var(--info-rgb),0.3)', color: '#58a6ff' }}>
                                  {getCorrelationCount(r.index)}
                                </span>
                              )}
                            </td>
                            <td style={{ padding: '4px 8px' }}>
                              {(() => {
                                const t = txnOf.get(r.index)
                                if (!t || !t.name.trim()) return <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>—</span>
                                const c = TXN_COLORS[t.n % TXN_COLORS.length]
                                return (
                                  <button onClick={() => setUploadTab('transactions')} title="Edit transactions"
                                    style={{ fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '10px', border: `1px solid ${c}55`, background: `${c}18`, color: c, cursor: 'pointer', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {t.name}
                                  </button>
                                )
                              })()}
                            </td>
                            <td style={{ padding: '4px 8px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
                                <button onClick={() => moveUp(r.index)} disabled={orderedRequests[0]?.index === r.index}
                                  style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: orderedRequests[0]?.index === r.index ? 'not-allowed' : 'pointer', padding: '3px', opacity: orderedRequests[0]?.index === r.index ? 0.3 : 1 }}>
                                  <ChevronUp size={13} />
                                </button>
                                <button onClick={() => moveDown(r.index)} disabled={orderedRequests[orderedRequests.length - 1]?.index === r.index}
                                  style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: orderedRequests[orderedRequests.length - 1]?.index === r.index ? 'not-allowed' : 'pointer', padding: '3px', opacity: orderedRequests[orderedRequests.length - 1]?.index === r.index ? 0.3 : 1 }}>
                                  <ChevronDown size={13} />
                                </button>
                                <button onClick={() => setDetailRequestIndex(r.index)} title="View details &amp; assertions"
                                  style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '3px', display: 'flex', alignItems: 'center', borderRadius: '4px' }}>
                                  <Eye size={14} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {/* Transactions tab */}
              {uploadTab === 'transactions' && storeSessionId && (
                <TransactionsPanel sessionId={storeSessionId} requests={includedRequests} />
              )}

              {/* Responses tab */}
              {uploadTab === 'responses' && (
                <div>
                  {responsesLoading && (
                    <div style={{ display: 'flex', justifyContent: 'center', padding: '48px' }}>
                      <Loader2 size={24} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
                    </div>
                  )}
                  {responsesError && <ErrorBox msg={responsesError} />}
                  {responses && (
                    <>
                      {/* Search */}
                      <div style={{ position: 'relative', marginBottom: '12px' }}>
                        <Search size={14} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
                        <input
                          value={responseSearch}
                          onChange={e => setResponseSearch(e.target.value)}
                          placeholder="Filter by URL, status, or content type…"
                          style={{ width: '100%', boxSizing: 'border-box', paddingLeft: '32px', paddingRight: '10px', paddingTop: '7px', paddingBottom: '7px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '7px', color: 'var(--text-primary)', fontSize: '13px', outline: 'none' }}
                        />
                      </div>

                      {/* Response rows */}
                      {responses
                        .filter(r => {
                          if (!responseSearch.trim()) return true
                          const q = responseSearch.toLowerCase()
                          return r.url.toLowerCase().includes(q) ||
                            String(r.status_code).includes(q) ||
                            (r.response_mime_type || '').toLowerCase().includes(q) ||
                            (r.response_body_preview || '').toLowerCase().includes(q)
                        })
                        .map(r => {
                          const expanded = expandedResponse === r.index
                          const isJson = (r.response_mime_type || '').includes('json') ||
                            (r.response_body_preview || '').trimStart().startsWith('{') ||
                            (r.response_body_preview || '').trimStart().startsWith('[')
                          let bodyDisplay = r.response_body_preview || ''
                          if (isJson && bodyDisplay) {
                            try { bodyDisplay = JSON.stringify(JSON.parse(bodyDisplay), null, 2) } catch { /* keep as-is */ }
                          }
                          return (
                            <div key={r.index} style={{ marginBottom: '6px', border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden' }}>
                              {/* Row header */}
                              <button
                                onClick={() => setExpandedResponse(expanded ? null : r.index)}
                                style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 14px', background: 'var(--bg-elevated)', border: 'none', cursor: 'pointer', textAlign: 'left' }}
                              >
                                <span style={{ fontSize: '11px', color: 'var(--text-muted)', width: '28px', flexShrink: 0 }}>#{r.index + 1}</span>
                                <MethodBadge method={r.method} />
                                <span style={{ flex: 1, fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.path}</span>
                                <span style={{ fontSize: '12px', fontWeight: 700, color: r.status_code < 400 ? 'var(--success)' : 'var(--danger)', background: r.status_code < 400 ? 'rgba(var(--accent-rgb),0.1)' : 'rgba(var(--danger-rgb),0.1)', padding: '2px 8px', borderRadius: '5px', flexShrink: 0 }}>{r.status_code}</span>
                                {r.response_mime_type && (
                                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace', flexShrink: 0, maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.response_mime_type.split(';')[0]}</span>
                                )}
                                {r.response_body_length > 0 && (
                                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', flexShrink: 0 }}>{(r.response_body_length / 1024).toFixed(1)} KB</span>
                                )}
                                <ChevronDown size={13} style={{ color: 'var(--text-muted)', flexShrink: 0, transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 0.15s' }} />
                              </button>

                              {/* Expanded body + headers */}
                              {expanded && (
                                <div style={{ padding: '12px 14px', borderTop: '1px solid var(--border)', background: 'var(--bg-surface)' }}>
                                  {/* Response headers */}
                                  {Object.keys(r.response_headers).length > 0 && (
                                    <div style={{ marginBottom: '12px' }}>
                                      <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '6px' }}>Response Headers</div>
                                      <div style={{ background: 'var(--bg-elevated)', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', fontFamily: 'monospace', maxHeight: '120px', overflowY: 'auto' }}>
                                        {Object.entries(r.response_headers).map(([k, v]) => (
                                          <div key={k} style={{ display: 'flex', gap: '8px', marginBottom: '3px', wordBreak: 'break-all' }}>
                                            <span style={{ color: 'var(--accent)', flexShrink: 0, minWidth: '120px' }}>{k}:</span>
                                            <span style={{ color: 'var(--text-secondary)' }}>{v}</span>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  )}

                                  {/* Response body */}
                                  <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '6px' }}>
                                    Response Body {r.response_body_length > 500 && <span style={{ fontWeight: 400, textTransform: 'none', color: 'var(--text-muted)', fontSize: '11px' }}>(first 500 chars)</span>}
                                  </div>
                                  {bodyDisplay ? (
                                    <pre style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '6px', padding: '12px', fontSize: '12px', fontFamily: 'monospace', overflowX: 'auto', maxHeight: '280px', overflowY: 'auto', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                                      {bodyDisplay}
                                    </pre>
                                  ) : (
                                    <div style={{ color: 'var(--text-muted)', fontSize: '13px', fontStyle: 'italic', padding: '8px 0' }}>No response body captured</div>
                                  )}
                                </div>
                              )}
                            </div>
                          )
                        })}
                    </>
                  )}
                </div>
              )}

              {/* Analysis tab */}
              {uploadTab === 'analysis' && (
                <div>
                  {!harAnalysis ? (
                    <div style={{ textAlign: 'center', padding: '32px 0' }}>
                      <Btn onClick={async () => {
                        if (!storeSessionId) return
                        setAnalysisLoading(true)
                        try { setHarAnalysis(await getHarAnalysis(storeSessionId)) } catch { /* ignore */ } finally { setAnalysisLoading(false) }
                      }} disabled={analysisLoading || !storeSessionId}>
                        {analysisLoading ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> Analysing…</> : <><BarChart2 size={14} /> Analyse HAR</>}
                      </Btn>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                      {/* Overview stats */}
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '10px' }}>
                        {[
                          { label: 'Avg Response', value: `${harAnalysis.avg_response_ms} ms` },
                          { label: 'Max Response', value: `${harAnalysis.max_response_ms} ms` },
                          { label: 'Errors (4xx/5xx)', value: harAnalysis.error_count },
                          { label: 'Duplicates', value: harAnalysis.duplicates.length },
                          { label: '429 Rate Limits', value: harAnalysis.rate_limited.length },
                        ].map(s => (
                          <div key={s.label} style={{ padding: '12px', background: 'var(--bg-surface)', borderRadius: '8px', border: '1px solid var(--border)', textAlign: 'center' }}>
                            <div style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text-primary)' }}>{s.value}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '3px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{s.label}</div>
                          </div>
                        ))}
                      </div>

                      {/* Protocol + Method breakdown */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                        <div style={{ padding: '12px 16px', background: 'var(--bg-surface)', borderRadius: '8px', border: '1px solid var(--border)' }}>
                          <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Protocol</div>
                          {Object.entries(harAnalysis.protocols).map(([p, n]) => (
                            <div key={p} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px', fontSize: '13px' }}>
                              <span style={{ color: 'var(--text-secondary)', textTransform: 'uppercase' }}>{p}</span>
                              <span style={{ fontWeight: 600, color: 'var(--accent)' }}>{n}</span>
                            </div>
                          ))}
                        </div>
                        <div style={{ padding: '12px 16px', background: 'var(--bg-surface)', borderRadius: '8px', border: '1px solid var(--border)' }}>
                          <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>HTTP Methods</div>
                          {Object.entries(harAnalysis.methods).map(([m, n]) => (
                            <div key={m} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px', fontSize: '13px' }}>
                              <MethodBadge method={m} />
                              <span style={{ fontWeight: 600 }}>{n}</span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Responses the browser did not save — correlation is blind to them */}
                      {(harAnalysis.missing_response_bodies?.length ?? 0) > 0 && (
                        <div style={{ padding: '12px 16px', background: 'rgba(var(--warning-rgb),0.08)', borderRadius: '8px', border: '1px solid rgba(var(--warning-rgb),0.3)', fontSize: '13px', color: 'var(--text-secondary)', display: 'flex', gap: '8px' }}>
                          <AlertTriangle size={15} style={{ color: '#d29922', flexShrink: 0, marginTop: '2px' }} />
                          <span>
                            {harAnalysis.missing_response_bodies!.length} response{harAnalysis.missing_response_bodies!.length !== 1 ? 's have' : ' has'} no saved body
                            (#{harAnalysis.missing_response_bodies!.slice(0, 6).map(i => i + 1).join(', #')}{harAnalysis.missing_response_bodies!.length > 6 ? ', …' : ''}).
                            Values inside them cannot be correlated from the recording — export the HAR with content
                            (Chrome: "Save all as HAR with content"), or use Replay &amp; fix to find them from live responses.
                          </span>
                        </div>
                      )}

                      {/* Auth detection */}
                      {harAnalysis.auth_flow.has_auth && (
                        <div style={{ padding: '12px 16px', background: 'rgba(var(--accent-rgb),0.06)', borderRadius: '8px', border: '1px solid rgba(var(--accent-rgb),0.2)' }}>
                          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--accent)', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <Shield size={14} /> Auth Flow Detected
                          </div>
                          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                            <span>{harAnalysis.auth_flow.auth_endpoints.length} auth endpoint(s)</span>
                            <span>{harAnalysis.auth_flow.bearer_request_count} Bearer token request(s)</span>
                            {harAnalysis.auth_flow.api_key_request_count > 0 && <span>{harAnalysis.auth_flow.api_key_request_count} API-key header request(s)</span>}
                          </div>
                        </div>
                      )}

                      {/* Sensitive data scan */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <Btn small variant="ghost" onClick={async () => {
                          if (!storeSessionId) return
                          setScanLoading(true)
                          try { const r = await scanSensitive(storeSessionId); setSensitiveFindings(r.findings) } catch { /* ignore */ } finally { setScanLoading(false) }
                        }} disabled={scanLoading || !storeSessionId}>
                          {scanLoading ? <><Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> Scanning…</> : <><Shield size={12} /> Scan for Sensitive Data</>}
                        </Btn>
                        {sensitiveFindings.length > 0 && <span style={{ fontSize: '12px', color: 'var(--danger)' }}>{sensitiveFindings.length} finding(s)</span>}
                      </div>
                      {sensitiveFindings.length > 0 && (
                        <div style={{ overflowX: 'auto' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                            <thead>
                              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                                {['Req #', 'Type', 'Field', 'Sample'].map(h => (
                                  <th key={h} style={{ textAlign: 'left', padding: '6px 10px', color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {sensitiveFindings.map((f, i) => (
                                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                                  <td style={{ padding: '5px 10px', color: 'var(--text-muted)' }}>#{f.request_index + 1}</td>
                                  <td style={{ padding: '5px 10px' }}><span style={{ fontSize: '11px', background: 'rgba(var(--danger-rgb),0.1)', color: 'var(--danger)', padding: '1px 6px', borderRadius: '4px', fontWeight: 600 }}>{f.type}</span></td>
                                  <td style={{ padding: '5px 10px', color: 'var(--text-secondary)' }}>{f.field}</td>
                                  <td style={{ padding: '5px 10px', fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-muted)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.sample}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}

                      {/* Duplicates */}
                      {harAnalysis.duplicates.length > 0 && (
                        <div style={{ padding: '10px 14px', background: 'rgba(var(--warning-rgb),0.06)', borderRadius: '8px', border: '1px solid rgba(var(--warning-rgb),0.2)', fontSize: '13px', color: '#d29922' }}>
                          <strong>{harAnalysis.duplicates.length}</strong> duplicate request(s) found.
                          Indices: {harAnalysis.duplicates.slice(0, 5).map(d => `#${d.index + 1}`).join(', ')}{harAnalysis.duplicates.length > 5 ? '…' : ''}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

            </div>
          </Card>
        </motion.div>
      )}

      {detailRequestIndex !== null && storeSessionId && (
        <RequestDetailPanel
          sessionId={storeSessionId}
          requestIndex={detailRequestIndex}
          onClose={() => setDetailRequestIndex(null)}
          correlatedValues={correlatedValues}
          assertions={customAssertions[String(detailRequestIndex)] ?? []}
          onAssertionsChange={a => updateRequestAssertions(detailRequestIndex, a)}
          csvConfigs={useAppStore.getState().csvConfigs[String(detailRequestIndex)] ?? []}
          onCsvConfigsChange={c => useAppStore.getState().updateCsvConfigs(detailRequestIndex, c)}
        />
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// Step 1: Correlate
// ---------------------------------------------------------------------------

const PROVIDER_LABELS: Record<string, { label: string; subtitle: string; tier: string }> = {
  groq:   { label: 'Groq',   subtitle: 'Llama 3.3 70B', tier: 'free' },
  gemini: { label: 'Gemini', subtitle: 'Flash 2.0',      tier: 'free' },
  ollama: { label: 'Ollama', subtitle: 'Local model',    tier: 'free' },
  claude: { label: 'Claude', subtitle: 'Opus 5.5',       tier: 'paid' },
  openai: { label: 'OpenAI', subtitle: 'GPT-4o-mini',    tier: 'paid' },
}

const PASS_META: Record<string, { label: string; color: string }> = {
  pass1:    { label: 'JWT / Bearer',       color: '#d29922' },
  pass2:    { label: 'JSON Response',      color: '#58a6ff' },
  pass_url: { label: 'URL Path IDs',       color: '#39c5cf' },
  pass3:    { label: 'Headers / Cookies',  color: '#f0883e' },
  pass4:    { label: 'Plain Text',         color: '#8b949e' },
  pass6:    { label: 'AI Detection',       color: '#d2a8ff' },
  manual:   { label: 'Manual',             color: '#3ccf7a' },
}

function getPassGroup(pass: string): string {
  if (pass.startsWith('pass1'))    return 'pass1'
  if (pass.startsWith('pass2'))    return 'pass2'
  if (pass.startsWith('pass_url')) return 'pass_url'
  if (pass.startsWith('pass3'))    return 'pass3'
  if (pass.startsWith('pass4'))    return 'pass4'
  if (pass.startsWith('pass6'))    return 'pass6'
  if (pass === 'manual')           return 'manual'
  return 'pass4'
}

type CorrRow = {
  id: string
  original: CorrelatedValue
  varName: string
  enabled: boolean
  isManual: boolean
  manualUnlocked?: boolean
}

const isStaticInBoth = (v: CorrelatedValue) => (v.detection_pass ?? '').includes('static_in_both')

function makeRows(values: CorrelatedValue[]): CorrRow[] {
  return values.map((v, i) => ({
    id: `r${i}-${v.extract_value.slice(0, 6)}`,
    original: v,
    varName: v.var_name,
    // Identical in both recordings: probably static, so it starts switched off
    enabled: !isStaticInBoth(v),
    isManual: false,
  }))
}

const ASSESSMENT_TONE: Record<CorrelationAssessment['label'], string> = { High: 'success', Medium: 'warning', Low: 'danger' }

/** Confidence in the auto-correlation result, shown when it finishes. */
function CorrelationConfidence({ a }: { a: CorrelationAssessment }) {
  const tone = ASSESSMENT_TONE[a.label]
  return (
    <div style={{ marginTop: '16px', padding: '16px 18px', borderRadius: '14px', background: `rgba(var(--${tone}-rgb),0.06)`, border: `1px solid rgba(var(--${tone}-rgb),0.3)` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap', marginBottom: '12px' }}>
        <div>
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>Auto-correlation confidence</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginTop: '2px' }}>
            <span style={{ fontSize: '22px', fontWeight: 700, color: `var(--${tone})` }}>{a.label}</span>
            <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{a.score}/100</span>
          </div>
        </div>
        <div style={{ flex: 1, minWidth: '160px' }}>
          <div role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={a.score} aria-label="Auto-correlation confidence"
            style={{ height: '6px', borderRadius: '3px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div style={{ width: `${a.score}%`, height: '100%', background: `var(--${tone})`, borderRadius: '3px', transition: 'width 0.4s ease' }} />
          </div>
          <div style={{ fontSize: '13px', color: 'var(--text-primary)', marginTop: '8px' }}>{a.headline}</div>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {a.factors.map((f, i) => {
          const Icon = f.impact === 'positive' ? CheckCircle : f.impact === 'negative' ? AlertTriangle : Info
          const color = f.impact === 'positive' ? 'var(--success)' : f.impact === 'negative' ? 'var(--warning)' : 'var(--text-muted)'
          return (
            <div key={i} style={{ display: 'flex', gap: '8px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
              <Icon size={13} style={{ color, flexShrink: 0, marginTop: '2px' }} />
              <span>{f.text}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function UsedInBadge({ indices, requests }: { indices: number[]; requests: RequestPreview[] }) {
  const [show, setShow] = useState(false)
  if (indices.length === 0) return <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>—</span>
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px', background: 'rgba(var(--info-rgb),0.12)', border: '1px solid rgba(var(--info-rgb),0.3)', color: '#58a6ff', cursor: 'default' }}>
        {indices.length}
      </span>
      {show && (
        <div style={{ position: 'absolute', bottom: '100%', left: '50%', transform: 'translateX(-50%)', marginBottom: '6px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '8px', padding: '8px 10px', zIndex: 100, minWidth: '240px', maxWidth: '340px', boxShadow: '0 4px 16px rgba(0,0,0,0.4)' }}>
          {indices.slice(0, 8).map(idx => {
            const req = requests.find(r => r.index === idx)
            let path = `#${idx + 1}`
            try { path = new URL(req?.url ?? '', 'http://x').pathname } catch { /* */ }
            return (
              <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px', fontSize: '11px' }}>
                <MethodBadge method={req?.method ?? '?'} />
                <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={req?.url}>{path}</span>
              </div>
            )
          })}
          {indices.length > 8 && <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>+{indices.length - 8} more</div>}
        </div>
      )}
    </div>
  )
}

function EditableVarName({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  if (editing) {
    return (
      <input
        value={draft}
        autoFocus
        onChange={e => setDraft(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'))}
        onBlur={() => { onChange(draft || value); setEditing(false) }}
        onKeyDown={e => {
          if (e.key === 'Enter')  { onChange(draft || value); setEditing(false) }
          if (e.key === 'Escape') { setDraft(value); setEditing(false) }
        }}
        style={{ background: 'var(--bg-surface)', border: '1px solid var(--accent)', borderRadius: '4px', padding: '2px 6px', fontSize: '12px', fontFamily: 'monospace', color: 'var(--accent)', width: '160px', outline: 'none' }}
      />
    )
  }
  return (
    <span onClick={() => { setDraft(value); setEditing(true) }} title="Click to rename"
      style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--accent)', cursor: 'text', borderBottom: '1px dashed rgba(var(--accent-rgb),0.4)', paddingBottom: '1px', whiteSpace: 'nowrap' }}>
      {value}
    </span>
  )
}

function CorrelationPassSection({ groupKey, rows, requests, onToggle, onRename, onDelete }: {
  groupKey: string
  rows: CorrRow[]
  requests: RequestPreview[]
  onToggle: (id: string) => void
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
}) {
  const [open, setOpen] = useState(true)
  const meta = PASS_META[groupKey] ?? { label: groupKey, color: '#8b949e' }
  const enabledCount = rows.filter(r => r.enabled).length

  return (
    <div style={{ marginBottom: '12px', border: '1px solid var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 16px', background: 'var(--bg-elevated)', border: 'none', cursor: 'pointer', borderLeft: `3px solid ${meta.color}`, textAlign: 'left' }}>
        <span style={{ fontSize: '12px', fontWeight: 700, color: meta.color, background: `${meta.color}1a`, padding: '2px 9px', borderRadius: '4px' }}>
          {meta.label}
        </span>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{enabledCount}/{rows.length} enabled</span>
        <ChevronDown size={13} style={{ marginLeft: 'auto', color: 'var(--text-muted)', transform: open ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 0.15s' }} />
      </button>
      {open && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-surface)' }}>
                {['', 'Variable', 'Source Request', 'Extract', 'Value', 'Used In', 'Reason', ''].map((h, i) => (
                  <th key={i} style={{ padding: '6px 10px', textAlign: i === 0 || i === 7 ? 'center' : 'left', color: 'var(--text-muted)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const srcReq = requests.find(r => r.index === row.original.source_request_index)
                let srcPath = `#${row.original.source_request_index + 1}`
                try { srcPath = new URL(srcReq?.url ?? '', 'http://x').pathname } catch { /* */ }
                return (
                  <tr key={row.id} style={{ borderBottom: '1px solid var(--border)', opacity: row.enabled ? 1 : 0.4 }}>
                    <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                      <input type="checkbox" checked={row.enabled} onChange={() => onToggle(row.id)}
                        style={{ accentColor: 'var(--accent)', cursor: 'pointer', width: '14px', height: '14px' }} />
                    </td>
                    <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                      <EditableVarName value={row.varName} onChange={name => onRename(row.id, name)} />
                    </td>
                    <td style={{ padding: '8px 10px', maxWidth: '180px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        {srcReq && <MethodBadge method={srcReq.method} />}
                        <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={srcReq?.url}>{srcPath}</span>
                      </div>
                    </td>
                    <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{row.original.extract_from}</span>
                      <span style={{ color: 'var(--border)', margin: '0 4px', fontSize: '11px' }}>›</span>
                      <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-secondary)', maxWidth: '120px', display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', verticalAlign: 'bottom' }} title={row.original.extract_key}>{row.original.extract_key}</span>
                    </td>
                    <td style={{ padding: '8px 10px', maxWidth: '140px' }}>
                      <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-muted)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.original.extract_value}>{row.original.extract_value}</span>
                    </td>
                    <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                      <UsedInBadge indices={row.original.used_in_requests} requests={requests} />
                    </td>
                    <td style={{ padding: '8px 10px', maxWidth: '200px' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.original.reason}>{row.original.reason}</span>
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                      <button onClick={() => onDelete(row.id)}
                        style={{ background: 'transparent', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: '16px', lineHeight: 1, padding: '2px 5px', opacity: 0.5 }}
                        onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                        onMouseLeave={e => (e.currentTarget.style.opacity = '0.5')}>×</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function CorrelationRuleForm({ requests, sessionId, onAdd, onClose }: {
  requests: RequestPreview[]
  sessionId: string
  onAdd: (row: CorrRow) => void
  onClose: () => void
}) {
  const [varName, setVarName] = useState('C_CUSTOM')
  const [sourceIdx, setSourceIdx] = useState(requests[0]?.index ?? 0)
  const [extractFrom, setExtractFrom] = useState<'body' | 'header'>('body')
  const [extractType, setExtractType] = useState<'json' | 'regex' | 'begin_end'>('json')
  const [extractKey, setExtractKey] = useState('')
  const [extractPattern, setExtractPattern] = useState('')
  const [extractBegin, setExtractBegin] = useState('')
  const [extractEnd, setExtractEnd] = useState('')
  const [headerName, setHeaderName] = useState('')
  const [preview, setPreview] = useState<CorrelatedValue | null>(null)
  const [previewErr, setPreviewErr] = useState('')
  const [testing, setTesting] = useState(false)

  const inputSt: React.CSSProperties = {
    width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)',
    borderRadius: '6px', padding: '7px 10px', color: 'var(--text-primary)',
    fontSize: '13px', outline: 'none', boxSizing: 'border-box',
  }

  const isReady = () => {
    if (!varName.trim()) return false
    if (extractFrom === 'header') return !!headerName.trim()
    if (extractType === 'json') return !!extractKey.trim()
    if (extractType === 'regex') return !!extractPattern.trim()
    if (extractType === 'begin_end') return !!extractBegin.trim() && !!extractEnd.trim()
    return false
  }

  const buildRule = (): CorrelationRuleRequest => ({
    var_name: varName,
    source_request_index: sourceIdx,
    extract_from: extractFrom,
    extract_type: extractType,
    extract_key: extractFrom === 'header' ? headerName : extractType === 'json' ? extractKey : undefined,
    extract_pattern: extractType === 'regex' ? extractPattern : undefined,
    extract_begin: extractType === 'begin_end' ? extractBegin : undefined,
    extract_end: extractType === 'begin_end' ? extractEnd : undefined,
  })

  const testRule = async () => {
    setTesting(true); setPreviewErr(''); setPreview(null)
    try {
      const result = await applyRule(sessionId, buildRule())
      setPreview(result)
    } catch (e) {
      setPreviewErr(String(e))
    } finally {
      setTesting(false)
    }
  }

  const addRule = () => {
    if (!preview) return
    onAdd({
      id: `rule-${Date.now()}`,
      original: { ...preview, var_name: varName },
      varName, enabled: true, isManual: true,
    })
    onClose()
  }

  return (
    <div style={{ padding: '20px', background: 'var(--bg-elevated)', border: '1px solid var(--accent)', borderRadius: '12px', marginBottom: '16px' }}>
      <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '16px', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <Zap size={14} style={{ color: 'var(--accent)' }} />
        Define Correlation Rule
      </div>

      {/* Row 1: Variable name + Source request */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '12px', marginBottom: '12px' }}>
        <div>
          <Label>Variable Name</Label>
          <input value={varName} onChange={e => setVarName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'))}
            style={{ ...inputSt, color: 'var(--accent)', fontFamily: 'monospace', fontWeight: 600 }} />
        </div>
        <div>
          <Label>Source Request (response to extract from)</Label>
          <select value={sourceIdx} onChange={e => { setSourceIdx(Number(e.target.value)); setPreview(null) }}
            style={{ ...inputSt, cursor: 'pointer' }}>
            {requests.map(r => {
              let path = r.url
              try { path = new URL(r.url, 'http://x').pathname } catch { /* */ }
              return <option key={r.index} value={r.index}>#{r.index + 1} {r.method} {path}</option>
            })}
          </select>
        </div>
      </div>

      {/* Row 2: Extractor configuration */}
      <div style={{ padding: '14px', background: 'var(--bg-surface)', borderRadius: '8px', marginBottom: '12px', border: '1px solid var(--border)' }}>
        <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.06em', marginBottom: '10px', textTransform: 'uppercase' }}>Extractor</div>
        <div style={{ display: 'grid', gridTemplateColumns: '140px 140px 1fr', gap: '10px', alignItems: 'end' }}>
          <div>
            <Label>Extract From</Label>
            <select value={extractFrom} onChange={e => { setExtractFrom(e.target.value as 'body' | 'header'); setPreview(null) }}
              style={{ ...inputSt, cursor: 'pointer' }}>
              <option value="body">Response Body</option>
              <option value="header">Response Header</option>
            </select>
          </div>

          {extractFrom === 'body' && (
            <div>
              <Label>Type</Label>
              <select value={extractType} onChange={e => { setExtractType(e.target.value as 'json' | 'regex' | 'begin_end'); setPreview(null) }}
                style={{ ...inputSt, cursor: 'pointer' }}>
                <option value="json">JSON Path</option>
                <option value="regex">Regex</option>
                <option value="begin_end">Begin / End</option>
              </select>
            </div>
          )}

          {extractFrom === 'header' && (
            <div style={{ gridColumn: '2 / -1' }}>
              <Label>Header Name</Label>
              <input value={headerName} onChange={e => { setHeaderName(e.target.value); setPreview(null) }}
                placeholder="X-Auth-Token"
                style={{ ...inputSt, fontFamily: 'monospace' }} />
            </div>
          )}

          {extractFrom === 'body' && extractType === 'json' && (
            <div>
              <Label>JSON Path</Label>
              <input value={extractKey} onChange={e => { setExtractKey(e.target.value); setPreview(null) }}
                placeholder="access_token  or  data.token"
                style={{ ...inputSt, fontFamily: 'monospace' }} />
            </div>
          )}

          {extractFrom === 'body' && extractType === 'regex' && (
            <div>
              <Label>Regex (capture group 1 = value)</Label>
              <input value={extractPattern} onChange={e => { setExtractPattern(e.target.value); setPreview(null) }}
                placeholder={'"token":"([^"]+)"'}
                style={{ ...inputSt, fontFamily: 'monospace' }} />
            </div>
          )}

          {extractFrom === 'body' && extractType === 'begin_end' && (
            <div style={{ gridColumn: '3 / -1', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
              <div>
                <Label>Begin (text before value)</Label>
                <input value={extractBegin} onChange={e => { setExtractBegin(e.target.value); setPreview(null) }}
                  placeholder={'"access_token":"'}
                  style={{ ...inputSt, fontFamily: 'monospace' }} />
              </div>
              <div>
                <Label>End (text after value)</Label>
                <input value={extractEnd} onChange={e => { setExtractEnd(e.target.value); setPreview(null) }}
                  placeholder={'"'}
                  style={{ ...inputSt, fontFamily: 'monospace' }} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Test button + preview */}
      <div style={{ marginBottom: '12px' }}>
        <Btn small onClick={testRule} disabled={testing || !isReady()}>
          {testing ? <><Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> Testing…</> : <><Search size={12} /> Test Rule</>}
        </Btn>
      </div>

      {previewErr && (
        <div style={{ marginBottom: '12px', padding: '10px 14px', background: 'rgba(var(--danger-rgb),0.08)', border: '1px solid rgba(var(--danger-rgb),0.3)', borderRadius: '8px', fontSize: '12px', color: 'var(--danger)' }}>
          {previewErr}
        </div>
      )}

      {preview && (
        <div style={{ marginBottom: '14px', padding: '12px 16px', background: 'rgba(var(--accent-rgb),0.06)', border: '1px solid rgba(var(--accent-rgb),0.3)', borderRadius: '8px' }}>
          <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--accent)', marginBottom: '8px', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
            Rule matched
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', fontSize: '12px' }}>
            <span style={{ color: 'var(--text-muted)' }}>Extracted value</span>
            <span style={{ fontFamily: 'monospace', color: 'var(--text-primary)', wordBreak: 'break-all' }}>
              {preview.extract_value.length > 80 ? preview.extract_value.slice(0, 80) + '…' : preview.extract_value}
            </span>
            <span style={{ color: 'var(--text-muted)' }}>Used in</span>
            <span style={{ color: preview.used_in_requests.length > 0 ? 'var(--accent)' : 'var(--text-muted)' }}>
              {preview.used_in_requests.length === 0
                ? 'No other requests — value not found in subsequent traffic'
                : `${preview.used_in_requests.length} request${preview.used_in_requests.length > 1 ? 's' : ''}: #${preview.used_in_requests.map(i => i + 1).join(', #')}`}
            </span>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '8px' }}>
        <Btn small onClick={addRule} disabled={!preview}>
          <CheckCircle size={12} /> Add Rule
        </Btn>
        <Btn small variant="ghost" onClick={onClose}>Cancel</Btn>
      </div>
    </div>
  )
}

// Renders text with correlated variable tokens replaced by highlighted ${VAR} spans
function HighlightedValue({ text, rows, testVars = [] }: { text: string; rows: CorrRow[]; testVars?: TestVar[] }) {
  type Seg = { t: string; varName?: string; kind?: 'corr' | 'p_var' }
  let segs: Seg[] = [{ t: text }]

  // Correlated-value substitutions (orange)
  for (const row of rows.filter(r => r.enabled && (r.original.extract_value?.length ?? 0) > 8)) {
    const token = row.original.extract_value
    const next: Seg[] = []
    for (const seg of segs) {
      if (seg.varName || !seg.t.includes(token)) { next.push(seg); continue }
      const parts = seg.t.split(token)
      parts.forEach((p, i) => {
        if (i > 0) next.push({ t: `\${${row.varName}}`, varName: row.varName, kind: 'corr' })
        if (p) next.push({ t: p })
      })
    }
    segs = next
  }

  // P_ variable substitutions (purple)
  for (const tv of testVars.filter(v => v.name && v.value && v.value.length >= 3)) {
    const token = tv.value
    const next: Seg[] = []
    for (const seg of segs) {
      if (seg.varName || !seg.t.includes(token)) { next.push(seg); continue }
      const parts = seg.t.split(token)
      parts.forEach((p, i) => {
        if (i > 0) next.push({ t: `\${${tv.name}}`, varName: tv.name, kind: 'p_var' })
        if (p) next.push({ t: p })
      })
    }
    segs = next
  }

  return (
    <span style={{ fontFamily: 'monospace', fontSize: '12px', wordBreak: 'break-all' }}>
      {segs.map((s, i) => {
        if (!s.varName) return <span key={i} style={{ color: 'var(--text-secondary)' }}>{s.t}</span>
        const isP = s.kind === 'p_var'
        return (
          <span key={i} style={{
            color: isP ? '#a78bfa' : '#e36209',
            background: isP ? 'rgba(139,92,246,0.13)' : 'rgba(227,98,9,0.13)',
            borderRadius: '3px', padding: '0 3px', fontWeight: 600,
          }}>{s.t}</span>
        )
      })}
    </span>
  )
}

const EXTRACTOR_LABEL: Record<string, string> = {
  json: 'JSONPath', css: 'CSS', header: 'Header', header_regex: 'Header regex', boundary: 'Boundary',
  regex: 'Regex', jwt_claim: 'JWT claim', cookie_header: 'Cookie → header', function: 'Generated',
}
const EXTRACTOR_HELP: Record<string, string> = {
  json: 'JSON Extractor (JSONPath) on the response body',
  css: 'CSS/JQuery Extractor — e.g. hidden form input or meta tag',
  header: 'Regular Expression Extractor on a response header',
  header_regex: 'Regular Expression Extractor on response headers (e.g. a redirect parameter)',
  boundary: 'Boundary Extractor — text between left/right boundaries',
  regex: 'Regular Expression Extractor on the response body',
  jwt_claim: 'Decodes a JWT from the response and reads one claim',
  cookie_header: 'Reads a cookie set by the response and sends it as a header (double-submit CSRF)',
  function: 'Generated by the browser — recreated at runtime with a JMeter function (__time / __UUID)',
}

function ConfidenceBadge({ score }: { score: number }) {
  const tone = score >= 75 ? 'success' : score >= 50 ? 'warning' : 'danger'
  return (
    <span title={`Confidence ${score}/100 that this value must be correlated`}
      style={{ fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '10px', whiteSpace: 'nowrap', flexShrink: 0, fontVariantNumeric: 'tabular-nums',
        background: `rgba(var(--${tone}-rgb),0.12)`, color: `var(--${tone})`, border: `1px solid rgba(var(--${tone}-rgb),0.3)` }}>
      {score}
    </span>
  )
}

function ReplayPanel({ sessionId, requests, busy, beforeReplay, onApplied }: {
  sessionId: string
  requests: RequestPreview[]
  busy: boolean
  beforeReplay: () => Promise<void>
  onApplied: (values: CorrelatedValue[]) => void
}) {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<ReplayFixResult | null>(null)
  const [showAll, setShowAll] = useState(false)

  const run = async () => {
    setRunning(true); setError('')
    try {
      await beforeReplay()
      const res = await replayFix(sessionId)
      setResult(res)
      onApplied(res.values)
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  const final = result?.final
  const added = result?.iterations.flatMap(it => it.added) ?? []
  const repaired = result?.iterations.flatMap(it => it.repaired) ?? []
  const failing = final?.results.filter(r => !r.ok) ?? []
  const rows = showAll ? final?.results ?? [] : failing

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', marginTop: '16px' }}>
      <div style={{ padding: '14px 16px', background: 'var(--bg-elevated)', display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
        <RotateCcw size={18} style={{ color: 'var(--accent)', flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: '240px' }}>
          <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>Replay &amp; auto-fix</div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
            Runs the script once with JMeter (1 user, 1 iteration), compares every response with the recording,
            repairs extractors and adds missed correlations — repeating until it replays cleanly.
            This sends real requests to the recorded application.
          </div>
        </div>
        <Btn small onClick={run} disabled={running || busy}>
          {running ? <><Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> Replaying…</> : <><Play size={12} /> {result ? 'Replay again' : 'Replay & fix'}</>}
        </Btn>
      </div>

      {error && <div style={{ padding: '12px 16px' }}><ErrorBox msg={error} /></div>}

      {result && final && (
        <div style={{ padding: '14px 16px', borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: '13px', fontWeight: 700, color: final.failed === 0 ? 'var(--success)' : 'var(--danger)', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              {final.failed === 0 ? <CheckCircle size={15} /> : <XCircle size={15} />}
              {final.passed}/{final.passed + final.failed} requests replay correctly
            </span>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              {result.iterations.length} replay{result.iterations.length !== 1 ? 's' : ''} ·
              {' '}{final.verified.length} verified dynamic · {final.static.length} unchanged
            </span>
            {result.iterations.some(it => it.rolled_back) && (
              <span style={{ fontSize: '11px', color: '#d29922' }}>Last changes made things worse and were rolled back</span>
            )}
            {result.unverified_changes && (
              <span style={{ fontSize: '11px', color: '#d29922' }}>Fixes from the last pass are not verified yet — replay again</span>
            )}
          </div>

          {(added.length > 0 || repaired.length > 0) && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {added.map(a => (
                <div key={`a-${a.var_name}`} style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                  <span style={{ color: 'var(--success)', fontWeight: 700 }}>+ added</span>
                  <span style={{ fontFamily: 'monospace', color: 'var(--accent)' }}>${'{'}{a.var_name}{'}'}</span>
                  <span style={{ color: 'var(--text-muted)' }}>from #{a.source_request_index + 1} — recorded</span>
                  <code style={{ fontSize: '11px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '180px' }}>{a.recorded}</code>
                  <span style={{ color: 'var(--text-muted)' }}>replayed</span>
                  <code style={{ fontSize: '11px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '180px' }}>{a.replayed}</code>
                </div>
              ))}
              {repaired.map(r => (
                <div key={`r-${r.var_name}`} style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <Wrench size={12} style={{ color: '#d29922' }} />
                  <span style={{ fontFamily: 'monospace', color: 'var(--accent)' }}>${'{'}{r.var_name}{'}'}</span>
                  <span style={{ color: 'var(--text-muted)' }}>extractor repaired: {EXTRACTOR_LABEL[r.from] ?? r.from} → {EXTRACTOR_LABEL[r.to] ?? r.to}</span>
                </div>
              ))}
            </div>
          )}

          {final.broken.length > 0 && (
            <div style={{ fontSize: '12px', color: 'var(--danger)' }}>
              {final.broken.map(b => <div key={b.var_name}>${'{'}{b.var_name}{'}'} — {b.reason}</div>)}
            </div>
          )}

          {(failing.length > 0 || showAll) && (
            <div style={{ border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden' }}>
              {rows.map(r => {
                const req = requests.find(q => q.index === r.index)
                return (
                  <div key={r.index} style={{ padding: '8px 12px', borderTop: '1px solid var(--border)', fontSize: '12px' }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      {r.ok ? <CheckCircle size={13} style={{ color: 'var(--success)', flexShrink: 0 }} /> : <XCircle size={13} style={{ color: 'var(--danger)', flexShrink: 0 }} />}
                      <MethodBadge method={req?.method ?? r.method} />
                      <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }} title={r.path}>#{r.index + 1} {r.path}</span>
                      <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>recorded {r.recorded_status} · replay {r.replay_status ?? '—'}</span>
                    </div>
                    {!r.ok && r.reason && <div style={{ marginTop: '4px', color: 'var(--danger)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{r.reason.split('\n')[0]}</div>}
                    {(r.hints ?? []).map((h, i) => <div key={i} style={{ marginTop: '3px', color: '#d29922' }}>Hint: {h}</div>)}
                  </div>
                )
              })}
            </div>
          )}
          {final.results.length > 0 && (
            <button onClick={() => setShowAll(s => !s)} style={{ alignSelf: 'flex-start', background: 'transparent', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: '12px', padding: 0 }}>
              {showAll ? 'Show failures only' : `Show all ${final.results.length} requests`}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function RuleCard({ row, requests, isMandatory, stage, onToggle, onRename, onDelete, onToggleLock }: {
  row: CorrRow; requests: RequestPreview[]; isMandatory: boolean; stage: Stage | null
  onToggle: (id: string) => void; onRename: (id: string, name: string) => void
  onDelete: (id: string) => void; onToggleLock: (id: string) => void
}) {
  const [confirmingUnlock, setConfirmingUnlock] = useState(false)
  const isLocked = isMandatory && !row.manualUnlocked
  const srcReq = requests.find(r => r.index === row.original.source_request_index)
  let srcPath = ''
  try { srcPath = new URL(srcReq?.url ?? '', 'http://x').pathname } catch { /* */ }

  return (
    <div style={{ borderTop: '1px solid var(--border)' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: '10px', padding: '9px 16px',
        opacity: row.enabled ? 1 : 0.5,
      }}>
        <span style={{ color: 'var(--border)', fontSize: '14px', letterSpacing: '-3px', userSelect: 'none', cursor: 'grab' }}>⠿⠿</span>
        <span style={{ fontSize: '10px', fontWeight: 800, padding: '2px 7px', borderRadius: '3px', background: 'rgba(227,98,9,0.15)', color: '#e36209', border: '1px solid rgba(227,98,9,0.3)', whiteSpace: 'nowrap', letterSpacing: '0.05em', flexShrink: 0 }}>
          CORRELATION
        </span>
        {srcPath && (
          <span style={{ fontSize: '11px', padding: '2px 9px', borderRadius: '10px', background: 'rgba(227,98,9,0.08)', color: '#e36209', border: '1px solid rgba(227,98,9,0.25)', fontFamily: 'monospace', whiteSpace: 'nowrap', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', flexShrink: 0 }} title={srcReq?.url}>
            {srcReq?.method} {srcPath}
          </span>
        )}
        <span style={{ fontSize: '12px', color: 'var(--text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '5px' }}>
          Correlate <EditableVarName value={row.varName} onChange={name => onRename(row.id, name)} />
          <span style={{ color: 'var(--text-muted)' }}>from {row.original.extract_from} ·</span>
          <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-muted)' }}>{(row.original.extract_key ?? '').slice(0, 32)}</span>
        </span>
        {stage !== null && (() => {
          const b = typeof stage === 'number'
            ? { label: `STEP ${stage}`, title: `Auto-correlation step ${stage}: ${AUTO_STEPS[stage - 1].title}`, color: '#8b949e' }
            : STAGE_BADGE[stage]
          return (
            <span title={b.title} style={{ fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '3px', background: `${b.color}22`, color: b.color, border: `1px solid ${b.color}55`, whiteSpace: 'nowrap', flexShrink: 0 }}>{b.label}</span>
          )
        })()}
        {row.original.extractor && (
          <span title={EXTRACTOR_HELP[row.original.extractor] ?? row.original.extractor}
            style={{ fontSize: '10px', fontWeight: 600, padding: '2px 6px', borderRadius: '3px', background: 'rgba(255,255,255,0.05)', color: 'var(--text-secondary)', border: '1px solid var(--border)', whiteSpace: 'nowrap', flexShrink: 0, fontFamily: 'monospace' }}>
            {EXTRACTOR_LABEL[row.original.extractor] ?? row.original.extractor}
          </span>
        )}
        {/(static|same)_in_both/.test(row.original.detection_pass ?? '') && (
          <span title={isStaticInBoth(row.original)
              ? 'Identical in both recordings, so probably static — switched off. Turn it on if it depends on the account or data you recorded with.'
              : 'A token with the same value in both recordings — kept on. If both runs shared a browser session, record the second one again after logging out.'}
            style={{ fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '3px', whiteSpace: 'nowrap', flexShrink: 0,
              background: isStaticInBoth(row.original) ? 'rgba(255,255,255,0.05)' : 'rgba(var(--warning-rgb),0.12)',
              color: isStaticInBoth(row.original) ? 'var(--text-muted)' : 'var(--warning)',
              border: `1px solid ${isStaticInBoth(row.original) ? 'var(--border)' : 'rgba(var(--warning-rgb),0.3)'}` }}>
            SAME IN BOTH
          </span>
        )}
        {row.original.detection_pass?.includes('replay_verified') && (
          <span title="Value changed when the script was replayed — confirmed dynamic"
            style={{ fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '3px', background: 'rgba(var(--success-rgb),0.12)', color: 'var(--success)', border: '1px solid rgba(var(--success-rgb),0.3)', whiteSpace: 'nowrap', flexShrink: 0 }}>
            ✓ REPLAY
          </span>
        )}
        {typeof row.original.confidence_score === 'number' && <ConfidenceBadge score={row.original.confidence_score} />}
        {isMandatory && (
          <button
            title={isLocked ? 'Confirmed dynamic — click to unlock' : 'Unlocked — click to lock'}
            onClick={() => {
              if (isLocked) { setConfirmingUnlock(true) }
              else { onToggleLock(row.id); setConfirmingUnlock(false) }
            }}
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '14px', padding: '2px 4px', flexShrink: 0, opacity: 0.85, lineHeight: 1 }}
            onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
            onMouseLeave={e => (e.currentTarget.style.opacity = '0.85')}
          >
            {isLocked ? '🔒' : '🔓'}
          </button>
        )}
        <UsedInBadge indices={row.original.used_in_requests} requests={requests} />
        <button onClick={() => !isLocked && onToggle(row.id)} title={isLocked ? 'Unlock to enable/disable' : row.enabled ? 'Disable' : 'Enable'}
          style={{ width: '38px', height: '21px', borderRadius: '11px', border: 'none', cursor: isLocked ? 'not-allowed' : 'pointer', position: 'relative', background: row.enabled ? 'var(--accent)' : '#30363d', transition: 'background 0.15s', flexShrink: 0, padding: 0, opacity: isLocked ? 0.5 : 1 }}>
          <span style={{ position: 'absolute', top: '2.5px', left: row.enabled ? '19px' : '2.5px', width: '16px', height: '16px', borderRadius: '50%', background: row.enabled ? '#fff' : '#8b949e', transition: 'left 0.15s' }} />
        </button>
        {!isLocked && (
          <button onClick={() => onDelete(row.id)} style={{ background: 'transparent', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: '16px', lineHeight: 1, padding: '2px 4px', opacity: 0.45, flexShrink: 0 }}
            onMouseEnter={e => (e.currentTarget.style.opacity = '1')} onMouseLeave={e => (e.currentTarget.style.opacity = '0.45')}>×</button>
        )}
      </div>

      {confirmingUnlock && (
        <div style={{ margin: '0 16px 10px 42px', padding: '10px 14px', background: 'rgba(var(--warning-rgb),0.08)', border: '1px solid rgba(var(--warning-rgb),0.3)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ fontSize: '13px' }}>⚠️</span>
          <span style={{ fontSize: '12px', color: 'var(--text-secondary)', flex: 1 }}>
            This value was confirmed dynamic between HAR recordings. Unlocking lets you disable or delete it, but it may cause script failures.
          </span>
          <button onClick={() => { onToggleLock(row.id); setConfirmingUnlock(false) }}
            style={{ fontSize: '12px', fontWeight: 600, padding: '4px 12px', borderRadius: '6px', border: 'none', cursor: 'pointer', background: 'rgba(var(--warning-rgb),0.2)', color: '#d29922' }}>
            Unlock
          </button>
          <button onClick={() => setConfirmingUnlock(false)}
            style={{ fontSize: '12px', padding: '4px 12px', borderRadius: '6px', border: '1px solid var(--border)', cursor: 'pointer', background: 'transparent', color: 'var(--text-muted)' }}>
            Keep Locked
          </button>
        </div>
      )}
    </div>
  )
}

// The rule-based auto-correlation engine, as five user-facing steps
const AUTO_STEPS: { title: string; detail: string; match: (pass: string) => boolean }[] = [
  { title: 'Tokens & JWTs', detail: 'Bearer tokens, JWTs and the claims inside them',
    match: p => p.startsWith('pass1') },
  { title: 'Response values', detail: 'JSON response fields, and IDs that reappear in URL paths',
    match: p => p.startsWith('pass2') || p.startsWith('pass_url') },
  { title: 'Headers, redirects & cookies', detail: 'Response headers, redirect parameters and cookie → header tokens (CSRF)',
    match: p => p.startsWith('pass3') },
  { title: 'Text, forms & framework rules', detail: 'Plain-text patterns, hidden form fields, meta tags and framework rule packs',
    match: p => p.startsWith('pass4') || p.startsWith('pass_html') || p.startsWith('pass_rule') },
  { title: 'Backward search', detail: 'Values a request sends, traced back to the response that produced them — plus browser-generated timestamps and IDs',
    match: p => p.startsWith('pass_request_scan') || p.startsWith('pass_client') || p.startsWith('pass5') },
]

type Stage = number | 'ai' | 'compare' | 'manual' | 'replay'

/** Which part of correlation found a value: auto step 1–5, AI, recording comparison, replay or a custom rule. */
function stageOf(pass: string | undefined): Stage | null {
  const p = (pass ?? '').split(',')[0]
  if (p.startsWith('pass6')) return 'ai'
  if (p.startsWith('custom_rule') || p === 'manual') return 'manual'
  if (p.startsWith('pass_comparison')) return 'compare'
  if (p.startsWith('pass_replay')) return 'replay'
  const i = AUTO_STEPS.findIndex(st => st.match(p))
  return i >= 0 ? i + 1 : null
}

const STAGE_BADGE: Record<string, { label: string; title: string; color: string }> = {
  ai: { label: 'AI', title: 'Found by AI auto-correlation', color: '#a78bfa' },
  compare: { label: 'COMPARE', title: 'Found by comparing the two recordings', color: '#d29922' },
  manual: { label: 'CUSTOM', title: 'Your custom rule', color: '#3ccf7a' },
  replay: { label: 'REPLAY', title: 'Added by Replay & auto-fix', color: '#39c5cf' },
}

function AutoCorrelationSteps({ values, ran, running }: { values: CorrelatedValue[]; ran: boolean; running: boolean }) {
  const counts = AUTO_STEPS.map((_, i) => values.filter(v => stageOf(v.detection_pass) === i + 1).length)
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '10px' }}>
      {AUTO_STEPS.map((st, i) => (
        <div key={st.title} style={{ padding: '12px 14px', borderRadius: '12px', background: 'var(--bg-surface)', border: `1px solid ${ran && counts[i] > 0 ? 'rgba(var(--accent-rgb),0.35)' : 'var(--border)'}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
            <span style={{ width: '22px', height: '22px', borderRadius: '50%', flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', fontWeight: 700, background: ran ? 'rgba(var(--success-rgb),0.15)' : 'rgba(255,255,255,0.06)', color: ran ? 'var(--success)' : 'var(--text-muted)' }}>
              {running ? <Loader2 size={11} style={{ animation: 'spin 1s linear infinite' }} /> : ran ? <CheckCircle size={12} /> : i + 1}
            </span>
            <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{st.title}</span>
            {ran && <span style={{ marginLeft: 'auto', fontSize: '13px', fontWeight: 700, color: counts[i] ? 'var(--accent)' : 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{counts[i]}</span>}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.5 }}>{st.detail}</div>
        </div>
      ))}
    </div>
  )
}

function StepCorrelate() {
  const { sessionId, secondSessionId, correlatedValues, setCorrelated, setStep, requests, testVars,
          correlationAssessment, setCorrelationAssessment } = useAppStore()
  const [loading, setLoading] = useState(false)
  const [aiRunning, setAiRunning] = useState(false)
  const [error, setError] = useState('')
  const [aiError, setAiError] = useState('')
  const [aiResult, setAiResult] = useState<{ added: number; provider: string } | null>(null)
  const [done, setDone] = useState(false)
  const [autoRan, setAutoRan] = useState(false)
  const [comparisonDone, setComparisonDone] = useState(false)
  const [rows, setRows] = useState<CorrRow[]>([])
  const [secondValues, setSecondValues] = useState<CorrelatedValue[]>([])
  const [provider, setProvider] = useState('groq')
  const [hasKey, setHasKey] = useState(true)
  const [showRuleForm, setShowRuleForm] = useState(false)
  // Request detail panel
  const [selectedReqIdx, setSelectedReqIdx] = useState<number | null>(null)
  const [reqDetail, setReqDetail] = useState<RequestDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reqSubTab, setReqSubTab] = useState<'headers' | 'payload' | 'cookies'>('headers')
  const [resSubTab, setResSubTab] = useState<'headers' | 'content' | 'cookies'>('headers')

  useEffect(() => {
    if (correlatedValues.length > 0 && !done) {
      setRows(makeRows(correlatedValues))
      setDone(true)
      setAutoRan(correlatedValues.some(v => typeof stageOf(v.detection_pass) === 'number'))
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    getSettings().then(s => {
      const p = s.ai_provider || 'groq'
      setProvider(p)
      resolveKey(p, s)
    }).catch(() => {})
  }, [])

  const resolveKey = (p: string, s: { groq_api_key?: string; gemini_api_key?: string; anthropic_api_key?: string; openai_api_key?: string }) => {
    if (p === 'ollama')      setHasKey(true)
    else if (p === 'groq')   setHasKey(!!s.groq_api_key)
    else if (p === 'gemini') setHasKey(!!s.gemini_api_key)
    else if (p === 'claude') setHasKey(!!s.anthropic_api_key)
    else if (p === 'openai') setHasKey(!!s.openai_api_key)
    else                     setHasKey(true)
  }

  const handleProviderChange = async (p: string) => {
    setProvider(p); setAiError('')
    try {
      await updateSettings({ ai_provider: p })
      const s = await getSettings()
      resolveKey(p, s)
    } catch { /* ignore */ }
  }

  // Auto-correlation: the 5-step rule-based engine (with a second recording, values that
  // differ between the two are confirmed dynamic). Never sends traffic to an AI provider.
  const runAutoCorrelation = async () => {
    if (!sessionId) return
    setLoading(true); setError(''); setAiResult(null); setAiError('')
    try {
      if (secondSessionId) {
        const res = await correlateCompare(sessionId, secondSessionId)
        setRows(makeRows(res.values))
        setCorrelated(res.values)
        setCorrelationAssessment(res.assessment ?? null)
        const secondMap: CorrelatedValue[] = res.values
          .filter(v => v.extract_value_2 !== undefined)
          .map(v => ({ ...v, extract_value: v.extract_value_2 ?? '' }))
        setSecondValues(secondMap)
        setComparisonDone(true)
      } else {
        const res = await correlate(sessionId)
        setRows(makeRows(res.values))
        setCorrelated(res.values)
        setCorrelationAssessment(res.assessment ?? null)
      }
      setDone(true); setAutoRan(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // AI auto-correlation: a separate feature that adds what the AI finds on top of the
  // current list. The list is saved first so the AI skips values already covered.
  const runAiCorrelation = async () => {
    if (!sessionId) return
    setAiRunning(true); setAiError(''); setAiResult(null)
    try {
      await persistSelection()
      const res = await aiCorrelate(sessionId, provider)
      const added = makeRows(res.added).map((r, i) => ({ ...r, id: `ai${Date.now()}-${i}` }))
      setRows(prev => [...prev, ...added])
      setCorrelated(res.values)
      setCorrelationAssessment(res.assessment ?? null)
      setAiResult({ added: res.added.length, provider: res.provider })
      setDone(true)
    } catch (e) {
      setAiError(e instanceof Error ? e.message : String(e))
    } finally {
      setAiRunning(false)
    }
  }

  const selectRequest = async (idx: number) => {
    if (!sessionId || idx === selectedReqIdx) return
    setSelectedReqIdx(idx); setReqDetail(null); setDetailLoading(true)
    try { setReqDetail(await getRequestDetail(sessionId, idx)); setReqSubTab('headers'); setResSubTab('headers') }
    catch { /* ignore */ }
    finally { setDetailLoading(false) }
  }

  const handleToggle = (id: string) => setRows(prev => prev.map(r => {
    if (r.id !== id) return r
    if (r.original.is_confirmed_dynamic && !r.manualUnlocked) return r
    if (!r.enabled && isStaticInBoth(r.original)) {
      // Switched back on by the user: keep it on when the list is saved and reloaded
      const pass = (r.original.detection_pass ?? '').replace(',static_in_both', ',kept_by_user')
      return { ...r, enabled: true, original: { ...r.original, detection_pass: pass } }
    }
    return { ...r, enabled: !r.enabled }
  }))
  const handleRename = (id: string, name: string) => setRows(prev => prev.map(r => r.id === id ? { ...r, varName: name } : r))
  const handleDelete = (id: string) => setRows(prev => prev.filter(r => r.id !== id))
  const handleAdd    = (row: CorrRow) => { setRows(prev => [...prev, row]); setDone(true) }
  const handleToggleLock = (id: string) => setRows(prev => prev.map(r => r.id === id ? { ...r, manualUnlocked: !r.manualUnlocked } : r))

  // Rows the user kept (locked rows are always kept), with their edited variable names
  const selectedValues = (): CorrelatedValue[] => rows
    .filter(r => (r.original.is_confirmed_dynamic && !r.manualUnlocked) || r.enabled)
    .map(r => ({ ...r.original, var_name: r.varName }))

  // The server generates and replays from its own copy of the correlations, so edits
  // (renames, disabled rows, custom rules) must be saved before either happens.
  const persistSelection = async () => {
    if (!sessionId) return
    const res = await saveCorrelations(sessionId, selectedValues())
    setCorrelated(res.values)
  }

  const [saving, setSaving] = useState(false)
  const applyAndNext = async () => {
    setSaving(true); setError('')
    try {
      await persistSelection()
      setStep(2)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const enabledCount = rows.filter(r => r.enabled).length
  const anyBusy = loading || aiRunning
  const pInfo = PROVIDER_LABELS[provider] ?? { label: provider, subtitle: '', tier: 'free' }
  const rowValues = rows.map(r => r.original)
  const extraCounts = (['compare', 'manual', 'replay'] as const)
    .map(k => [k, rowValues.filter(v => stageOf(v.detection_pass) === k).length] as const)
    .filter(([, n]) => n > 0)

  type DiffRow = CorrRow & { secondValue: string | null; isDynamic: boolean; isMandatory: boolean }
  const buildDiff = (): DiffRow[] => rows.map(r => {
    const match = secondValues.find(s => s.extract_key === r.original.extract_key)
    const isConfirmed = r.original.is_confirmed_dynamic === true
    const valuesDiffer = !!match && match.extract_value !== r.original.extract_value
    return {
      ...r,
      secondValue: r.original.extract_value_2 ?? match?.extract_value ?? null,
      isDynamic: isConfirmed || valuesDiffer,
      isMandatory: isConfirmed,
    }
  })

  const sectionHeadSt: React.CSSProperties = {
    fontSize: '22px', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 6px 0', letterSpacing: '-0.01em',
  }
  const sectionDescSt: React.CSSProperties = {
    fontSize: '13px', color: 'var(--text-muted)', margin: 0,
  }

  return (
    <motion.div {...fadeSlide}>
      <SectionTitle>Correlate Values</SectionTitle>

      {/* ── Auto-correlation: the 5-step rule engine ─────────────────── */}
      <Card style={{ marginBottom: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px', marginBottom: '16px', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: '260px' }}>
            <h2 style={sectionHeadSt}>Auto-correlation</h2>
            <p style={sectionDescSt}>
              Five rule-based steps find values the server hands out and the browser sends back.
              Runs locally — nothing leaves your machine.
              {secondSessionId && ' With the second recording, values that differ between the two runs are confirmed dynamic, and values that stay the same are switched off as probably static.'}
            </p>
          </div>
          <Btn onClick={runAutoCorrelation} disabled={anyBusy || !sessionId}>
            {loading ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> Correlating…</> : <><Zap size={14} /> {autoRan ? 'Re-run auto-correlation' : 'Run auto-correlation'}</>}
          </Btn>
        </div>
        <AutoCorrelationSteps values={rowValues} ran={autoRan && !loading} running={loading} />
        {autoRan && !loading && (
          <div style={{ marginTop: '12px', fontSize: '13px', color: 'var(--text-secondary)', display: 'flex', gap: '14px', flexWrap: 'wrap' }}>
            <span><strong style={{ color: 'var(--text-primary)' }}>{rowValues.filter(v => typeof stageOf(v.detection_pass) === 'number').length}</strong> found by the 5 steps</span>
            {extraCounts.map(([k, n]) => <span key={k}>+{n} {STAGE_BADGE[k].title.toLowerCase()}</span>)}
          </div>
        )}
        {autoRan && !loading && correlationAssessment && <CorrelationConfidence a={correlationAssessment} />}
        {autoRan && rows.length > 0 && !loading && (
          <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-muted)' }}>Re-running replaces the list below, including AI results and edits.</div>
        )}
        {error && <div style={{ marginTop: '12px' }}><ErrorBox msg={error} /></div>}
      </Card>

      {/* ── AI auto-correlation: separate, opt-in ────────────────────── */}
      <Card style={{ marginBottom: '32px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px', flexWrap: 'wrap' }}>
          <div style={{ width: '40px', height: '40px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(139,92,246,0.12)', border: '1px solid rgba(139,92,246,0.3)', flexShrink: 0 }}>
            <Sparkles size={17} style={{ color: '#a78bfa' }} />
          </div>
          <div style={{ flex: 1, minWidth: '260px' }}>
            <h2 style={sectionHeadSt}>AI auto-correlation</h2>
            <p style={sectionDescSt}>
              Asks an AI model for dynamic values the rules missed and adds them to the list, tagged AI.
              It sends summaries of your requests and responses to {pInfo.label}{pInfo.tier === 'free' && provider === 'ollama' ? ' (runs locally)' : ''}.
              {!autoRan && ' Run auto-correlation first so the AI only looks for what is left.'}
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <select value={provider} onChange={e => handleProviderChange(e.target.value)} aria-label="AI provider"
              style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '8px', padding: '7px 10px', color: 'var(--text-primary)', fontSize: '13px', outline: 'none', cursor: 'pointer' }}>
              {Object.entries(PROVIDER_LABELS).map(([k, info]) => (
                <option key={k} value={k}>{info.label} · {info.subtitle}</option>
              ))}
            </select>
            <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px', background: pInfo.tier === 'free' ? 'rgba(var(--success-rgb),0.15)' : 'rgba(var(--warning-rgb),0.15)', color: pInfo.tier === 'free' ? 'var(--success)' : '#d2991a', border: `1px solid ${pInfo.tier === 'free' ? 'rgba(var(--success-rgb),0.3)' : 'rgba(var(--warning-rgb),0.3)'}` }}>
              {pInfo.tier.toUpperCase()}
            </span>
            <Btn variant="ghost" onClick={runAiCorrelation} disabled={anyBusy || !sessionId || !hasKey}>
              {aiRunning ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> AI is analysing…</> : <><Sparkles size={14} /> Run AI auto-correlation</>}
            </Btn>
          </div>
        </div>
        {!hasKey && (
          <div style={{ marginTop: '12px', fontSize: '13px', color: 'var(--danger)' }}>
            No API key for {pInfo.label} — add one in <a href="/settings" style={{ color: 'var(--accent)' }}>Settings</a>, or pick another provider.
          </div>
        )}
        {aiError && <div style={{ marginTop: '12px' }}><ErrorBox msg={aiError} /></div>}
        {aiResult && (
          <div style={{ marginTop: '12px' }}>
            <InfoBox msg={aiResult.added > 0
              ? `${PROVIDER_LABELS[aiResult.provider]?.label ?? aiResult.provider} added ${aiResult.added} correlation${aiResult.added !== 1 ? 's' : ''} — tagged AI in the list below. Review them before generating the script.`
              : `${PROVIDER_LABELS[aiResult.provider]?.label ?? aiResult.provider} found nothing the current correlations do not already cover.`} />
          </div>
        )}
      </Card>

      {/* ── Dynamic values confirmed by comparing two recordings ─────── */}
      {secondSessionId && comparisonDone && (
      <div style={{ marginBottom: '32px' }}>
        <div style={{ marginBottom: '16px' }}>
          <h2 style={sectionHeadSt}>Dynamic Values</h2>
          <p style={sectionDescSt}>Values that changed between your two HAR recordings — confirmed candidates for correlation</p>
        </div>
          <div style={{ border: '1px solid var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
            <div style={{ padding: '10px 16px', background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                {buildDiff().filter(r => r.isDynamic).length} dynamic value{buildDiff().filter(r => r.isDynamic).length !== 1 ? 's' : ''} detected
              </span>
              {rows.filter(r => isStaticInBoth(r.original)).length > 0 && (
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  · {rows.filter(r => isStaticInBoth(r.original)).length} identical in both recordings (switched off)
                </span>
              )}
              {buildDiff().filter(r => r.isMandatory).length > 0 && (
                <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '8px', background: 'rgba(var(--danger-rgb),0.1)', border: '1px solid rgba(var(--danger-rgb),0.3)', color: 'var(--danger)' }}>
                  🔒 {buildDiff().filter(r => r.isMandatory).length} confirmed dynamic — mandatory in script
                </span>
              )}
            </div>
            {buildDiff().filter(r => r.isDynamic).length === 0 ? (
              <div style={{ padding: '16px 20px', fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                No values differed between the two HARs — all detected correlations appear static.
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                  <thead>
                    <tr style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}>
                      {['Variable', 'Source Request', 'HAR 1 Value', 'HAR 2 Value', 'Status', 'Used In'].map(h => (
                        <th key={h} style={{ padding: '7px 12px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: '11px', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {buildDiff().filter(r => r.isDynamic).map(row => {
                      const srcReq = requests.find(r => r.index === row.original.source_request_index)
                      let srcPath = `#${row.original.source_request_index + 1}`
                      try { srcPath = new URL(srcReq?.url ?? '', 'http://x').pathname } catch { /* */ }
                      return (
                        <tr key={row.id} style={{ borderBottom: '1px solid var(--border)', background: row.isMandatory ? 'rgba(var(--danger-rgb),0.025)' : 'transparent' }}>
                          <td style={{ padding: '8px 12px', whiteSpace: 'nowrap' }}>
                            <span style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--accent)', fontWeight: 600 }}>${'{'}{ row.varName }{'}'}</span>
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                              {srcReq && <MethodBadge method={srcReq.method} />}
                              <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-secondary)', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={srcReq?.url}>{srcPath}</span>
                            </div>
                          </td>
                          <td style={{ padding: '8px 12px', maxWidth: '160px' }}>
                            <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-muted)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.original.extract_value}>{row.original.extract_value || '—'}</span>
                          </td>
                          <td style={{ padding: '8px 12px', maxWidth: '160px' }}>
                            {row.secondValue
                              ? <span style={{ fontFamily: 'monospace', fontSize: '11px', color: '#d29922', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.secondValue}>{row.secondValue}</span>
                              : <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic' }}>not found</span>}
                          </td>
                          <td style={{ padding: '8px 12px', whiteSpace: 'nowrap' }}>
                            {row.isMandatory
                              ? <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px', background: 'rgba(var(--danger-rgb),0.12)', border: '1px solid rgba(var(--danger-rgb),0.35)', color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '4px', width: 'fit-content' }}>🔒 Confirmed</span>
                              : <span style={{ fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '10px', background: 'rgba(var(--warning-rgb),0.12)', border: '1px solid rgba(var(--warning-rgb),0.35)', color: '#d29922' }}>Dynamic</span>}
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <UsedInBadge indices={row.original.used_in_requests} requests={requests} />
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
      </div>
      )}

      {/* ── Correlation rules: inspect requests and review every rule ── */}
      <div>
        <div style={{ marginBottom: '16px' }}>
          <h2 style={sectionHeadSt}>Correlation rules</h2>
          <p style={sectionDescSt}>Review, rename or switch off what was found, or add your own rule — click a request to see correlated variables highlighted</p>
        </div>

        {/* k6 Studio panel */}
        <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden' }}>

          {/* Two-column: request list | request detail */}
          <div style={{ display: 'flex', height: '440px' }}>

            {/* Left: request list */}
            <div style={{ width: '320px', flexShrink: 0, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ padding: '10px 14px', background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center' }}>
                <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)' }}>Requests ({requests.length})</span>
                {done && <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginLeft: 'auto' }}>{enabledCount} active</span>}
              </div>
              <div style={{ overflowY: 'auto', flex: 1 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}>
                      {['Method', 'Status', 'Path', ''].map((h, i) => (
                        <th key={i} style={{ padding: '6px 10px', textAlign: 'left', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {requests.map(r => {
                      let path = r.url
                      try { path = new URL(r.url, 'http://x').pathname } catch { /* */ }
                      const isSelected = r.index === selectedReqIdx
                      const hasCor = rows.some(row => row.enabled && (row.original.source_request_index === r.index || row.original.used_in_requests.includes(r.index)))
                      return (
                        <tr key={r.index} onClick={() => selectRequest(r.index)}
                          style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', borderLeft: `3px solid ${isSelected ? '#e36209' : 'transparent'}`, background: isSelected ? 'rgba(227,98,9,0.06)' : 'transparent', transition: 'background 0.1s' }}
                          onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'rgba(255,255,255,0.025)' }}
                          onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}>
                          <td style={{ padding: '7px 10px' }}><MethodBadge method={r.method} /></td>
                          <td style={{ padding: '7px 6px' }}>
                            <span style={{ fontSize: '11px', fontWeight: 700, color: r.status_code < 300 ? 'var(--success)' : r.status_code < 400 ? '#d29922' : 'var(--danger)' }}>{r.status_code}</span>
                          </td>
                          <td style={{ padding: '7px 10px', maxWidth: '120px' }}>
                            <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-secondary)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.url}>{path}</span>
                          </td>
                          <td style={{ padding: '7px 8px', textAlign: 'center', width: '20px' }}>
                            {hasCor && <span title="Has correlation rules" style={{ fontSize: '11px', color: '#e36209' }}>✏</span>}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Right: request detail — Chrome DevTools style */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              {selectedReqIdx === null ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', gap: '10px' }}>
                  <Eye size={32} style={{ opacity: 0.25 }} />
                  <span style={{ fontSize: '13px' }}>Select a request to inspect</span>
                  <span style={{ fontSize: '12px', opacity: 0.6 }}>Correlated vars in orange · P_ variables in purple</span>
                </div>
              ) : detailLoading ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', padding: '20px 16px' }}>
                  <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /><span style={{ fontSize: '13px' }}>Loading…</span>
                </div>
              ) : reqDetail ? (
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  {/* ── REQUEST SECTION ── */}
                  <div style={{ borderBottom: '2px solid var(--border)' }}>
                    <div style={{ padding: '8px 14px 0', background: 'var(--bg-elevated)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-secondary)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>Request</span>
                    </div>
                    {/* Request sub-tabs */}
                    <div style={{ display: 'flex', background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border)', paddingLeft: '14px' }}>
                      {(['headers', 'payload', 'cookies'] as const).map(t => (
                        <button key={t} onClick={() => setReqSubTab(t)} style={{ padding: '7px 14px', border: 'none', background: 'transparent', fontSize: '12px', fontWeight: reqSubTab === t ? 600 : 400, color: reqSubTab === t ? 'var(--text-primary)' : 'var(--text-muted)', borderBottom: reqSubTab === t ? '2px solid #e36209' : '2px solid transparent', marginBottom: '-1px', cursor: 'pointer', textTransform: 'capitalize' }}>
                          {t}
                        </button>
                      ))}
                    </div>
                    <div style={{ padding: '12px 14px' }}>
                      {reqSubTab === 'headers' && (
                        <>
                          <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: '8px' }}>General</div>
                          <div style={{ marginBottom: '12px' }}>
                            {[['Request URL', reqDetail.url], ['Request Method', reqDetail.method]].map(([k, v]) => (
                              <div key={k} style={{ display: 'flex', gap: '12px', marginBottom: '5px' }}>
                                <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '140px', flexShrink: 0 }}>{k}</span>
                                <span style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{v}</span>
                              </div>
                            ))}
                          </div>
                          <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: '8px' }}>Headers</div>
                          {Object.entries(reqDetail.headers).map(([k, v]) => (
                            <div key={k} style={{ display: 'flex', gap: '12px', marginBottom: '5px', alignItems: 'flex-start' }}>
                              <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '140px', flexShrink: 0, paddingTop: '1px' }}>{k}</span>
                              <HighlightedValue text={v} rows={rows} testVars={testVars} />
                            </div>
                          ))}
                        </>
                      )}
                      {reqSubTab === 'payload' && (
                        reqDetail.body
                          ? <pre style={{ margin: 0, fontSize: '12px', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: 'var(--text-secondary)' }}>
                              <HighlightedValue text={reqDetail.body} rows={rows} testVars={testVars} />
                            </pre>
                          : <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No request body</span>
                      )}
                      {reqSubTab === 'cookies' && (
                        (() => {
                          const cookieHeader = Object.entries(reqDetail.headers).find(([k]) => k.toLowerCase() === 'cookie')?.[1] ?? ''
                          const cookies = cookieHeader ? cookieHeader.split(';').map(s => s.trim()).filter(Boolean) : []
                          return cookies.length > 0
                            ? <div>{cookies.map((c, i) => {
                                const eq = c.indexOf('=')
                                const name = eq >= 0 ? c.slice(0, eq) : c
                                const val = eq >= 0 ? c.slice(eq + 1) : ''
                                return (
                                  <div key={i} style={{ display: 'flex', gap: '12px', marginBottom: '5px' }}>
                                    <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '140px', flexShrink: 0 }}>{name}</span>
                                    <span style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{val}</span>
                                  </div>
                                )
                              })}</div>
                            : <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No cookies</span>
                        })()
                      )}
                    </div>
                  </div>

                  {/* ── RESPONSE SECTION ── */}
                  <div>
                    <div style={{ padding: '8px 14px 0', background: 'var(--bg-elevated)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-secondary)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>Response</span>
                    </div>
                    {/* Response sub-tabs */}
                    <div style={{ display: 'flex', background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border)', paddingLeft: '14px' }}>
                      {(['headers', 'content', 'cookies'] as const).map(t => (
                        <button key={t} onClick={() => setResSubTab(t)} style={{ padding: '7px 14px', border: 'none', background: 'transparent', fontSize: '12px', fontWeight: resSubTab === t ? 600 : 400, color: resSubTab === t ? 'var(--text-primary)' : 'var(--text-muted)', borderBottom: resSubTab === t ? '2px solid #e36209' : '2px solid transparent', marginBottom: '-1px', cursor: 'pointer', textTransform: 'capitalize' }}>
                          {t}
                        </button>
                      ))}
                    </div>
                    <div style={{ padding: '12px 14px' }}>
                      {resSubTab === 'headers' && (
                        <>
                          <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: '8px' }}>General</div>
                          <div style={{ marginBottom: '12px' }}>
                            <div style={{ display: 'flex', gap: '12px', marginBottom: '5px' }}>
                              <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '140px', flexShrink: 0 }}>Status Code</span>
                              <span style={{ fontFamily: 'monospace', fontSize: '12px', fontWeight: 700, color: reqDetail.status_code < 400 ? 'var(--success)' : 'var(--danger)' }}>{reqDetail.status_code}</span>
                            </div>
                          </div>
                          <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: '8px' }}>Headers</div>
                          {Object.entries(reqDetail.response_headers).map(([k, v]) => (
                            <div key={k} style={{ display: 'flex', gap: '12px', marginBottom: '5px', alignItems: 'flex-start' }}>
                              <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '140px', flexShrink: 0 }}>{k}</span>
                              <span style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{v}</span>
                            </div>
                          ))}
                        </>
                      )}
                      {resSubTab === 'content' && (
                        reqDetail.response_body
                          ? <pre style={{ margin: 0, fontSize: '11px', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: 'var(--text-secondary)', maxHeight: '300px', overflowY: 'auto' }}>
                              {reqDetail.response_body.slice(0, 8000)}{reqDetail.response_body.length > 8000 ? '\n…' : ''}
                            </pre>
                          : <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No response body</span>
                      )}
                      {resSubTab === 'cookies' && (
                        (() => {
                          const setCookieHeader = Object.entries(reqDetail.response_headers).find(([k]) => k.toLowerCase() === 'set-cookie')?.[1] ?? ''
                          const cookies = setCookieHeader ? setCookieHeader.split(';').map(s => s.trim()).filter(Boolean) : []
                          return cookies.length > 0
                            ? <div>{cookies.map((c, i) => {
                                const eq = c.indexOf('=')
                                const name = eq >= 0 ? c.slice(0, eq) : c
                                const val = eq >= 0 ? c.slice(eq + 1) : ''
                                return (
                                  <div key={i} style={{ display: 'flex', gap: '12px', marginBottom: '5px' }}>
                                    <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '140px', flexShrink: 0 }}>{name}</span>
                                    <span style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{val}</span>
                                  </div>
                                )
                              })}</div>
                            : <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No response cookies</span>
                        })()
                      )}
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          {/* Bottom: correlation rules */}
          <div style={{ borderTop: '2px solid var(--border)' }}>
            <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', gap: '12px', background: 'var(--bg-elevated)', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)' }}>Correlation rules ({enabledCount})</span>
              {rows.filter(r => r.original.is_confirmed_dynamic && !r.manualUnlocked).length > 0 && (
                <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '8px', background: 'rgba(var(--danger-rgb),0.1)', border: '1px solid rgba(var(--danger-rgb),0.3)', color: 'var(--danger)' }}>
                  🔒 {rows.filter(r => r.original.is_confirmed_dynamic && !r.manualUnlocked).length} mandatory
                </span>
              )}
              <div style={{ marginLeft: 'auto' }}>
                <Btn small variant="ghost" onClick={() => setShowRuleForm(s => !s)}>
                  {showRuleForm ? '× Cancel' : '+ Add rule'}
                </Btn>
              </div>
            </div>

            {showRuleForm && sessionId && (
              <div style={{ padding: '0 16px 16px', background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border)' }}>
                <CorrelationRuleForm requests={requests} sessionId={sessionId}
                  onAdd={row => { handleAdd(row); setShowRuleForm(false) }}
                  onClose={() => setShowRuleForm(false)} />
              </div>
            )}

            {rows.length === 0 && !anyBusy ? (
              <div style={{ padding: '14px 20px', background: 'var(--bg-elevated)' }}>
                <InfoBox msg="No correlations yet — run auto-correlation above, add AI auto-correlation if you want a second opinion, or add your own rule." />
              </div>
            ) : (
              rows.map(row => (
                <RuleCard key={row.id} row={row} requests={requests}
                  isMandatory={row.original.is_confirmed_dynamic === true}
                  stage={stageOf(row.original.detection_pass)}
                  onToggle={handleToggle} onRename={handleRename} onDelete={handleDelete} onToggleLock={handleToggleLock} />
              ))
            )}
          </div>
        </div>

        {done && sessionId && (
          <ReplayPanel sessionId={sessionId} requests={requests} busy={anyBusy || saving}
            beforeReplay={persistSelection}
            onApplied={values => { setRows(makeRows(values)); setCorrelated(values) }} />
        )}

      </div>

      {/* ── Test Data: Variables + CSV Files ─────────────────────────── */}
      <div style={{ marginTop: '32px' }}>
        <div style={{ marginBottom: '12px' }}>
          <h2 style={sectionHeadSt}>Test Data</h2>
          <p style={sectionDescSt}>Define P_-prefixed variables and CSV files — used as JMeter User Defined Variables and CSV Data Set configs</p>
        </div>
        <TestDataPanel />
      </div>

      {sessionId && (
        <div style={{ marginTop: '24px', display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
          <Btn onClick={applyAndNext} disabled={saving || anyBusy}>{saving ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> Saving…</> : <>Next: Generate Script <ChevronRight size={15} /></>}</Btn>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {enabledCount > 0
              ? `${enabledCount} correlation${enabledCount !== 1 ? 's' : ''} will be applied to the generated script`
              : done ? 'No correlations — the script will send the recorded values as they are'
                : 'Run auto-correlation first, unless the recording has no dynamic values'}
          </span>
        </div>
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// Test Data Panel (Variables + Data Files)
// ---------------------------------------------------------------------------

function TestDataPanel() {
  const { sessionId, testVars, testDataFiles, setTestVars, setTestDataFiles } = useAppStore()
  const [tab, setTab] = useState<'variables' | 'files'>('variables')
  const [showFileMenu, setShowFileMenu] = useState(false)
  const [suggestions, setSuggestions] = useState<ParamSuggestion[]>([])
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [editingVarNames, setEditingVarNames] = useState<Record<string, string>>({})
  const fileInputRef = useRef<HTMLInputElement>(null)

  const genId = () => Math.random().toString(36).slice(2, 9)

  // Fetch static-value suggestions whenever the session changes
  useEffect(() => {
    if (!sessionId) return
    suggestParams(sessionId)
      .then(r => setSuggestions(r.suggestions))
      .catch(() => {})
  }, [sessionId])

  const visibleSuggestions = suggestions.filter(
    s => !dismissed.has(s.var_name) && !testVars.find(v => v.name === s.var_name)
  )

  const acceptSuggestion = (s: ParamSuggestion) => {
    setTestVars([...testVars, { id: genId(), name: s.var_name, value: s.value }])
    setDismissed(prev => new Set([...prev, s.var_name]))
  }
  const dismissSuggestion = (varName: string) => setDismissed(prev => new Set([...prev, varName]))

  const addVar = () => setTestVars([...testVars, { id: genId(), name: 'P_', value: '' }])
  const removeVar = (id: string) => setTestVars(testVars.filter(v => v.id !== id))
  const updateVar = (id: string, patch: Partial<TestVar>) =>
    setTestVars(testVars.map(v => v.id === id ? { ...v, ...patch } : v))

  const handleFileImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => {
      const text = (ev.target?.result as string) ?? ''
      const firstLine = text.split('\n')[0] ?? ''
      const variables = firstLine.split(',')
        .map(v => v.trim().replace(/^["']|["']$/g, ''))
        .filter(Boolean)
      setTestDataFiles([...testDataFiles, {
        id: genId(), filename: file.name, data: text, accessMethod: 'unique', variables,
      }])
    }
    reader.readAsText(file)
    e.target.value = ''
    setShowFileMenu(false)
  }
  const removeFile = (id: string) => setTestDataFiles(testDataFiles.filter(f => f.id !== id))
  const updateFile = (id: string, patch: Partial<TestDataFile>) =>
    setTestDataFiles(testDataFiles.map(f => f.id === id ? { ...f, ...patch } : f))

  // Commit edited variable names string → array
  const commitVarNames = (id: string) => {
    const raw = editingVarNames[id]
    if (raw === undefined) return
    const variables = raw.split(',').map(v => v.trim()).filter(Boolean)
    updateFile(id, { variables })
    setEditingVarNames(prev => { const n = { ...prev }; delete n[id]; return n })
  }

  const thSt: React.CSSProperties = {
    padding: '8px 14px', textAlign: 'left', fontSize: '11px', fontWeight: 600,
    color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em',
    background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border)',
  }
  const tdSt: React.CSSProperties = { padding: '0', borderBottom: '1px solid var(--border)' }

  return (
    <Card style={{ padding: 0, overflow: 'visible' }}>
      {/* Tab header */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', paddingLeft: '16px', background: 'var(--bg-surface)' }}>
        {(['variables', 'files'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '9px 18px', border: 'none', background: 'transparent',
            fontSize: '13px', fontWeight: tab === t ? 700 : 400,
            color: tab === t ? 'var(--text-primary)' : 'var(--text-muted)',
            borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
            marginBottom: '-1px', cursor: 'pointer',
          }}>
            {t === 'variables'
              ? `Variables${testVars.length > 0 ? ` (${testVars.length})` : ''}`
              : `Data Files${testDataFiles.length > 0 ? ` (${testDataFiles.length})` : ''}`}
          </button>
        ))}
      </div>

      {/* ── Variables tab ── */}
      {tab === 'variables' && (
        <div>
          {/* Suggestions banner */}
          {visibleSuggestions.length > 0 && (
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'rgba(139,92,246,0.05)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: '#a78bfa' }}>
                  ✦ Suggested parameters from request bodies
                </span>
                <button onClick={() => setDismissed(new Set(suggestions.map(s => s.var_name)))}
                  style={{ background: 'transparent', border: 'none', fontSize: '11px', color: 'var(--text-muted)', cursor: 'pointer', padding: '2px 6px' }}>
                  Dismiss all
                </button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {visibleSuggestions.map(s => (
                  <div key={s.var_name} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', background: 'rgba(139,92,246,0.1)', border: '1px solid rgba(139,92,246,0.3)', borderRadius: '20px', padding: '4px 10px 4px 12px', fontSize: '12px' }}>
                    <span style={{ fontFamily: 'monospace', color: '#a78bfa', fontWeight: 600 }}>{s.var_name}</span>
                    <span style={{ color: 'var(--text-muted)' }}>= "{s.value.slice(0, 28)}{s.value.length > 28 ? '…' : ''}"</span>
                    <button onClick={() => acceptSuggestion(s)} title="Add as variable"
                      style={{ background: 'rgba(139,92,246,0.25)', border: 'none', borderRadius: '50%', width: '18px', height: '18px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: '#a78bfa', fontSize: '13px', lineHeight: 1, padding: 0, fontWeight: 700 }}>
                      +
                    </button>
                    <button onClick={() => dismissSuggestion(s.var_name)} title="Dismiss"
                      style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '13px', lineHeight: 1, padding: 0 }}>
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div style={{ padding: '10px 16px 6px', fontSize: '12px', color: 'var(--text-muted)' }}>
            Variables are added as <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>User Defined Variables</span> in the JMX and their values are substituted in request bodies (shown in <span style={{ color: '#a78bfa', fontWeight: 600 }}>purple</span> in the inspector).
          </div>

          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={thSt}>Name</th>
                <th style={thSt}>Value (String)</th>
                <th style={{ ...thSt, width: '40px' }} />
              </tr>
            </thead>
            <tbody>
              {testVars.map(v => (
                <tr key={v.id}>
                  <td style={tdSt}>
                    <input value={v.name} onChange={e => updateVar(v.id, { name: e.target.value })}
                      placeholder="P_USERNAME"
                      style={{ width: '100%', background: 'transparent', border: 'none', padding: '10px 14px', color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'monospace', outline: 'none', boxSizing: 'border-box' }} />
                  </td>
                  <td style={tdSt}>
                    <input value={v.value} onChange={e => updateVar(v.id, { value: e.target.value })}
                      placeholder="value"
                      style={{ width: '100%', background: 'transparent', border: 'none', padding: '10px 14px', color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'monospace', outline: 'none', boxSizing: 'border-box' }} />
                  </td>
                  <td style={{ ...tdSt, textAlign: 'center' }}>
                    <button onClick={() => removeVar(v.id)}
                      style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '8px', lineHeight: 1 }}
                      onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                      onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}>
                      🗑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ padding: '12px 16px', textAlign: 'center' }}>
            <button onClick={addVar} style={{ background: 'transparent', border: 'none', color: 'var(--accent)', fontSize: '13px', fontWeight: 600, cursor: 'pointer', padding: '4px 8px' }}>
              Add variable
            </button>
          </div>
        </div>
      )}

      {/* ── Data Files tab ── */}
      {tab === 'files' && (
        <div>
          <div style={{ padding: '10px 16px 6px', fontSize: '12px', color: 'var(--text-muted)' }}>
            Uploaded CSV/TXT files become <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>CSVDataSet Config</span> elements in the JMX. Use <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>${'{column_name}'}</span> in request bodies to reference rows.
          </div>

          {testDataFiles.length === 0 ? (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              No data files — import a CSV or TXT file to parameterize requests with test data.
            </div>
          ) : (
            <div>
              {testDataFiles.map(f => {
                const varNamesEditing = editingVarNames[f.id]
                const displayVarNames = varNamesEditing !== undefined
                  ? varNamesEditing
                  : f.variables.join(', ')
                return (
                  <div key={f.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    {/* Main row */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0', padding: '0' }}>
                      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 14px' }}>
                        <FileText size={13} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                        <span style={{ fontFamily: 'monospace', fontSize: '13px', color: 'var(--text-primary)' }}>{f.filename}</span>
                      </div>
                      <div style={{ width: '200px', borderLeft: '1px solid var(--border)' }}>
                        <select value={f.accessMethod} onChange={e => updateFile(f.id, { accessMethod: e.target.value as TestDataFile['accessMethod'] })}
                          style={{ background: 'transparent', border: 'none', padding: '10px 14px', color: 'var(--text-primary)', fontSize: '12px', outline: 'none', cursor: 'pointer', width: '100%' }}>
                          <option value="unique">Unique per thread</option>
                          <option value="shared">Shared across threads</option>
                        </select>
                      </div>
                      <div style={{ borderLeft: '1px solid var(--border)', display: 'flex', alignItems: 'center', padding: '0 10px' }}>
                        <button onClick={() => removeFile(f.id)}
                          style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '6px', lineHeight: 1 }}
                          onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}>
                          🗑
                        </button>
                      </div>
                    </div>
                    {/* Variable names row */}
                    <div style={{ padding: '6px 14px 10px 35px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', flexShrink: 0 }}>Column variables:</span>
                      {f.variables.length === 0 && varNamesEditing === undefined ? (
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic' }}>no header row detected</span>
                      ) : varNamesEditing === undefined ? (
                        <>
                          {f.variables.map(v => (
                            <span key={v} style={{ fontSize: '11px', fontFamily: 'monospace', background: 'rgba(var(--info-rgb),0.1)', border: '1px solid rgba(var(--info-rgb),0.25)', color: '#58a6ff', borderRadius: '4px', padding: '1px 7px' }}>
                              {'${' + v + '}'}
                            </span>
                          ))}
                        </>
                      ) : null}
                      {/* Editable field */}
                      {varNamesEditing !== undefined ? (
                        <>
                          <input value={varNamesEditing}
                            onChange={e => setEditingVarNames(prev => ({ ...prev, [f.id]: e.target.value }))}
                            onBlur={() => commitVarNames(f.id)}
                            onKeyDown={e => { if (e.key === 'Enter') commitVarNames(f.id) }}
                            placeholder="col1, col2, col3"
                            autoFocus
                            style={{ flex: 1, background: 'var(--bg-surface)', border: '1px solid var(--accent)', borderRadius: '5px', padding: '3px 8px', color: 'var(--text-primary)', fontSize: '12px', fontFamily: 'monospace', outline: 'none' }} />
                          <button onClick={() => commitVarNames(f.id)}
                            style={{ background: 'transparent', border: 'none', color: 'var(--accent)', fontSize: '12px', cursor: 'pointer', padding: '2px 6px', fontWeight: 600 }}>
                            Save
                          </button>
                        </>
                      ) : (
                        <button onClick={() => setEditingVarNames(prev => ({ ...prev, [f.id]: displayVarNames }))}
                          style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', fontSize: '11px', cursor: 'pointer', padding: '1px 5px', textDecoration: 'underline' }}>
                          edit
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Add data file button with dropdown */}
          <div style={{ padding: '14px 16px', textAlign: 'center', position: 'relative', display: 'inline-block', width: '100%' }}>
            <div style={{ display: 'inline-block', position: 'relative' }}>
              <button onClick={() => setShowFileMenu(m => !m)}
                style={{ background: 'transparent', border: '1px solid var(--accent)', borderRadius: '6px', color: 'var(--accent)', fontSize: '13px', fontWeight: 600, cursor: 'pointer', padding: '7px 18px' }}>
                Add data file +
              </button>
              {showFileMenu && (
                <div style={{ position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)', marginTop: '4px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '8px', boxShadow: '0 4px 16px rgba(0,0,0,0.3)', zIndex: 50, minWidth: '180px' }}>
                  <button onClick={() => fileInputRef.current?.click()}
                    style={{ display: 'flex', alignItems: 'center', gap: '8px', width: '100%', background: 'transparent', border: 'none', padding: '11px 16px', fontSize: '13px', color: 'var(--text-primary)', cursor: 'pointer', borderRadius: '8px' }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.06)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                    <FileText size={14} style={{ color: 'var(--text-muted)' }} />
                    Import new data file
                  </button>
                </div>
              )}
            </div>
          </div>
          <input ref={fileInputRef} type="file" accept=".csv,.txt" style={{ display: 'none' }} onChange={handleFileImport} />
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Thread groups + SLA helpers (shared by Generate / Run / Report)
// ---------------------------------------------------------------------------

const THREAD_GROUP_TYPES: { type: ThreadGroupType; label: string; detail: string; plugin: boolean }[] = [
  { type: 'standard', label: 'Thread Group', detail: 'Fixed users with a linear ramp-up, by loops or duration', plugin: false },
  { type: 'stepping', label: 'Stepping Thread Group', detail: 'Add users in steps, hold the load, then stop them in steps', plugin: true },
  { type: 'concurrency', label: 'Concurrency Thread Group', detail: 'Keep a target number of users, ramped up in steps', plugin: true },
  { type: 'ultimate', label: 'Ultimate Thread Group', detail: 'Any shape: schedule several stages that overlap', plugin: true },
]

function fmtSecs(s: number): string {
  if (s < 120) return `${Math.round(s)}s`
  const m = Math.floor(s / 60), r = Math.round(s % 60)
  return r ? `${m}m ${r}s` : `${m}m`
}

type ProfileShape = { pts: [number, number][]; peak: number; total: number; toPeak: number }

/** Users over time for a thread group, for the load-shape preview (mirrors profile_summary on the server). */
function profileShape(tg: ThreadGroupConfig, std: { threads: number; rampup: number; loops: number; durationSecs: number }): ProfileShape {
  const n = (v: number) => Math.max(0, Number.isFinite(v) ? v : 0)
  if (tg.type === 'stepping') {
    const N = Math.max(1, n(tg.threads)), S = Math.max(1, n(tg.step_users)), B = Math.min(n(tg.start_burst) || S, N)
    const P = n(tg.step_period), R = n(tg.step_rampup), D = n(tg.initial_delay)
    const pts: [number, number][] = [[0, 0], [D, 0], [D + R, B]]
    let users = B, t = D + R, k = 0
    while (users < N && k < 400) {
      k++
      pts.push([t + P, users])
      users = Math.min(N, users + S)
      t = D + k * (P + R) + R
      pts.push([t, users])
    }
    const toPeak = t
    t += n(tg.hold)
    pts.push([t, users])
    const stopN = n(tg.stop_users) || N, stopP = n(tg.stop_period)
    let guard = 0
    while (users > 0 && guard++ < 400) {
      t += stopP
      pts.push([t, users])
      users = Math.max(0, users - stopN)
      pts.push([t, users])
    }
    return { pts, peak: N, total: t, toPeak }
  }
  if (tg.type === 'concurrency') {
    const T = Math.max(1, n(tg.target)), R = n(tg.ramp_up), St = n(tg.steps), H = n(tg.hold)
    const pts: [number, number][] = [[0, 0]]
    if (St > 0 && R > 0) {
      for (let i = 0; i < St; i++) {
        const lvl = Math.round(T * (i + 1) / St), at = (i * R) / St
        pts.push([at, pts[pts.length - 1][1]], [at, lvl])
      }
      pts.push([R, T])
    } else pts.push([R, T])
    pts.push([R + H, T], [R + H, 0])
    return { pts, peak: T, total: R + H, toPeak: R }
  }
  if (tg.type === 'ultimate') {
    const rows = tg.rows.length ? tg.rows : []
    const usersAt = (t: number) => rows.reduce((sum, r) => {
      const s = n(r.initial_delay), up = n(r.startup), hold = n(r.hold), down = n(r.shutdown), th = n(r.threads)
      if (t < s || t > s + up + hold + down) return sum
      if (t < s + up) return sum + (up ? th * (t - s) / up : th)
      if (t <= s + up + hold) return sum + th
      return sum + (down ? th * (1 - (t - s - up - hold) / down) : 0)
    }, 0)
    const marks = [...new Set(rows.flatMap(r => {
      const s = n(r.initial_delay), up = n(r.startup), hold = n(r.hold), down = n(r.shutdown)
      return [s, s + up, s + up + hold, s + up + hold + down]
    }))].sort((a, b) => a - b)
    const pts: [number, number][] = [[0, 0], ...marks.map(t => [t, usersAt(t)] as [number, number])]
    const peakPt = pts.reduce((a, b) => (b[1] > a[1] ? b : a), [0, 0] as [number, number])
    return { pts, peak: Math.max(1, Math.round(peakPt[1])), total: marks[marks.length - 1] ?? 0, toPeak: peakPt[0] }
  }
  const th = Math.max(1, std.threads), ru = Math.max(0, std.rampup)
  const total = std.durationSecs > 0 ? Math.max(std.durationSecs, ru + 1) : ru + Math.max(10, std.loops * 2)
  return { pts: [[0, 0], [ru, th], [total, th], [total, 0]], peak: th, total, toPeak: ru }
}

function LoadShapeChart({ shape }: { shape: ProfileShape }) {
  const svgW = 320, svgH = 140, pad = 28
  const innerW = svgW - pad * 2, innerH = svgH - pad * 2
  const total = Math.max(1, shape.total), peak = Math.max(1, shape.peak)
  const x = (t: number) => pad + (t / total) * innerW
  const y = (u: number) => svgH - pad - (u / peak) * innerH
  const line = shape.pts.map(([t, u]) => `${x(t).toFixed(1)},${y(u).toFixed(1)}`).join(' ')
  const area = `${x(0)},${y(0)} ${line} ${x(total)},${y(0)}`
  return (
    <svg viewBox={`0 0 ${svgW} ${svgH}`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Load shape: up to ${peak} users over ${fmtSecs(total)}`}>
      <line x1={pad} y1={pad} x2={pad} y2={svgH - pad} stroke="var(--border)" strokeWidth="1" />
      <line x1={pad} y1={svgH - pad} x2={svgW - pad} y2={svgH - pad} stroke="var(--border)" strokeWidth="1" />
      <polygon points={area} fill="rgba(var(--accent-rgb),0.1)" />
      <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinejoin="round" />
      <text x={pad - 4} y={svgH - pad + 1} fontSize="10" fill="var(--text-muted)" textAnchor="end">0</text>
      <text x={pad - 4} y={pad + 4} fontSize="10" fill="var(--text-primary)" textAnchor="end" fontWeight="600">{peak}u</text>
      <text x={pad} y={svgH - pad + 14} fontSize="10" fill="var(--text-muted)" textAnchor="middle">0</text>
      {shape.toPeak > 0 && shape.toPeak < total * 0.9 && (
        <text x={x(shape.toPeak)} y={svgH - pad + 14} fontSize="10" fill="var(--text-muted)" textAnchor="middle">{fmtSecs(shape.toPeak)}</text>
      )}
      <text x={svgW - pad} y={svgH - pad + 14} fontSize="10" fill="var(--text-muted)" textAnchor="middle">{fmtSecs(total)}</text>
    </svg>
  )
}

/** One-line description of a generated script's thread group. */
function describeThreadGroup(tg: Partial<ThreadGroupConfig> & { type: ThreadGroupType }): string {
  const v = (k: keyof ThreadGroupConfig) => Number(tg[k] ?? 0)
  if (tg.type === 'stepping') {
    return `Stepping: ${v('threads')} users — ${v('start_burst') || v('step_users')} first, then +${v('step_users')} every ${v('step_period')}s` +
      `${v('step_rampup') ? ` (${v('step_rampup')}s ramp)` : ''}, hold ${fmtSecs(v('hold'))}, stop ${v('stop_users') || 'all'} every ${v('stop_period')}s`
  }
  if (tg.type === 'concurrency') {
    return `Concurrency: ${v('target')} users, ramp-up ${fmtSecs(v('ramp_up'))}${v('steps') ? ` in ${v('steps')} steps` : ''}, hold ${fmtSecs(v('hold'))}` +
      `${v('iterations') ? `, max ${v('iterations')} iterations` : ''}`
  }
  if (tg.type === 'ultimate') {
    const rows = (tg.rows ?? []) as UltimateRow[]
    return `Ultimate: ${rows.length} stage${rows.length !== 1 ? 's' : ''} — ` +
      rows.map(r => `${r.threads}u from ${fmtSecs(r.initial_delay)} (+${r.startup}s, hold ${fmtSecs(r.hold)})`).join('; ')
  }
  return 'Thread Group'
}

const SLA_METRICS: Record<SlaMetric, string> = { avg: 'Average', p90: '90th percentile', p95: '95th percentile', p99: '99th percentile' }

/** Readable SLA targets (mirrors describe() on the server). */
function slaTargets(sla: SlaConfig): string[] {
  const m = sla.response_time_metric
  const out: string[] = []
  if (sla.response_time_ms != null) out.push(`${m} ≤ ${sla.response_time_ms} ms per transaction`)
  for (const [name, ms] of Object.entries(sla.transaction_targets)) if (ms != null) out.push(`${name}: ${m} ≤ ${ms} ms`)
  if (sla.error_rate_pct != null) out.push(`errors ≤ ${sla.error_rate_pct}%`)
  if (sla.throughput_rps != null) out.push(`throughput ≥ ${sla.throughput_rps} req/s`)
  return out
}

const numOrNull = (v: string): number | null => {
  if (v.trim() === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function NumField({ label, value, onChange, min = 0, hint }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; hint?: string
}) {
  return (
    <div>
      <Label>{label}</Label>
      <Input value={value} type="number" min={min} onChange={v => onChange(Math.max(min, Math.floor(Number(v) || 0)))} />
      {hint && <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>{hint}</div>}
    </div>
  )
}

function SlaEditor({ transactionNames: txnNames, grouped }: { transactionNames: string[]; grouped: boolean }) {
  const { sla, setSla } = useAppStore()
  const lastRef = useRef<SlaConfig>(sla ?? EMPTY_SLA)
  const on = sla !== null
  const cur = sla ?? lastRef.current
  const set = (patch: Partial<SlaConfig>) => { const next = { ...cur, ...patch }; lastRef.current = next; setSla(next) }
  const empty = on && activeSla(sla) === null
  const scope = grouped ? 'transaction' : 'request'

  return (
    <Card style={{ marginTop: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '14px', flexWrap: 'wrap' }}>
        <Target size={18} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: '3px' }} />
        <div style={{ flex: 1, minWidth: '260px' }}>
          <div style={{ fontSize: '16px', fontWeight: 600 }}>Service level agreement (SLA) <span style={{ fontSize: '12px', fontWeight: 400, color: 'var(--text-muted)' }}>optional</span></div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '3px' }}>
            {on
              ? 'The run is marked pass or fail against these targets only. They are checked when the test runs — changing them does not need a new script.'
              : 'No SLA — the run will be reported as measured, without pass or fail. Define one if you have response-time, error or throughput targets.'}
          </div>
        </div>
        <Toggle checked={on} onChange={v => setSla(v ? lastRef.current : null)} label="Define an SLA" />
      </div>

      {on && (
        <div style={{ marginTop: '18px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '14px' }}>
            <div>
              <Label>Response-time metric</Label>
              <select value={cur.response_time_metric} onChange={e => set({ response_time_metric: e.target.value as SlaMetric })}
                style={{ width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px', padding: '10px 14px', color: 'var(--text-primary)', fontSize: '14px', outline: 'none', cursor: 'pointer' }}>
                {(Object.keys(SLA_METRICS) as SlaMetric[]).map(m => <option key={m} value={m}>{SLA_METRICS[m]}</option>)}
              </select>
            </div>
            <div>
              <Label>Max response time per {scope} (ms)</Label>
              <Input value={cur.response_time_ms ?? ''} type="number" min={1} placeholder="e.g. 2000" onChange={v => set({ response_time_ms: numOrNull(v) })} />
            </div>
            <div>
              <Label>Max error rate (%)</Label>
              <Input value={cur.error_rate_pct ?? ''} type="number" min={0} placeholder="e.g. 1" onChange={v => set({ error_rate_pct: numOrNull(v) })} />
            </div>
            <div>
              <Label>Min throughput (req/s)</Label>
              <Input value={cur.throughput_rps ?? ''} type="number" min={0} placeholder="optional" onChange={v => set({ throughput_rps: numOrNull(v) })} />
            </div>
          </div>

          {grouped && txnNames.length > 0 && (
            <div>
              <Label>Per-transaction response time (ms) — overrides the target above</Label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '8px' }}>
                {txnNames.map(name => (
                  <div key={name} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 10px', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '10px' }}>
                    <span style={{ flex: 1, minWidth: 0, fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>{name}</span>
                    <input type="number" min={1} value={cur.transaction_targets[name] ?? ''} aria-label={`${name} response-time target`}
                      placeholder={cur.response_time_ms != null ? String(cur.response_time_ms) : '—'}
                      onChange={e => set({ transaction_targets: { ...cur.transaction_targets, [name]: numOrNull(e.target.value) } })}
                      style={{ width: '90px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '6px', padding: '5px 8px', color: 'var(--text-primary)', fontSize: '13px', outline: 'none' }} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {empty
            ? <InfoBox msg="Add at least one target, or turn the SLA off — an SLA without targets is ignored." />
            : <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Targets: {slaTargets(cur).join(' · ')}</div>}
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Step 2: Generate JMX
// ---------------------------------------------------------------------------

function StepGenerate() {
  const { setGeneratedLoadConfig, sessionId, correlatedValues, baseUrl, baseUrls, jmx: storedJmx, setJmx, setStep,
          selectedRequestIndices, customAssertions, requestCount, requestOrder, transactionNames, requests,
          testVars, testDataFiles, threadGroup, setThreadGroup, setGeneratedThreadGroup } = useAppStore()
  const hasUserTransactions = Object.values(transactionNames).some(v => v.trim())
  const [testName, setTestName] = useState('ProjectStack Test')
  const [threads, setThreads] = useState('1')
  const [rampup, setRampup] = useState('1')
  const [loops, setLoops] = useState('1')
  const [includeViewTree, setIncludeViewTree] = useState(true)
  // Grouping is on whenever the user defined transactions in step 1
  const [noTransactions, setNoTransactions] = useState(!hasUserTransactions)
  const [useGrafana, setUseGrafana] = useState(false)
  const [grafanaUrl, setGrafanaUrl] = useState('')
  const [selectedVars, setSelectedVars] = useState<Set<string>>(new Set())
  const [includeThinkTimes, setIncludeThinkTimes] = useState(false)
  const [infiniteLoops, setInfiniteLoops] = useState(false)
  const [durationSeconds, setDurationSeconds] = useState('300')
  const [loadProfile, setLoadProfile] = useState<'smoke' | 'soak' | 'spike' | 'stress' | 'custom'>('custom')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<null | { jmx: string; length: number; sampler_count?: number; validation_errors?: { code: string; message: string }[] }>(null)
  const [jmeter, setJmeter] = useState<JmeterInfo | null>(null)

  const LOAD_PROFILES: Record<string, { label: string; threads: string; rampup: string; loops: string; color: string }> = {
    smoke:  { label: 'Smoke',  threads: '1',  rampup: '10',  loops: '1',  color: '#58a6ff' },
    soak:   { label: 'Soak',   threads: '10', rampup: '60',  loops: '60', color: '#de5353' },
    spike:  { label: 'Spike',  threads: '50', rampup: '5',   loops: '5',  color: '#d29922' },
    stress: { label: 'Stress', threads: '25', rampup: '30',  loops: '20', color: 'var(--danger)' },
  }

  const applyProfile = (key: string) => {
    const p = LOAD_PROFILES[key]
    if (!p) { setLoadProfile('custom'); return }
    setLoadProfile(key as typeof loadProfile)
    setThreads(p.threads)
    setRampup(p.rampup)
    setLoops(p.loops)
  }

  useEffect(() => {
    getSettings().then(s => {
      if (s.default_threads) setThreads(s.default_threads)
      if (s.default_rampup)  setRampup(s.default_rampup)
      if (s.default_loops)   setLoops(s.default_loops)
      if (s.grafana_url)     { setGrafanaUrl(s.grafana_url); setUseGrafana(true) }
    }).catch(() => {})
    getJmeterInfo().then(setJmeter).catch(() => {})
    // Pre-select all correlated vars
    setSelectedVars(new Set(correlatedValues.map(v => v.var_name)))
    // Reinitialize from store when navigating back
    if (storedJmx && !result) {
      setResult({ jmx: storedJmx, length: storedJmx.length })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Transactions as they will be generated: included requests, in execution order
  const orderedIncluded = (requestOrder ?? requests.map(r => r.index))
    .filter(i => selectedRequestIndices === null || selectedRequestIndices.includes(i))
  const blocks = hasUserTransactions ? computeBlocks(orderedIncluded, transactionNames) : []
  const userTxnNames = [...new Set(blocks.map(b => b.name.trim()).filter(Boolean))]

  const tg = threadGroup
  const setTg = (patch: Partial<ThreadGroupConfig>) => setThreadGroup({ ...tg, ...patch })
  const tgMeta = THREAD_GROUP_TYPES.find(t => t.type === tg.type) ?? THREAD_GROUP_TYPES[0]
  const pluginMissing = tgMeta.plugin && jmeter !== null && !jmeter.custom_thread_groups
  const shape = profileShape(tg, {
    threads: parseInt(threads) || 1, rampup: parseInt(rampup) || 0,
    loops: infiniteLoops ? 0 : (parseInt(loops) || 1), durationSecs: infiniteLoops ? (parseInt(durationSeconds) || 300) : 0,
  })

  const run = async () => {
    if (!sessionId) return
    setLoading(true); setError('')
    try {
      const res = await generate(sessionId, {
        test_name: testName,
        threads: parseInt(threads) || 1,
        rampup: parseInt(rampup) || 1,
        loops: parseInt(loops) || 1,
        base_url: baseUrls.length > 0 ? baseUrls[0] : baseUrl,
        base_urls: baseUrls.length > 0 ? baseUrls : undefined,
        test_vars: testVars.length > 0 ? testVars.map(v => ({ name: v.name, value: v.value })) : undefined,
        test_data_files: testDataFiles.length > 0 ? testDataFiles.map(f => ({ filename: f.filename, data: f.data, accessMethod: f.accessMethod, variables: f.variables })) : undefined,
        include_view_tree: includeViewTree,
        grafana_url: useGrafana ? grafanaUrl : '',
        no_transactions: noTransactions,
        selected_var_names: selectedVars.size < correlatedValues.length ? [...selectedVars] : null,
        selected_request_indices: selectedRequestIndices,
        custom_assertions: Object.keys(customAssertions).length > 0 ? customAssertions : null,
        custom_transaction_names: Object.keys(transactionNames).length > 0 ? transactionNames : null,
        request_order: requestOrder,
        include_think_times: includeThinkTimes,
        load_profile: tg.type === 'standard' ? loadProfile : 'custom',
        infinite_loops: infiniteLoops,
        duration_seconds: infiniteLoops ? (parseInt(durationSeconds) || 300) : 0,
        thread_group: tg,
      })
      setResult(res)
      if (res.load_config) setGeneratedLoadConfig(res.load_config)
      setGeneratedThreadGroup(res.thread_group ?? null)
      setJmx(res.jmx)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const ultimateRows = tg.rows
  const setRow = (i: number, patch: Partial<UltimateRow>) => setTg({ rows: ultimateRows.map((r, j) => j === i ? { ...r, ...patch } : r) })

  return (
    <motion.div {...fadeSlide}>
      <SectionTitle>Generate Script</SectionTitle>

      {error && <div style={{ marginBottom: '16px' }}><ErrorBox msg={error} /></div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '20px', alignItems: 'start' }}>
        {/* Left: Config form */}
        <Card>
          <div style={{ marginBottom: '16px' }}>
            <Label>Test Name</Label>
            <Input value={testName} onChange={setTestName} placeholder="My Performance Test" />
          </div>

          {/* Thread group type */}
          <div style={{ marginBottom: '16px' }}>
            <Label>Thread Group</Label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '6px' }}>
              {THREAD_GROUP_TYPES.map(t => {
                const active = tg.type === t.type
                return (
                  <button key={t.type} onClick={() => setTg({ type: t.type })} aria-pressed={active}
                    style={{ textAlign: 'left', padding: '10px 12px', borderRadius: '12px', cursor: 'pointer', border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`, background: active ? 'rgba(var(--accent-rgb),0.1)' : 'var(--bg-surface)', color: 'var(--text-primary)' }}>
                    <div style={{ fontSize: '13px', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px' }}>
                      {t.label}
                      {t.plugin && <span style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.05em', padding: '1px 5px', borderRadius: '4px', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>PLUGIN</span>}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '3px', lineHeight: 1.45 }}>{t.detail}</div>
                  </button>
                )
              })}
            </div>
            {tgMeta.plugin && jmeter && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: pluginMissing ? 'var(--warning)' : 'var(--text-muted)' }}>
                {pluginMissing
                  ? <>Needs the JMeter Plugins “Custom Thread Groups” jar (jpgc-casutg), which {jmeter.home ?? 'your JMeter'} does not have. The script still downloads, but it cannot run here until you install the plugin or set Settings → JMeter path to an install that has it.</>
                  : <>Uses JMeter Plugins (Custom Thread Groups) — installed in {jmeter.home}.</>}
              </div>
            )}
          </div>

          {/* Thread group parameters */}
          {tg.type === 'standard' && (
            <>
              <div style={{ marginBottom: '16px' }}>
                <Label>Load Profile</Label>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '6px' }}>
                  {(['custom', 'smoke', 'soak', 'spike', 'stress'] as const).map(key => {
                    const p = key === 'custom' ? null : LOAD_PROFILES[key]
                    const isActive = loadProfile === key
                    return (
                      <button key={key} onClick={() => applyProfile(key)} style={{
                        padding: '5px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: isActive ? 700 : 500, cursor: 'pointer', border: `1px solid ${isActive ? (p?.color ?? 'var(--accent)') : 'var(--border)'}`,
                        background: isActive ? `${p?.color ?? 'var(--accent)'}22` : 'transparent',
                        color: isActive ? (p?.color ?? 'var(--accent)') : 'var(--text-muted)',
                      }}>
                        {key.charAt(0).toUpperCase() + key.slice(1)}
                        {p && <span style={{ marginLeft: '6px', opacity: 0.7, fontWeight: 400 }}>{p.threads}u</span>}
                      </button>
                    )
                  })}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '20px' }}>
                <div><Label>Virtual Users (Threads)</Label><Input value={threads} onChange={e => { setThreads(e); setLoadProfile('custom') }} type="number" min={1} /></div>
                <div><Label>Ramp-up Period (seconds)</Label><Input value={rampup} onChange={e => { setRampup(e); setLoadProfile('custom') }} type="number" min={0} /></div>
                <div>
                  <Label>Loops</Label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Input value={infiniteLoops ? '∞' : loops} onChange={e => { setLoops(e); setLoadProfile('custom') }} type={infiniteLoops ? 'text' : 'number'} min={1} disabled={infiniteLoops} />
                    <label style={{ display: 'flex', alignItems: 'center', gap: '5px', cursor: 'pointer', whiteSpace: 'nowrap', fontSize: '12px', color: 'var(--text-secondary)' }}>
                      <input type="checkbox" checked={infiniteLoops} onChange={e => setInfiniteLoops(e.target.checked)}
                        style={{ accentColor: 'var(--accent)', cursor: 'pointer' }} />
                      Forever
                    </label>
                  </div>
                </div>
                {infiniteLoops && (
                  <div>
                    <Label>Duration (seconds)</Label>
                    <Input value={durationSeconds} onChange={setDurationSeconds} type="number" min={1} placeholder="300" />
                  </div>
                )}
              </div>
            </>
          )}

          {tg.type === 'stepping' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px', marginBottom: '20px' }}>
              <NumField label="Total users" value={tg.threads} min={1} onChange={v => setTg({ threads: v })} />
              <NumField label="Initial delay (s)" value={tg.initial_delay} onChange={v => setTg({ initial_delay: v })} />
              <NumField label="Start with (users)" value={tg.start_burst} onChange={v => setTg({ start_burst: v })} hint="0 = one step's worth" />
              <NumField label="Then add (users)" value={tg.step_users} min={1} onChange={v => setTg({ step_users: v })} />
              <NumField label="Every (s)" value={tg.step_period} onChange={v => setTg({ step_period: v })} />
              <NumField label="Ramp-up per step (s)" value={tg.step_rampup} onChange={v => setTg({ step_rampup: v })} />
              <NumField label="Hold load for (s)" value={tg.hold} onChange={v => setTg({ hold: v })} />
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                <NumField label="Stop (users)" value={tg.stop_users} onChange={v => setTg({ stop_users: v })} hint="0 = all at once" />
                <NumField label="Every (s)" value={tg.stop_period} onChange={v => setTg({ stop_period: v })} />
              </div>
            </div>
          )}

          {tg.type === 'concurrency' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px', marginBottom: '20px' }}>
              <NumField label="Target concurrency (users)" value={tg.target} min={1} onChange={v => setTg({ target: v })} />
              <NumField label="Ramp-up time (s)" value={tg.ramp_up} onChange={v => setTg({ ramp_up: v })} />
              <NumField label="Ramp-up steps" value={tg.steps} onChange={v => setTg({ steps: v })} hint="0 = smooth ramp" />
              <NumField label="Hold target for (s)" value={tg.hold} onChange={v => setTg({ hold: v })} />
              <NumField label="Iterations per user" value={tg.iterations} onChange={v => setTg({ iterations: v })} hint="0 = no limit" />
            </div>
          )}

          {tg.type === 'ultimate' && (
            <div style={{ marginBottom: '20px' }}>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                  <thead>
                    <tr>
                      {['Users', 'Start after (s)', 'Ramp-up (s)', 'Hold (s)', 'Ramp-down (s)', ''].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '4px 6px', color: 'var(--text-muted)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {ultimateRows.map((r, i) => (
                      <tr key={i}>
                        {(['threads', 'initial_delay', 'startup', 'hold', 'shutdown'] as const).map(k => (
                          <td key={k} style={{ padding: '3px 4px' }}>
                            <input type="number" min={k === 'threads' ? 1 : 0} value={r[k]} aria-label={`Stage ${i + 1} ${k}`}
                              onChange={e => setRow(i, { [k]: Math.max(k === 'threads' ? 1 : 0, Math.floor(Number(e.target.value) || 0)) })}
                              style={{ width: '100%', minWidth: '56px', boxSizing: 'border-box', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 8px', color: 'var(--text-primary)', fontSize: '13px', outline: 'none' }} />
                          </td>
                        ))}
                        <td style={{ padding: '3px 4px' }}>
                          <button onClick={() => setTg({ rows: ultimateRows.filter((_, j) => j !== i) })} disabled={ultimateRows.length <= 1}
                            aria-label={`Remove stage ${i + 1}`}
                            style={{ background: 'transparent', border: 'none', color: 'var(--danger)', cursor: ultimateRows.length <= 1 ? 'not-allowed' : 'pointer', opacity: ultimateRows.length <= 1 ? 0.3 : 0.7, fontSize: '16px' }}>×</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ marginTop: '8px' }}>
                <Btn small variant="ghost" onClick={() => {
                  const last = ultimateRows[ultimateRows.length - 1] ?? DEFAULT_THREAD_GROUP.rows[0]
                  setTg({ rows: [...ultimateRows, { ...last, initial_delay: last.initial_delay + last.startup + last.hold }] })
                }}><Plus size={12} /> Add stage</Btn>
              </div>
            </div>
          )}

          {(baseUrls.length > 0 || baseUrl) && (
            <div style={{ marginBottom: '16px' }}>
              <Label>Base URL{baseUrls.length > 1 ? 's' : ''} (parameterized in JMX)</Label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {(baseUrls.length > 0 ? baseUrls : [baseUrl]).map((u, i) => (
                  <span key={u} style={{ padding: '5px 12px', background: 'rgba(var(--accent-rgb),0.08)', border: '1px solid rgba(var(--accent-rgb),0.25)', borderRadius: '6px', fontFamily: 'monospace', fontSize: '12px', color: 'var(--accent)' }}>
                    ${'{'}BASE_URL{i > 0 ? `_${i + 1}` : ''}{'}'}  =  {u}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Options */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', padding: '16px', background: 'var(--bg-surface)', borderRadius: '8px', marginBottom: '16px' }}>
            <Toggle checked={includeViewTree} onChange={setIncludeViewTree} label="Include View Results Tree listener (for manual validation in JMeter)" />
            <Toggle checked={!noTransactions} onChange={v => setNoTransactions(!v)} label="Group requests into Transaction Controllers" />
            {!noTransactions && (
              <div style={{ paddingLeft: '25px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                {blocks.length > 0 ? (
                  <>
                    {blocks.map((b, i) => (
                      <span key={i} style={{ display: 'inline-block', marginRight: '10px' }}>
                        <code style={{ color: TXN_COLORS[i % TXN_COLORS.length] }}>{txnLabel(i, b.name)}</code> <span style={{ color: 'var(--text-muted)' }}>({b.indices.length})</span>
                      </span>
                    ))}
                    <button onClick={() => setStep(0)} style={{ background: 'transparent', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: '12px', padding: 0 }}>Edit in step 1 →</button>
                  </>
                ) : (
                  <>No transactions defined — requests will be grouped automatically.{' '}
                    <button onClick={() => setStep(0)} style={{ background: 'transparent', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: '12px', padding: 0 }}>Define them in step 1 → Transactions</button></>
                )}
              </div>
            )}
            {noTransactions && hasUserTransactions && (
              <div style={{ paddingLeft: '25px', fontSize: '12px', color: 'var(--warning)' }}>
                Your {userTxnNames.length} transaction{userTxnNames.length !== 1 ? 's' : ''} from step 1 will be ignored while this is off.
              </div>
            )}
            <Toggle checked={includeThinkTimes} onChange={setIncludeThinkTimes} label="Inject think times from HAR timestamps (ConstantTimer)" />
            <Toggle checked={useGrafana} onChange={setUseGrafana} label="Send metrics to Grafana (InfluxDB Backend Listener)" />
            {useGrafana && (
              <div style={{ marginTop: '4px', paddingLeft: '24px' }}>
                <Label>InfluxDB Write URL</Label>
                <Input value={grafanaUrl} onChange={setGrafanaUrl} placeholder="http://localhost:8086/api/v2/write?org=jmeter&bucket=jmeter" />
              </div>
            )}
          </div>

          {selectedRequestIndices !== null && requestCount > 0 && (
            <div style={{ marginBottom: '14px' }}>
              <InfoBox msg={`Including ${selectedRequestIndices.length} of ${requestCount} requests. Deselected requests will be excluded from the JMX script.`} />
            </div>
          )}
          <Btn onClick={run} disabled={loading || !sessionId || (tg.type === 'ultimate' && ultimateRows.length === 0)} fullWidth>
            {loading ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> Generating…</> : <><FileText size={14} /> Generate Script</>}
          </Btn>
        </Card>

        {/* Right: Load shape */}
        <Card>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '16px' }}>Load shape · {tgMeta.label}</div>
          <LoadShapeChart shape={shape} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px', marginTop: '12px' }}>
            {[
              [String(shape.peak), 'Peak users'],
              [tg.type === 'standard' ? `${parseInt(rampup) || 0}s` : fmtSecs(shape.toPeak), 'To peak'],
              tg.type === 'standard' && !infiniteLoops ? [String(parseInt(loops) || 1), 'Loops'] : [fmtSecs(shape.total), 'Duration'],
            ].map(([v, l]) => (
              <div key={l} style={{ textAlign: 'center', background: 'var(--bg-surface)', borderRadius: '6px', padding: '8px' }}>
                <div style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text-primary)' }}>{v}</div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{l}</div>
              </div>
            ))}
          </div>
          {result && (
            <div style={{ marginTop: '20px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}>
              {/* Inline export section */}
              <div style={{ marginBottom: '14px' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>Export Script</div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '10px' }}>
                  <Btn onClick={() => sessionId && downloadJmx(sessionId).catch(() => {})}>
                    <Download size={14} /> Download JMX
                  </Btn>
                  <Btn variant="ghost" small onClick={() => sessionId && downloadK6(sessionId).catch(() => {})}>k6</Btn>
                  <Btn variant="ghost" small onClick={() => sessionId && downloadCiYaml(sessionId, 'github').catch(() => {})}>GitHub Actions</Btn>
                  <Btn variant="ghost" small onClick={() => sessionId && downloadCiYaml(sessionId, 'jenkins').catch(() => {})}>Jenkins</Btn>
                  <Btn variant="ghost" small onClick={() => sessionId && downloadZip(sessionId).catch(() => {})}>ZIP</Btn>
                </div>
              </div>
              <Btn onClick={() => setStep(3)} fullWidth>
                Next: Run Test <ChevronRight size={15} />
              </Btn>
            </div>
          )}
        </Card>
      </div>

      <SlaEditor transactionNames={!noTransactions ? userTxnNames : []} grouped={!noTransactions} />

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// Step 3: Run Test
// ---------------------------------------------------------------------------

function SlaSummary({ sla, onEdit }: { sla: SlaConfig | null; onEdit: () => void }) {
  const active = activeSla(sla)
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', padding: '14px 16px', borderRadius: '14px', background: 'var(--bg-surface)', border: '1px solid var(--border)', flexWrap: 'wrap' }}>
      <Target size={16} style={{ color: active ? 'var(--accent)' : 'var(--text-muted)', flexShrink: 0, marginTop: '2px' }} />
      <div style={{ flex: 1, minWidth: '220px', fontSize: '13px' }}>
        {active ? (
          <>
            <div style={{ fontWeight: 600, marginBottom: '2px' }}>SLA for this run</div>
            <div style={{ color: 'var(--text-secondary)' }}>{slaTargets(active).join(' · ')}</div>
          </>
        ) : (
          <>
            <div style={{ fontWeight: 600, marginBottom: '2px' }}>No SLA defined</div>
            <div style={{ color: 'var(--text-muted)' }}>The run will be reported as measured, without pass or fail.</div>
          </>
        )}
      </div>
      <Btn small variant="ghost" onClick={onEdit}>{active ? 'Change in step 3' : 'Define in step 3'}</Btn>
    </div>
  )
}

function SlaVerdict({ sla }: { sla?: SlaResult }) {
  if (!sla?.defined) return null
  const ok = sla.passed
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '12px 16px', borderRadius: '12px', marginBottom: '16px', fontSize: '14px', fontWeight: 600, color: ok ? 'var(--success)' : 'var(--danger)', background: `rgba(var(--${ok ? 'success' : 'danger'}-rgb),0.08)`, border: `1px solid rgba(var(--${ok ? 'success' : 'danger'}-rgb),0.3)` }}>
      {ok ? <CheckCircle size={16} /> : <XCircle size={16} />}
      SLA {ok ? 'met' : 'missed'} — {sla.passed_count} of {sla.checks.length} check{sla.checks.length !== 1 ? 's' : ''} passed
    </div>
  )
}

function StepRun() {
  const { sessionId, results: storedResults, setResults, setStep, setLastRunConfig, generatedLoadConfig, generatedThreadGroup, sla } = useAppStore()
  const pluginTg = generatedThreadGroup && generatedThreadGroup.type !== 'standard' ? generatedThreadGroup : null
  const [threads, setThreads] = useState('1')
  const [rampup, setRampup] = useState('1')
  const [loops, setLoops] = useState('1')
  const [duration, setDuration] = useState('0')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [done, setDone] = useState(false)
  const [summary, setSummary] = useState<null | { total: number; passed: number; avg: number }>(null)
  const [grafanaUrl, setGrafanaUrl] = useState('')
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [live, setLive] = useState<LiveSnapshot | null>(null)
  const [stopping, setStopping] = useState(false)

  const pollLive = async () => {
    if (!sessionId) return
    try { setLive(await getRunLive(sessionId)) } catch { /* run not registered yet */ }
  }

  const stopTest = async () => {
    if (!sessionId) return
    setStopping(true)
    try { await stopRun(sessionId) } catch (e) { setError(String(e)); setStopping(false) }
  }

  useEffect(() => {
    // Pre-fill from the script that was just generated; the Settings defaults are
    // only a fallback for scripts generated in an earlier session.
    const g = generatedLoadConfig
    if (g) {
      setThreads(String(g.threads)); setRampup(String(g.rampup))
      setLoops(g.loops === -1 ? '' : String(g.loops)); setDuration(String(g.duration_seconds || 0))
    }
    getSettings().then(s => {
      if (!g) {
        if (s.default_threads) setThreads(s.default_threads)
        if (s.default_rampup)  setRampup(s.default_rampup)
        if (s.default_loops)   setLoops(s.default_loops)
      }
      if (s.grafana_url)     setGrafanaUrl(s.grafana_url)
    }).catch(() => {})
    // Reinitialize from store when navigating back
    if (storedResults && !done) {
      setDone(true)
      setSummary({ total: storedResults.total_requests, passed: storedResults.passed, avg: storedResults.avg_ms })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async () => {
    if (!sessionId) return
    const t = Math.max(1, parseInt(threads) || 1)
    const r = Math.max(0, parseInt(rampup) || 0)
    const d = Math.max(0, parseInt(duration) || 0)
    // Blank loops = keep looping until the duration ends
    const l = loops.trim() === '' ? (d > 0 ? -1 : 1) : Math.max(1, parseInt(loops) || 1)
    if (!pluginTg && l === -1 && d === 0) { setError('Set a duration, or a loop count'); return }
    setLoading(true); setError(''); setDone(false); setElapsed(0); setLive(null); setStopping(false)
    // Plugin thread groups follow the schedule in the script; the settings shown are what it plans
    setLastRunConfig(pluginTg && generatedLoadConfig ? generatedLoadConfig : { threads: t, rampup: r, loops: l, duration_seconds: d })
    timerRef.current = setInterval(() => setElapsed(e => e + 1), 1000)
    pollRef.current = setInterval(pollLive, 1000)
    try {
      const res = await runTest(sessionId, pluginTg
        ? { sla: activeSla(sla) }
        : { threads: t, rampup: r, loops: l, duration_seconds: d, sla: activeSla(sla) })
      setResults(res)
      setSummary({ total: res.total_requests, passed: res.passed, avg: res.avg_ms })
      setDone(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false); setStopping(false)
      if (timerRef.current) clearInterval(timerRef.current)
      if (pollRef.current) clearInterval(pollRef.current)
      pollLive()   // final snapshot so the charts show the whole run
    }
  }

  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (pollRef.current) clearInterval(pollRef.current)
  }, [])

  return (
    <motion.div {...fadeSlide}>
      <SectionTitle>Run Performance Test</SectionTitle>

      <div style={{ marginBottom: '16px' }}>
        <InfoBox msg="JMeter must be installed locally. Configure the executable path in Settings if needed." />
      </div>

      <Card style={{ marginBottom: '16px' }}>
        {pluginTg ? (
          <div style={{ marginBottom: '16px' }}>
            <Label>Thread group (from the generated script)</Label>
            <div style={{ fontSize: '14px', color: 'var(--text-primary)', marginBottom: '4px' }}>{describeThreadGroup(pluginTg)}</div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              {generatedLoadConfig && <>Peak {generatedLoadConfig.threads} users · about {fmtSecs(generatedLoadConfig.duration_seconds)} in total. </>}
              Change the schedule in step 3 and generate again.
            </div>
          </div>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '16px', marginBottom: '12px' }}>
              <div><Label>Virtual Users</Label><Input value={threads} onChange={setThreads} type="number" min={1} /></div>
              <div><Label>Ramp-up (seconds)</Label><Input value={rampup} onChange={setRampup} type="number" min={0} /></div>
              <div><Label>Loops (blank = until duration)</Label><Input value={loops} onChange={setLoops} type="number" min={1} placeholder="∞" /></div>
              <div><Label>Duration (seconds, 0 = off)</Label><Input value={duration} onChange={setDuration} type="number" min={0} /></div>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              {generatedLoadConfig ? 'Pre-filled from the generated script. ' : ''}
              {(parseInt(duration) || 0) > 0
                ? `Runs ${parseInt(threads) || 1} user(s) for ${parseInt(duration)}s${loops.trim() ? ` or ${loops} loop(s), whichever ends first` : ''}.`
                : `Runs ${parseInt(threads) || 1} user(s) × ${parseInt(loops) || 1} loop(s).`}
            </div>
          </>
        )}
        <div style={{ marginBottom: '16px' }}>
          <SlaSummary sla={sla} onEdit={() => setStep(2)} />
        </div>
        <Btn onClick={run} disabled={loading || !sessionId}>
          {loading
            ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> Running… ({elapsed}s)</>
            : <><Play size={14} /> Run Test</>}
        </Btn>
      </Card>

      {live ? (
        <Card style={{ marginBottom: '16px', padding: '20px' }}>
          <LiveRunMonitor snapshot={live} onStop={loading ? stopTest : undefined} stopping={stopping} />
        </Card>
      ) : loading && (
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', color: 'var(--text-secondary)' }}>
            <Loader2 size={20} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
            <div>
              <div style={{ fontWeight: 500, marginBottom: '2px' }}>Starting JMeter…</div>
              <div style={{ fontSize: '13px' }}>Elapsed: {elapsed}s</div>
            </div>
          </div>
        </Card>
      )}

      {error && (
        <div>
          <ErrorBox msg={error} />
          <div style={{ marginTop: '10px' }}>
            <InfoBox msg="Make sure JMeter is installed and the path is configured in Settings." />
          </div>
        </div>
      )}

      {done && summary && (
        <motion.div {...fadeSlide}>
          <SlaVerdict sla={storedResults?.sla} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '20px' }}>
            <StatCard label="Total Requests" value={summary.total} />
            <StatCard label="Successful" value={summary.total ? `${((summary.passed / summary.total) * 100).toFixed(1)}%` : '—'} />
            <StatCard label="Avg Response" value={`${summary.avg.toFixed(0)} ms`} />
          </div>
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <Btn onClick={() => setStep(4)}>View Report <ChevronRight size={15} /></Btn>
            {grafanaUrl && (
              <Btn variant="ghost" onClick={() => {
                const base = grafanaUrl.replace('/api/v2/write', '').split('?')[0]
                const grafanaUi = base.replace(':8086', ':3000')
                window.open(grafanaUi, '_blank')
              }}>
                Open Grafana UI
              </Btn>
            )}
          </div>
          {sessionId && <AnalyzeRunPrompt sessionId={sessionId} />}
        </motion.div>
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// Step 4: Report (no charts — high-level table + HTML download)
// ---------------------------------------------------------------------------

function SlaResultCard({ sla, onDefine }: { sla?: SlaResult; onDefine: () => void }) {
  if (!sla?.defined) {
    return (
      <Card style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
        <Target size={18} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: '220px' }}>
          <div style={{ fontSize: '14px', fontWeight: 600 }}>No SLA defined</div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>This run is reported as measured — it is not marked pass or fail. Define an SLA in step 3 and run again to judge it.</div>
        </div>
        <Btn small variant="ghost" onClick={onDefine}>Define SLA</Btn>
      </Card>
    )
  }
  const fmt = (c: SlaCheck, v: number | null) =>
    v == null ? '—' : c.unit === 'ms' ? `${Math.round(v)} ms` : c.unit === '%' ? `${v}%` : `${v} req/s`
  return (
    <Card style={{ marginBottom: '20px' }}>
      <SlaVerdict sla={sla} />
      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>Targets: {sla.targets.join(' · ')}</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              {['Scope', 'Target', 'Actual', 'Result'].map(h => (
                <th key={h} style={{ textAlign: h === 'Scope' ? 'left' : 'right', padding: '8px 12px', color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sla.checks.map((c, i) => (
              <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '9px 12px', fontFamily: c.label ? 'monospace' : undefined, fontSize: '12px' }}>
                  {c.label ?? (c.kind === 'error_rate' ? 'Error rate (all requests)' : 'Throughput (all requests)')}
                  {c.note && <div style={{ fontFamily: 'inherit', color: 'var(--text-muted)', marginTop: '2px' }}>{c.note}</div>}
                </td>
                <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                  {c.kind === 'response_time' ? `${c.metric} ` : ''}{c.kind === 'throughput' ? '≥' : '≤'} {fmt(c, c.target)}
                </td>
                <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmt(c, c.actual)}</td>
                <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 600, color: c.passed ? 'var(--success)' : 'var(--danger)' }}>{c.passed ? 'Met' : 'Missed'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function StepReport() {
  const { results, sessionId, filename, reportUrl: storedReportUrl, lastRunConfig, setStep } = useAppStore()
  const [downloadError, setDownloadError] = useState('')
  const [reportUrl, setReportUrl] = useState<string | null>(storedReportUrl ?? null)
  const [reportChecking, setReportChecking] = useState(false)
  // Check JMeter HTML report availability on mount
  useEffect(() => {
    if (sessionId && !reportUrl) {
      setReportChecking(true)
      getReportStatus(sessionId)
        .then(s => { if (s.available && s.url) setReportUrl(s.url) })
        .catch(() => {})
        .finally(() => setReportChecking(false))
    }
    if (storedReportUrl) setReportUrl(storedReportUrl)
  }, [sessionId, storedReportUrl]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!results) {
    return (
      <motion.div {...fadeSlide}>
        <SectionTitle>View Report</SectionTitle>
        <InfoBox msg="No test results yet. Run a test first." />
      </motion.div>
    )
  }

  const passRate = results.total_requests > 0 ? ((results.passed / results.total_requests) * 100).toFixed(1) : '0'
  const slaByLabel = new Map(
    (results.sla?.defined ? results.sla.checks : []).filter(c => c.kind === 'response_time' && c.label).map(c => [c.label as string, c]),
  )
  const threads = results.by_label ? Object.values(results.by_label).length : 0

  const handleDownloadReport = async () => {
    if (!sessionId) return
    try {
      await downloadReport(sessionId)
    } catch (e) {
      setDownloadError(String(e))
    }
  }

  const summaryRows = [
    { label: 'Virtual Users', value: lastRunConfig ? String(lastRunConfig.threads) : '—', note: lastRunConfig ? '' : 'configured at run time' },
    { label: 'Ramp-up', value: lastRunConfig ? `${lastRunConfig.rampup}s` : '—', note: lastRunConfig ? '' : 'configured at run time' },
    { label: 'Configured', value: lastRunConfig ? (lastRunConfig.duration_seconds ? `${lastRunConfig.duration_seconds}s` : `${lastRunConfig.loops} loop${lastRunConfig.loops !== 1 ? 's' : ''}`) : '—', note: lastRunConfig?.duration_seconds ? 'scheduled duration' : '' },
    { label: 'Duration', value: `${results.duration_sec.toFixed(1)}s`, note: '' },
    { label: 'Total Requests', value: String(results.total_requests), note: '' },
    { label: 'Passed', value: String(results.passed), note: `${passRate}%` },
    { label: 'Failed', value: String(results.failed), note: results.failed > 0 ? `${results.error_rate.toFixed(1)}%` : '—' },
    { label: 'Error Rate', value: `${results.error_rate.toFixed(2)}%`, note: '' },
    { label: 'Throughput', value: `${results.throughput.toFixed(2)} req/s`, note: '' },
    { label: 'Avg Response Time', value: `${results.avg_ms.toFixed(0)} ms`, note: '' },
    { label: 'P90 Response Time', value: `${results.p90_ms} ms`, note: '' },
    { label: 'P95 Response Time', value: `${results.p95_ms} ms`, note: '' },
    { label: 'Min / Max', value: `${results.min_ms} / ${results.max_ms} ms`, note: '' },
  ]

  return (
    <motion.div {...fadeSlide}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <h2 style={{ fontSize: '32px', fontWeight: 600, lineHeight: 1.15, display: 'flex', alignItems: 'center', gap: '14px' }}>
          <span style={{ width: '4px', height: '28px', borderRadius: '4px', background: 'var(--accent-gradient)', flexShrink: 0 }} />
          Test Report
        </h2>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          {sessionId && (
            <Btn variant="ghost" small onClick={() => downloadJmx(sessionId)}>
              <Download size={13} /> Download JMX
            </Btn>
          )}
          {reportUrl ? (
            <Btn small onClick={() => window.open(reportUrl, '_blank')}>
              <BarChart2 size={13} /> Open JMeter Report
            </Btn>
          ) : (
            <Btn variant="ghost" small onClick={handleDownloadReport} disabled={reportChecking}>
              {reportChecking
                ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> Checking…</>
                : <><Download size={13} /> Download Summary Report</>}
            </Btn>
          )}
        </div>
      </div>

      {downloadError && <div style={{ marginBottom: '12px' }}><ErrorBox msg={downloadError} /></div>}

      <SlaResultCard sla={results.sla} onDefine={() => setStep(2)} />

      {/* High-level summary table */}
      <Card style={{ marginBottom: '20px' }}>
        <div style={{ fontSize: '14px', fontWeight: 600, marginBottom: '16px', color: 'var(--text-secondary)' }}>Test Execution Summary</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '14px' }}>
          <tbody>
            {summaryRows.map(r => (
              <tr key={r.label} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '10px 12px', color: 'var(--text-muted)', width: '40%' }}>{r.label}</td>
                <td style={{ padding: '10px 12px', fontWeight: 600, color: r.label === 'Failed' && results.failed > 0 ? 'var(--danger)' : r.label === 'Error Rate' && results.error_rate > 0 ? 'var(--danger)' : 'var(--text-primary)' }}>
                  {r.value}
                </td>
                {r.note && <td style={{ padding: '10px 12px', color: 'var(--text-muted)', fontSize: '12px' }}>{r.note}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* Per-endpoint breakdown */}
      {Object.keys(results.by_label).length > 0 && (
        <Card>
          <div style={{ fontSize: '14px', fontWeight: 600, marginBottom: '14px', color: 'var(--text-secondary)' }}>Per-Transaction / Endpoint Detail</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Endpoint', 'Total', 'Success %', 'Error %', 'Avg ms', 'P90 ms', 'P95 ms', ...(slaByLabel.size ? ['SLA'] : [])].map(h => (
                    <th key={h} style={{ textAlign: h === 'Endpoint' ? 'left' : 'right', padding: '8px 12px', color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(results.by_label).map(([label, m]) => {
                  const check = slaByLabel.get(label)
                  return (
                    <tr key={label} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '9px 12px', fontFamily: 'monospace', fontSize: '12px', maxWidth: '320px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={label}>{label}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--text-secondary)' }}>{m.total}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                        {m.total > 0 ? ((m.passed / m.total) * 100).toFixed(1) : '0'}%
                      </td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: m.error_rate > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>{m.error_rate.toFixed(1)}%</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--text-secondary)' }}>{m.avg_ms.toFixed(0)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--text-secondary)' }}>{m.p90_ms ?? '—'}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: 'var(--text-secondary)' }}>{m.p95_ms}</td>
                      {slaByLabel.size > 0 && (
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 600, color: check ? (check.passed ? 'var(--success)' : 'var(--danger)') : 'var(--text-muted)' }}>
                          {check ? (check.passed ? 'Met' : 'Missed') : '—'}
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Report analyzer: insights from the JMeter HTML report + Gmail sharing */}
      {sessionId && <ReportAnalyzer sessionId={sessionId} />}

      <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
        File: {filename || 'unknown'} &nbsp;·&nbsp; {threads} endpoint(s) tested
      </div>
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// Main AppPage
// ---------------------------------------------------------------------------

export default function AppPage() {
  const { currentStep, maxReachedStep, setStep } = useAppStore()

  const stepComponents = [
    <StepUpload key="upload" />,
    <StepCorrelate key="correlate" />,
    <StepGenerate key="generate" />,
    <StepRun key="run" />,
    <StepReport key="report" />,
  ]

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 72px)', overflow: 'hidden' }}>
      <aside style={{ width: '264px', flexShrink: 0, background: 'rgba(22,21,21,0.6)', backdropFilter: 'blur(16px)', borderRight: '1px solid var(--border)', padding: '32px 18px', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
        <div style={{ marginBottom: '24px', paddingLeft: '10px' }}>
          <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--accent)', fontWeight: 600, marginBottom: '6px' }}>Workflow</div>
          <div style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>HAR to JMeter</div>
          <div style={{ marginTop: '16px', height: '3px', borderRadius: '3px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${(currentStep / (STEPS.length - 1)) * 100}%`, background: 'var(--accent-gradient)', borderRadius: '3px', transition: 'width 0.4s ease' }} />
          </div>
          <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>Step {currentStep + 1} of {STEPS.length}</div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {STEPS.map((s, i) => {
            const isActive = i === currentStep
            const isReachable = i <= maxReachedStep
            const isDone = i < currentStep && isReachable
            const Icon = s.icon
            const idleBg = isActive ? 'rgba(var(--accent-rgb),0.12)' : 'transparent'
            return (
              <button key={i} onClick={() => isReachable ? setStep(i) : undefined}
                style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 12px', borderRadius: '14px', border: `1px solid ${isActive ? 'rgba(var(--accent-rgb),0.35)' : 'transparent'}`, background: idleBg, color: isActive ? 'var(--text-primary)' : isReachable ? 'var(--text-secondary)' : 'var(--text-muted)', cursor: isReachable ? 'pointer' : 'default', width: '100%', textAlign: 'left', transition: 'all 0.2s', fontSize: '14px', fontWeight: isActive ? 600 : 500, opacity: isReachable ? 1 : 0.55 }}
                onMouseEnter={e => { if (isReachable && !isActive) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.05)' }}
                onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = idleBg }}
              >
                <div style={{ width: '32px', height: '32px', flexShrink: 0, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: isActive ? 'var(--accent-gradient)' : isDone ? 'rgba(var(--success-rgb),0.12)' : 'rgba(255,255,255,0.05)', border: isActive ? 'none' : `1px solid ${isDone ? 'rgba(var(--success-rgb),0.3)' : 'var(--border)'}`, boxShadow: isActive ? '0 6px 16px -6px rgba(var(--accent-rgb),0.8)' : 'none' }}>
                  {isDone ? <CheckCircle size={15} style={{ color: 'var(--success)' }} />
                    : <Icon size={14} style={{ color: isActive ? '#fff' : 'var(--text-muted)' }} />}
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: '11px', fontWeight: 500, color: isActive ? 'var(--accent)' : 'var(--text-muted)', letterSpacing: '0.06em' }}>{String(i + 1).padStart(2, '0')}</div>
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</div>
                </div>
              </button>
            )
          })}
        </div>

        <div style={{ marginTop: 'auto', paddingTop: '24px' }}>
          <button onClick={() => { useAppStore.getState().reset(); setStep(0) }}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', width: '100%', padding: '10px 14px', borderRadius: '999px', border: '1px solid var(--border-strong)', background: 'rgba(255,255,255,0.04)', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '13px', fontWeight: 500, transition: 'all 0.2s' }}
            onMouseEnter={e => { const el = e.currentTarget as HTMLButtonElement; el.style.background = 'rgba(255,255,255,0.1)'; el.style.color = 'var(--text-primary)' }}
            onMouseLeave={e => { const el = e.currentTarget as HTMLButtonElement; el.style.background = 'rgba(255,255,255,0.04)'; el.style.color = 'var(--text-secondary)' }}
          >
            <RefreshCw size={13} /> Start Over
          </button>
        </div>
      </aside>

      <main style={{ flex: 1, overflowY: 'auto', padding: '48px 56px' }}>
        <div style={{ maxWidth: '1200px' }}>
          <AnimatePresence mode="wait">
            {stepComponents[currentStep]}
          </AnimatePresence>
        </div>
      </main>
    </div>
  )
}
