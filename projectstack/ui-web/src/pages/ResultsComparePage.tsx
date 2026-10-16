/**
 * Compare two analysed runs: headline figures side by side, both timelines overlaid,
 * every label's change, and which findings are new, resolved or still present.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, ArrowLeftRight, Download, TrendingDown, TrendingUp } from 'lucide-react'
import {
  compareRuns, downloadComparison, listResultRuns, type CompareRow, type RunComparison, type ResultsRunRow,
} from '../api/client'
import MultiTimeline from '../components/results/MultiTimeline'
import {
  Btn, Card, CardTitle, Notice, SeverityBadge, Spinner, VerdictBadge, fieldStyle, fmtDate, fmtMs, fmtNum, fmtPct,
  num, td, th,
} from '../components/results/ui'

const fade = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0, transition: { duration: 0.3 } } }
const BASE_COLOR = 'var(--series-2)'
const CUR_COLOR = 'var(--series-1)'

function runName(r: { tag?: string; run_id: string }) { return r.tag || r.run_id }

export default function ResultsComparePage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const base = params.get('base') ?? 'previous'
  const current = params.get('current') ?? ''
  const [runs, setRuns] = useState<ResultsRunRow[]>([])
  const [c, setC] = useState<RunComparison | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState<'all' | 'regressed' | 'improved'>('all')

  useEffect(() => { listResultRuns(200).then(r => setRuns(r.runs)).catch(() => {}) }, [])
  useEffect(() => {
    if (!current) return
    setBusy(true); setError(''); setC(null)
    compareRuns(base, current).then(setC).catch(e => setError(e instanceof Error ? e.message : String(e))).finally(() => setBusy(false))
  }, [base, current])

  const pick = (key: 'base' | 'current', value: string) => {
    const next = new URLSearchParams(params)
    next.set(key, value)
    setParams(next)
  }

  const runOptions = (
    <>
      {runs.map(r => <option key={r.run_id} value={r.run_id}>{r.scenario} · {runName(r)} · {fmtDate(r.created_at)}</option>)}
    </>
  )

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '32px 24px 64px' }}>
      <Link to={current ? `/analyze/${current}` : '/analyze'} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)', textDecoration: 'none', fontSize: '14px', marginBottom: '18px' }}>
        <ArrowLeft size={15} /> {current ? 'Back to the run' : 'All analyses'}
      </Link>
      <motion.div {...fade}>
        <h1 style={{ fontSize: '30px', fontWeight: 600, lineHeight: 1.15, marginBottom: '18px' }}>Compare runs</h1>

        <Card style={{ marginBottom: '20px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto minmax(0,1fr)', gap: '12px', alignItems: 'end' }}>
            <div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                <span style={{ width: 14, height: 2, background: BASE_COLOR, display: 'inline-block' }} /> Baseline
              </label>
              <select style={fieldStyle} value={base} onChange={e => pick('base', e.target.value)}>
                <option value="previous">Previous run of the same scenario</option>
                <option value="last-green">Last passing run of the same scenario</option>
                {runOptions}
              </select>
            </div>
            <Btn small variant="ghost" title="Swap" disabled={!c} onClick={() => c && setParams(new URLSearchParams({ base: c.current.run_id, current: c.baseline.run_id }))}>
              <ArrowLeftRight size={14} />
            </Btn>
            <div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                <span style={{ width: 14, height: 2, background: CUR_COLOR, display: 'inline-block' }} /> Current
              </label>
              <select style={fieldStyle} value={current} onChange={e => pick('current', e.target.value)}>
                {!current && <option value="">Choose a run…</option>}
                {runOptions}
              </select>
            </div>
          </div>
        </Card>

        {busy && <div style={{ color: 'var(--text-muted)', display: 'flex', gap: 8, alignItems: 'center' }}><Spinner /> Comparing…</div>}
        {error && <Notice tone="danger">{error}</Notice>}
        {c && <Comparison c={c} filter={filter} setFilter={setFilter} openRun={id => navigate(`/analyze/${id}`)} />}
      </motion.div>
      <style>{'@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }'}</style>
    </div>
  )
}

function Delta({ before, after, lowerIsBetter = true, unit = '%', pp = false }: {
  before: number | null | undefined; after: number | null | undefined; lowerIsBetter?: boolean; unit?: string; pp?: boolean
}) {
  if (before == null || after == null) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  const change = pp ? after - before : before ? ((after - before) / before) * 100 : 0
  if (Math.abs(change) < (pp ? 0.05 : 1)) return <span style={{ color: 'var(--text-muted)' }}>no change</span>
  const worse = lowerIsBetter ? change > 0 : change < 0
  const Icon = change > 0 ? TrendingUp : TrendingDown
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontWeight: 600, color: worse ? 'var(--danger)' : 'var(--success)', whiteSpace: 'nowrap' }}>
      <Icon size={14} /> {change > 0 ? '+' : ''}{change.toFixed(pp ? 2 : 0)}{pp ? ' pt' : unit} {worse ? 'worse' : 'better'}
    </span>
  )
}

function Comparison({ c, filter, setFilter, openRun }: {
  c: RunComparison; filter: 'all' | 'regressed' | 'improved'; setFilter: (f: 'all' | 'regressed' | 'improved') => void
  openRun: (id: string) => void
}) {
  const b = c.runs.baseline, cur = c.runs.current
  const kind = c.load.kind as 'transaction' | 'request'
  const bo = b?.overall?.[kind], co = cur?.overall?.[kind]
  const br = b?.overall?.request ?? bo, cr = cur?.overall?.request ?? co
  const panels = useMemo(() => {
    if (!b || !cur) return []
    const series = (key: 'users' | 'tps' | 'p95_ms' | 'error_pct') => [
      { name: runName(c.baseline), color: BASE_COLOR, points: b.timeline.points.map(p => ({ t: p.t_offset_s, v: Number(p[key]) || 0 })) },
      { name: runName(c.current), color: CUR_COLOR, points: cur.timeline.points.map(p => ({ t: p.t_offset_s, v: Number(p[key]) || 0 })) },
    ]
    return [
      { title: 'Active users', fmt: (v: number) => fmtNum(v), series: series('users') },
      { title: 'Throughput (requests/s)', fmt: (v: number) => fmtNum(v, 1), series: series('tps') },
      { title: 'p95 response time (ms)', fmt: (v: number) => fmtMs(v), floor: 10, series: series('p95_ms') },
      { title: 'Error rate (%)', fmt: (v: number) => fmtPct(v), series: series('error_pct') },
    ]
  }, [c, b, cur])

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      {!c.load.comparable && (
        <Notice tone="warning">The load differed — {c.load.reason}. Differences are shown, but they are not counted as regressions.</Notice>
      )}

      <Card>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <div style={{ fontSize: '15px', fontWeight: 600 }}>
            {c.summary.regressed > 0 && c.load.comparable
              ? `${c.summary.regressed} label${c.summary.regressed > 1 ? 's' : ''} regressed`
              : c.summary.regressed > 0 ? `${c.summary.regressed} label(s) worse, at different load` : 'No regressions'}
            <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> · {c.summary.improved} improved · {c.summary.compared} compared</span>
          </div>
          <Btn small variant="ghost" onClick={() => downloadComparison(c.baseline.run_id, c.current.run_id)}><Download size={13} /> Markdown</Btn>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={{ ...th, textAlign: 'left' }} />
              <th style={{ ...th, textAlign: 'left' }}><span style={{ display: 'inline-block', width: 12, height: 2, background: BASE_COLOR, marginRight: 6, verticalAlign: 'middle' }} />{runName(c.baseline)}</th>
              <th style={{ ...th, textAlign: 'left' }}><span style={{ display: 'inline-block', width: 12, height: 2, background: CUR_COLOR, marginRight: 6, verticalAlign: 'middle' }} />{runName(c.current)}</th>
              <th style={{ ...th, textAlign: 'left' }}>Change</th>
            </tr></thead>
            <tbody>
              <tr><td style={td}>Analysed</td>
                <td style={td}><button onClick={() => openRun(c.baseline.run_id)} style={linkBtn}>{fmtDate(c.baseline.created_at)}</button></td>
                <td style={td}><button onClick={() => openRun(c.current.run_id)} style={linkBtn}>{fmtDate(c.current.created_at)}</button></td><td style={td} /></tr>
              <tr><td style={td}>Verdict</td><td style={td}>{b ? <VerdictBadge verdict={b.verdict} /> : '—'}</td><td style={td}>{cur ? <VerdictBadge verdict={cur.verdict} /> : '—'}</td><td style={td} /></tr>
              <tr><td style={td}>Steady-state users</td><td style={{ ...td, ...num, textAlign: 'left' }}>{fmtNum(c.load.baseline_users)}</td>
                <td style={{ ...td, ...num, textAlign: 'left' }}>{fmtNum(c.load.current_users)}</td>
                <td style={td}><span style={{ color: c.load.comparable ? 'var(--text-muted)' : 'var(--warning)' }}>{c.load.comparable ? 'comparable' : 'not comparable'}</span></td></tr>
              <tr><td style={td}>p95 ({kind}s)</td><td style={{ ...td, textAlign: 'left' }}>{fmtMs(bo?.p95_ms)}</td><td style={{ ...td, textAlign: 'left', color: 'var(--text-primary)' }}>{fmtMs(co?.p95_ms)}</td>
                <td style={td}><Delta before={bo?.p95_ms} after={co?.p95_ms} /></td></tr>
              <tr><td style={td}>p99 ({kind}s)</td><td style={{ ...td, textAlign: 'left' }}>{fmtMs(bo?.p99_ms)}</td><td style={{ ...td, textAlign: 'left', color: 'var(--text-primary)' }}>{fmtMs(co?.p99_ms)}</td>
                <td style={td}><Delta before={bo?.p99_ms} after={co?.p99_ms} /></td></tr>
              <tr><td style={td}>Error rate (requests)</td><td style={{ ...td, textAlign: 'left' }}>{fmtPct(br?.error_pct)}</td><td style={{ ...td, textAlign: 'left', color: 'var(--text-primary)' }}>{fmtPct(cr?.error_pct)}</td>
                <td style={td}><Delta before={br?.error_pct} after={cr?.error_pct} pp /></td></tr>
              <tr><td style={td}>Throughput ({kind}s/s)</td><td style={{ ...td, textAlign: 'left' }}>{fmtNum(c.load.baseline_tps, 1)}</td><td style={{ ...td, textAlign: 'left', color: 'var(--text-primary)' }}>{fmtNum(c.load.current_tps, 1)}</td>
                <td style={td}><Delta before={c.load.baseline_tps} after={c.load.current_tps} lowerIsBetter={false} /></td></tr>
            </tbody>
          </table>
        </div>
      </Card>

      {panels.length > 0 ? (
        <Card>
          <CardTitle>Timelines</CardTitle>
          <MultiTimeline panels={panels} />
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>Both runs from their first sample; hover to read both values.</div>
        </Card>
      ) : (
        <Notice tone="info">The timeline of one of these runs is not on this machine, so only the statistics are compared.</Notice>
      )}

      <Card>
        <CardTitle right={
          <div style={{ display: 'flex', gap: '4px' }}>
            {(['all', 'regressed', 'improved'] as const).map(f => (
              <button key={f} onClick={() => setFilter(f)} style={{
                padding: '5px 12px', borderRadius: '999px', fontSize: '12px', cursor: 'pointer', textTransform: 'capitalize',
                border: '1px solid var(--border)', color: filter === f ? 'var(--text-primary)' : 'var(--text-secondary)',
                background: filter === f ? 'rgba(255,255,255,0.1)' : 'transparent',
              }}>{f}</button>
            ))}
          </div>
        }>Requests and transactions</CardTitle>
        {Object.entries(c.kinds).map(([k, data]) => (
          <KindTable key={k} kind={k} rows={data.labels} filter={filter} runId={c.current.run_id}
            onlyBase={c.only_in_baseline[k] ?? []} onlyCur={c.only_in_current[k] ?? []} />
        ))}
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
          Regressed: p95 +{c.thresholds.p95_pct}% and +{c.thresholds.p95_ms} ms, or errors +{c.thresholds.err_pp} pt, with at least {c.thresholds.min_samples} samples in both runs. Steady-state figures.
        </div>
      </Card>

      <Card>
        <CardTitle>Findings</CardTitle>
        {(['new', 'resolved', 'persisting'] as const).map(key => {
          const items = c.findings_diff[key]
          const title = key === 'new' ? 'New in the current run' : key === 'resolved' ? 'Resolved since the baseline' : 'Still present'
          return (
            <div key={key} style={{ marginBottom: '12px' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>{title} ({items.length})</div>
              {items.length === 0 ? <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>None.</div> : items.map(f => (
                <div key={`${key}${f.n}`} style={{ display: 'flex', gap: '10px', alignItems: 'baseline', fontSize: '13.5px', lineHeight: 1.6 }}>
                  <SeverityBadge severity={f.severity} /><span style={{ color: key === 'resolved' ? 'var(--text-muted)' : 'var(--text-primary)', textDecoration: key === 'resolved' ? 'line-through' : 'none' }}>{f.title}</span>
                </div>
              ))}
            </div>
          )
        })}
      </Card>
    </div>
  )
}

const linkBtn: React.CSSProperties = { background: 'none', border: 'none', padding: 0, color: 'var(--text-primary)', cursor: 'pointer', textDecoration: 'underline', font: 'inherit' }

function KindTable({ kind, rows, filter, runId, onlyBase, onlyCur }: {
  kind: string; rows: CompareRow[]; filter: string; runId: string; onlyBase: string[]; onlyCur: string[]
}) {
  const shown = rows.filter(r => filter === 'all' || r.status === filter)
    .sort((a, b) => (b.p95_change_pct ?? -1e9) - (a.p95_change_pct ?? -1e9))
  if (!rows.length) return null
  return (
    <div style={{ marginBottom: '18px' }}>
      <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', margin: '4px 0 8px', textTransform: 'capitalize' }}>{kind}s</div>
      {shown.length === 0 ? <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>None {filter}.</div> : (
        <div style={{ overflowX: 'auto', maxHeight: '520px', overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={{ ...th, textAlign: 'left' }}>Label</th><th style={{ ...th, ...num }}>Samples</th>
              <th style={{ ...th, ...num }}>p95 baseline → now</th><th style={{ ...th, ...num }}>Change</th>
              <th style={{ ...th, ...num }}>Errors baseline → now</th><th style={{ ...th, textAlign: 'left' }}>Status</th>
            </tr></thead>
            <tbody>{shown.map(r => (
              <tr key={r.label}>
                <td style={{ ...td, wordBreak: 'break-word', maxWidth: '360px' }}>
                  <Link to={`/analyze/${runId}/label?name=${encodeURIComponent(r.label)}`} style={{ color: 'var(--text-primary)', textDecoration: 'none' }}>{r.label}</Link>
                </td>
                <td style={{ ...td, ...num }}>{fmtNum(r.baseline.samples)} → {fmtNum(r.current.samples)}</td>
                <td style={{ ...td, ...num }}>{fmtNum(r.baseline.p95_ms)} → <span style={{ color: 'var(--text-primary)' }}>{fmtNum(r.current.p95_ms)}</span> ms</td>
                <td style={{ ...td, ...num }}>{r.p95_change_pct == null ? '—' : `${r.p95_change_pct > 0 ? '+' : ''}${r.p95_change_pct}%`}</td>
                <td style={{ ...td, ...num }}>{fmtPct(r.baseline.error_pct)} → {fmtPct(r.current.error_pct)}</td>
                <td style={td}>{r.status === 'regressed'
                  ? <span style={{ color: 'var(--danger)', fontWeight: 600 }}>✕ Regressed ({r.regressed.join(', ').replace('error_rate', 'errors')})</span>
                  : r.status === 'improved' ? <span style={{ color: 'var(--success)', fontWeight: 600 }}>✓ Improved</span>
                  : r.status === 'too_few_samples' ? <span style={{ color: 'var(--text-muted)' }}>Too few samples</span>
                  : <span style={{ color: 'var(--text-muted)' }}>No change</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {(onlyBase.length > 0 || onlyCur.length > 0) && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px', lineHeight: 1.6 }}>
          {onlyCur.length > 0 && <div>New in the current run: {onlyCur.slice(0, 8).join(', ')}{onlyCur.length > 8 ? ` +${onlyCur.length - 8} more` : ''}</div>}
          {onlyBase.length > 0 && <div>Only in the baseline: {onlyBase.slice(0, 8).join(', ')}{onlyBase.length > 8 ? ` +${onlyBase.length - 8} more` : ''}</div>}
        </div>
      )}
    </div>
  )
}
