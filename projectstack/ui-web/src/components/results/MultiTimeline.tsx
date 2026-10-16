/**
 * Small multiples on one time axis, each panel with one or more series of the same
 * unit (p50/p95/p99 of one label; or the baseline and current run of a comparison).
 * One y-axis per panel, never two. A legend appears when a panel has several series
 * (which are also named in the tooltip); hover or arrow keys move one crosshair
 * across all panels.
 */
import { useMemo, useState } from 'react'
import { fmtOffset } from './ui'

export interface Series { name: string; color: string; points: { t: number; v: number }[] }
export interface Panel { title: string; subtitle?: string; fmt: (v: number) => string; floor?: number; series: Series[] }

const W = 760, LEFT = 52, RIGHT = 14, PANEL_H = 74, GAP = 34, TOP = 20, AXIS_H = 24

function nice(v: number, floor: number): number {
  const x = Math.max(v, floor)
  const exp = 10 ** Math.floor(Math.log10(x))
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * exp >= x * 1.04) return m * exp
  return 10 * exp
}

// Per-panel legend laid out right to left from estimated text widths (11px text), with names
// shortened when they do not fit beside the title; the tooltip always shows them in full
function panelLegend(panel: Panel): { name: string; color: string; x: number }[] {
  const titleW = panel.title.length * 7 + (panel.subtitle ? panel.subtitle.length * 6.2 + 8 : 0)
  const room = W - RIGHT - LEFT - titleW - 24
  const n = panel.series.length
  const maxChars = Math.max(4, Math.floor((room / n - 32) / 6.2))
  let x = W - RIGHT
  return panel.series.slice().reverse().map(s => {
    const name = s.name.length > maxChars ? s.name.slice(0, maxChars - 1) + '…' : s.name
    x -= 18 + name.length * 6.2
    const item = { name, color: s.color, x }
    x -= 14
    return item
  }).reverse()
}

function tick(v: number): string {
  if (v >= 1000) return `${+(v / 1000).toFixed(1)}K`
  return Number.isInteger(v) || v >= 10 ? String(+v.toFixed(0)) : String(+v.toFixed(2))
}

