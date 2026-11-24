/**
 * The five report graphs (Running Vusers, Hits Per Second, Throughput, Response Time, Error) as
 * JPEGs captured from JMeter's HTML report, so they are exactly what the report shows. Each one
 * downloads on its own, or all five in a ZIP. Used on Reports → HTML Report and on analysed runs.
 */
import { useEffect, useState } from 'react'
import { Download, FileArchive, ImageOff, RefreshCw } from 'lucide-react'
import { buildRunGraphs, getRunGraphs, type RunGraphsState } from '../../api/client'
import { Btn, Notice, Spinner } from './ui'

export const GRAPHS = [
  { slug: 'running-vusers', title: 'Running Vusers Graph', chart: 'Active Threads Over Time' },
  { slug: 'hits-per-second', title: 'Hits Per Second Graph', chart: 'Hits Per Second' },
  { slug: 'throughput', title: 'Throughput Graph', chart: 'Bytes Throughput Over Time' },
  { slug: 'response-time', title: 'Response Time Graph', chart: 'Response Times Over Time' },
  { slug: 'errors', title: 'Error Graph', chart: 'Codes Per Second' },
] as const
// Part of each picture's address: changed with the captured panels (the Error Graph was Total Transactions
// Per Second at first), so browsers fetch the new pictures instead of showing ones they kept
const IMAGE_REV = 2

const linkBtn = (primary = false): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: '6px', borderRadius: '999px', fontWeight: 500, fontSize: '13px',
  padding: '7px 14px', whiteSpace: 'nowrap', textDecoration: 'none',
  ...(primary
    ? { background: 'var(--accent-gradient)', color: '#fff', boxShadow: '0 6px 20px -8px rgba(var(--accent-rgb),0.7)' }
    : { background: 'rgba(var(--fg-rgb),0.05)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)' }),
})

function GraphTile({ base, slug, title, chart }: { base: string; slug: string; title: string; chart: string }) {
  const [state, setState] = useState<'loading' | 'ok' | 'failed'>('loading')
  const [reason, setReason] = useState('')
  const src = `${base}/${slug}.jpg?rev=${IMAGE_REV}`

  const failed = async () => {
    setState('failed')
    try {   // the picture did not load: ask again for the server's reason
      const res = await fetch(src)
      const body = await res.json().catch(() => null)
      setReason(body?.detail || `${res.status} ${res.statusText}`)
    } catch { setReason('The server could not be reached') }
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: '14px', padding: '12px', background: 'rgba(var(--fg-rgb),0.02)', display: 'flex', flexDirection: 'column', gap: '10px', minWidth: 0 }}>
      {/* The pictures are the JMeter report's own panels (white), so the frame stays white in dark mode too */}
      <a href={state === 'ok' ? src : undefined} target="_blank" rel="noopener noreferrer" title={state === 'ok' ? `Open the ${title} full size` : undefined}
        style={{ display: 'block', aspectRatio: '2603 / 1070', borderRadius: '8px', overflow: 'hidden', background: '#fff', border: '1px solid var(--border)', position: 'relative' }}>
        {state !== 'failed' && (
          <img src={src} alt={title} loading="lazy" onLoad={() => setState('ok')} onError={failed}
            style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block', opacity: state === 'ok' ? 1 : 0, transition: 'opacity 0.3s ease' }} />
        )}
        {state === 'loading' && (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', fontSize: '12px', color: '#87857f' }}>Loading…</div>
        )}
        {state === 'failed' && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '6px', padding: '10px', textAlign: 'center', fontSize: '12px', color: '#52514e' }}>
            <ImageOff size={18} /> {reason || 'This graph is not available'}
          </div>
        )}
      </a>
      <div>
        <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>{title}</div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.4 }}>JMeter: {chart}</div>
      </div>
      <div style={{ opacity: state === 'failed' ? 0.4 : 1, pointerEvents: state === 'failed' ? 'none' : 'auto' }}>
        <a href={`${src}&download=1`} download style={linkBtn()} aria-label={`Download the ${title} as JPEG`}><Download size={13} /> Download JPEG</a>
      </div>
    </div>
  )
}

