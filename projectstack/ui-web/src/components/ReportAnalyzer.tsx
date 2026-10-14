import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity, AlertOctagon, AlertTriangle, CheckCircle, Download, Info, Loader2, Mail,
  RefreshCw, Send, Sparkles, X,
} from 'lucide-react'
import {
  getReportInsights, downloadInsightsReport, getGmailStatus, shareReport,
  type InsightSeverity, type ReportInsights, type ShareReportResult,
} from '../api/client'

// ---------------------------------------------------------------------------
// Small local primitives (AppPage's are not exported)
// ---------------------------------------------------------------------------

const spin = { animation: 'spin 1s linear infinite' }

function Button({ children, onClick, disabled, variant = 'primary', type = 'button' }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; variant?: 'primary' | 'ghost'; type?: 'button' | 'submit'
}) {
  const primary = variant === 'primary'
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '6px', borderRadius: '999px', fontWeight: 500, fontSize: '13px',
        padding: '8px 16px', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1, whiteSpace: 'nowrap',
        border: primary ? 'none' : '1px solid var(--border-strong)',
        background: primary ? 'var(--accent-gradient)' : 'rgba(255,255,255,0.05)',
        color: primary ? '#fff' : 'var(--text-primary)',
        boxShadow: primary ? '0 6px 20px -8px rgba(var(--accent-rgb),0.7)' : 'none',
      }}>
      {children}
    </button>
  )
}

const fieldStyle: React.CSSProperties = {
  width: '100%', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '12px',
  padding: '10px 14px', color: 'var(--text-primary)', fontSize: '14px', outline: 'none', boxSizing: 'border-box',
}

const SEVERITY: Record<InsightSeverity, { label: string; color: string; rgb: string; Icon: typeof Info }> = {
  critical: { label: 'Critical', color: 'var(--danger)', rgb: 'var(--danger-rgb)', Icon: AlertOctagon },
  warning: { label: 'Warning', color: 'var(--warning)', rgb: 'var(--warning-rgb)', Icon: AlertTriangle },
  info: { label: 'Note', color: 'var(--info)', rgb: 'var(--info-rgb)', Icon: Info },
  good: { label: 'Good', color: 'var(--success)', rgb: 'var(--success-rgb)', Icon: CheckCircle },
}

// Pass/fail exists only against an SLA the user defined; otherwise the run is just described
const VERDICT = {
  pass: { label: 'SLA MET', color: 'var(--success)', rgb: 'var(--success-rgb)' },
  at_risk: { label: 'AT RISK', color: 'var(--warning)', rgb: 'var(--warning-rgb)' },
  fail: { label: 'SLA MISSED', color: 'var(--danger)', rgb: 'var(--danger-rgb)' },
  no_sla: { label: 'NO SLA DEFINED', color: 'var(--text-secondary)', rgb: 'var(--info-rgb)' },
}

