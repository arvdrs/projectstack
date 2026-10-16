/**
 * Run timeline as four small multiples on one time axis (active users, throughput,
 * p95, error rate) — one y-axis per panel instead of a dual-axis chart. The
 * steady-state window is shaded grey and finding windows are shaded by severity
 * and numbered like the findings list. Hover (or arrow keys) moves one crosshair
 * across all panels; the same values are in the table view.
 */
import { useMemo, useState } from 'react'
import type { AnalysisFinding, TimelinePoint } from '../../api/client'
import { fmtNum, fmtOffset, fmtPct, num, td, th } from './ui'

const W = 760, LEFT = 52, RIGHT = 14, PANEL_H = 74, GAP = 34, TOP = 46, AXIS_H = 24
const PANELS: { key: keyof TimelinePoint; title: string; floor: number; fmt: (v: number) => string }[] = [
  { key: 'users', title: 'Active users', floor: 1, fmt: v => fmtNum(v) },
  { key: 'tps', title: 'Throughput (requests/s)', floor: 1, fmt: v => fmtNum(v, 1) },
  { key: 'p95_ms', title: 'p95 response time (ms)', floor: 10, fmt: v => `${fmtNum(v)} ms` },
  { key: 'error_pct', title: 'Error rate (%)', floor: 1, fmt: v => fmtPct(v) },
]
const SEV_FILL: Record<string, string> = {
  critical: 'rgba(var(--danger-rgb),0.16)', warning: 'rgba(var(--warning-rgb),0.2)', info: 'rgba(var(--info-rgb),0.14)',
}

function nice(v: number, floor: number): number {
  const x = Math.max(v, floor)
  const exp = 10 ** Math.floor(Math.log10(x))
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * exp >= x * 1.04) return m * exp
  return 10 * exp
}

function tick(v: number): string {
  if (v >= 1000) return `${+(v / 1000).toFixed(1)}K`
  return Number.isInteger(v) || v >= 10 ? String(+v.toFixed(0)) : String(+v.toFixed(2))
}

