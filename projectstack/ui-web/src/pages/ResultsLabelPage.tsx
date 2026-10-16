/**
 * One request or transaction of an analysed run: a plain-language synthesis, its
 * statistics, timeline, response-time distribution, response codes, errors,
 * slowest samples, where its time goes, its findings and the change from baseline.
 */
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { AlertOctagon, AlertTriangle, ArrowLeft, CheckCircle, MinusCircle } from 'lucide-react'
import { getLabelDetail, type LabelDetail, type LabelStatus } from '../api/client'
import MultiTimeline from '../components/results/MultiTimeline'
import {
  Card, CardTitle, Notice, SeverityBadge, Spinner, Stat, fmtMs, fmtNum, fmtOffset, fmtPct, num, td, th,
} from '../components/results/ui'

const STATUS: Record<LabelStatus, { label: string; color: string; rgb: string; Icon: typeof CheckCircle }> = {
  critical: { label: 'Needs action', color: 'var(--danger)', rgb: 'var(--danger-rgb)', Icon: AlertOctagon },
  attention: { label: 'Worth a look', color: 'var(--warning)', rgb: 'var(--warning-rgb)', Icon: AlertTriangle },
  healthy: { label: 'Healthy', color: 'var(--success)', rgb: 'var(--success-rgb)', Icon: CheckCircle },
  idle: { label: 'Idle', color: 'var(--text-muted)', rgb: '255,255,255', Icon: MinusCircle },
}

export function StatusBadge({ status }: { status: LabelStatus }) {
  const s = STATUS[status]
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', borderRadius: '999px', padding: '4px 12px', fontSize: '13px',
      fontWeight: 600, color: s.color, background: `rgba(${s.rgb},0.1)`, border: `1px solid rgba(${s.rgb},0.3)`, whiteSpace: 'nowrap' }}>
      <s.Icon size={14} /> {s.label}
    </span>
  )
}

const fade = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0, transition: { duration: 0.3 } } }

