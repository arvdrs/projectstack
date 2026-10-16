/**
 * Small building blocks shared by the results-analysis screens (the analysis page
 * and the prompt at the end of a test run). Styled like AppPage's primitives.
 */
import { AlertOctagon, AlertTriangle, CheckCircle, Info, Loader2, MinusCircle, XCircle } from 'lucide-react'
import type { FindingSeverity, RunVerdict } from '../../api/client'

export const spin: React.CSSProperties = { animation: 'spin 1s linear infinite' }

export function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ background: 'var(--glass), var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '20px', padding: '24px', boxShadow: '0 20px 40px -24px rgba(0,0,0,0.6)', ...style }}>
      {children}
    </div>
  )
}

export function CardTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', marginBottom: '14px', flexWrap: 'wrap' }}>
      <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>{children}</div>
      {right}
    </div>
  )
}

export function Btn({ children, onClick, disabled, variant = 'primary', small = false, type = 'button', title }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; variant?: 'primary' | 'ghost' | 'danger'
  small?: boolean; type?: 'button' | 'submit'; title?: string
}) {
  const styles: Record<string, React.CSSProperties> = {
    primary: { background: 'var(--accent-gradient)', color: '#fff', border: 'none', boxShadow: '0 6px 20px -8px rgba(var(--accent-rgb),0.7)' },
    ghost: { background: 'rgba(255,255,255,0.05)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)' },
    danger: { background: 'rgba(var(--danger-rgb),0.1)', color: 'var(--danger)', border: '1px solid rgba(var(--danger-rgb),0.3)' },
  }
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '6px', borderRadius: '999px', fontWeight: 500,
        fontSize: small ? '13px' : '14px', padding: small ? '7px 14px' : '10px 22px', whiteSpace: 'nowrap',
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1, ...styles[variant],
      }}>
      {children}
    </button>
  )
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <Loader2 size={size} style={spin} />
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'warning' | 'danger'; children: React.ReactNode }) {
  const color = tone === 'danger' ? 'var(--danger)' : tone === 'warning' ? 'var(--warning)' : 'var(--info)'
  const rgb = tone === 'danger' ? 'var(--danger-rgb)' : tone === 'warning' ? 'var(--warning-rgb)' : 'var(--info-rgb)'
  const Icon = tone === 'info' ? Info : AlertTriangle
  return (
    <div style={{ background: `rgba(${rgb},0.08)`, border: `1px solid rgba(${rgb},0.3)`, borderRadius: '12px', padding: '12px 16px', color, fontSize: '14px', display: 'flex', gap: '10px', alignItems: 'flex-start', lineHeight: 1.5 }}>
      <Icon size={16} style={{ flexShrink: 0, marginTop: '2px' }} />
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  )
}

// Status colours always come with an icon and a label, never colour alone
export const VERDICT: Record<string, { label: string; color: string; rgb: string; Icon: typeof Info }> = {
  pass: { label: 'Passed', color: 'var(--success)', rgb: 'var(--success-rgb)', Icon: CheckCircle },
  at_risk: { label: 'At risk', color: 'var(--warning)', rgb: 'var(--warning-rgb)', Icon: AlertTriangle },
  fail: { label: 'Failed', color: 'var(--danger)', rgb: 'var(--danger-rgb)', Icon: XCircle },
  no_sla: { label: 'No SLA defined', color: 'var(--text-secondary)', rgb: '255,255,255', Icon: MinusCircle },
  unknown: { label: 'No verdict', color: 'var(--text-muted)', rgb: '255,255,255', Icon: MinusCircle },
}

export function VerdictBadge({ verdict, big = false }: { verdict: RunVerdict | string; big?: boolean }) {
  const v = VERDICT[verdict] ?? { label: verdict || 'Unknown', color: 'var(--text-muted)', rgb: '255,255,255', Icon: MinusCircle }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '6px', borderRadius: '999px', whiteSpace: 'nowrap',
      padding: big ? '6px 14px' : '3px 10px', fontSize: big ? '14px' : '12px', fontWeight: 600, color: v.color,
      background: `rgba(${v.rgb},0.1)`, border: `1px solid rgba(${v.rgb},0.3)`,
    }}>
      <v.Icon size={big ? 16 : 13} /> {v.label}
    </span>
  )
}

