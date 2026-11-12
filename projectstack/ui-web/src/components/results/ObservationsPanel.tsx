/**
 * The run's Performance Highlights & Observations: when and how the test ran, its headline
 * statistics and what stood out, followed by the recommended action and the main findings.
 *
 * Two options: Without AI (the default) — rules write the section from the results and the
 * run's test context (region, test type), and nothing leaves the machine; With AI — the AI
 * provider rewrites it in report style using the test notes. It gets findings.json, the
 * computed facts and the notes, and every number it writes is checked. The AI version is
 * saved, so switching back to it later is instant.
 */
import { useState } from 'react'
import { Check, Copy, Pencil, RefreshCw, Sparkles } from 'lucide-react'
import {
  aiHighlights, saveTestContext, type AiHighlights, type Highlights, type HighlightsState, type RunAnalysis,
  type TestContext, type TestContextFields,
} from '../../api/client'
import { Btn, FieldLabel, Notice, SeverityBadge, Spinner, VerdictBadge, fieldStyle, fmtOffset } from './ui'

const TITLE = 'Performance Highlights & Observations'
const STATS_LEAD = 'Below are the high-level statistics from the test:'

export function highlightsText(h: Highlights): string {
  return [TITLE, '', ...h.intro, '', STATS_LEAD, ...h.stats.map(s => `${s.name}: ${s.value}`), '',
    ...h.observations.map(o => `• ${o}`)].join('\n')
}

type Mode = 'rules' | 'ai'

