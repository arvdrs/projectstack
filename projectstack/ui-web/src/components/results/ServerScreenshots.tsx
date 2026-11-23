/**
 * Screenshots of server monitoring (CPU, memory, connections … from Grafana, CloudWatch,
 * New Relic, Datadog or any other tool) attached to a run, and the observations an AI
 * vision model writes from them. The model gets the screenshots with the run's timeline,
 * so it can line each chart up with the load steps, and the tester picks which provider
 * reads them (a local Ollama vision model keeps them on this machine).
 */
import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Copy, ImagePlus, Images, RefreshCw, Sparkles, Trash2 } from 'lucide-react'
import {
  addScreenshots, captionScreenshot, deleteScreenshot, getScreenshotJob, getScreenshots, readScreenshots, screenshotImageUrl,
  type RunAnalysis, type Screenshot, type ScreenshotJob, type ScreenshotReading, type ScreenshotsState,
} from '../../api/client'
import { Btn, Card, CardTitle, Notice, Spinner, fieldStyle, fmtDate } from './ui'
import LoadRelation, { unitPanels } from './LoadRelation'

const ACCEPT = 'image/png,image/jpeg,image/webp,image/gif'

const COVERAGE_NOTE: Record<string, string> = {
  no: 'The test window is not on this chart, so it says nothing about this test.',
  partly: 'The chart covers only part of the test window.',
  unclear: 'The AI could not tell where the test falls on this chart (no readable time axis or time zone).',
}

function localWindow(start: string, end: string): string {
  try {
    const opts: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' }
    const s = new Date(start), e = new Date(end)
    const utc = (d: Date) => d.toISOString().slice(11, 16)
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    const local = `${s.toLocaleTimeString([], opts)} – ${e.toLocaleTimeString([], opts)}`
    return `${utc(s)} – ${utc(e)} UTC${zone && zone !== 'UTC' && zone !== 'Etc/UTC' ? ` (${local} your time)` : ''}`
  } catch {
    return ''
  }
}

/** The lines the AI read off the screenshots, as chart series on the run's time axis. */
function readPanels(reading: ScreenshotReading) {
  const times = reading.content.read_at_s ?? []
  const unit = (u: string) => (['%', 'percent', 'pct'].includes(u.trim().toLowerCase()) ? '%' : ['ms', 'milliseconds'].includes(u.trim().toLowerCase()) ? 'ms' : u.trim())
  const series = reading.content.screenshots
    .filter(s => s.covers_test !== 'no' && s.readable)
    .flatMap(s => (s.series ?? []).map(line => ({
      name: line.name, unit: unit(line.unit),
      points: line.values.flatMap((v, i) => v == null || times[i] == null ? [] : [{ t: times[i] + 30, v }]),
    })))
    .filter(s => s.points.length >= 3)
  return unitPanels(series, 'read from the screenshots')
}

export function readingText(reading: ScreenshotReading, items: Screenshot[]): string {
  const lines = ['Server metrics (from screenshots)', '', ...reading.content.observations.map(o => `• ${o}`)]
  for (const s of reading.content.screenshots) {
    const item = items.find(i => i.id === s.id)
    if (!s.observations.length) continue
    lines.push('', s.title || item?.caption || item?.name || 'Screenshot', ...s.observations.map(o => `• ${o}`))
  }
  if (reading.content.caveats.length) lines.push('', `Caveats: ${reading.content.caveats.join(' ')}`)
  const rel = reading.content.relation
  if (rel?.observations.length) lines.push('', 'Server metrics vs the load test', ...rel.observations.map(o => `• ${o}`))
  return lines.join('\n')
}