function ms(v: number | null | undefined): string {
  if (v == null) return '—'
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`
}

// ---------------------------------------------------------------------------
// Report analyzer card
// ---------------------------------------------------------------------------

export default function ReportAnalyzer({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<ReportInsights | null>(null)
  const [loading, setLoading] = useState<'rules' | 'ai' | null>(null)
  const [error, setError] = useState('')
  const [shareOpen, setShareOpen] = useState(false)

  const run = async (ai: boolean) => {
    setLoading(ai ? 'ai' : 'rules'); setError('')
    try {
      setData(await getReportInsights(sessionId, ai))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(null)
    }
  }

  // Rule-based insights are cheap, so compute them as soon as the report step opens
  useEffect(() => { run(false) }, [sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  const v = data ? VERDICT[data.verdict] : null
  const k = data?.kpis

  return (
    <div style={{ marginTop: '20px', background: 'var(--glass), var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: '20px', padding: '28px', boxShadow: '0 20px 40px -24px rgba(0,0,0,0.6)' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
        <div style={{ width: '40px', height: '40px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(var(--accent-rgb),0.12)', border: '1px solid rgba(var(--accent-rgb),0.3)', flexShrink: 0 }}>
          <Activity size={17} style={{ color: 'var(--accent)' }} />
        </div>
        <div style={{ flex: 1, minWidth: '200px' }}>
          <div style={{ fontSize: '18px', fontWeight: 600 }}>Report Analyzer</div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>High-level insights from the JMeter HTML report, with an optional AI narrative</div>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <Button variant="ghost" onClick={() => run(false)} disabled={!!loading}>
            {loading === 'rules' ? <Loader2 size={13} style={spin} /> : <RefreshCw size={13} />} Re-analyze
          </Button>
          <Button variant="ghost" onClick={() => run(true)} disabled={!!loading || !data}>
            {loading === 'ai' ? <><Loader2 size={13} style={spin} /> Writing…</> : <><Sparkles size={13} /> {data?.ai_summary ? 'Regenerate AI narrative' : 'Add AI narrative'}</>}
          </Button>
          <Button variant="ghost" onClick={() => downloadInsightsReport(sessionId).catch(e => setError(String(e)))} disabled={!data}>
            <Download size={13} /> Download
          </Button>
          <Button onClick={() => setShareOpen(true)} disabled={!data}>
            <Mail size={13} /> Share via Gmail
          </Button>
        </div>
      </div>

      {error && <Banner tone="danger" text={error} />}
      {loading === 'rules' && !data && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '20px', color: 'var(--text-muted)', fontSize: '14px' }}>
          <Loader2 size={16} style={spin} /> Reading the JMeter report…
        </div>
      )}

      {data && v && k && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
          {/* Verdict */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '20px', marginTop: '24px', padding: '18px 22px', borderRadius: '16px', background: `rgba(${v.rgb},0.08)`, border: `1px solid rgba(${v.rgb},0.3)`, flexWrap: 'wrap' }}>
            {data.score != null
              ? <ScoreRing score={data.score} color={v.color} />
              : <div style={{ width: '64px', height: '64px', borderRadius: '50%', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', border: '6px solid rgba(255,255,255,0.08)' }}><Activity size={20} style={{ color: 'var(--text-muted)' }} /></div>}
            <div style={{ flex: 1, minWidth: '220px' }}>
              <div style={{ fontSize: '12px', letterSpacing: '0.1em', fontWeight: 600, color: v.color }}>{v.label}</div>
              <div style={{ fontSize: '17px', fontWeight: 500, marginTop: '4px' }}>{data.headline}</div>
              {data.window.start && <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>Run window: {data.window.start} – {data.window.end}</div>}
            </div>
          </div>
          {data.note && <Banner tone="info" text={data.note} />}

          {/* KPIs */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px', marginTop: '16px' }}>
            {[
              ['Samples', k.samples.toLocaleString()],
              ['Error rate', `${k.error_pct}%`],
              ['Throughput', k.throughput != null ? `${k.throughput} req/s` : '—'],
              ['p95', ms(k.p95_ms)],
              ['p99', ms(k.p99_ms)],
              // Apdex needs a response-time target, so it is shown only with an SLA
              ...(k.apdex != null ? [['Apdex', k.apdex.toFixed(2), k.apdex_rating ?? undefined]] : []),
            ].map(([label, value, sub]) => (
              <div key={label} style={{ padding: '14px 16px', borderRadius: '14px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)' }}>
                <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>{label}</div>
                <div style={{ fontSize: '22px', fontWeight: 600, marginTop: '6px', letterSpacing: '-0.01em' }}>{value}</div>
                {sub && <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{sub}</div>}
              </div>
            ))}
          </div>

          {/* Findings */}
          <div style={{ fontSize: '14px', fontWeight: 600, margin: '24px 0 10px' }}>High-level insights</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {data.findings.map((f, i) => {
              const s = SEVERITY[f.severity]
              return (
                <motion.div key={f.title + i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
                  style={{ display: 'flex', gap: '14px', padding: '14px 16px', borderRadius: '14px', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderLeft: `3px solid ${s.color}` }}>
                  <s.Icon size={17} style={{ color: s.color, flexShrink: 0, marginTop: '1px' }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '14px', fontWeight: 600 }}>{f.title}</span>
                      <span style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 8px', borderRadius: '999px', color: s.color, background: `rgba(${s.rgb},0.12)` }}>{s.label}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: '3px' }}>{f.detail}</div>
                  </div>
                  {f.metric && <div style={{ fontSize: '13px', fontWeight: 600, color: s.color, whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>{f.metric}</div>}
                </motion.div>
              )
            })}
          </div>

          {/* AI narrative */}
          {data.ai_error && <Banner tone="warning" text={`AI narrative unavailable: ${data.ai_error}. The rule-based insights above are unaffected.`} />}
          {data.ai_summary && (
            <div style={{ marginTop: '20px', padding: '18px 20px', borderRadius: '16px', background: 'rgba(var(--accent-rgb),0.05)', border: '1px solid rgba(var(--accent-rgb),0.25)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600, color: 'var(--accent)', marginBottom: '10px' }}>
                <Sparkles size={14} /> AI narrative
              </div>
              <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>{data.ai_summary}</div>
            </div>
          )}
        </motion.div>
      )}

      <AnimatePresence>
        {shareOpen && data && (
          <ShareModal sessionId={sessionId} insights={data} onClose={() => setShareOpen(false)} />
        )}
      </AnimatePresence>
    </div>
  )
}

function ScoreRing({ score, color }: { score: number; color: string }) {
  const r = 26, c = 2 * Math.PI * r
  return (
    <div style={{ position: 'relative', width: '64px', height: '64px', flexShrink: 0 }}>
      <svg width="64" height="64" viewBox="0 0 64 64" style={{ transform: 'rotate(-90deg)' }}>
        <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="6" />
        <motion.circle cx="32" cy="32" r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - score / 100) }}
          transition={{ duration: 1, ease: 'easeOut' }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '18px', fontWeight: 600 }}>{score}</div>
    </div>
  )
}

function Banner({ tone, text }: { tone: 'danger' | 'warning' | 'info' | 'success'; text: string }) {
  const Icon = tone === 'success' ? CheckCircle : tone === 'info' ? Info : AlertTriangle
  return (
    <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', marginTop: '16px', padding: '12px 16px', borderRadius: '12px', fontSize: '13px', lineHeight: 1.6, color: `var(--${tone})`, background: `rgba(var(--${tone}-rgb),0.08)`, border: `1px solid rgba(var(--${tone}-rgb),0.3)` }}>
      <Icon size={15} style={{ flexShrink: 0, marginTop: '2px' }} />
      <span>{text}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Share via Gmail
// ---------------------------------------------------------------------------

function ShareModal({ sessionId, insights, onClose }: { sessionId: string; insights: ReportInsights; onClose: () => void }) {
  const [gmail, setGmail] = useState<{ configured: boolean; address: string | null } | null>(null)
  const [to, setTo] = useState('')
  const [cc, setCc] = useState('')
  const [subject, setSubject] = useState('')
  const [note, setNote] = useState('')
  const [includeAi, setIncludeAi] = useState(!!insights.ai_summary)
  const [attachDashboard, setAttachDashboard] = useState(insights.source === 'jmeter_html')
  const [attachHtml, setAttachHtml] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState<ShareReportResult | null>(null)

  useEffect(() => { getGmailStatus().then(setGmail).catch(() => setGmail({ configured: false, address: null })) }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !sending) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, sending])

  const verdict = insights.verdict === 'no_sla' ? '' : VERDICT[insights.verdict].label
  const send = async (e: React.FormEvent) => {
    e.preventDefault()
    setSending(true); setError('')
    try {
      setSent(await shareReport(sessionId, {
        to, cc, subject, note, include_ai: includeAi, attach_jmeter_report: attachDashboard, attach_insights_html: attachHtml,
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSending(false)
    }
  }

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      onClick={() => { if (!sending) onClose() }}
      style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(6px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <motion.div role="dialog" aria-modal="true" aria-label="Share report via Gmail"
        initial={{ opacity: 0, y: 20, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10 }}
        onClick={e => e.stopPropagation()}
        style={{ width: '100%', maxWidth: '560px', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto', background: '#1a1919', border: '1px solid var(--border-strong)', borderRadius: '24px', padding: '28px', boxShadow: '0 40px 80px -20px rgba(0,0,0,0.8)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
          <div style={{ width: '40px', height: '40px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--accent-gradient)' }}>
            <Mail size={17} color="#fff" />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '18px', fontWeight: 600 }}>Share report via Gmail</div>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
              {gmail?.configured ? <>Sending from <span style={{ color: 'var(--text-secondary)' }}>{gmail.address}</span></> : 'Email the insights, metrics and observations'}
            </div>
          </div>
          <button onClick={onClose} disabled={sending} aria-label="Close" style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', borderRadius: '50%', width: '34px', height: '34px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', cursor: 'pointer' }}>
            <X size={15} />
          </button>
        </div>

        {gmail === null ? (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', color: 'var(--text-muted)', fontSize: '14px' }}><Loader2 size={15} style={spin} /> Checking Gmail connection…</div>
        ) : !gmail.configured ? (
          <div>
            <Banner tone="warning" text="Gmail isn't connected yet. Add your Gmail address and an App Password in Settings → Email, then come back to share." />
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '20px' }}>
              <a href="/settings#email" style={{ textDecoration: 'none' }}><Button>Open Settings</Button></a>
            </div>
          </div>
        ) : sent ? (
          <div>
            <Banner tone="success" text={`Sent to ${[...sent.to, ...sent.cc].join(', ')}.`} />
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '14px', lineHeight: 1.7 }}>
              <div><span style={{ color: 'var(--text-muted)' }}>Subject:</span> {sent.subject}</div>
              <div><span style={{ color: 'var(--text-muted)' }}>Attachments:</span> {sent.attachments.length ? sent.attachments.join(', ') : 'none'}</div>
            </div>
            {sent.skipped.map(s => <Banner key={s} tone="warning" text={`Not attached: ${s}.`} />)}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '20px' }}><Button onClick={onClose}>Done</Button></div>
          </div>
        ) : (
          <form onSubmit={send}>
            <Field label="To" hint="Separate multiple addresses with commas">
              <input style={fieldStyle} value={to} onChange={e => setTo(e.target.value)} placeholder="lead@company.com, qa@company.com" required autoFocus />
            </Field>
            <Field label="Cc">
              <input style={fieldStyle} value={cc} onChange={e => setCc(e.target.value)} placeholder="Optional" />
            </Field>
            <Field label="Subject">
              <input style={fieldStyle} value={subject} onChange={e => setSubject(e.target.value)} placeholder={`${verdict ? `[${verdict}] ` : ''}Performance test report`} />
            </Field>
            <Field label="Note" hint="Shown at the top of the email — context such as environment, build or what to review">
              <textarea style={{ ...fieldStyle, minHeight: '84px', resize: 'vertical', fontFamily: 'inherit' }} value={note} onChange={e => setNote(e.target.value)} placeholder="Ran against staging, build 1.4.2…" />
            </Field>

            <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', margin: '4px 0 8px' }}>Include</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px 14px', borderRadius: '14px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)' }}>
              <Check checked disabled label={`${insights.sla?.defined ? 'SLA result, k' : 'K'}ey metrics, insights, transactions and errors`} />
              <Check checked={includeAi} onChange={setIncludeAi} disabled={!insights.ai_summary}
                label={insights.ai_summary ? 'AI narrative and recommendations' : 'AI narrative — generate it first with "Add AI narrative"'} />
              <Check checked={attachDashboard} onChange={setAttachDashboard} disabled={insights.source !== 'jmeter_html'}
                label="Attach full JMeter HTML dashboard (.zip)" />
              <Check checked={attachHtml} onChange={setAttachHtml} label="Attach insights report (.html)" />
            </div>

            {error && <Banner tone="danger" text={error} />}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '22px' }}>
              <Button variant="ghost" onClick={onClose} disabled={sending}>Cancel</Button>
              <Button type="submit" disabled={sending || !to.trim()}>
                {sending ? <><Loader2 size={13} style={spin} /> Sending…</> : <><Send size={13} /> Send report</>}
              </Button>
            </div>
          </form>
        )}
      </motion.div>
    </motion.div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block', marginBottom: '14px' }}>
      <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px' }}>{label}</div>
      {children}
      {hint && <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '5px' }}>{hint}</div>}
    </label>
  )
}

function Check({ checked, onChange, label, disabled }: { checked: boolean; onChange?: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '13px', color: disabled && !checked ? 'var(--text-muted)' : 'var(--text-primary)', cursor: disabled ? 'default' : 'pointer' }}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={e => onChange?.(e.target.checked)}
        style={{ accentColor: 'var(--accent)', width: '15px', height: '15px' }} />
      {label}
    </label>
  )
}
