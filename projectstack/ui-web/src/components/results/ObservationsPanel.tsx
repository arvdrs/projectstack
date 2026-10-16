/**
 * The high-level observations of an analysed run: verdict, headline, key points,
 * recommended action and the main findings. Rule-based text is always there; an
 * AI-written version can be requested (only findings.json is sent to the provider).
 */
import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { aiObservations, type AiObservations, type RunAnalysis } from '../../api/client'
import { Btn, Notice, SeverityBadge, Spinner, VerdictBadge, fmtOffset } from './ui'

export default function ObservationsPanel({ analysis, compact = false }: { analysis: RunAnalysis; compact?: boolean }) {
  const [ai, setAi] = useState<AiObservations | null>(analysis.observations_ai)
  const [showAi, setShowAi] = useState(Boolean(analysis.observations_ai))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const o = showAi && ai ? ai.content : analysis.observations

  const writeAi = async () => {
    setBusy(true); setError('')
    try {
      const res = await aiObservations(analysis.run.run_id)
      setAi(res); setShowAi(true)
      if (res.source !== 'llm') setError(res.note || 'The AI summary was not available; showing the rule-based one.')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const main = analysis.findings.filter(f => f.severity !== 'info').slice(0, compact ? 3 : 6)

  return (
    <div style={{ display: 'grid', gap: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <VerdictBadge verdict={analysis.verdict} big />
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: '6px' }}>
          {ai && ai.content && (
            <Btn small variant="ghost" onClick={() => setShowAi(v => !v)}>{showAi ? 'Show rule-based' : 'Show AI summary'}</Btn>
          )}
          {!ai && (
            <Btn small variant="ghost" onClick={writeAi} disabled={busy} title="Sends only the run's findings.json to your AI provider">
              {busy ? <><Spinner size={13} /> Writing…</> : <><Sparkles size={13} /> AI summary</>}
            </Btn>
          )}
        </div>
      </div>

      <div style={{ fontSize: compact ? '17px' : '19px', fontWeight: 600, lineHeight: 1.4, color: 'var(--text-primary)' }}>{o.headline}</div>

      {o.key_points.length > 0 && (
        <ul style={{ paddingLeft: '20px', display: 'grid', gap: '6px', fontSize: '14px', lineHeight: 1.55, color: 'var(--text-secondary)' }}>
          {o.key_points.map((p, i) => <li key={i}>{p}</li>)}
        </ul>
      )}

      {!compact && o.business_impact && (
        <div style={{ fontSize: '14px', lineHeight: 1.55, color: 'var(--text-secondary)' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Impact: </span>{o.business_impact}
        </div>
      )}
      {o.recommended_action && (
        <div style={{ fontSize: '14px', lineHeight: 1.55, color: 'var(--text-secondary)', borderLeft: '3px solid var(--series-1)', paddingLeft: '12px' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Recommended: </span>{o.recommended_action}
        </div>
      )}

      {showAi && ai && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Written by {ai.provider || 'AI'} from the run's findings
          {ai.numbers && <> · {ai.numbers.ok ? `all ${ai.numbers.checked} numbers verified` : `${ai.numbers.unverified.length} number(s) could not be verified: ${ai.numbers.unverified.map(u => `“${u.text}”`).join(', ')}`}</>}
        </div>
      )}
      {error && <Notice tone="warning">{error}</Notice>}

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
