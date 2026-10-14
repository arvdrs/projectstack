import { useEffect, useMemo, useRef, useState } from 'react'
import { Square, Users, Activity, Timer, AlertTriangle, Gauge, Hash } from 'lucide-react'
import type { LiveSnapshot, LivePoint } from '../api/client'

// ---------------------------------------------------------------------------
// Phase + progress
// ---------------------------------------------------------------------------

function phaseOf(s: LiveSnapshot): { label: string; tone: 'info' | 'accent' | 'success' | 'warning' | 'danger' } {
  const secs = s.timeline.length
  switch (s.status) {
    case 'starting':  return { label: 'Starting JMeter…', tone: 'info' }
    case 'stopping':  return { label: 'Stopping — letting in-flight requests finish', tone: 'warning' }
    case 'finishing': return { label: 'Load finished — building the HTML report', tone: 'info' }
    case 'done':      return { label: 'Test complete', tone: 'success' }
    case 'failed':    return { label: 'Test failed', tone: 'danger' }
  }
  const { threads, rampup } = s.config
  if (secs < rampup && s.totals.active_users < threads) {
    return { label: `Ramping up — ${s.totals.active_users} of ${threads} users`, tone: 'accent' }
  }
  return { label: `Steady load — ${s.totals.active_users} users`, tone: 'accent' }
}

function progressOf(s: LiveSnapshot): { pct: number; text: string } | null {
  const secs = s.timeline.length
  const { duration_seconds } = s.config
  if (duration_seconds > 0) {
    const pct = Math.min(100, (secs / duration_seconds) * 100)
    return { pct, text: `${fmtDuration(Math.min(secs, duration_seconds))} / ${fmtDuration(duration_seconds)}` }
  }
  if (s.expected_requests) {
    const pct = Math.min(100, (s.totals.requests / s.expected_requests) * 100)
    return { pct, text: `${s.totals.requests.toLocaleString()} / ${s.expected_requests.toLocaleString()} requests` }
  }
  return null
}

function fmtDuration(sec: number): string {
  const m = Math.floor(sec / 60), r = Math.round(sec % 60)
  return m ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`
}

function fmtRate(v: number): string {
  return v === 0 ? '0' : v < 10 ? v.toFixed(1) : Math.round(v).toLocaleString()
}

function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`
}

// ---------------------------------------------------------------------------
// Traffic animation: virtual users → target app, responses flowing back
// ---------------------------------------------------------------------------