export default function ServerScreenshotsCard({ analysis }: { analysis: RunAnalysis }) {
  const runId = analysis.run.run_id
  const [state, setState] = useState<ScreenshotsState | null>(null)
  const [provider, setProvider] = useState<string>('')
  const [uploading, setUploading] = useState(false)
  const [job, setJob] = useState<ScreenshotJob | null>(null)
  const reading = job?.status === 'running'
  const [dragging, setDragging] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const input = useRef<HTMLInputElement>(null)

  const apply = (s: ScreenshotsState) => {
    setState(s)
    setProvider(prev => prev && s.providers.choices.some(c => c.spec === prev) ? prev : (s.providers.default ?? ''))
  }

  // A reading runs in the background on the server: follow it until it is done (also after a page reload)
  const alive = useRef(0)
  const follow = (j: ScreenshotJob, token = alive.current) => {
    if (token !== alive.current) return
    setJob(j)
    if (j.status === 'running') {
      window.setTimeout(() => getScreenshotJob(runId, j.id).then(n => follow(n, token))
        .catch(e => { if (token === alive.current) { setError(e instanceof Error ? e.message : String(e)); setJob(null) } }), 1500)
      return
    }
    if (j.status === 'done' && j.result) setState(s => s ? { ...s, analysis: j.result } : s)
    if (j.status === 'failed') setError(j.error || 'The AI could not read the screenshots')
    setJob(null)
  }

  useEffect(() => {
    const token = ++alive.current
    setState(null); setError(''); setJob(null)
    getScreenshots(runId).then(s => { if (token !== alive.current) return; apply(s); if (s.job) follow(s.job, token) })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
    return () => { alive.current++ }
  }, [runId]) // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (files: File[]) => {
    const images = files.filter(f => f.type.startsWith('image/'))
    if (!images.length) { setError('Choose PNG, JPEG, WebP or GIF images'); return }
    setUploading(true); setError(''); setWarnings([])
    try {
      const s = await addScreenshots(runId, images)
      apply(s); setWarnings(s.errors ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }

  // Paste a screenshot straight from the clipboard (Ctrl+V), unless the user is typing in a field
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      const files = Array.from(e.clipboardData?.files ?? []).filter(f => f.type.startsWith('image/'))
      if (!files.length) return
      e.preventDefault()
      upload(files.map((f, i) => new File([f], f.name && f.name !== 'image.png' ? f.name : `pasted-${Date.now()}-${i + 1}.png`, { type: f.type })))
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }) // re-bound each render so it uploads to the current run

  const read = async (fresh: boolean) => {
    setError('')
    try {
      follow(await readScreenshots(runId, provider || null, fresh))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const copy = async () => {
    if (!state?.analysis) return
    try {
      await navigator.clipboard.writeText(readingText(state.analysis, state.items))
      setCopied(true); setTimeout(() => setCopied(false), 1600)
    } catch {
      setError('Could not copy to the clipboard')
    }
  }

  const items = state?.items ?? []
  const choices = state?.providers.choices ?? []
  const chosen = choices.find(c => c.spec === provider)
  const ai = state?.analysis ?? null
  const byId = new Map((ai?.content.screenshots ?? []).map(s => [s.id, s]))
  const full = state ? items.length >= state.limits.files : false
  const retryable = Boolean(ai && !ai.stale && (ai.failed ?? 0) > 0)
  const testWindow = localWindow(analysis.run.start, analysis.run.end)

  return (
    <Card style={{ marginBottom: '20px' }}>
      <CardTitle right={items.length > 0 && !full ? (
        <Btn small variant="ghost" onClick={() => input.current?.click()} disabled={uploading}>
          {uploading ? <Spinner size={13} /> : <ImagePlus size={13} />} Add screenshots
        </Btn>
      ) : undefined}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}><Images size={16} /> Server metrics screenshots</span>
      </CardTitle>

      <input ref={input} type="file" accept={ACCEPT} multiple hidden
        onChange={e => { const f = Array.from(e.target.files ?? []); e.target.value = ''; if (f.length) upload(f) }} />

      {!state && !error && <div style={{ color: 'var(--text-muted)', fontSize: '13px', display: 'flex', gap: 8 }}><Spinner size={13} /> Loading…</div>}

      {state && items.length === 0 && (
        <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: '14px' }}>
          Attach screenshots of CPU, memory, connections, queues or errors from Grafana, CloudWatch, New Relic, Datadog or any
          other monitoring tool, covering the test window{testWindow && <> ({testWindow})</>}. The AI reads them together with this run's
          timeline and load steps, and writes observations for the report.
        </div>
      )}

      {state && !full && (
        <div
          onClick={() => input.current?.click()}
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.current?.click() } }}
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={e => { e.preventDefault(); setDragging(false); upload(Array.from(e.dataTransfer.files)) }}
          role="button" tabIndex={0} aria-label="Add screenshots"
          style={{
            border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border-strong)'}`, borderRadius: '14px',
            padding: items.length ? '14px' : '28px 16px', textAlign: 'center', cursor: 'pointer', marginBottom: '16px',
            background: dragging ? 'rgba(var(--accent-rgb),0.06)' : 'rgba(var(--fg-rgb),0.02)', transition: 'all 0.2s',
            color: 'var(--text-secondary)', fontSize: '13.5px',
          }}>
          {uploading ? <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}><Spinner /> Adding…</span> : (
            <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
              <ImagePlus size={16} /> Drop screenshots here, click to choose, or paste one with Ctrl+V
              <span style={{ color: 'var(--text-muted)' }}>· PNG, JPEG, WebP or GIF · up to {state.limits.files} per run, {Math.round(state.limits.bytes / 1048576)} MB each</span>
            </span>
          )}
        </div>
      )}

      {error && <div style={{ marginBottom: '12px' }}><Notice tone="danger">{error}</Notice></div>}
      {warnings.length > 0 && <div style={{ marginBottom: '12px' }}><Notice tone="warning">{warnings.join(' · ')}</Notice></div>}

      {items.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 300px), 1fr))', gap: '14px', marginBottom: '18px' }}>
          {items.map((item, i) => (
            <Shot key={item.id} n={i + 1} runId={runId} item={item} reading={byId.get(item.id)} dim={reading}
              captionMax={state!.limits.caption} onChange={apply} onError={setError} />
          ))}
        </div>
      )}

      {items.length > 0 && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '16px', display: 'grid', gap: '12px' }}>
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', flex: 1, minWidth: '200px' }}>What the AI reads from them</div>
            {choices.length > 0 && (
              <>
                <select aria-label="AI provider that reads the screenshots" value={provider} onChange={e => setProvider(e.target.value)}
                  disabled={reading} style={{ ...fieldStyle, width: 'auto', padding: '7px 12px', fontSize: '13px' }}>
                  {choices.map(c => <option key={c.spec} value={c.spec}>{c.label}{c.local ? ' · this machine' : ''}</option>)}
                </select>
                {ai && retryable ? (
                  <>
                    <Btn small onClick={() => read(false)} disabled={reading || !provider} title="Read only the screenshots that could not be read">
                      {reading ? <><Spinner size={13} /> Reading…</> : <><RefreshCw size={13} /> Retry {ai.failed} failed</>}
                    </Btn>
                    <Btn small variant="ghost" onClick={() => read(true)} disabled={reading || !provider}>Read all again</Btn>
                  </>
                ) : (
                  <Btn small onClick={() => read(Boolean(ai) && !ai!.stale)} disabled={reading || !provider}>
                    {reading ? <><Spinner size={13} /> Reading…</> : ai ? <><RefreshCw size={13} /> Read again</> : <><Sparkles size={13} /> Read with AI</>}
                  </Btn>
                )}
              </>
            )}
            {ai && !ai.stale && (
              <Btn small variant="ghost" onClick={copy} title="Copy the observations as plain text, for a report">
                {copied ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy</>}
              </Btn>
            )}
          </div>

          {choices.length === 0 ? (
            <Notice tone="info">
              No AI provider that can read images is set up. Add a Gemini, Claude or OpenAI key in Settings, or install a
              vision model in Ollama (for example <code>ollama pull gemma4</code>) to keep the screenshots on this machine.
            </Notice>
          ) : !reading && chosen && (
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.55 }}>
              {chosen.local
                ? <>Read on this machine by {chosen.label}. Nothing leaves this machine; it needs several GB of free memory and can take a few minutes.</>
                : <>Sends the {items.length} screenshot{items.length === 1 ? '' : 's'} with this run's timeline, load steps and test notes to {chosen.where} ({chosen.label}).</>}
            </div>
          )}

          {reading && job && (
            <div style={{ display: 'grid', gap: '6px' }}>
              <div style={{ fontSize: '13px', color: 'var(--text-secondary)', display: 'flex', gap: 8, alignItems: 'center' }}>
                <Spinner /> {job.message}
              </div>
              {job.total > 0 && (
                <div role="progressbar" aria-valuemin={0} aria-valuemax={job.total} aria-valuenow={job.done}
                  style={{ height: 6, borderRadius: 3, background: 'rgba(var(--fg-rgb),0.08)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${Math.round((job.done / job.total) * 100)}%`, background: 'var(--accent-gradient)', transition: 'width 0.4s' }} />
                </div>
              )}
              <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                {chosen?.local ? 'On this machine each screenshot can take a few minutes.' : chosen?.spec.startsWith('groq') ? "A few screenshots per request, one request at a time (Groq's free tier limits tokens per minute)." : 'A few screenshots per request, two requests at a time; busy models are skipped.'} You can leave this page: the reading carries on.
              </div>
            </div>
          )}

          {ai && retryable && !reading && (
            <Notice tone="warning">
              {ai.failed} of {ai.content.screenshots.length} screenshot{ai.content.screenshots.length === 1 ? '' : 's'} could not be read
              {(() => { const e = ai.content.screenshots.find(x => x.error)?.error; return e ? `: ${e}` : '' })()}. The others are kept;
              <strong> Retry {ai.failed} failed</strong> reads only those.
            </Notice>
          )}

          {ai?.stale && !reading && (
            <Notice tone="warning">The screenshots, captions or test notes changed after this reading, so it is out of date and left out of reports. Read again to update it.</Notice>
          )}

          {ai && (
            <div style={{ opacity: reading || ai.stale ? 0.55 : 1, transition: 'opacity 0.2s', display: 'grid', gap: '10px' }}>
              {ai.content.observations.length > 0 ? (
                <ul style={{ paddingLeft: '20px', margin: 0, display: 'grid', gap: '7px', listStyle: 'disc', fontSize: '14.5px', lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                  {ai.content.observations.map((o, i) => <li key={i}>{o}</li>)}
                </ul>
              ) : <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>The AI wrote no overall observations.</div>}
              {ai.content.caveats.length > 0 && (
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Caveats: </span>{ai.content.caveats.join(' ')}
                </div>
              )}
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.55 }}>
                Read by {ai.provider || 'your AI provider'}{ai.local ? ' on this machine' : ''}{ai.generated_at ? ` · ${fmtDate(ai.generated_at)}` : ''}.
                {' '}Figures about the servers are read from the images by the AI; check them against the screenshots. Included in the technical report and, once read, the executive summary.
              </div>
            </div>
          )}

          {ai && !reading && (ai.content.read_at_s?.length ?? 0) > 0 && (
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: '16px', opacity: ai.stale ? 0.55 : 1 }}>
              <LoadRelation analysis={analysis} relation={ai.content.relation} serverPanels={readPanels(ai)}
                sourceNote="The server lines were read off the screenshots by the AI at the times shown, so the values are approximate: compare the lines here with your screenshots." />
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

function Shot({ n, runId, item, reading, dim, captionMax, onChange, onError }: {
  n: number; runId: string; item: Screenshot; reading?: ScreenshotReading['content']['screenshots'][number]; dim: boolean
  captionMax: number; onChange: (s: ScreenshotsState) => void; onError: (e: string) => void
}) {
  const [caption, setCaption] = useState(item.caption)
  const [saving, setSaving] = useState(false)
  const [confirm, setConfirm] = useState(false)
  useEffect(() => setCaption(item.caption), [item.caption])
  const url = screenshotImageUrl(runId, item.id)

  const save = async () => {
    if (caption.trim() === item.caption) return
    setSaving(true)
    try { onChange(await captionScreenshot(runId, item.id, caption)) } catch (e) { onError(e instanceof Error ? e.message : String(e)) } finally { setSaving(false) }
  }
  const remove = async () => {
    try { onChange(await deleteScreenshot(runId, item.id)) } catch (e) { onError(e instanceof Error ? e.message : String(e)) }
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: '14px', overflow: 'hidden', background: 'var(--bg-surface)', display: 'flex', flexDirection: 'column' }}>
      <a href={url} target="_blank" rel="noopener noreferrer" title="Open full size"
        style={{ display: 'block', background: 'rgba(var(--fg-rgb),0.04)', borderBottom: '1px solid var(--border)', position: 'relative' }}>
        <img src={url} alt={item.caption || item.name} loading="lazy"
          style={{ display: 'block', width: '100%', height: 'auto', minHeight: '90px', maxHeight: '240px', objectFit: 'contain' }} />
        <span style={{ position: 'absolute', top: 8, left: 8, fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '999px', background: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>Image {n}</span>
      </a>
      <div style={{ padding: '10px 12px', display: 'grid', gap: '8px', flex: 1, alignContent: 'start' }}>
        <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
          <input value={caption} maxLength={captionMax} onChange={e => setCaption(e.target.value)} onBlur={save}
            onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
            placeholder="Caption, e.g. CPU – app servers" aria-label={`Caption for image ${n}`}
            style={{ ...fieldStyle, padding: '6px 10px', fontSize: '13px' }} />
          {saving && <Spinner size={13} />}
          {confirm ? (
            <span style={{ display: 'inline-flex', gap: 4 }}>
              <Btn small variant="danger" onClick={remove}>Remove</Btn>
              <Btn small variant="ghost" onClick={() => setConfirm(false)}>Keep</Btn>
            </span>
          ) : (
            <button onClick={() => setConfirm(true)} title="Remove this screenshot" aria-label={`Remove image ${n}`}
              style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '4px', display: 'inline-flex' }}>
              <Trash2 size={14} />
            </button>
          )}
        </div>
        <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', wordBreak: 'break-all' }}>
          {item.name}{item.width ? ` · ${item.width}×${item.height}` : ''} · {Math.max(1, Math.round(item.bytes / 1024))} KB
        </div>
        {reading?.error && (
          <div style={{ fontSize: '12.5px', color: 'var(--warning)', display: 'flex', gap: 6, alignItems: 'flex-start' }}>
            <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} /> Not read: {reading.error}
          </div>
        )}
        {reading && !reading.error && reading.caveat && (
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>{reading.caveat}</div>
        )}
        {reading && (reading.title || reading.observations.length > 0) && (
          <div style={{ opacity: dim ? 0.55 : 1, transition: 'opacity 0.2s' }}>
            {reading.title && <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>{reading.title}</div>}
            {(reading.time_axis || reading.test_on_chart) && (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', lineHeight: 1.5 }}>
                {reading.time_axis && <>Chart shows {reading.time_axis}</>}{reading.time_axis && reading.test_on_chart && ' · '}
                {reading.test_on_chart && <>test {reading.test_on_chart}</>}
              </div>
            )}
            {COVERAGE_NOTE[reading.covers_test] && (
              <div style={{ fontSize: '12.5px', color: 'var(--warning)', marginBottom: '6px', display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} /> {COVERAGE_NOTE[reading.covers_test]}
              </div>
            )}
            {!reading.readable && <div style={{ fontSize: '12.5px', color: 'var(--warning)', marginBottom: '4px' }}>The AI could not read this screenshot clearly.</div>}
            <ul style={{ paddingLeft: '18px', margin: 0, display: 'grid', gap: '4px', listStyle: 'disc', fontSize: '13px', lineHeight: 1.5, color: 'var(--text-secondary)' }}>
              {reading.observations.map((o, i) => <li key={i}>{o}</li>)}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
