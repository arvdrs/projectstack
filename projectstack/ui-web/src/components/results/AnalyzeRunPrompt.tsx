/**
 * Shown when a test run finishes: offers to analyse the run's JTL with the results
 * engine (steady state, detectors, verdict, comparison with the last passing run of
 * this script, correlation health) and shows the high-level observations inline.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity, ArrowRight, Microscope } from 'lucide-react'
import {
  analyzeSessionRun, getResultRun, getResultsStatus, type AnalysisJob, type RunAnalysis, type ResultsStatus,
} from '../../api/client'
import { useAppStore } from '../../store/useAppStore'
import ObservationsPanel from './ObservationsPanel'
import { useAnalysisJob } from './useAnalysisJob'
import { Btn, Card, Notice, Spinner } from './ui'

export default function AnalyzeRunPrompt({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate()
  const { analysisRunId, setAnalysisRunId } = useAppStore()
  const [status, setStatus] = useState<ResultsStatus | null>(null)
  const [started, setStarted] = useState<AnalysisJob | null>(null)
  const job = useAnalysisJob(started)
  const [analysis, setAnalysis] = useState<RunAnalysis | null>(null)
  const [error, setError] = useState('')
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => { getResultsStatus().then(setStatus).catch(() => setStatus({ available: false })) }, [])

  // An analysis already made for these results (navigating back to this step)
  useEffect(() => {
    if (analysisRunId && !analysis) getResultRun(analysisRunId).then(setAnalysis).catch(() => setAnalysisRunId(null))
  }, [analysisRunId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (job?.status === 'done' && job.run_id) {
      setAnalysisRunId(job.run_id)
      getResultRun(job.run_id).then(setAnalysis).catch(e => setError(String(e instanceof Error ? e.message : e)))
    } else if (job?.status === 'failed') {
      setError(job.error || 'The analysis failed')
    }
  }, [job?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const [starting, setStarting] = useState(false)
  const start = async () => {
    if (starting) return
    setError(''); setAnalysis(null); setStarting(true)
    try {
      setStarted(await analyzeSessionRun(sessionId))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const running = starting || Boolean(job && (job.status === 'queued' || job.status === 'running'))

  if (analysis) {
    return (
      <Card style={{ marginTop: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px', flexWrap: 'wrap' }}>
          <Microscope size={18} style={{ color: 'var(--accent)' }} />
          <div style={{ fontSize: '16px', fontWeight: 600 }}>Results analysis</div>
          <div style={{ flex: 1 }} />
          <Btn small onClick={() => navigate(`/analyze/${analysis.run.run_id}`)}>Open full analysis <ArrowRight size={14} /></Btn>
        </div>
        <ObservationsPanel analysis={analysis} compact />
        {analysis.baseline && !analysis.baseline.found && (
          <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
            No earlier passing run of this script to compare with yet — once a run passes its SLA, later runs are compared with it.
          </div>
        )}
      </Card>
    )
  }

  if (dismissed) {
    return (
      <div style={{ marginTop: '16px' }}>
        <Btn small variant="ghost" onClick={() => setDismissed(false)}><Microscope size={13} /> Analyse this run</Btn>
      </div>
    )
  }

  return (
    <Card style={{ marginTop: '20px', borderColor: 'rgba(var(--accent-rgb),0.35)' }}>
      <div style={{ display: 'flex', gap: '14px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ width: 40, height: 40, borderRadius: 12, background: 'rgba(var(--accent-rgb),0.12)', display: 'grid', placeItems: 'center', flexShrink: 0 }}>
          <Microscope size={20} style={{ color: 'var(--accent)' }} />
        </div>
        <div style={{ flex: 1, minWidth: '240px' }}>
          <div style={{ fontSize: '16px', fontWeight: 600, marginBottom: '4px' }}>Analyse this run?</div>
          <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
            Find the steady state, detect slowdowns, error bursts, saturation and broken correlation, judge the run
            against your SLA and compare it with the last passing run of this script. Takes a few seconds; nothing is
            sent to an AI service unless you ask for an AI summary.
          </div>
        </div>
      </div>

      {status && !status.available && (
        <div style={{ marginTop: '14px' }}>
          <Notice tone="warning">
            Results analysis needs the local ClickHouse database, which is not running. Start it with{' '}
            <code style={{ background: 'rgba(255,255,255,0.08)', padding: '1px 6px', borderRadius: 6 }}>{status.hint || 'docker compose -f deploy/clickhouse/docker-compose.yml up -d'}</code>{' '}
            from the ProjectStack folder, then try again.
          </Notice>
        </div>
      )}

      {running && job && (
        <div style={{ marginTop: '14px', display: 'grid', gap: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}>
          {job.progress.slice(-4).map((p, i, arr) => (
            <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'center', opacity: i === arr.length - 1 ? 1 : 0.6 }}>
              {i === arr.length - 1 ? <Spinner size={13} /> : <Activity size={13} />} {p}
            </div>
          ))}
          {!job.progress.length && <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}><Spinner size={13} /> Starting…</div>}
        </div>
      )}
      {error && <div style={{ marginTop: '14px' }}><Notice tone="danger">{error}</Notice></div>}

      <div style={{ display: 'flex', gap: '10px', marginTop: '16px', flexWrap: 'wrap' }}>
        <Btn onClick={start} disabled={running || status?.available === false}>
          {running ? <><Spinner /> Analysing…</> : <><Microscope size={14} /> Analyse results</>}
        </Btn>
        {!running && <Btn variant="ghost" onClick={() => setDismissed(true)}>Not now</Btn>}
      </div>
    </Card>
  )
}
