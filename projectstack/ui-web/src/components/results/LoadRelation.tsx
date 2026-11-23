/**
 * Server metrics set against the load test: the test's own graph (active users, throughput,
 * p95, errors) with the server metrics under it on one clock, and for each server metric
 * whether it followed the load, moved with the response times or with the errors. The
 * numbers come from projectstack/results/server_load.py, and so do the observations.
 */
import { Activity } from 'lucide-react'
import type { RelationRow, RelationStrength, RunAnalysis, ServerRelation } from '../../api/client'
import MultiTimeline, { type Panel } from './MultiTimeline'
import { fmtMs, fmtNum, fmtOffset, fmtPct, num, td, th } from './ui'

export const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)', 'var(--series-6)', 'var(--series-7)', 'var(--series-8)']

export function fmtUnit(v: number | null | undefined, unit: string): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (unit === '%') return `${v.toFixed(Math.abs(v) < 10 ? 1 : 0)}%`
  if (unit === 'ms') return fmtMs(v)
  if (unit === 'bytes' || unit === 'bytes/s') {
    const sizes: [number, string][] = [[2 ** 30, 'GB'], [2 ** 20, 'MB'], [2 ** 10, 'KB']]
    const hit = sizes.find(([s]) => Math.abs(v) >= s)
    const text = hit ? `${(v / hit[0]).toFixed(1)} ${hit[1]}` : `${fmtNum(v)} B`
    return unit === 'bytes/s' ? `${text}/s` : text
  }
  if (unit === '/min' || unit === '/s') return `${fmtNum(v, 1)}${unit}`
  if (unit === 'cores') return `${fmtNum(v, 2)} cores`
  const text = fmtNum(v, Math.abs(v) < 10 ? 2 : Math.abs(v) < 100 ? 1 : 0)
  return unit && unit !== 'count' ? `${text} ${unit}` : text
}

/** The run's start as epoch ms, for a clock axis. */
export function runStartMs(analysis: RunAnalysis): number | undefined {
  const d = new Date(analysis.run.start)
  return Number.isNaN(d.getTime()) ? undefined : d.getTime()
}

/** The load test's execution graph: active users, throughput, p95 and (when there were any) errors. */
export function testPanels(analysis: RunAnalysis): Panel[] {
  const half = analysis.timeline.bucket_s / 2
  const pts = analysis.timeline.points.filter(p => p.samples > 0)
  const at = (f: (p: typeof pts[number]) => number) => pts.map(p => ({ t: p.t_offset_s + half, v: f(p) }))
  const panels: Panel[] = [
    { title: 'Active users', fmt: v => fmtNum(v), floor: 1, series: [{ name: 'users', color: 'var(--text-secondary)', points: at(p => p.users) }] },
    { title: 'Throughput (requests/s)', fmt: v => fmtNum(v, 1), floor: 1, series: [{ name: 'throughput', color: 'var(--text-secondary)', points: at(p => p.tps) }] },
    { title: 'p95 response time (ms)', fmt: v => fmtMs(v), floor: 10, series: [{ name: 'p95', color: 'var(--text-secondary)', points: at(p => p.p95_ms) }] },
  ]
  if (pts.some(p => p.errors > 0)) {
    panels.push({ title: 'Error rate (%)', fmt: v => fmtPct(v), floor: 1, series: [{ name: 'errors', color: 'var(--text-secondary)', points: at(p => p.error_pct) }] })
  }
  return panels
}

/**
 * Server series in panels, one per metric and unit (one y-axis per panel): "CPU · app-server-1" and
 * "CPU · orders-db" share a CPU panel, named by resource in its legend. Colours follow the series in a
 * fixed order across panels, so no two lines on the page share one.
 */
export function unitPanels(series: { name: string; unit: string; points: { t: number; v: number }[] }[], subtitle?: string): Panel[] {
  const groups = new Map<string, { metric: string; unit: string; lines: { name: string; points: { t: number; v: number }[] }[] }>()
  for (const s of series) {
    const cut = s.name.indexOf(' · ')
    const metric = cut > 0 ? s.name.slice(0, cut) : s.name
    const resource = cut > 0 ? s.name.slice(cut + 3) : s.name
    const key = `${metric}|${s.unit}`
    const g = groups.get(key) ?? { metric, unit: s.unit, lines: [] }
    g.lines.push({ name: resource, points: s.points })
    groups.set(key, g)
  }
  // One colour per server, the same in every panel ("orders-db (RDS)" and "orders-db" are one server)
  const slots = new Map<string, number>()
  const colorOf = (resource: string) => {
    const key = resource.replace(/\s*\([^)]*\)\s*$/, '').trim().toLowerCase()
    if (!slots.has(key)) slots.set(key, slots.size)
    return SERIES_COLORS[slots.get(key)! % SERIES_COLORS.length]
  }
  return [...groups.values()].map(g => ({
    title: `${g.metric}${g.unit ? ` (${g.unit})` : ''}`, subtitle, fmt: v => fmtUnit(v, g.unit), floor: g.unit === '%' ? 10 : 1,
    series: g.lines.map(l => ({ name: l.name, color: colorOf(l.name), points: l.points })),
  }))
}