export default function MultiTimeline({ panels, band, durationS }: {
  panels: Panel[]
  band?: { from: number; to: number; label: string }
  durationS?: number
}) {
  const [hoverT, setHoverT] = useState<number | null>(null)
  const allT = useMemo(() => Array.from(new Set(panels.flatMap(p => p.series.flatMap(s => s.points.map(x => x.t))))).sort((a, b) => a - b), [panels])
  const geo = useMemo(() => {
    const x0 = Math.min(0, allT[0] ?? 0)
    const x1 = Math.max(durationS ?? 0, allT[allT.length - 1] ?? 1)
    const plotW = W - LEFT - RIGHT
    const x = (t: number) => LEFT + ((t - x0) / ((x1 - x0) || 1)) * plotW
    const tops = panels.map(p => nice(Math.max(0, ...p.series.flatMap(s => s.points.map(pt => pt.v))), p.floor ?? 1))
    const spanMin = (x1 - x0) / 60
    const step = [1, 2, 5, 10, 15, 30, 60, 120, 240].find(s => spanMin / s <= 8) ?? 480
    const ticks: number[] = []
    for (let t = Math.ceil(x0 / (step * 60)) * step * 60; t <= x1; t += step * 60) ticks.push(t)
    return { x0, x1, plotW, x, tops, ticks, step }
  }, [panels, allT, durationS])

  if (!allT.length) return <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No timeline data.</div>

  const top0 = TOP
  const axisY = top0 + 16 + panels.length * (PANEL_H + GAP) - GAP
  const height = axisY + AXIS_H
  const nearestIn = (pts: { t: number; v: number }[], t: number) => {
    let best: { t: number; v: number } | null = null
    for (const p of pts) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p
    return best
  }
  const snap = (clientX: number, rect: DOMRect) => {
    const svgX = ((clientX - rect.left) / rect.width) * W
    const t = geo.x0 + ((svgX - LEFT) / geo.plotW) * (geo.x1 - geo.x0)
    let best = allT[0]
    for (const v of allT) if (Math.abs(v - t) < Math.abs(best - t)) best = v
    return best
  }
  const hx = hoverT != null ? geo.x(hoverT) : 0
  // One legend for the chart when every panel shows the same series (a comparison); otherwise a
  // legend beside the title of each panel that has more than one
  const names = (p: Panel) => p.series.map(x => x.name).join('|')
  const shared = panels.every(p => names(p) === names(panels[0]))
  const legend = shared ? panels[0].series.map(x => [x.name, x.color] as const) : []

  return (
    <div>
      {legend.length > 1 && (
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
          {legend.map(([name, color]) => (
            <span key={name} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: 16, height: 2, background: color, display: 'inline-block', borderRadius: 1 }} /> {name}
            </span>
          ))}
        </div>
      )}
      <div style={{ position: 'relative', overflowX: 'auto' }}>
        <svg viewBox={`0 0 ${W} ${height}`} role="img" style={{ width: '100%', minWidth: '560px', height: 'auto', display: 'block' }}
          aria-label={panels.map(p => p.title).join(', ') + ' over the run'}>
          {panels.map((panel, k) => {
            const y0 = top0 + 16 + k * (PANEL_H + GAP)
            const top = geo.tops[k]
            const y = (v: number) => y0 + PANEL_H - (v / top) * PANEL_H
            return (
              <g key={panel.title}>
                <text x={LEFT} y={y0 - 8} fontSize="12" fontWeight={600} fill="var(--text-secondary)">
                  {panel.title}
                  {panel.subtitle && <tspan fontWeight={400} fill="var(--text-muted)"> · {panel.subtitle}</tspan>}
                </text>
                {!shared && panel.series.length > 1 && panelLegend(panel).map((x, i) => (
                  <g key={i} transform={`translate(${x.x.toFixed(1)}, ${y0 - 12})`}>
                    <line x1={0} x2={14} y1={0} y2={0} stroke={x.color} strokeWidth={2} />
                    <text x={18} y={4} fontSize="11" fill="var(--text-secondary)">{x.name}</text>
                  </g>
                ))}
                {band && band.to > band.from && (
                  <rect x={geo.x(Math.max(geo.x0, band.from))} y={y0} height={PANEL_H}
                    width={Math.max(0, geo.x(Math.min(geo.x1, band.to)) - geo.x(Math.max(geo.x0, band.from)))}
                    style={{ fill: 'rgba(255,255,255,0.05)' }} />
                )}
                {[0, 0.5, 1].map(f => (
                  <g key={f}>
                    <line x1={LEFT} x2={W - RIGHT} y1={y(top * f)} y2={y(top * f)} stroke={f === 0 ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.07)'} />
                    <text x={LEFT - 8} y={y(top * f) + 4} fontSize="10" textAnchor="end" fill="var(--text-muted)" style={{ fontVariantNumeric: 'tabular-nums' }}>{tick(top * f)}</text>
                  </g>
                ))}
                {panel.series.map(s => s.points.length === 1
                  ? <circle key={s.name} cx={geo.x(s.points[0].t)} cy={y(s.points[0].v)} r={4} fill={s.color} />
                  : <path key={s.name} d={`M${s.points.map(p => `${geo.x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' L')}`}
                      fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
                {hoverT != null && panel.series.map(s => {
                  const p = nearestIn(s.points, hoverT)
                  return p ? <circle key={s.name} cx={geo.x(p.t)} cy={y(p.v)} r={4} fill={s.color} stroke="var(--bg-elevated)" strokeWidth={2} /> : null
                })}
              </g>
            )
          })}
          {geo.ticks.map(t => (
            <text key={t} x={geo.x(t)} y={axisY + 12} fontSize="10" textAnchor="middle" fill="var(--text-muted)">
              {geo.step >= 60 ? `${Math.round(t / 3600)}h` : `${Math.round(t / 60)}m`}
            </text>
          ))}
          {hoverT != null && <line x1={hx} x2={hx} y1={top0 + 16} y2={axisY} stroke="var(--text-muted)" strokeWidth={1} pointerEvents="none" />}
          <rect x={LEFT} y={top0 + 16} width={geo.plotW} height={axisY - top0 - 16} fill="transparent" tabIndex={0}
            style={{ cursor: 'crosshair', outline: 'none' }} aria-label="Timeline values: use the left and right arrow keys"
            onPointerMove={e => setHoverT(snap(e.clientX, (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect()))}
            onPointerLeave={() => setHoverT(null)}
            onFocus={() => setHoverT(t => t ?? allT[0])}
            onBlur={() => setHoverT(null)}
            onKeyDown={e => {
              const i = hoverT == null ? -1 : allT.indexOf(hoverT)
              if (e.key === 'ArrowRight') { setHoverT(allT[Math.min(allT.length - 1, i + 1)]); e.preventDefault() }
              if (e.key === 'ArrowLeft') { setHoverT(allT[Math.max(0, i - 1)]); e.preventDefault() }
              if (e.key === 'Escape') setHoverT(null)
            }} />
        </svg>
        {hoverT != null && (
          <div style={{
            position: 'absolute', top: 8, left: `calc(${(hx / W) * 100}% + ${hx / W > 0.65 ? '-230px' : '12px'})`,
            pointerEvents: 'none', background: 'var(--bg-surface)', border: '1px solid var(--border-strong)', borderRadius: '10px',
            padding: '8px 12px', fontSize: '12px', lineHeight: 1.6, boxShadow: '0 8px 24px rgba(0,0,0,0.45)', whiteSpace: 'nowrap',
          }}>
            <div style={{ color: 'var(--text-muted)' }}>{fmtOffset(hoverT)}</div>
            {panels.map(panel => (
              <div key={panel.title}>
                <span style={{ color: 'var(--text-secondary)' }}>{panel.title}{panel.subtitle ? ` · ${panel.subtitle}` : ''}: </span>
                {panel.series.map((s, i) => {
                  const p = nearestIn(s.points, hoverT)
                  return (
                    <span key={s.name}>
                      {i > 0 && <span style={{ color: 'var(--text-muted)' }}> · </span>}
                      {panel.series.length > 1 && <span style={{ display: 'inline-block', width: 10, height: 2, background: s.color, verticalAlign: 'middle', marginRight: 4 }} />}
                      <strong style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{p ? panel.fmt(p.v) : '—'}</strong>
                      {panel.series.length > 1 && <span style={{ color: 'var(--text-muted)' }}> {s.name}</span>}
                    </span>
                  )
                })}
              </div>
            ))}
          </div>
        )}
      </div>
      {band && <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px' }}>
        <span style={{ display: 'inline-block', width: 12, height: 9, borderRadius: 2, background: 'rgba(255,255,255,0.12)', marginRight: 6 }} />{band.label}
      </div>}
    </div>
  )
}
