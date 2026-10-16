/**
 * Results analysis: upload a JMeter result file (JTL) and get it analysed, or open
 * an earlier analysis. A standalone feature next to the HAR → JMX workflow.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Activity, ChevronDown, ChevronRight, FileUp, GitCompare, Microscope, RefreshCw } from 'lucide-react'
import {
  analyzeJtl, getResultsStatus, listResultRuns, type AnalysisJob, type ResultsRunRow, type ResultsStatus,
} from '../api/client'
import { useAnalysisJob } from '../components/results/useAnalysisJob'
import {
  Btn, Card, CardTitle, FieldLabel, Notice, Spinner, VerdictBadge, fieldStyle, fmtDate, fmtMs, fmtNum, fmtPct,
  num, td, th,
} from '../components/results/ui'

const ACCEPT = '.jtl,.csv,.xml,.gz'
const fade = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0, transition: { duration: 0.3 } } }

export default function ResultsPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<ResultsStatus | null>(null)
  const [runs, setRuns] = useState<ResultsRunRow[] | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [scenario, setScenario] = useState('')
  const [tag, setTag] = useState('')
  const [metric, setMetric] = useState('p95')
  const [rtMs, setRtMs] = useState('')
  const [errPct, setErrPct] = useState('')
  const [tps, setTps] = useState('')
  const [baseline, setBaseline] = useState('')
  const [steady, setSteady] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [started, setStarted] = useState<AnalysisJob | null>(null)
  const job = useAnalysisJob(started)
  const [upload, setUpload] = useState<{ sent: number; total: number } | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  const refresh = () => {
    getResultsStatus().then(s => {
      setStatus(s)
      if (s.available) listResultRuns().then(r => setRuns(r.runs)).catch(e => setError(String(e.message || e)))
    }).catch(() => setStatus({ available: false }))
  }
  useEffect(refresh, [])

  useEffect(() => {
    if (job?.status === 'done' && job.run_id) navigate(`/analyze/${job.run_id}`)
    if (job?.status === 'failed') setError(job.error || 'The analysis failed')
  }, [job?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (f: File | undefined | null) => {
    if (!f) return
    setFile(f); setError('')
    if (!scenario) setScenario(f.name.split('.')[0])
  }

  const submit = async () => {
    if (!file) return
    setError('')
    const sla = (rtMs || errPct || tps) ? {
      response_time_metric: metric as 'p90' | 'p95' | 'p99' | 'avg',
      response_time_ms: rtMs ? Number(rtMs) : null,
      error_rate_pct: errPct ? Number(errPct) : null,
      throughput_rps: tps ? Number(tps) : null,
      transaction_targets: {},
    } : null
    setUpload({ sent: 0, total: file.size })
    try {
      setStarted(await analyzeJtl(file, { scenario: scenario.trim(), tag: tag.trim(), sla, steady: steady.trim(), baseline },
        (sent, total) => setUpload({ sent, total })))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setUpload(null)
    }
  }

  // Busy from the moment Analyse is clicked: uploading, then analysing (no second submission meanwhile)
  const running = Boolean(upload) || Boolean(job && (job.status === 'queued' || job.status === 'running'))
  const mb = (n: number) => n >= 1_073_741_824 ? `${(n / 1_073_741_824).toFixed(2)} GB` : `${(n / 1_048_576).toFixed(0)} MB`
  const scenarios = Array.from(new Set((runs ?? []).map(r => r.scenario))).sort()
  const sameScenario = (runs ?? []).filter(r => r.scenario === scenario.trim())

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '40px 24px 64px' }}>
      <motion.div {...fade}>
        <h1 style={{ fontSize: '34px', fontWeight: 600, lineHeight: 1.15, display: 'flex', alignItems: 'center', gap: '14px', marginBottom: '10px' }}>
          <span style={{ width: '4px', height: '30px', borderRadius: '4px', background: 'var(--accent-gradient)', flexShrink: 0 }} />
          Analyse Test Results
        </h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: '15px', lineHeight: 1.6, maxWidth: '760px', marginBottom: '28px' }}>
          Upload a JMeter result file (JTL) from any test. ProjectStack finds the steady state, computes exact
          percentiles per transaction, detects slowdowns, error bursts, saturation, load-generator and correlation
          problems, gives a verdict against your SLA and writes the reports. Raw results stay on this machine.
        </p>

        {status && !status.available && (
          <div style={{ marginBottom: '20px' }}>
            <Notice tone="warning">
              Results analysis stores runs in a local ClickHouse database, which is not running.{' '}
              Start it from the ProjectStack folder with{' '}
              <code style={{ background: 'rgba(255,255,255,0.08)', padding: '1px 6px', borderRadius: 6 }}>{status.hint || 'docker compose -f deploy/clickhouse/docker-compose.yml up -d'}</code>
              , then <button onClick={refresh} style={{ background: 'none', border: 'none', color: 'inherit', textDecoration: 'underline', cursor: 'pointer', font: 'inherit', padding: 0 }}>check again</button>.
            </Notice>
          </div>
        )}

        <Card style={{ marginBottom: '24px' }}>
          <CardTitle>New analysis</CardTitle>
          <div
            onClick={() => inputRef.current?.click()}
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={e => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0]) }}
            role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
            style={{
              border: `1.5px dashed ${dragging ? 'var(--accent)' : 'var(--border-strong)'}`, borderRadius: '16px',
              padding: '28px', textAlign: 'center', cursor: 'pointer', marginBottom: '20px',
              background: dragging ? 'rgba(var(--accent-rgb),0.06)' : 'rgba(255,255,255,0.02)',
            }}>
            <FileUp size={26} style={{ color: file ? 'var(--accent)' : 'var(--text-muted)', margin: '0 auto 8px', display: 'block' }} />
            {file ? (
              <div style={{ fontSize: '15px', fontWeight: 500 }}>{file.name} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>· {(file.size / 1_048_576).toFixed(1)} MB</span></div>
            ) : (
              <div style={{ fontSize: '15px', fontWeight: 500 }}>Drop a JTL file here, or click to choose</div>
            )}
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>CSV or XML results, optionally .gz — files of several GB are fine</div>
            <input ref={inputRef} type="file" accept={ACCEPT} style={{ display: 'none' }} onChange={e => pick(e.target.files?.[0])} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginBottom: '18px' }}>
            <div>
              <FieldLabel hint="groups runs for comparison">Scenario</FieldLabel>
              <input style={fieldStyle} value={scenario} onChange={e => setScenario(e.target.value)} placeholder="checkout" list="ps-scenarios" />
              <datalist id="ps-scenarios">{scenarios.map(s => <option key={s} value={s} />)}</datalist>
            </div>
            <div>
              <FieldLabel hint="optional">Build or tag</FieldLabel>
              <input style={fieldStyle} value={tag} onChange={e => setTag(e.target.value)} placeholder="build-142" />
            </div>
            <div>
              <FieldLabel>Compare with</FieldLabel>
              <select style={fieldStyle} value={baseline} onChange={e => setBaseline(e.target.value)}>
                <option value="">Don't compare</option>
                <option value="last-green">Last passing run of this scenario</option>
                {sameScenario.slice(0, 15).map(r => (
                  <option key={r.run_id} value={r.run_id}>{r.tag || r.run_id} · {fmtDate(r.created_at)}</option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>
            SLA <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>· optional; without targets the run is described, not judged</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '16px', marginBottom: '14px' }}>
            <div>
              <FieldLabel>Response time</FieldLabel>
              <div style={{ display: 'flex', gap: '6px' }}>
                <select style={{ ...fieldStyle, width: '84px', flexShrink: 0 }} value={metric} onChange={e => setMetric(e.target.value)}>
                  {['p90', 'p95', 'p99', 'avg'].map(m => <option key={m} value={m}>{m}</option>)}
                </select>
                <input style={fieldStyle} type="number" min={1} value={rtMs} onChange={e => setRtMs(e.target.value)} placeholder="ms" />
              </div>
            </div>
            <div>
              <FieldLabel>Max error rate (%)</FieldLabel>
              <input style={fieldStyle} type="number" min={0} step="0.1" value={errPct} onChange={e => setErrPct(e.target.value)} placeholder="1" />
            </div>
            <div>
              <FieldLabel>Min throughput (req/s)</FieldLabel>
              <input style={fieldStyle} type="number" min={0} value={tps} onChange={e => setTps(e.target.value)} placeholder="—" />
            </div>
          </div>

          <button onClick={() => setAdvanced(v => !v)} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '4px', padding: 0, marginBottom: advanced ? '10px' : '18px' }}>
            {advanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Advanced
          </button>
          {advanced && (
            <div style={{ maxWidth: '360px', marginBottom: '18px' }}>
              <FieldLabel hint="default: detected automatically">Steady-state window</FieldLabel>
              <input style={fieldStyle} value={steady} onChange={e => setSteady(e.target.value)} placeholder="5m:35m" />
            </div>
          )}

          {upload && (
            <div style={{ marginBottom: '14px' }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                <Spinner size={13} /> Uploading {file?.name} — {mb(upload.sent)} of {mb(upload.total)}
                ({upload.total ? Math.floor((upload.sent / upload.total) * 100) : 0}%)
              </div>
              <div style={{ height: '6px', borderRadius: '6px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${upload.total ? (upload.sent / upload.total) * 100 : 0}%`, background: 'var(--series-1)', transition: 'width 0.3s ease' }} />
              </div>
            </div>
          )}
          {!upload && job && (job.status === 'queued' || job.status === 'running') && (
            <div style={{ display: 'grid', gap: '4px', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
              {job.progress.slice(-5).map((p, i, arr) => (
                <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'center', opacity: i === arr.length - 1 ? 1 : 0.6 }}>
                  {i === arr.length - 1 ? <Spinner size={13} /> : <Activity size={13} />} {p}
                </div>
              ))}
            </div>
          )}
          {error && <div style={{ marginBottom: '14px' }}><Notice tone="danger">{error}</Notice></div>}

          <Btn onClick={submit} disabled={!file || running || status?.available === false}>
            {upload ? <><Spinner /> Uploading…</> : running ? <><Spinner /> Analysing…</> : <><Microscope size={15} /> Analyse</>}
          </Btn>
          {running && !upload && (
            <span style={{ marginLeft: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
              Large files take a while — about 10 seconds per million samples. The results open when it finishes.
            </span>
          )}
        </Card>

        <Card>
          <CardTitle right={
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              {selected.length > 0 && <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{selected.length === 2 ? '2 selected' : 'select one more to compare'}</span>}
              <Btn small onClick={() => {
                const [x, y] = [...selected].sort()
                navigate(`/analyze/compare?base=${encodeURIComponent(x)}&current=${encodeURIComponent(y)}`)
              }} disabled={selected.length !== 2}><GitCompare size={13} /> Compare</Btn>
              <Btn small variant="ghost" onClick={refresh}><RefreshCw size={13} /> Refresh</Btn>
            </div>
          }>
            Analysed runs
          </CardTitle>
          {runs == null ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '14px' }}>{status?.available === false ? 'Start the database to see analysed runs.' : 'Loading…'}</div>
          ) : runs.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No runs analysed yet. Upload a JTL above, or analyse a test run at the end of the Run Test step.</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr>
                  <th style={{ ...th, width: '28px' }} />
                  <th style={{ ...th, textAlign: 'left' }}>Scenario</th>
                  <th style={{ ...th, textAlign: 'left' }}>Build / tag</th>
                  <th style={{ ...th, textAlign: 'left' }}>Analysed</th>
                  <th style={{ ...th, ...num }}>Users</th>
                  <th style={{ ...th, ...num }}>Samples</th>
                  <th style={{ ...th, ...num }}>p95</th>
                  <th style={{ ...th, ...num }}>Errors</th>
                  <th style={{ ...th, textAlign: 'left' }}>Verdict</th>
                </tr></thead>
                <tbody>
                  {runs.map(r => (
                    <tr key={r.run_id} onClick={() => navigate(`/analyze/${r.run_id}`)} style={{ cursor: 'pointer' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                      <td style={td} onClick={e => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`Select ${r.scenario} ${r.tag} for comparison`} checked={selected.includes(r.run_id)}
                          onChange={e => setSelected(prev => e.target.checked ? [...prev.filter(x => x !== r.run_id), r.run_id].slice(-2) : prev.filter(x => x !== r.run_id))} />
                      </td>
                      <td style={{ ...td, color: 'var(--text-primary)', fontWeight: 500 }}>{r.scenario}</td>
                      <td style={td}>{r.tag || <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                      <td style={{ ...td, whiteSpace: 'nowrap' }}>{fmtDate(r.created_at)}</td>
                      <td style={{ ...td, ...num }}>{fmtNum(r.peak_threads)}</td>
                      <td style={{ ...td, ...num }}>{fmtNum(r.samples)}</td>
                      <td style={{ ...td, ...num }}>{fmtMs(r.summary?.p95_ms)}</td>
                      <td style={{ ...td, ...num }}>{fmtPct(r.summary?.error_pct)}</td>
                      <td style={td}><VerdictBadge verdict={r.verdict} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </motion.div>
      <style>{'@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }'}</style>
    </div>
  )
}