const LABEL: Record<RelationStrength, string> = { strong: 'Strong', moderate: 'Moderate', weak: 'Weak' }

function Strength({ r, label, flat, note }: { r: number | null; label: RelationStrength | null; flat?: boolean; note?: string }) {
  if (flat) return <span style={{ color: 'var(--text-muted)' }}>flat</span>
  if (r == null || !label) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', whiteSpace: 'nowrap' }} title={`Pearson r = ${r.toFixed(2)}${note ? ` (${note})` : ''}`}>
      <span aria-hidden style={{ width: 44, height: 6, borderRadius: 3, background: 'rgba(var(--fg-rgb),0.1)', overflow: 'hidden', display: 'inline-block' }}>
        <span style={{ display: 'block', height: '100%', width: `${Math.round(Math.abs(r) * 100)}%`, background: label === 'strong' ? 'var(--text-primary)' : 'var(--text-muted)' }} />
      </span>
      <span style={{ color: label === 'strong' ? 'var(--text-primary)' : 'var(--text-secondary)', fontWeight: label === 'strong' ? 600 : 400 }}>
        {LABEL[label]}{r < 0 ? ', inverse' : ''}
      </span>
      <span style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{r.toFixed(2)}{note ? '*' : ''}</span>
    </span>
  )
}

export default function LoadRelation({ analysis, relation, serverPanels, sourceNote }: {
  analysis: RunAnalysis
  relation: ServerRelation | null | undefined
  serverPanels?: Panel[]            // server series to draw under the test's graph; none: no chart here
  sourceNote?: string
}) {
  const start = runStartMs(analysis)
  const steadyBand = { from: analysis.steady.start_offset_s, to: analysis.steady.end_offset_s, label: 'Steady state (where response times are compared)' }
  const wholeTest = relation?.rows.some(r => r.p95_scope === 'test' && r.r_p95 != null)
  return (
    <div style={{ display: 'grid', gap: '14px' }}>
      <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <Activity size={16} /> Server metrics vs the load test
      </div>

      {serverPanels && serverPanels.length > 0 && (
        <MultiTimeline panels={[...testPanels(analysis), ...serverPanels]} durationS={analysis.run.duration_s} band={steadyBand} clockStartMs={start} />
      )}

      {!relation ? (
        <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
          Not enough server values inside the test window to compare with the load test.
        </div>
      ) : (
        <>
          <ul style={{ paddingLeft: '20px', margin: 0, display: 'grid', gap: '7px', listStyle: 'disc', fontSize: '14.5px', lineHeight: 1.6, color: 'var(--text-secondary)' }}>
            {relation.observations.map((o, i) => <li key={i}>{o}</li>)}
          </ul>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }}>Server metric</th>
                <th style={{ ...th, textAlign: 'left' }}>With the load (users)</th>
                <th style={{ ...th, textAlign: 'left' }}>With p95 response time</th>
                <th style={{ ...th, textAlign: 'left' }}>With errors</th>
                <th style={{ ...th, ...num }}>Steady state</th>
                <th style={{ ...th, ...num }}>Peak</th>
                <th style={{ ...th, textAlign: 'left' }}>Under flat load</th>
              </tr></thead>
              <tbody>{relation.rows.map((r: RelationRow) => (
                <tr key={r.key}>
                  <td style={{ ...td, color: 'var(--text-primary)', maxWidth: '260px', wordBreak: 'break-word' }}>{r.name}</td>
                  <td style={td}><Strength r={r.r_users} label={r.load} flat={r.flat} /></td>
                  <td style={td}><Strength r={r.r_p95} label={r.with_p95} flat={r.flat} note={r.p95_scope === 'test' ? 'whole test: the steady state had too few points' : undefined} /></td>
                  <td style={td}><Strength r={r.r_errors} label={r.with_errors} flat={r.flat} /></td>
                  <td style={{ ...td, ...num }}>{fmtUnit(r.steady_avg, r.unit)}</td>
                  <td style={{ ...td, ...num }}>{fmtUnit(r.peak, r.unit)} <span style={{ color: 'var(--text-muted)' }}>{fmtOffset(r.peak_offset_s)}</span></td>
                  <td style={td}>{r.trend
                    ? <span style={{ color: r.trend.direction === 'rising' ? 'var(--warning)' : 'var(--text-secondary)' }}>{r.trend.direction === 'rising' ? '↗ rising' : '↘ falling'} {fmtUnit(r.trend.from, r.unit)} → {fmtUnit(r.trend.to, r.unit)}</span>
                    : <span style={{ color: 'var(--text-muted)' }}>steady</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.55 }}>
            Each server metric is compared minute by minute with the test's active users, p95 response time and error rate
            (Pearson r: strong from {relation.thresholds.strong}, moderate from {relation.thresholds.moderate}). Response times are
            compared over the steady state, where the load is flat, so the two simply growing with the users does not count
            {wholeTest ? ' (* over the whole test, where the steady state had too few points)' : ''}. Correlation shows what moved
            together, not what caused it.{sourceNote ? ` ${sourceNote}` : ''}
          </div>
        </>
      )}
    </div>
  )
}