export default function ResultsLabelPage() {
  const { runId = '' } = useParams()
  const [params] = useSearchParams()
  const name = params.get('name') ?? ''
  const [d, setD] = useState<LabelDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setD(null); setError('')
    getLabelDetail(runId, name).then(setD).catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [runId, name])

  const back = (
    <Link to={`/analyze/${runId}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)', textDecoration: 'none', fontSize: '14px', marginBottom: '18px' }}>
      <ArrowLeft size={15} /> Back to the run
    </Link>
  )
  if (error) return <Page>{back}<Notice tone="danger">{error}</Notice></Page>
  if (!d) return <Page>{back}<div style={{ color: 'var(--text-muted)', display: 'flex', gap: 8, alignItems: 'center' }}><Spinner /> Loading {name}…</div></Page>

  const s = d.steady
  const pts = d.timeline.points
  const hmax = Math.max(1, ...d.histogram.bins.map(b => b.samples))

  return (
    <Page>
      {back}
      <motion.div {...fade}>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '6px' }}>
          <h1 style={{ fontSize: '28px', fontWeight: 600, lineHeight: 1.2, wordBreak: 'break-word' }}>{d.label}</h1>
          <StatusBadge status={d.status} />
        </div>
        <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
          {d.kind === 'transaction' ? 'Transaction' : d.kind === 'request' ? 'Request' : 'Sub-result'} · steady state {fmtOffset(d.window.start_offset_s)} to {fmtOffset(d.window.end_offset_s)}
        </div>

        <Card style={{ marginBottom: '20px' }}>
          <CardTitle>Summary</CardTitle>
          <ul style={{ paddingLeft: '20px', display: 'grid', gap: '6px', fontSize: '14px', lineHeight: 1.55, color: 'var(--text-secondary)' }}>
            {d.synthesis.map((x, i) => <li key={i}>{x}</li>)}
          </ul>
        </Card>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px', marginBottom: '20px' }}>
          <Stat label="Samples" value={fmtNum(s.samples)} sub={`${fmtNum(s.tps, 2)} per second`} />
          <Stat label="p50" value={fmtMs(s.p50_ms)} />
          <Stat label="p95" value={fmtMs(s.p95_ms)} sub={d.baseline?.p95_change_pct != null ? `${d.baseline.p95_change_pct > 0 ? '+' : ''}${d.baseline.p95_change_pct}% vs baseline` : undefined} />
          <Stat label="p99" value={fmtMs(s.p99_ms)} />
          <Stat label="Errors" value={fmtPct(s.error_pct)} sub={`${fmtNum(s.errors)} failed`} />
          <Stat label="Share of time" value={fmtPct(d.share_pct)} sub={`of all ${d.kind} time`} />
        </div>

        <Card style={{ marginBottom: '20px' }}>
          <CardTitle>Timeline</CardTitle>
          <MultiTimeline
            band={{ from: d.window.start_offset_s, to: d.window.end_offset_s, label: 'Steady state (statistics window)' }}
            panels={[
              { title: 'Requests per second', fmt: v => fmtNum(v, 2), series: [{ name: 'per second', color: 'var(--series-1)', points: pts.map(p => ({ t: p.t_offset_s, v: p.tps })) }] },
              { title: 'Response time (ms)', fmt: v => fmtMs(v), floor: 10, series: [
                { name: 'p50', color: 'var(--series-3)', points: pts.map(p => ({ t: p.t_offset_s, v: p.p50_ms })) },
                { name: 'p95', color: 'var(--series-1)', points: pts.map(p => ({ t: p.t_offset_s, v: p.p95_ms })) },
                { name: 'p99', color: 'var(--series-2)', points: pts.map(p => ({ t: p.t_offset_s, v: p.p99_ms })) },
              ] },
              { title: 'Error rate (%)', fmt: v => fmtPct(v), series: [{ name: 'errors', color: 'var(--series-1)', points: pts.map(p => ({ t: p.t_offset_s, v: p.error_pct })) }] },
            ]} />
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>{d.timeline.bucket_s} s steps over the whole run.</div>
        </Card>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))', gap: '20px', marginBottom: '20px' }}>
          <Card>
            <CardTitle>Distribution of response times</CardTitle>
            <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr max-content', gap: '3px 10px', alignItems: 'center', fontSize: '12px' }}>
              {d.histogram.bins.map(b => (
                <HistRow key={b.from_ms} from={b.from_ms} to={b.to_ms} n={b.samples} max={hmax} total={s.samples} />
              ))}
              {d.histogram.overflow > 0 && (
                <>
                  <span style={{ color: 'var(--text-secondary)' }}>over {fmtNum(d.histogram.bins[d.histogram.bins.length - 1]?.to_ms)} ms</span>
                  <span />
                  <span style={{ color: 'var(--text-muted)', textAlign: 'right' }}>{fmtNum(d.histogram.overflow)}</span>
                </>
              )}
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>Steady state, up to the 99.9th percentile; hover a bar for its share.</div>
          </Card>
          <Card>
            <CardTitle>Percentiles and averages</CardTitle>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {d.ladder.map(x => (
                  <tr key={x.p}><td style={td}>p{+(x.p * 100).toFixed(1)}</td><td style={{ ...td, ...num, color: 'var(--text-primary)' }}>{fmtMs(x.ms)}</td></tr>
                ))}
                <tr><td style={td}>Average</td><td style={{ ...td, ...num }}>{fmtMs(s.avg_ms)}</td></tr>
                <tr><td style={td}>Standard deviation</td><td style={{ ...td, ...num }}>{fmtMs(d.stddev_ms)}</td></tr>
                <tr><td style={td}>Min / max</td><td style={{ ...td, ...num }}>{fmtNum(s.min_ms)} / {fmtNum(s.max_ms)} ms</td></tr>
              </tbody>
            </table>
            {d.breakdown && (
              <>
                <div style={{ fontSize: '13px', fontWeight: 600, margin: '16px 0 8px' }}>Where the time goes (average)</div>
                <Breakdown b={d.breakdown} />
              </>
            )}
          </Card>
        </div>

        <Card style={{ marginBottom: '20px' }}>
          <CardTitle>Statistics</CardTitle>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }} />
                <th style={{ ...th, ...num }}>Steady state</th><th style={{ ...th, ...num }}>Whole run</th>
                {d.baseline && <th style={{ ...th, ...num }}>Baseline ({d.baseline.tag || d.baseline.run_id})</th>}
              </tr></thead>
              <tbody>
                {([['samples', 'Samples'], ['avg_ms', 'Average (ms)'], ['p50_ms', 'p50 (ms)'], ['p95_ms', 'p95 (ms)'], ['p99_ms', 'p99 (ms)'], ['max_ms', 'Max (ms)'], ['error_pct', 'Errors'], ['tps', 'Per second']] as const).map(([k, h]) => {
                  const f = (v: number | undefined | null) => v == null ? '—' : k === 'error_pct' ? fmtPct(v) : k === 'tps' ? fmtNum(v, 2) : fmtNum(v)
                  const base = d.baseline ? (d.baseline as Record<string, unknown>)[k] as number | undefined : undefined
                  return (
                    <tr key={k}>
                      <td style={td}>{h}</td>
                      <td style={{ ...td, ...num, color: 'var(--text-primary)' }}>{f(s[k])}</td>
                      <td style={{ ...td, ...num }}>{f(d.whole_run[k])}</td>
                      {d.baseline && <td style={{ ...td, ...num }}>{f(base)}</td>}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {d.sla.length > 0 && (
            <div style={{ marginTop: '10px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              {d.sla.map((c, i) => <div key={i}>SLA {c.metric} ≤ {fmtMs(c.target)}: <strong style={{ color: c.passed ? 'var(--success)' : 'var(--danger)' }}>{c.passed ? '✓ met' : '✕ missed'}</strong> ({fmtMs(c.actual)})</div>)}
            </div>
          )}
        </Card>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))', gap: '20px', marginBottom: '20px' }}>
          <Card>
            <CardTitle>Response codes</CardTitle>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={{ ...th, textAlign: 'left' }}>Code</th><th style={{ ...th, ...num }}>Samples</th><th style={{ ...th, ...num }}>Share</th><th style={{ ...th, ...num }}>Failed</th></tr></thead>
              <tbody>{d.codes.map(c => (
                <tr key={c.code}><td style={{ ...td, color: 'var(--text-primary)' }}>{c.code}</td><td style={{ ...td, ...num }}>{fmtNum(c.samples)}</td>
                  <td style={{ ...td, ...num }}>{fmtPct(c.share_pct)}</td><td style={{ ...td, ...num }}>{fmtNum(c.failed)}</td></tr>
              ))}</tbody>
            </table>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>Whole run.</div>
          </Card>
          <Card>
            <CardTitle>Errors</CardTitle>
            {d.errors.length === 0 ? <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>No failed samples.</div> : (
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={{ ...th, textAlign: 'left' }}>Code</th><th style={{ ...th, textAlign: 'left' }}>Message</th><th style={{ ...th, ...num }}>Count</th><th style={{ ...th, textAlign: 'left' }}>First</th></tr></thead>
                <tbody>{d.errors.map((e, i) => (
                  <tr key={i}><td style={td}>{e.code}</td><td style={{ ...td, wordBreak: 'break-word' }}>{e.message || '—'}</td>
                    <td style={{ ...td, ...num }}>{fmtNum(e.count)}</td><td style={td}>{fmtOffset(e.first_offset_s)}</td></tr>
                ))}</tbody>
              </table>
            )}
          </Card>
        </div>

        {d.findings.length > 0 && (
          <Card style={{ marginBottom: '20px' }}>
            <CardTitle>Findings that name this {d.kind}</CardTitle>
            <div style={{ display: 'grid', gap: '6px' }}>
              {d.findings.map(f => (
                <div key={f.n} style={{ display: 'flex', gap: '10px', alignItems: 'baseline', fontSize: '14px' }}>
                  <span style={{ color: 'var(--text-muted)' }}>#{f.n}</span><SeverityBadge severity={f.severity} /><span>{f.title}</span>
                </div>
              ))}
            </div>
          </Card>
        )}

        {d.slowest.length > 0 && (
          <Card style={{ marginBottom: '20px' }}>
            <CardTitle>Slowest samples in the steady state</CardTitle>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr>
                  <th style={{ ...th, textAlign: 'left' }}>Time</th><th style={{ ...th, ...num }}>Elapsed</th>{d.kind !== 'transaction' && <th style={{ ...th, ...num }}>First byte</th>}
                  <th style={{ ...th, textAlign: 'left' }}>Code</th><th style={{ ...th, textAlign: 'left' }}>Result</th><th style={{ ...th, textAlign: 'left' }}>Thread</th>
                </tr></thead>
                <tbody>{d.slowest.map((x, i) => (
                  <tr key={i}>
                    <td style={td}>{fmtOffset(x.t_offset_s)}</td><td style={{ ...td, ...num, color: 'var(--text-primary)' }}>{fmtMs(x.elapsed_ms)}</td>
                    {d.kind !== 'transaction' && <td style={{ ...td, ...num }}>{fmtMs(x.latency_ms)}</td>}<td style={td}>{x.code}</td>
                    <td style={td}>{x.success ? '✓ OK' : '✕ Failed'}</td><td style={{ ...td, wordBreak: 'break-word' }}>{x.thread}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
        )}
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

function HistRow({ from, to, n, max, total }: { from: number; to: number; n: number; max: number; total: number }) {
  const [hover, setHover] = useState(false)
  const share = total ? (n / total) * 100 : 0
  return (
    <>
      <span style={{ color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(from)}–{fmtNum(to)} ms</span>
      <span onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)} title={`${fmtNum(n)} samples (${fmtPct(share)})`}
        style={{ position: 'relative', padding: '4px 0', cursor: 'default' }}>
        <span style={{ display: 'block', height: '10px', borderRadius: '2px', width: `${(n / max) * 100}%`, minWidth: n ? '2px' : 0,
          background: 'var(--series-1)', opacity: hover ? 0.75 : 1 }} />
      </span>
      <span style={{ color: hover ? 'var(--text-primary)' : 'var(--text-muted)', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {hover ? fmtPct(share) : fmtNum(n)}
      </span>
    </>
  )
}

function Breakdown({ b }: { b: NonNullable<LabelDetail['breakdown']> }) {
  const parts = [
    { name: 'Connecting', v: b.connect_ms, color: 'var(--series-2)' },
    { name: 'Waiting for the server', v: b.server_ms, color: 'var(--series-1)' },
    { name: 'Downloading', v: b.download_ms, color: 'var(--series-3)' },
  ]
  const total = parts.reduce((a, p) => a + p.v, 0) || 1
  return (
    <div>
      <div style={{ display: 'flex', height: '14px', borderRadius: '4px', overflow: 'hidden', gap: '2px', marginBottom: '8px' }}>
        {parts.filter(p => p.v > 0).map(p => <div key={p.name} title={`${p.name}: ${fmtMs(p.v)}`} style={{ width: `${(p.v / total) * 100}%`, background: p.color }} />)}
      </div>
      <div style={{ display: 'grid', gap: '3px', fontSize: '13px' }}>
        {parts.map(p => (
          <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)' }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: p.color, display: 'inline-block' }} />
            <span style={{ flex: 1 }}>{p.name}</span>
            <span style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(p.v, 1)} ms</span>
          </div>
        ))}
        <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>Average response size {fmtNum(b.bytes / 1024, 1)} KB</div>
      </div>
    </div>
  )
}
