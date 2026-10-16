/**
 * One analysed run: verdict and observations, the timeline, findings with their
 * evidence, statistics per transaction and request, the comparison with its
 * baseline, reports, and questions answered by the AI over the run's data.
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, ChevronDown, ChevronRight, Download, ExternalLink, FileText, GitCompare, ListTree, MessageSquare, Search, Send, Trash2 } from 'lucide-react'
import {
  askRun, deleteResultRun, downloadLabelsReport, downloadResultReport, getResultRun, labelsReportUrl, listRunLabels,
  resultReportUrl, type AnalysisFinding, type AskAnswer, type LabelStats, type RunAnalysis,
} from '../api/client'
import ObservationsPanel from '../components/results/ObservationsPanel'
import ServerMetricsCard from '../components/results/ServerMetrics'
import Timeline from '../components/results/Timeline'
import {
  Btn, Card, CardTitle, Notice, RichText, SeverityBadge, Spinner, Stat, VerdictBadge, fieldStyle, fmtDate,
  fmtDuration, fmtMs, fmtNum, fmtOffset, fmtPct, num, td, th,
} from '../components/results/ui'

const fade = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0, transition: { duration: 0.3 } } }

export default function ResultsRunPage() {
  const { runId = '' } = useParams()
  const navigate = useNavigate()
  const [a, setA] = useState<RunAnalysis | null>(null)
  const [error, setError] = useState('')
  const [useAi, setUseAi] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  useEffect(() => {
    setA(null); setError('')
    getResultRun(runId).then(setA).catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [runId])

  if (error) {
    return <Page><BackLink /><Notice tone="danger">{error}</Notice></Page>
  }
  if (!a) {
    return <Page><BackLink /><div style={{ color: 'var(--text-muted)', display: 'flex', gap: 8, alignItems: 'center' }}><Spinner /> Loading analysis…</div></Page>
  }

  const kind = a.overall.transaction ? 'transaction' : 'request'
  const primary = a.overall[kind]
  const req = a.overall.request ?? primary
  const errorTarget = a.sla.checks?.find(c => c.kind === 'error_rate')?.target ?? null

  return (
    <Page>
      <BackLink />
      <motion.div {...fade}>
        {/* Header */}
        <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', marginBottom: '20px' }}>
          <div style={{ minWidth: 0 }}>
            <h1 style={{ fontSize: '32px', fontWeight: 600, lineHeight: 1.15, marginBottom: '6px', wordBreak: 'break-word' }}>
              {a.run.scenario}{a.run.tag && <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}> · {a.run.tag}</span>}
            </h1>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              {fmtDuration(a.run.duration_s)} run · {fmtNum(a.run.samples)} samples · peak {fmtNum(a.run.peak_users)} users
              {a.source_file && <> · {a.source_file}</>} · analysed {fmtDate(a.created_at)}
              <br /><span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>{a.run.run_id}</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <Btn small onClick={() => window.open(resultReportUrl(a.run.run_id, 'tech', useAi), '_blank')}><FileText size={13} /> Technical report <ExternalLink size={12} /></Btn>
            <Btn small variant="ghost" onClick={() => window.open(resultReportUrl(a.run.run_id, 'exec', useAi), '_blank')}>Executive summary <ExternalLink size={12} /></Btn>
            <Btn small variant="ghost" onClick={() => downloadResultReport(a.run.run_id, 'tech', 'md', useAi).catch(e => setError(String(e)))}><Download size={13} /> Markdown</Btn>
            <Btn small variant="ghost" onClick={() => window.open(labelsReportUrl(a.run.run_id), '_blank')} title="A section per transaction and main request, with a plain-language summary of each">
              <ListTree size={13} /> Requests & transactions <ExternalLink size={12} />
            </Btn>
            <Btn small variant="ghost" onClick={() => navigate(`/analyze/compare?current=${encodeURIComponent(a.run.run_id)}&base=${encodeURIComponent(a.baseline?.found && a.baseline.run_id ? a.baseline.run_id : 'previous')}`)}>
              <GitCompare size={13} /> Compare
            </Btn>
            <label style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}
              title="Reports with AI-written text (only findings.json is sent to your AI provider)">
              <input type="checkbox" checked={useAi} onChange={e => setUseAi(e.target.checked)} /> AI narrative
            </label>
          </div>
        </div>

        {(a.warnings ?? []).length > 0 && (
          <div style={{ marginBottom: '16px', display: 'grid', gap: '8px' }}>
            {a.warnings!.map((w, i) => <Notice key={i} tone="info">{w}.</Notice>)}
          </div>
        )}

        {/* KPIs */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px', marginBottom: '20px' }}>
          <Stat label="Peak users" value={fmtNum(a.run.peak_users)} sub={`steady state ${fmtDuration(a.steady.duration_s)}`} />
          <Stat label={`p95 (${kind}s)`} value={fmtMs(primary?.p95_ms)} sub="steady state" />
          <Stat label="Error rate" value={fmtPct(req?.error_pct)} sub="requests, steady state" />
          <Stat label="Throughput" value={`${fmtNum(primary?.tps, 1)}/s`} sub={`${kind}s per second`} />
        </div>

        <Card style={{ marginBottom: '20px' }}>
          <CardTitle>Observations</CardTitle>
          <ObservationsPanel analysis={a} />
        </Card>

        <Card style={{ marginBottom: '20px' }}>
          <CardTitle>Timeline</CardTitle>
          <Timeline points={a.timeline.points} bucketS={a.timeline.bucket_s} durationS={a.run.duration_s}
            steady={a.steady} findings={a.findings} errorTarget={errorTarget} />
        </Card>

        <ServerMetricsCard analysis={a} onAttached={() => getResultRun(runId).then(setA).catch(() => {})} />

        <Card style={{ marginBottom: '20px' }}>
          <CardTitle>Findings <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>({a.findings.length})</span></CardTitle>
          {a.findings.length === 0
            ? <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>No problems found: no SLA breach, change point, error burst, saturation, load-generator or correlation problem.</div>
            : <div style={{ display: 'grid' }}>{a.findings.map(f => <FindingRow key={f.n} f={f} />)}</div>}
          {a.skipped.length > 0 && (
            <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
              Not checked: {a.skipped.map(s => `${s.detector.replace(/_/g, ' ')} (${s.reason})`).join('; ')}
            </div>
          )}
        </Card>

        {a.sla.defined && a.sla.checks && (
          <Card style={{ marginBottom: '20px' }}>
            <CardTitle>SLA results</CardTitle>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr>
                  <th style={{ ...th, textAlign: 'left' }}>Check</th><th style={{ ...th, textAlign: 'left' }}>Scope</th>
                  <th style={{ ...th, ...num }}>Target</th><th style={{ ...th, ...num }}>Measured</th><th style={{ ...th, textAlign: 'left' }}>Result</th>
                </tr></thead>
                <tbody>{a.sla.checks.map((c, i) => (
                  <tr key={i}>
                    <td style={td}>{c.metric.replace('_', ' ')}</td>
                    <td style={td}>{c.label || 'All requests'}</td>
                    <td style={{ ...td, ...num }}>{fmtNum(c.target, c.unit === 'ms' ? 0 : 2)} {c.unit}</td>
                    <td style={{ ...td, ...num, color: 'var(--text-primary)' }}>{fmtNum(c.actual, c.unit === 'ms' ? 0 : 2)} {c.unit}</td>
                    <td style={td}><VerdictBadge verdict={c.passed ? 'pass' : 'fail'} /></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
              Measured over the steady state, {fmtOffset(a.steady.start_offset_s)} to {fmtOffset(a.steady.end_offset_s)}.
            </div>
          </Card>
        )}

        {a.baseline && <BaselineCard a={a} />}

        {(['transaction', 'request'] as const).map(k => a.labels[k]?.length ? (
          <Card key={k} style={{ marginBottom: '20px' }}>
            <CardTitle right={k === 'request' ? (
              <Btn small variant="ghost" onClick={() => downloadLabelsReport(a.run.run_id, 'md').catch(e => setError(String(e)))}><Download size={13} /> Per-label report (Markdown)</Btn>
            ) : undefined}>{k === 'transaction' ? 'Transactions' : 'Requests'}</CardTitle>
            <LabelTable rows={a.labels[k]!} runId={a.run.run_id} />
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>Times in ms over the steady state; percentiles are exact (JMeter Aggregate Report definition).</div>
          </Card>
        ) : null)}

        <LabelSearch runId={a.run.run_id} total={a.run.labels.request + a.run.labels.transaction} />

        {a.errors.length > 0 && (
          <Card style={{ marginBottom: '20px' }}>
            <CardTitle>Top errors</CardTitle>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr>
                  <th style={{ ...th, textAlign: 'left' }}>Label</th><th style={{ ...th, textAlign: 'left' }}>Code</th>
                  <th style={{ ...th, textAlign: 'left' }}>Message</th><th style={{ ...th, ...num }}>Count</th>
                  <th style={{ ...th, textAlign: 'left' }}>First</th><th style={{ ...th, textAlign: 'left' }}>Last</th>
                </tr></thead>
                <tbody>{a.errors.map((e, i) => (
                  <tr key={i}>
                    <td style={{ ...td, color: 'var(--text-primary)' }}>{e.label}</td><td style={td}>{e.response_code}</td>
                    <td style={{ ...td, maxWidth: '360px' }}>{e.failure || e.response_message}</td>
                    <td style={{ ...td, ...num }}>{fmtNum(e.count)}</td>
                    <td style={td}>{fmtOffset(e.first_offset_s)}</td><td style={td}>{fmtOffset(e.last_offset_s)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>Whole run, including ramp-up and ramp-down.</div>
          </Card>
        )}

        <AskCard runId={a.run.run_id} />

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
          <span style={{ flex: 1, minWidth: '260px', lineHeight: 1.6 }}>
            Steady state: {a.steady.note || `${fmtOffset(a.steady.start_offset_s)} to ${fmtOffset(a.steady.end_offset_s)}`}.
            {a.has_plan ? ' Correlation problems were mapped using the test plan.' : ''}
          </span>
          {confirmDelete ? (
            <span style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              Delete this analysis and its stored samples?
              <Btn small variant="danger" onClick={() => deleteResultRun(a.run.run_id).then(() => navigate('/analyze')).catch(e => setError(String(e)))}>Delete</Btn>
              <Btn small variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Btn>
            </span>
          ) : (
            <Btn small variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 size={13} /> Delete analysis</Btn>
          )}
        </div>
      </motion.div>
    </Page>
  )
}

function Page({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '32px 24px 64px' }}>
      {children}
      <style>{'@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }'}</style>
    </div>
  )
}

function BackLink() {
  return (
    <Link to="/analyze" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)', textDecoration: 'none', fontSize: '14px', marginBottom: '18px' }}>
      <ArrowLeft size={15} /> All analyses
    </Link>
  )
}

function FindingRow({ f }: { f: AnalysisFinding }) {
  const [open, setOpen] = useState(false)
  const ev = f.evidence || {}
  const mapping = (ev.mapping as { var: string; extractors: { description: string; source_error_pct?: number | null }[] }[] | undefined) ?? []
  const scalars = Object.entries(ev).filter(([k, v]) => k !== 'mapping' && k !== 'fixes' && (v == null || typeof v !== 'object'))
  const lists = Object.entries(ev).filter(([k, v]) => k !== 'mapping' && k !== 'fixes' && Array.isArray(v) && v.length && typeof v[0] === 'object') as [string, Record<string, unknown>[]][]
  const windowText = f.start_offset_s != null
    ? (f.end_offset_s != null ? `${fmtOffset(f.start_offset_s)} to ${fmtOffset(f.end_offset_s)}` : `from ${fmtOffset(f.start_offset_s)}`) : ''
  return (
    <div style={{ borderTop: f.n > 1 ? '1px solid var(--border)' : 'none', padding: f.n > 1 ? '14px 0' : '2px 0 14px' }}>
      <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--text-muted)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>#{f.n}</span>
        <SeverityBadge severity={f.severity} />
        <span style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>{f.title}</span>
      </div>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '4px 0 6px', paddingLeft: '32px' }}>
        {windowText}{windowText && f.labels.length ? ' · ' : ''}{f.labels.slice(0, 5).join(', ')}{f.labels.length > 5 ? ` +${f.labels.length - 5}` : ''}
      </div>
      <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.55, paddingLeft: '32px' }}>{f.detail}</div>
      {mapping.length > 0 && (
        <ul style={{ paddingLeft: '52px', marginTop: '6px', fontSize: '13px', color: 'var(--text-secondary)', display: 'grid', gap: '2px' }}>
          {mapping.flatMap(m => m.extractors.map((x, i) => (
            <li key={`${m.var}-${i}`}><span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{m.var}</span> ← {x.description}
              {x.source_error_pct ? <span style={{ color: 'var(--warning)' }}> (that request failed {x.source_error_pct}%)</span> : null}</li>
          )))}
        </ul>
      )}
      <button onClick={() => setOpen(o => !o)} style={{ marginLeft: '32px', marginTop: '6px', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '4px', padding: 0 }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Evidence
      </button>
      {open && (
        <div style={{ paddingLeft: '32px', marginTop: '8px', display: 'grid', gap: '10px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '3px 16px', fontSize: '12.5px' }}>
            {scalars.map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}>
                <span style={{ color: 'var(--text-muted)' }}>{k.replace(/_/g, ' ')}</span>
                <span style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{typeof v === 'number' ? fmtNum(v, Number.isInteger(v) ? 0 : 2) : String(v)}</span>
              </div>
            ))}
          </div>
          {lists.map(([k, rows]) => {
            const cols = Array.from(new Set(rows.flatMap(r => Object.keys(r))))
            return (
              <div key={k} style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', minWidth: '50%' }}>
                  <thead><tr>{cols.map(c => <th key={c} style={{ ...th, textAlign: 'left' }}>{c.replace(/_/g, ' ')}</th>)}</tr></thead>
                  <tbody>{rows.map((r, i) => <tr key={i}>{cols.map(c => <td key={c} style={td}>{typeof r[c] === 'number' ? fmtNum(r[c] as number, Number.isInteger(r[c]) ? 0 : 2) : String(r[c] ?? '—')}</td>)}</tr>)}</tbody>
                </table>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function LabelTable({ rows, runId }: { rows: LabelStats[]; runId: string }) {
  const [sort, setSort] = useState<keyof LabelStats>('p95_ms')
  const sorted = [...rows].sort((x, y) => sort === 'label' ? x.label.localeCompare(y.label) : (Number(y[sort]) - Number(x[sort])))
  const cols: [keyof LabelStats, string][] = [['samples', 'Samples'], ['avg_ms', 'Avg'], ['p50_ms', 'p50'], ['p90_ms', 'p90'], ['p95_ms', 'p95'], ['p99_ms', 'p99'], ['max_ms', 'Max'], ['error_pct', 'Errors'], ['tps', 'Per s']]
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr>
          <th style={{ ...th, textAlign: 'left', cursor: 'pointer' }} onClick={() => setSort('label')}>Label{sort === 'label' ? ' ↓' : ''}</th>
          {cols.map(([k, h]) => <th key={k} style={{ ...th, ...num, cursor: 'pointer' }} onClick={() => setSort(k)}>{h}{sort === k ? ' ↓' : ''}</th>)}
        </tr></thead>
        <tbody>{sorted.map(r => (
          <tr key={r.label}>
            <td style={{ ...td, maxWidth: '360px', wordBreak: 'break-word' }}>
              <Link to={`/analyze/${runId}/label?name=${encodeURIComponent(r.label)}`} style={{ color: 'var(--text-primary)', textDecoration: 'none' }}
                onMouseEnter={e => (e.currentTarget.style.textDecoration = 'underline')} onMouseLeave={e => (e.currentTarget.style.textDecoration = 'none')}>{r.label}</Link>
            </td>
            {cols.map(([k]) => (
              <td key={k} style={{ ...td, ...num, color: k === 'error_pct' && r.error_pct > 0 ? 'var(--text-primary)' : undefined }}>
                {k === 'error_pct' ? fmtPct(r.error_pct) : k === 'tps' ? fmtNum(r.tps, 2) : fmtNum(Number(r[k]))}
              </td>
            ))}
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
}

function LabelSearch({ runId, total }: { runId: string; total: number }) {
  const [all, setAll] = useState<(LabelStats & { kind: string })[] | null>(null)
  const [q, setQ] = useState('')
  const load = () => { if (!all) listRunLabels(runId).then(r => setAll(r.labels)).catch(() => setAll([])) }
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  const matches = (all ?? []).filter(r => words.every(w => r.label.toLowerCase().includes(w)))
    .sort((x, y) => y.samples - x.samples).slice(0, 40)
  return (
    <Card style={{ marginBottom: '20px' }}>
      <CardTitle>Find a request or transaction <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>({fmtNum(total)} in this run)</span></CardTitle>
      <div style={{ position: 'relative', marginBottom: '10px' }}>
        <Search size={15} style={{ position: 'absolute', left: 12, top: 12, color: 'var(--text-muted)' }} />
        <input style={{ ...fieldStyle, paddingLeft: '34px' }} value={q} onFocus={load} onChange={e => { setQ(e.target.value); load() }}
          placeholder="Type part of a label, e.g. login or /api/v1/orders" />
      </div>
      {all && q && (
        matches.length === 0 ? <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>No label matches “{q}”.</div> : (
          <div style={{ overflowX: 'auto', maxHeight: '360px', overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }}>Label</th><th style={{ ...th, textAlign: 'left' }}>Kind</th>
                <th style={{ ...th, ...num }}>Samples</th><th style={{ ...th, ...num }}>p95</th><th style={{ ...th, ...num }}>Errors</th>
              </tr></thead>
              <tbody>{matches.map(r => (
                <tr key={r.label}>
                  <td style={{ ...td, wordBreak: 'break-word', maxWidth: '420px' }}>
                    <Link to={`/analyze/${runId}/label?name=${encodeURIComponent(r.label)}`} style={{ color: 'var(--text-primary)', textDecoration: 'none' }}>{r.label}</Link>
                  </td>
                  <td style={td}>{r.kind}</td><td style={{ ...td, ...num }}>{fmtNum(r.samples)}</td>
                  <td style={{ ...td, ...num }}>{fmtMs(r.p95_ms)}</td><td style={{ ...td, ...num }}>{fmtPct(r.error_pct)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )
      )}
      {!q && <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Every label that ran in the steady state; open one for its own timeline, distribution, errors and summary.</div>}
    </Card>
  )
}

function BaselineCard({ a }: { a: RunAnalysis }) {
  const b = a.baseline!
  const regressions = a.findings.filter(f => f.type === 'regression')
  return (
    <Card style={{ marginBottom: '20px' }}>
      <CardTitle>Compared with the baseline</CardTitle>
      {!b.found ? (
        <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>No baseline: {b.note}.</div>
      ) : (
        <div style={{ display: 'grid', gap: '12px' }}>
          <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Baseline <Link to={`/analyze/${b.run_id}`} style={{ color: 'var(--text-primary)' }}>{b.tag || b.run_id}</Link>
            {b.created_at && <> (analysed {fmtDate(b.created_at)})</>}. Load {fmtNum(b.load?.baseline_users)} → {fmtNum(b.load?.current_users)} users,{' '}
            {fmtNum(b.load?.baseline_tps, 1)} → {fmtNum(b.load?.current_tps, 1)} {b.load?.kind}s/s —{' '}
            {b.load?.comparable ? 'comparable.' : <strong style={{ color: 'var(--warning)' }}>not comparable ({b.load?.reason}), so differences are not counted.</strong>}
          </div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
            {b.summary?.regressed} regressed · {b.summary?.improved} improved · {b.summary?.compared} labels compared
            {b.thresholds && <> · regression = p95 +{b.thresholds.p95_pct}% and +{b.thresholds.p95_ms} ms, or errors +{b.thresholds.err_pp} pt</>}
          </div>
          {regressions.map(f => {
            const rows = (f.evidence.labels as Record<string, number | string>[]) ?? []
            const p95 = f.evidence.metric === 'p95_ms'
            return (
              <div key={f.n} style={{ overflowX: 'auto' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>{p95 ? 'Slower than the baseline' : 'More errors than the baseline'}</div>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr>
                    <th style={{ ...th, textAlign: 'left' }}>Label</th><th style={{ ...th, ...num }}>Baseline</th>
                    <th style={{ ...th, ...num }}>Now</th><th style={{ ...th, ...num }}>Change</th>
                  </tr></thead>
                  <tbody>{rows.map((r, i) => (
                    <tr key={i}>
                      <td style={{ ...td, color: 'var(--text-primary)' }}>{String(r.label)}</td>
                      <td style={{ ...td, ...num }}>{p95 ? fmtMs(Number(r.p95_before)) : fmtPct(Number(r.err_before))}</td>
                      <td style={{ ...td, ...num, color: 'var(--text-primary)' }}>{p95 ? fmtMs(Number(r.p95_after)) : fmtPct(Number(r.err_after))}</td>
                      <td style={{ ...td, ...num }}>{p95 ? `+${fmtNum(Number(r.increase_pct), 1)}%` : `+${fmtNum(Number(r.increase_pp), 2)} pt`}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

const SUGGESTIONS = [
  'Which request slowed down first, and by how much?',
  'Were all requests affected equally by the errors?',
  'What did response times look like per minute during the steady state?',
]

function AskCard({ runId }: { runId: string }) {
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [answers, setAnswers] = useState<AskAnswer[]>([])
  const [error, setError] = useState('')

  const ask = async (q: string) => {
    if (!q.trim()) return
    setBusy(true); setError('')
    try {
      const res = await askRun(runId, q.trim())
      setAnswers(prev => [res, ...prev]); setQuestion('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card style={{ marginBottom: '20px' }}>
      <CardTitle><span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}><MessageSquare size={16} /> Ask about this run</span></CardTitle>
      <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '12px', lineHeight: 1.55 }}>
        The AI starts from the run's findings and can run read-only queries on this run's samples (200 rows and 10 seconds per query). It shows the queries it used, and its numbers are checked against the results.
      </div>
      <form onSubmit={e => { e.preventDefault(); ask(question) }} style={{ display: 'flex', gap: '8px', marginBottom: '10px' }}>
        <input style={fieldStyle} value={question} onChange={e => setQuestion(e.target.value)} placeholder="e.g. When did response times start to rise, and which request was hit hardest?" disabled={busy} />
        <Btn type="submit" disabled={busy || !question.trim()}>{busy ? <><Spinner /> Thinking…</> : <><Send size={14} /> Ask</>}</Btn>
      </form>
      {!answers.length && !busy && (
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {SUGGESTIONS.map(s => (
            <button key={s} onClick={() => ask(s)} style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', borderRadius: '999px', padding: '5px 12px', color: 'var(--text-secondary)', fontSize: '12px', cursor: 'pointer' }}>{s}</button>
          ))}
        </div>
      )}
      {busy && <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '8px' }}>This can take a minute: the AI may run several queries.</div>}
      {error && <div style={{ marginTop: '10px' }}><Notice tone="danger">{error}</Notice></div>}
      {answers.map((r, i) => <AnswerBlock key={i} r={r} />)}
    </Card>
  )
}

function AnswerBlock({ r }: { r: AskAnswer }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ borderTop: '1px solid var(--border)', marginTop: '16px', paddingTop: '16px' }}>
      <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '8px' }}>{r.question}</div>
      <RichText text={r.answer} />
      {r.warning && <div style={{ marginTop: '8px' }}><Notice tone="warning">{r.warning}</Notice></div>}
      <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '10px', display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
        <span>{r.provider} · {r.queries.length} quer{r.queries.length === 1 ? 'y' : 'ies'}</span>
        {r.numbers && <span>{r.numbers.ok ? `all ${r.numbers.checked} numbers verified` : `${r.numbers.unverified.length} number(s) not found in the data: ${r.numbers.unverified.map(u => `“${u.text}”`).join(', ')}`}</span>}
        {r.queries.length > 0 && (
          <button onClick={() => setOpen(o => !o)} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '12px', display: 'inline-flex', alignItems: 'center', gap: '4px', padding: 0 }}>
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Queries used
          </button>
        )}
      </div>
      {open && (
        <div style={{ display: 'grid', gap: '12px', marginTop: '10px' }}>
          {r.queries.map(q => (
            <div key={q.n}>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                Q{q.n} · {q.error ? <span style={{ color: 'var(--warning)' }}>{q.error}</span> : `${q.rows.length} row(s)${q.truncated ? ' (cut)' : ''} · ${q.seconds.toFixed(2)}s`}
              </div>
              <pre style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '10px', padding: '10px 12px', fontSize: '12px', overflowX: 'auto', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{q.sql}</pre>
              {!q.error && q.rows.length > 0 && (
                <div style={{ overflowX: 'auto', maxHeight: '220px', overflowY: 'auto', marginTop: '6px' }}>
                  <table style={{ borderCollapse: 'collapse' }}>
                    <thead><tr>{q.columns.map(c => <th key={c} style={{ ...th, textAlign: 'left' }}>{c}</th>)}</tr></thead>
                    <tbody>{q.rows.slice(0, 50).map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} style={{ ...td, fontVariantNumeric: 'tabular-nums' }}>{v}</td>)}</tr>)}</tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