/** The five graphs of a report whose graphs folder is `base` (<base>/<slug>.jpg), and the ZIP of all five. */
export default function GraphDownloads({ base, zipUrl }: { base: string; zipUrl: string }) {
  return (
    <div style={{ display: 'grid', gap: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
        <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
          JMeter's own charts from its HTML report, as JPEG: the same pictures as the report shows.
        </div>
        <a href={zipUrl} download style={linkBtn(true)} aria-label="Download all five graphs as JPEGs in a ZIP">
          <FileArchive size={14} /> Download all (ZIP)
        </a>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 360px), 1fr))', gap: '14px' }}>
        {GRAPHS.map(g => <GraphTile key={g.slug} base={base} {...g} />)}
      </div>
    </div>
  )
}

/** An analysed run's graphs: shown when ready, followed while they are built, or built on request. */
export function RunGraphs({ runId }: { runId: string }) {
  const [state, setState] = useState<RunGraphsState | null>(null)
  const [error, setError] = useState('')
  const [misses, setMisses] = useState(0)

  useEffect(() => {
    setState(null); setError('')
    getRunGraphs(runId).then(setState).catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [runId])

  // Follow a build; a status check that fails now and then does not stop it (only several in a row)
  useEffect(() => {
    if (state?.status !== 'building') return
    const timer = setTimeout(() => {
      getRunGraphs(runId).then(s => { setState(s); setMisses(0) }).catch(e => {
        if (misses >= 4) setError(e instanceof Error ? e.message : String(e))
        else { setMisses(m => m + 1); setState(s => s && { ...s }) }
      })
    }, 1500)
    return () => clearTimeout(timer)
  }, [state, runId]) // eslint-disable-line react-hooks/exhaustive-deps

  const build = () => {
    setError('')
    buildRunGraphs(runId).then(setState).catch(e => setError(e instanceof Error ? e.message : String(e)))
  }

  if (error) return <Notice tone="danger">{error}</Notice>
  if (!state) return <div style={{ color: 'var(--text-muted)', fontSize: '14px', display: 'flex', gap: 8, alignItems: 'center' }}><Spinner /> Loading…</div>

  if (state.status === 'ready' && state.graphs_url && state.zip_url) {
    return (
      <div style={{ display: 'grid', gap: '14px' }}>
        {state.source === 'stored-samples' && (
          <Notice tone="info">
            Built from the run's stored samples, because its original result file was no longer available. The numbers are
            the same as in JMeter's report of the original file. With many labels, the line colours of the Response Time
            graph can differ from it, since JMeter orders those lines partly by where each label first appears in the file.
          </Notice>
        )}
        <GraphDownloads base={state.graphs_url} zipUrl={state.zip_url} />
      </div>
    )
  }
  if (state.status === 'building') {
    return (
      <div style={{ display: 'grid', gap: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}>
        {(state.progress ?? []).map((p, i, arr) => (
          <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'center', opacity: i === arr.length - 1 ? 1 : 0.6 }}>
            {i === arr.length - 1 ? <Spinner size={13} /> : <span style={{ width: 13 }} />} {p}
          </div>
        ))}
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px' }}>JMeter reads every sample, so a large run takes a few minutes.</div>
      </div>
    )
  }
  return (
    <div style={{ display: 'grid', gap: '12px', justifyItems: 'start' }}>
      {state.status === 'failed' && state.error && <Notice tone="danger">{state.error}</Notice>}
      <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.6, maxWidth: '820px' }}>
        {state.has_report
          ? <>JMeter's HTML report for this run is ready; the five graphs are captured from it.</>
          : <>The graphs are JMeter's own charts, captured from its HTML report for the run. New analyses build that report
            from the uploaded file. This run was analysed before that, so its report is built from the run's stored samples:
            the numbers are the same, though with many labels the line colours of the Response Time graph can differ from a
            report of the original file.</>}
      </div>
      <Btn onClick={build}>
        {state.status === 'failed' ? <><RefreshCw size={14} /> Try again</> : <><Download size={14} /> Create the graphs</>}
      </Btn>
    </div>
  )
}