export default function ObservationsPanel({ analysis, compact = false }: { analysis: RunAnalysis; compact?: boolean }) {
  const [rules, setRules] = useState<Highlights>(analysis.highlights)
  const [ai, setAi] = useState<AiHighlights | null>(analysis.highlights_ai)
  const [mode, setMode] = useState<Mode>('rules')            // without AI by default, even when an AI version is saved
  const [ctx, setCtx] = useState<TestContext>(analysis.context)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const showingAi = mode === 'ai' && Boolean(ai)
  const h = showingAi ? ai!.content : rules
  const exec = analysis.observations_ai?.content ?? analysis.observations

  /** Write the AI version; when there is none to show and it cannot be written, go back to Without AI. */
  const writeAi = async (fresh: boolean, hasVersion: boolean) => {
    setBusy(true); setError('')
    try {
      const res = await aiHighlights(analysis.run.run_id, fresh)
      if (res.source === 'llm') setAi(res)
      else {
        setError(res.note || 'The AI version is not available, so this is the version without AI.')
        if (!hasVersion) setMode('rules')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      if (!hasVersion) setMode('rules')
    } finally {
      setBusy(false)
    }
  }

  const choose = (next: Mode) => {
    setMode(next); setError('')
    if (next === 'ai' && !ai && !busy) writeAi(false, false)
  }

  const onSaved = (state: HighlightsState) => {
    setRules(state.highlights); setCtx(state.context); setAi(state.highlights_ai)
    // New notes make the saved AI version out of date: in With AI, write it again
    if (mode === 'ai' && !state.highlights_ai) writeAi(false, false)
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(highlightsText(h))
      setCopied(true); setTimeout(() => setCopied(false), 1600)
    } catch {
      setError('Could not copy to the clipboard')
    }
  }

  const main = analysis.findings.filter(f => f.severity !== 'info').slice(0, compact ? 3 : 6)
  const observations = compact ? h.observations.slice(0, 3) : h.observations

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <VerdictBadge verdict={analysis.verdict} big />
        <div style={{ flex: 1 }} />
        {!compact && (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <ModeSwitch mode={mode} onChange={choose} />
            {showingAi && (
              <Btn small variant="ghost" onClick={() => writeAi(true, true)} disabled={busy} title="Write the AI version again">
                {busy ? <><Spinner size={13} /> Rewriting…</> : <><RefreshCw size={13} /> Rewrite</>}
              </Btn>
            )}
            <Btn small variant="ghost" onClick={copy} disabled={mode === 'ai' && !ai} title="Copy the section as plain text, for a report">
              {copied ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy</>}
            </Btn>
          </div>
        )}
      </div>

      {mode === 'ai' && !ai ? (
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', padding: '20px 0', fontSize: '14px', color: 'var(--text-secondary)' }}>
          {busy && <><Spinner /> Writing the observations with your AI provider. This can take up to a minute…</>}
        </div>
      ) : (
      <div style={{ display: 'grid', gap: '6px', fontSize: '14.5px', lineHeight: 1.6, color: 'var(--text-secondary)',
        opacity: busy && showingAi ? 0.5 : 1, transition: 'opacity 0.2s' }}>
        {h.intro.map((p, i) => <p key={i} style={{ margin: 0, color: i === 0 ? 'var(--text-primary)' : undefined }}>{p}</p>)}
        <p style={{ margin: '8px 0 0' }}>{STATS_LEAD}</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '8px', margin: '4px 0 6px' }}>
          {h.stats.map(s => (
            <div key={s.name} style={{ border: '1px solid var(--border)', borderRadius: '10px', padding: '8px 12px' }}>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{s.name}</div>
              <div style={{ fontSize: '17px', fontWeight: 600, color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{s.value}</div>
            </div>
          ))}
        </div>
        <ul style={{ paddingLeft: '20px', margin: 0, display: 'grid', gap: '7px', listStyle: 'disc' }}>
          {observations.map((o, i) => <li key={i}>{o}</li>)}
        </ul>
        {compact && h.observations.length > observations.length && (
          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>…and {h.observations.length - observations.length} more in the full analysis</div>
        )}
      </div>
      )}

      {!compact && mode === 'rules' && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Without AI: written from the results by rules. Nothing was sent to an AI provider.
        </div>
      )}
      {showingAi && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          With AI: written by {ai!.provider || 'your AI provider'} from the results and your test notes
          {ai!.numbers && <> · {ai!.numbers.ok ? `all ${ai!.numbers.checked} numbers verified` : `${ai!.numbers.unverified.length} number(s) could not be verified: ${ai!.numbers.unverified.map(u => `“${u.text}”`).join(', ')}`}</>}
        </div>
      )}
      {error && <Notice tone="warning">{error}</Notice>}

      {!compact && <TestContextEditor runId={analysis.run.run_id} ctx={ctx} onSaved={onSaved} />}

      {!compact && exec.recommended_action && (
        <div style={{ fontSize: '14px', lineHeight: 1.55, color: 'var(--text-secondary)', borderLeft: '3px solid var(--series-1)', paddingLeft: '12px' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Recommended: </span>{exec.recommended_action}
        </div>
      )}

      {main.length > 0 && (
        <div style={{ display: 'grid', gap: '8px', marginTop: '4px' }}>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500 }}>Main findings</div>
          {main.map(f => (
            <div key={f.n} style={{ display: 'flex', gap: '10px', alignItems: 'baseline', fontSize: '13.5px', lineHeight: 1.5 }}>
              <span style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums', minWidth: '22px' }}>#{f.n}</span>
              <SeverityBadge severity={f.severity} />
              <span style={{ color: 'var(--text-primary)' }}>{f.title}
                {f.start_offset_s != null && f.type !== 'sla_breach' && f.type !== 'regression' &&
                  <span style={{ color: 'var(--text-muted)' }}> · {fmtOffset(f.start_offset_s)}</span>}
              </span>
            </div>
          ))}
          {analysis.findings.filter(f => f.severity !== 'info').length > main.length && (
            <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>…and {analysis.findings.filter(f => f.severity !== 'info').length - main.length} more in the full analysis</div>
          )}
        </div>
      )}
    </div>
  )
}

const MODES: { key: Mode; label: string; title: string }[] = [
  { key: 'rules', label: 'Without AI', title: 'Written by rules from the results. Nothing leaves this machine.' },
  { key: 'ai', label: 'With AI', title: 'Written by your AI provider in report style, using your test notes. It gets findings.json, the computed figures and the notes; every number it writes is checked.' },
]

function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div role="tablist" aria-label="How the observations are written"
      style={{ display: 'inline-flex', gap: '3px', padding: '3px', borderRadius: '999px', border: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
      {MODES.map(m => (
        <button key={m.key} role="tab" aria-selected={mode === m.key} title={m.title} onClick={() => onChange(m.key)}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 14px', borderRadius: '999px', border: 'none',
            cursor: 'pointer', fontSize: '13px', fontWeight: 600, whiteSpace: 'nowrap',
            background: mode === m.key ? 'var(--accent-gradient)' : 'transparent', color: mode === m.key ? '#fff' : 'var(--text-secondary)',
          }}>
          {m.key === 'ai' && <Sparkles size={13} />}{m.label}
        </button>
      ))}
    </div>
  )
}