export default function Timeline({ points, bucketS, durationS, steady, findings, errorTarget }: {
  points: TimelinePoint[]
  bucketS: number
  durationS: number
  steady?: { start_offset_s: number; end_offset_s: number; source: string }
  findings: AnalysisFinding[]
  errorTarget?: number | null
}) {
  const [hover, setHover] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)

  const geo = useMemo(() => {
    const x0 = Math.min(0, points[0]?.t_offset_s ?? 0)
    const x1 = Math.max(durationS, (points[points.length - 1]?.t_offset_s ?? 0) + bucketS)
    const plotW = W - LEFT - RIGHT
    const x = (t: number) => LEFT + ((t - x0) / ((x1 - x0) || 1)) * plotW
    const tops = PANELS.map(p => nice(Math.max(0, ...points.map(pt => Number(pt[p.key]) || 0)), p.floor))
    const bands = findings
      .filter(f => f.start_offset_s != null && f.type !== 'sla_breach' && f.type !== 'regression')
      .map(f => {
        const a = Math.max(x0, f.start_offset_s as number)
        const b = f.end_offset_s == null ? x1 : Math.min(x1, f.end_offset_s)
        return { n: f.n, a, b, severity: f.severity }
      })
      .filter(b => !(b.b - b.a > 0.6 * (x1 - x0) && findings.find(f => f.n === b.n)?.type === 'correlation'))
    const spanMin = (x1 - x0) / 60
    const step = [1, 2, 5, 10, 15, 30, 60, 120, 240].find(s => spanMin / s <= 8) ?? 480
    const ticks: number[] = []
    for (let t = Math.ceil(x0 / (step * 60)) * step * 60; t <= x1; t += step * 60) ticks.push(t)
    return { x0, x1, plotW, x, tops, bands, ticks, step }
  }, [points, bucketS, durationS, findings])

  if (!points.length) return <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No timeline data.</div>

  const height = TOP + PANELS.length * PANEL_H + (PANELS.length - 1) * GAP + AXIS_H
  const axisY = TOP + PANELS.length * PANEL_H + (PANELS.length - 1) * GAP
  const mid = (p: TimelinePoint) => p.t_offset_s + bucketS / 2
  const nearest = (clientX: number, rect: DOMRect) => {
    const svgX = ((clientX - rect.left) / rect.width) * W
    const t = geo.x0 + ((svgX - LEFT) / geo.plotW) * (geo.x1 - geo.x0)
    let best = 0
    points.forEach((p, i) => { if (Math.abs(mid(p) - t) < Math.abs(mid(points[best]) - t)) best = i })
    return best
  }
  const hp = hover != null ? points[hover] : null
  const hx = hp ? geo.x(mid(hp)) : 0
  const showSteady = steady && steady.source !== 'full'

  return (
    <div>
      <div style={{ position: 'relative', overflowX: 'auto' }}>
        <svg viewBox={`0 0 ${W} ${height}`} role="img" style={{ width: '100%', minWidth: '560px', height: 'auto', display: 'block' }}
          aria-label="Active users, throughput, p95 response time and error rate over the run, with finding windows shaded">
          {/* band labels row */}
          {(() => {
            let lastEnd = 0
            return [...geo.bands].sort((a, b) => a.a - b.a).map(b => {
              const lx = Math.max(geo.x(b.a) + 2, lastEnd + 4)
              lastEnd = lx + 8 * String(b.n).length + 8
              return <text key={`l${b.n}`} x={lx} y={TOP - 32} fontSize="11" fontWeight={600} fill="var(--text-secondary)">#{b.n}</text>
            })
          })()}
          {showSteady && (
            <text x={geo.x(steady!.end_offset_s) - 3} y={TOP - 32} fontSize="11" fontWeight={600} textAnchor="end" fill="var(--text-muted)">Steady state</text>
          )}
          {PANELS.map((panel, k) => {
            const y0 = TOP + k * (PANEL_H + GAP)
            const top = geo.tops[k]
            const y = (v: number) => y0 + PANEL_H - (v / top) * PANEL_H
            const coords = points.map(p => [geo.x(mid(p)), y(Number(p[panel.key]) || 0)])
            return (
              <g key={panel.key}>
                <text x={LEFT} y={y0 - 10} fontSize="12" fontWeight={600} fill="var(--text-secondary)">{panel.title}</text>
                {showSteady && (
                  <rect x={geo.x(Math.max(geo.x0, steady!.start_offset_s))} y={y0}
                    width={Math.max(0, geo.x(Math.min(geo.x1, steady!.end_offset_s)) - geo.x(Math.max(geo.x0, steady!.start_offset_s)))}
                    height={PANEL_H} fill="rgba(255,255,255,0.05)" />
                )}
                {geo.bands.map(b => (
                  <rect key={b.n} x={geo.x(b.a)} y={y0} width={Math.max(2, geo.x(b.b) - geo.x(b.a))} height={PANEL_H}
                    style={{ fill: SEV_FILL[b.severity] ?? SEV_FILL.info }} />
                ))}
                {[0, 0.5, 1].map(f => (
                  <g key={f}>
                    <line x1={LEFT} x2={W - RIGHT} y1={y(top * f)} y2={y(top * f)}
                      stroke={f === 0 ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.07)'} />
                    <text x={LEFT - 8} y={y(top * f) + 4} fontSize="10" textAnchor="end" fill="var(--text-muted)"
                      style={{ fontVariantNumeric: 'tabular-nums' }}>{tick(top * f)}</text>
                  </g>
                ))}
                {panel.key === 'error_pct' && errorTarget != null && errorTarget <= top && (
                  <g>
                    <line x1={LEFT} x2={W - RIGHT} y1={y(errorTarget)} y2={y(errorTarget)} stroke="var(--text-secondary)" strokeDasharray="4 3" />
                    <text x={W - RIGHT - 4} y={y(errorTarget) - 5} fontSize="10" textAnchor="end" fill="var(--text-secondary)">SLA {errorTarget}%</text>
                  </g>
                )}
                {coords.length === 1
                  ? <circle cx={coords[0][0]} cy={coords[0][1]} r={4} fill="var(--series-1)" />
                  : <path d={`M${coords.map(c => `${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(' L')}`} fill="none"
                      stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
                {hp && <circle cx={hx} cy={y(Number(hp[panel.key]) || 0)} r={4} fill="var(--series-1)" stroke="var(--bg-elevated)" strokeWidth={2} />}
              </g>
            )
          })}
          {geo.ticks.map(t => (
            <text key={t} x={geo.x(t)} y={axisY + 18} fontSize="10" textAnchor="middle" fill="var(--text-muted)">
              {geo.step >= 60 ? `${Math.round(t / 3600)}h` : `${Math.round(t / 60)}m`}
            </text>
          ))}
          {hp && <line x1={hx} x2={hx} y1={TOP} y2={axisY} stroke="var(--text-muted)" strokeWidth={1} pointerEvents="none" />}
          <rect x={LEFT} y={TOP} width={geo.plotW} height={axisY - TOP} fill="transparent" tabIndex={0}
            style={{ cursor: 'crosshair', outline: 'none' }}
            aria-label="Timeline values: use the left and right arrow keys"
            onPointerMove={e => setHover(nearest(e.clientX, (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect()))}
            onPointerLeave={() => setHover(null)}
            onFocus={() => setHover(h => h ?? 0)}
            onBlur={() => setHover(null)}
            onKeyDown={e => {
              if (e.key === 'ArrowRight') { setHover(h => Math.min(points.length - 1, (h ?? -1) + 1)); e.preventDefault() }
              if (e.key === 'ArrowLeft') { setHover(h => Math.max(0, (h ?? 1) - 1)); e.preventDefault() }
              if (e.key === 'Escape') setHover(null)
            }} />
        </svg>
        {hp && (
          <div style={{
            position: 'absolute', top: 8, left: `calc(${(hx / W) * 100}% + ${hx / W > 0.7 ? '-190px' : '12px'})`,
            pointerEvents: 'none', background: 'var(--bg-surface)', border: '1px solid var(--border-strong)', borderRadius: '10px',
            padding: '8px 12px', fontSize: '12px', lineHeight: 1.6, boxShadow: '0 8px 24px rgba(0,0,0,0.45)', whiteSpace: 'nowrap',
          }}>
            <div style={{ color: 'var(--text-muted)' }}>{fmtOffset(hp.t_offset_s)}</div>
            {PANELS.map(p => (
              <div key={p.key}><strong style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{p.fmt(Number(hp[p.key]) || 0)}</strong>
                <span style={{ color: 'var(--text-secondary)' }}> {p.key === 'users' ? 'users' : p.key === 'tps' ? 'requests/s' : p.key === 'p95_ms' ? 'p95' : 'errors'}</span></div>
            ))}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', alignItems: 'center', marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
        {showSteady && <span><span style={{ display: 'inline-block', width: 12, height: 9, borderRadius: 2, background: 'rgba(255,255,255,0.12)', marginRight: 6 }} />Steady state (statistics window)</span>}
        {geo.bands.length > 0 && <span>#n: finding window, shaded by severity</span>}
        <span>{bucketS} s buckets</span>
        <button onClick={() => setShowTable(v => !v)} style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '12px', textDecoration: 'underline' }}>
          {showTable ? 'Hide table' : 'Show as table'}
        </button>
      </div>
      {showTable && (
        <div style={{ overflowX: 'auto', marginTop: '10px', maxHeight: '320px', overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={{ ...th, textAlign: 'left' }}>Time</th>
              {['Users', 'Requests/s', 'p95 (ms)', 'Errors', 'Samples'].map(h => <th key={h} style={{ ...th, ...num }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {points.map(p => (
                <tr key={p.t_offset_s}>
                  <td style={td}>{fmtOffset(p.t_offset_s)}</td>
                  <td style={{ ...td, ...num }}>{fmtNum(p.users)}</td>
                  <td style={{ ...td, ...num }}>{fmtNum(p.tps, 1)}</td>
                  <td style={{ ...td, ...num }}>{fmtNum(p.p95_ms)}</td>
                  <td style={{ ...td, ...num }}>{fmtPct(p.error_pct)}</td>
                  <td style={{ ...td, ...num }}>{fmtNum(p.samples)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