export const SEVERITY: Record<FindingSeverity, { label: string; color: string; rgb: string; Icon: typeof Info }> = {
  critical: { label: 'Critical', color: 'var(--danger)', rgb: 'var(--danger-rgb)', Icon: AlertOctagon },
  warning: { label: 'Warning', color: 'var(--warning)', rgb: 'var(--warning-rgb)', Icon: AlertTriangle },
  info: { label: 'Info', color: 'var(--info)', rgb: 'var(--info-rgb)', Icon: Info },
}

export function SeverityBadge({ severity }: { severity: FindingSeverity }) {
  const s = SEVERITY[severity] ?? SEVERITY.info
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '12px', fontWeight: 600, color: s.color, whiteSpace: 'nowrap' }}>
      <s.Icon size={13} /> {s.label}
    </span>
  )
}

export function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{ background: 'var(--glass), var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '16px', padding: '16px 20px', minWidth: 0 }}>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500, marginBottom: '8px' }}>{label}</div>
      <div style={{ fontSize: '26px', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.1, letterSpacing: '-0.02em' }}>{value}</div>
      {sub && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{sub}</div>}
    </div>
  )
}

export const fieldStyle: React.CSSProperties = {
  width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px',
  padding: '10px 14px', color: 'var(--text-primary)', fontSize: '14px', outline: 'none', boxSizing: 'border-box',
}

export function FieldLabel({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px' }}>
      {children}{hint && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> · {hint}</span>}
    </label>
  )
}

export const th: React.CSSProperties = { padding: '8px 10px', color: 'var(--text-muted)', fontWeight: 500, fontSize: '12px', whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' }
export const td: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)', fontSize: '13px', color: 'var(--text-secondary)' }
export const num: React.CSSProperties = { textAlign: 'right', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }

// -- formatting ---------------------------------------------------------------

export function fmtNum(v: number | null | undefined, decimals = 0): string {
  if (v == null || Number.isNaN(v)) return '—'
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: decimals, minimumFractionDigits: decimals })
}

export function fmtMs(v: number | null | undefined): string {
  return v == null ? '—' : `${fmtNum(v)} ms`
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  const a = Math.abs(v)
  return `${v.toFixed(a > 0 && a < 10 ? 2 : a < 100 ? 1 : 0)}%`
}

export function fmtDuration(seconds: number | null | undefined): string {
  const s = Math.round(seconds ?? 0)
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : m ? `${m}m ${String(sec).padStart(2, '0')}s` : `${sec}s`
}

export function fmtOffset(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  if (!h && !m) return sec ? `t+${sec}s` : 't+0'
  const head = h ? `${h}h${String(m).padStart(2, '0')}m` : `${m}m`
  return sec ? `t+${head}${String(sec).padStart(2, '0')}s` : `t+${head}`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** Minimal rendering for AI answers: paragraphs, "- " bullets and **bold**, as text nodes (never HTML). */
export function RichText({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/)
  const inline = (line: string, key: number) => (
    <span key={key}>{line.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith('**') && part.endsWith('**') ? <strong key={i} style={{ color: 'var(--text-primary)' }}>{part.slice(2, -2)}</strong> : part)}</span>
  )
  return (
    <div style={{ fontSize: '14px', lineHeight: 1.6, color: 'var(--text-secondary)', display: 'grid', gap: '10px' }}>
      {blocks.map((block, bi) => {
        const lines = block.split('\n').filter(l => l.trim())
        if (lines.length && lines.every(l => /^\s*[-*•]\s+/.test(l))) {
          return <ul key={bi} style={{ paddingLeft: '20px', display: 'grid', gap: '4px' }}>{lines.map((l, i) => <li key={i}>{inline(l.replace(/^\s*[-*•]\s+/, ''), i)}</li>)}</ul>
        }
        return <p key={bi}>{lines.map((l, i) => <span key={i}>{i > 0 && <br />}{inline(l, i)}</span>)}</p>
      })}
    </div>
  )
}