const NOTES_PLACEHOLDER = `What the AI should know, for example:
/api/orders/sync is the CRM order-sync integration; its code was optimised since the last run.
The lead-capture API has a quota of 1,000 leads per day.`

function TestContextEditor({ runId, ctx, onSaved }: { runId: string; ctx: TestContext; onSaved: (s: HighlightsState) => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<TestContextFields>({ region: ctx.region, test_type: ctx.test_type, notes: ctx.notes })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const edit = (from: TestContextFields = ctx) => {
    setForm({ region: from.region, test_type: from.test_type, notes: from.notes }); setError(''); setOpen(true)
  }
  const save = async () => {
    setSaving(true); setError('')
    try {
      onSaved(await saveTestContext(runId, form)); setOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const box: React.CSSProperties = { border: '1px dashed var(--border-strong)', borderRadius: '12px', padding: '12px 14px' }
  const muted: React.CSSProperties = { fontSize: '12.5px', color: 'var(--text-muted)', lineHeight: 1.55 }

  if (!open) {
    const parts = [ctx.region && `Region ${ctx.region}`, ctx.test_type && `${ctx.test_type}`,
      ctx.notes && `notes (${ctx.notes.split('\n').filter(Boolean).length} line${ctx.notes.split('\n').filter(Boolean).length === 1 ? '' : 's'})`].filter(Boolean)
    return (
      <div style={{ ...box, display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: '220px' }}>
          <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
            Test context{parts.length > 0 && <span style={{ fontWeight: 400, color: 'var(--text-secondary)' }}>: {parts.join(' · ')}</span>}
          </div>
          <div style={muted}>
            {ctx.source === 'inherited' && <>From run {ctx.from_run} of the same scenario. Save to keep it for this run only. </>}
            {ctx.source === 'none' && <>Add the region, and notes on what endpoints do, what changed and which limits apply. With AI uses the notes. </>}
            {ctx.source === 'none' && ctx.suggestion && <>The latest context is on run {ctx.suggestion.from_run}{ctx.suggestion.scenario ? ` (${ctx.suggestion.scenario})` : ''}.</>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          {ctx.source === 'none' && ctx.suggestion && <Btn small variant="ghost" onClick={() => edit(ctx.suggestion!)}>Use that</Btn>}
          <Btn small variant="ghost" onClick={() => edit()}><Pencil size={13} /> {ctx.source === 'none' ? 'Add' : 'Edit'}</Btn>
        </div>
      </div>
    )
  }

  return (
    <div style={{ ...box, display: 'grid', gap: '12px' }}>
      <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>Test context</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
        <div>
          <FieldLabel hint="where the load came from">Region</FieldLabel>
          <input style={fieldStyle} value={form.region} placeholder="us-east-1" maxLength={80}
            onChange={e => setForm({ ...form, region: e.target.value })} />
        </div>
        <div>
          <FieldLabel hint="empty: worked out from the load profile">Test type</FieldLabel>
          <input style={fieldStyle} value={form.test_type} placeholder="incremental load test" maxLength={60}
            onChange={e => setForm({ ...form, test_type: e.target.value })} />
        </div>
      </div>
      <div>
        <FieldLabel hint="used by the With AI option">Notes</FieldLabel>
        <textarea style={{ ...fieldStyle, minHeight: '96px', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.5 }}
          value={form.notes} placeholder={NOTES_PLACEHOLDER} maxLength={4000}
          onChange={e => setForm({ ...form, notes: e.target.value })} />
      </div>
      <div style={muted}>The region and test type go into both versions straight away; the notes are only used With AI. Runs of the same scenario that have no context of their own use this one.</div>
      {error && <Notice tone="danger">{error}</Notice>}
      <div style={{ display: 'flex', gap: '8px' }}>
        <Btn small onClick={save} disabled={saving}>{saving ? <><Spinner size={13} /> Saving…</> : 'Save'}</Btn>
        <Btn small variant="ghost" onClick={() => setOpen(false)} disabled={saving}>Cancel</Btn>
      </div>
    </div>
  )
}