type Particle = { user: number; born: number; travel: number; ok: boolean }

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function TrafficCanvas({ snapshot }: { snapshot: LiveSnapshot }) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const latest = useRef(snapshot)
  latest.current = snapshot
  const [reducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)

  // Requests per animated dot — keeps the animation readable at high throughput
  const MAX_DOTS_PER_SEC = 45
  const perDot = Math.max(1, Math.ceil(snapshot.totals.rps_now / MAX_DOTS_PER_SEC))

  useEffect(() => {
    const canvas = canvasRef.current, wrap = wrapRef.current
    if (!canvas || !wrap) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const colors = {
      req: cssVar('--series-1', '#3987e5'),
      ok: cssVar('--success', '#3ccf7a'),
      err: cssVar('--danger', '#ff6b6b'),
      muted: cssVar('--text-muted', '#8a8a8a'),
      text: cssVar('--text-secondary', '#a3a3a3'),
      primary: cssVar('--text-primary', '#f5f5f5'),
      line: 'rgba(255,255,255,0.08)',
      surface: cssVar('--bg-surface', '#161515'),
    }
    let width = 0, height = 0
    const dpr = window.devicePixelRatio || 1
    const resize = () => {
      width = wrap.clientWidth; height = 260
      canvas.width = width * dpr; canvas.height = height * dpr
      canvas.style.width = `${width}px`; canvas.style.height = `${height}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(wrap)

    const particles: Particle[] = []
    const lastSent: number[] = []
    let spawnDebt = 0
    let last = performance.now()
    let raf = 0
    let serverHeat = 0

    const userPos = (i: number, shown: number) => {
      const cols = shown > 24 ? 4 : shown > 8 ? 3 : 2
      const rows = Math.ceil(shown / cols)
      const cell = Math.min(26, (height - 60) / Math.max(rows, 1))
      const gridH = rows * cell
      const x0 = 34, y0 = (height - gridH) / 2 + cell / 2
      return { x: x0 + (i % cols) * cell, y: y0 + Math.floor(i / cols) * cell, r: Math.max(4, Math.min(8, cell / 2 - 3)), right: x0 + cols * cell }
    }

    const frame = (now: number) => {
      const dt = Math.min(100, now - last); last = now
      const s = latest.current
      const target = Math.max(1, s.config.threads)
      const shown = Math.min(target, 48)
      const active = Math.min(shown, Math.round((s.totals.active_users / target) * shown))
      const live = s.status === 'running' || s.status === 'stopping'
      const errRate = s.totals.requests ? s.totals.errors / s.totals.requests : 0
      const recentErr = s.recent.length ? s.recent.filter(r => !r.ok).length / s.recent.length : errRate
      // Travel time reflects real latency (log-scaled so 5 ms and 5 s both read)
      const avg = s.totals.avg_ms_now || s.totals.avg_ms || 100
      const travel = Math.min(1600, Math.max(380, 260 * Math.log10(avg + 10)))

      if (live && active > 0 && !reducedMotion) {
        spawnDebt += (Math.min(s.totals.rps_now, MAX_DOTS_PER_SEC) * dt) / 1000
        while (spawnDebt >= 1) {
          spawnDebt -= 1
          const user = Math.floor(Math.random() * active)
          particles.push({ user, born: now, travel, ok: Math.random() >= recentErr })
          lastSent[user] = now
        }
      } else {
        spawnDebt = 0
      }
      serverHeat += ((live ? Math.min(1, s.totals.rps_now / 50) : 0) - serverHeat) * 0.05

      ctx.clearRect(0, 0, width, height)
      const server = { x: width - 150, y: height / 2 - 52, w: 116, h: 104 }
      const first = userPos(0, shown)
      const laneStart = first.right + 10, laneEnd = server.x - 10

      // Lanes
      ctx.strokeStyle = colors.line; ctx.lineWidth = 1
      ctx.setLineDash([3, 6])
      ctx.beginPath(); ctx.moveTo(laneStart, height / 2 - 14); ctx.lineTo(laneEnd, height / 2 - 14)
      ctx.moveTo(laneEnd, height / 2 + 14); ctx.lineTo(laneStart, height / 2 + 14); ctx.stroke()
      ctx.setLineDash([])

      // Users
      for (let i = 0; i < shown; i++) {
        const p = userPos(i, shown)
        const on = i < active
        const pulse = lastSent[i] ? Math.max(0, 1 - (now - lastSent[i]) / 350) : 0
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r + pulse * 3, 0, Math.PI * 2)
        if (on) { ctx.fillStyle = colors.req; ctx.globalAlpha = 0.55 + pulse * 0.45; ctx.fill(); ctx.globalAlpha = 1 }
        else { ctx.strokeStyle = colors.muted; ctx.globalAlpha = 0.5; ctx.lineWidth = 1.5; ctx.stroke(); ctx.globalAlpha = 1 }
      }
      ctx.fillStyle = colors.text; ctx.font = '600 11px "General Sans", system-ui, sans-serif'; ctx.textAlign = 'left'
      ctx.fillText(`VIRTUAL USERS  ${s.totals.active_users}/${target}${target > shown ? ` (showing ${shown})` : ''}`, 18, 18)

      // Server
      ctx.fillStyle = colors.surface
      ctx.strokeStyle = serverHeat > 0.02 ? colors.req : colors.line
      ctx.globalAlpha = 1; ctx.lineWidth = 1 + serverHeat * 2
      ctx.beginPath(); ctx.roundRect(server.x, server.y, server.w, server.h, 14); ctx.fill(); ctx.stroke()
      for (let k = 0; k < 3; k++) {
        const y = server.y + 22 + k * 22
        ctx.fillStyle = 'rgba(255,255,255,0.06)'; ctx.beginPath(); ctx.roundRect(server.x + 14, y, server.w - 28, 12, 4); ctx.fill()
        ctx.fillStyle = colors.req; ctx.globalAlpha = 0.25 + serverHeat * 0.75 * (0.6 + 0.4 * Math.sin(now / 120 + k))
        ctx.beginPath(); ctx.arc(server.x + server.w - 22, y + 6, 3, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1
      }
      ctx.fillStyle = colors.text; ctx.textAlign = 'center'
      ctx.fillText('TARGET APP', server.x + server.w / 2, server.y - 10)

      // Particles: out on the upper lane, back on the lower lane
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i]
        const age = (now - p.born) / p.travel
        if (age >= 2) { particles.splice(i, 1); continue }
        const u = userPos(Math.min(p.user, shown - 1), shown)
        let x: number, y: number, color: string
        if (age < 1) {
          const e = age * age * (3 - 2 * age)
          x = u.x + (server.x - u.x) * e; y = u.y + (height / 2 - 14 - u.y) * Math.min(1, age * 3)
          color = colors.req
        } else {
          const b = age - 1, e = b * b * (3 - 2 * b)
          x = server.x - (server.x - u.x) * e; y = height / 2 + 14 + (u.y - height / 2 - 14) * Math.max(0, (b - 0.66) * 3)
          color = p.ok ? colors.ok : colors.err
        }
        ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill()
      }

      // Lane captions
      ctx.font = '500 11px "General Sans", system-ui, sans-serif'; ctx.fillStyle = colors.muted
      ctx.fillText(`requests →  ${s.totals.rps_now.toFixed(1)}/s`, (laneStart + laneEnd) / 2, height / 2 - 24)
      ctx.fillText(`← responses  avg ${fmtMs(s.totals.avg_ms_now || s.totals.avg_ms)}`, (laneStart + laneEnd) / 2, height / 2 + 34)

      if (s.status === 'starting') {
        ctx.fillStyle = colors.primary; ctx.font = '600 14px "General Sans", system-ui, sans-serif'
        ctx.fillText('Starting JMeter…', (laneStart + laneEnd) / 2, height / 2 + 4)
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [reducedMotion])

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <canvas ref={canvasRef} role="img"
        aria-label={`${snapshot.totals.active_users} virtual users sending ${snapshot.totals.rps_now} requests per second; average response ${fmtMs(snapshot.totals.avg_ms_now)}`} />
      <div style={{ position: 'absolute', right: 12, bottom: 8, display: 'flex', gap: '12px', fontSize: '11px', color: 'var(--text-muted)' }}>
        <Dot color="var(--series-1)" label="request" />
        <Dot color="var(--success)" label="OK response" />
        <Dot color="var(--danger)" label="error" />
        {perDot > 1 && <span>each dot ≈ {perDot} requests</span>}
      </div>
    </div>
  )
}

function Dot({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />{label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Live line chart (one axis, crosshair + tooltip)
// ---------------------------------------------------------------------------

type Series = { key: keyof LivePoint; label: string; color: string; format: (v: number) => string }

function niceMax(v: number): number {
  if (v <= 0) return 1
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  const n = v / p
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p
}

function LiveLineChart({ title, points, series, height = 150 }: {
  title: string; points: LivePoint[]; series: Series[]; height?: number
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(320)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const pad = { l: 44, r: 12, t: 10, b: 22 }
  const w = Math.max(160, width), h = height
  const n = Math.max(points.length, 2)
  const yMax = niceMax(Math.max(1, ...points.flatMap(p => series.map(s => Number(p[s.key] ?? 0)))))
  const x = (i: number) => pad.l + (i / (n - 1)) * (w - pad.l - pad.r)
  const y = (v: number) => pad.t + (1 - v / yMax) * (h - pad.t - pad.b)

  const paths = useMemo(() => series.map(s => {
    let d = '', pen = false
    points.forEach((p, i) => {
      const v = p[s.key]
      if (v == null) { pen = false; return }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(Number(v)).toFixed(1)}`
      pen = true
    })
    return d
  }), [points, series, w, yMax]) // eslint-disable-line react-hooks/exhaustive-deps

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - rect.left
    const i = Math.round(((px - pad.l) / (w - pad.l - pad.r)) * (n - 1))
    setHover(points.length ? Math.max(0, Math.min(points.length - 1, i)) : null)
  }

  const hp = hover != null ? points[hover] : null
  const lastIdx = points.length - 1
  const ticks = [0, yMax / 2, yMax]

  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px', padding: '12px 14px 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '4px' }}>
        <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>{title}</span>
        {series.length > 1 && (
          <span style={{ marginLeft: 'auto', display: 'flex', gap: '10px', fontSize: '11px', color: 'var(--text-muted)' }}>
            {series.map(s => (
              <span key={String(s.key)} style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
                <span style={{ width: 12, height: 2, borderRadius: 1, background: s.color }} />{s.label}
              </span>
            ))}
          </span>
        )}
      </div>
      <div ref={wrapRef} style={{ position: 'relative' }}>
        <svg width={w} height={h} onPointerMove={onMove} onPointerLeave={() => setHover(null)}
          role="img" aria-label={`${title} over the last ${points.length} seconds`} style={{ display: 'block', touchAction: 'none' }}>
          {ticks.map(t => (
            <g key={t}>
              <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="rgba(255,255,255,0.07)" />
              <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--text-muted)" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {series[0].format(t)}
              </text>
            </g>
          ))}
          <text x={pad.l} y={h - 6} fontSize="10" fill="var(--text-muted)">0s</text>
          <text x={w - pad.r} y={h - 6} fontSize="10" fill="var(--text-muted)" textAnchor="end">{points.length ? `${points.length - 1}s` : ''}</text>
          {series.map((s, si) => (
            <path key={String(s.key)} d={paths[si]} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {/* Current value marker on the newest point */}
          {lastIdx >= 0 && hover == null && series.map(s => {
            const v = points[lastIdx][s.key]
            return v == null ? null : (
              <circle key={String(s.key)} cx={x(lastIdx)} cy={y(Number(v))} r={4} fill={s.color} stroke="var(--bg-surface)" strokeWidth={2} />
            )
          })}
          {hp && hover != null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={h - pad.b} stroke="var(--text-muted)" strokeWidth={1} />
              {series.map(s => {
                const v = hp[s.key]
                return v == null ? null : (
                  <circle key={String(s.key)} cx={x(hover)} cy={y(Number(v))} r={4} fill={s.color} stroke="var(--bg-surface)" strokeWidth={2} />
                )
              })}
            </g>
          )}
        </svg>
        {hp && hover != null && (
          <div style={{
            position: 'absolute', top: 4, left: Math.min(x(hover) + 10, w - 150), pointerEvents: 'none',
            background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '8px',
            padding: '6px 9px', fontSize: '11px', color: 'var(--text-primary)', minWidth: 120, boxShadow: '0 6px 18px rgba(0,0,0,0.4)',
          }}>
            <div style={{ color: 'var(--text-muted)', marginBottom: 3 }}>at {hp.t}s</div>
            {series.map(s => (
              <div key={String(s.key)} style={{ display: 'flex', alignItems: 'center', gap: 6, fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: s.color }} />
                <span style={{ color: 'var(--text-secondary)' }}>{s.label}</span>
                <span style={{ marginLeft: 'auto' }}>{hp[s.key] == null ? '—' : s.format(Number(hp[s.key]))}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Tiles, tables, feed
// ---------------------------------------------------------------------------

function Tile({ icon: Icon, label, value, sub, tone }: {
  icon: React.ElementType; label: string; value: string; sub?: string; tone?: 'danger' | 'success'
}) {
  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px', padding: '12px 14px', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        <Icon size={12} />{label}
      </div>
      <div style={{ fontSize: '22px', fontWeight: 600, marginTop: '6px', color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.01em' }}>{value}</div>
      {sub && (
        <div style={{ fontSize: '11px', marginTop: '2px', color: tone ? `var(--${tone})` : 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{sub}</div>
      )}
    </div>
  )
}

function EndpointTable({ labels }: { labels: LiveSnapshot['labels'] }) {
  const maxAvg = Math.max(1, ...labels.map(l => l.avg_ms))
  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden' }}>
      <div style={{ padding: '10px 14px', fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>Endpoints</div>
      <div style={{ maxHeight: 260, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>
          <thead>
            <tr style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
              {['Request', 'Count', 'Avg', 'p95', 'Errors'].map((h, i) => (
                <th key={h} style={{ padding: '6px 10px', textAlign: i === 0 ? 'left' : 'right', fontWeight: 600 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {labels.map(l => (
              <tr key={l.label} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '6px 10px', maxWidth: 220 }}>
                  <div style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={l.label}>{l.label}</div>
                  <div style={{ height: 3, marginTop: 4, borderRadius: 2, background: 'rgba(255,255,255,0.06)' }}>
                    <div style={{ height: 3, borderRadius: 2, width: `${(l.avg_ms / maxAvg) * 100}%`, background: 'var(--series-1)', transition: 'width 0.5s' }} />
                  </div>
                </td>
                <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-primary)' }}>{l.requests.toLocaleString()}</td>
                <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-primary)' }}>{fmtMs(l.avg_ms)}</td>
                <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--text-secondary)' }}>{fmtMs(l.p95_ms)}</td>
                <td style={{ padding: '6px 10px', textAlign: 'right', color: l.errors ? 'var(--danger)' : 'var(--text-muted)' }}>
                  {l.errors ? `${l.errors} (${((l.errors / l.requests) * 100).toFixed(0)}%)` : '0'}
                </td>
              </tr>
            ))}
            {labels.length === 0 && (
              <tr><td colSpan={5} style={{ padding: '14px', color: 'var(--text-muted)', textAlign: 'center' }}>Waiting for the first responses…</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function RecentFeed({ recent }: { recent: LiveSnapshot['recent'] }) {
  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden' }}>
      <div style={{ padding: '10px 14px', fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>Latest requests</div>
      <div style={{ maxHeight: 260, overflowY: 'auto' }}>
        {recent.map((r, i) => (
          <div key={`${r.t}-${i}-${r.label}`} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '5px 12px', borderTop: i ? '1px solid var(--border)' : 'none', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
            <span style={{ fontWeight: 700, minWidth: 34, color: r.ok ? 'var(--success)' : 'var(--danger)' }}>{r.ok ? '✓' : '✕'} {r.code}</span>
            <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.label}>{r.label}</span>
            <span style={{ color: 'var(--text-primary)' }}>{fmtMs(r.ms)}</span>
            <span style={{ color: 'var(--text-muted)', minWidth: 38, textAlign: 'right' }}>{r.t}s</span>
          </div>
        ))}
        {recent.length === 0 && <div style={{ padding: '14px', fontSize: '12px', color: 'var(--text-muted)', textAlign: 'center' }}>—</div>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main monitor
// ---------------------------------------------------------------------------

export default function LiveRunMonitor({ snapshot, onStop, stopping }: {
  snapshot: LiveSnapshot
  onStop?: () => void
  stopping?: boolean
}) {
  const phase = phaseOf(snapshot)
  const progress = progressOf(snapshot)
  const t = snapshot.totals
  const running = snapshot.status === 'starting' || snapshot.status === 'running'
  const toneColor = `var(--${phase.tone === 'accent' ? 'accent' : phase.tone})`

  const rpsSeries: Series[] = [
    { key: 'rps', label: 'Requests/s', color: 'var(--series-1)', format: fmtRate },
    { key: 'errors', label: 'Errors/s', color: 'var(--danger)', format: fmtRate },
  ]
  const msSeries: Series[] = [
    { key: 'avg_ms', label: 'Average', color: 'var(--series-1)', format: v => fmtMs(v) },
    { key: 'p95_ms', label: 'p95', color: 'var(--series-2)', format: v => fmtMs(v) },
  ]
  const userSeries: Series[] = [
    { key: 'users', label: 'Active users', color: 'var(--series-1)', format: v => String(Math.round(v)) },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      {/* Status bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
          <span className={running ? 'live-pulse' : undefined} style={{ width: 9, height: 9, borderRadius: '50%', background: toneColor }} />
          {phase.label}
        </span>
        {progress && (
          <div style={{ flex: 1, minWidth: 200, display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.07)', overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${progress.pct}%`, background: 'var(--accent-gradient)', borderRadius: 3, transition: 'width 0.9s linear' }} />
            </div>
            <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{progress.text}</span>
          </div>
        )}
        <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>elapsed {fmtDuration(snapshot.elapsed_s)}</span>
        {onStop && running && (
          <button onClick={onStop} disabled={stopping}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 14px', borderRadius: '999px', fontSize: '12px', fontWeight: 600,
              border: '1px solid rgba(var(--danger-rgb),0.4)', background: 'rgba(var(--danger-rgb),0.1)', color: 'var(--danger)', cursor: stopping ? 'wait' : 'pointer' }}>
            <Square size={11} fill="currentColor" /> {stopping ? 'Stopping…' : 'Stop test'}
          </button>
        )}
      </div>

      {snapshot.error && (
        <div style={{ fontSize: '12px', color: 'var(--danger)', display: 'flex', gap: 6 }}><AlertTriangle size={14} />{snapshot.error}</div>
      )}

      {/* What is happening right now */}
      <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px', padding: '6px 8px' }}>
        <TrafficCanvas snapshot={snapshot} />
      </div>

      {/* Headline numbers */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px' }}>
        <Tile icon={Users} label="Active users" value={`${t.active_users}`} sub={`of ${snapshot.config.threads} target`} />
        <Tile icon={Activity} label="Throughput" value={`${t.rps_now.toFixed(1)}/s`} sub="last 3 s" />
        <Tile icon={Timer} label="Response time" value={fmtMs(t.avg_ms_now || t.avg_ms)} sub={`overall avg ${fmtMs(t.avg_ms)}`} />
        <Tile icon={Gauge} label="p95" value={fmtMs(t.p95_ms)} sub="whole run" />
        <Tile icon={Hash} label="Requests" value={t.requests.toLocaleString()} sub={snapshot.expected_requests ? `of ${snapshot.expected_requests.toLocaleString()}` : undefined} />
        <Tile icon={AlertTriangle} label="Errors" value={t.errors.toLocaleString()} sub={`${t.error_pct.toFixed(1)}% of requests`} tone={t.errors ? 'danger' : 'success'} />
      </div>

      {/* Live charts — one measure per chart */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '10px' }}>
        <LiveLineChart title="Throughput" points={snapshot.timeline} series={rpsSeries} />
        <LiveLineChart title="Response time" points={snapshot.timeline} series={msSeries} />
        <LiveLineChart title="Active users" points={snapshot.timeline} series={userSeries} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '10px' }}>
        <EndpointTable labels={snapshot.labels} />
        <RecentFeed recent={snapshot.recent} />
      </div>
    </div>
  )
}
