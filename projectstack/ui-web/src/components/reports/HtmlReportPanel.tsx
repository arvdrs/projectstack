/**
 * Reports → HTML Report: upload a JMeter result file (JTL) and get JMeter's own HTML
 * dashboard for it, as `jmeter -g results.jtl -o report/` makes it, with the five report
 * graphs as JPEGs. It needs JMeter, not ClickHouse.
 */
import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  BarChart3, CheckCircle, ChevronDown, ChevronRight, Download, ExternalLink, FileBarChart, RefreshCw, Trash2, XCircle,
} from 'lucide-react'
import {
  createHtmlReport, deleteHtmlReport, getHtmlReport, getJmeterInfo, listHtmlReports,
  type HtmlReport, type JmeterInfo,
} from '../../api/client'
import {
  Btn, Card, CardTitle, FieldLabel, Notice, Spinner, Stat, fieldStyle, fmtDate, fmtMs, fmtNum, fmtPct, num, td, th,
} from '../results/ui'
import GraphDownloads from '../results/GraphDownloads'
import { JobProgress, JtlDropzone, LinkBtn, UploadProgress, busy, mb } from './shared'

const GRANULARITY: [string, string][] = [
  ['', '1 minute (JMeter default)'], ['auto', 'Automatic, from the test length'], ['2000', '2 seconds'],
  ['5000', '5 seconds'], ['10000', '10 seconds'], ['30000', '30 seconds'], ['300000', '5 minutes'], ['600000', '10 minutes'],
]
const tps = (v: number | null | undefined) => v == null ? '—' : `${fmtNum(v, v < 10 ? 2 : 1)}/s`

/** A report's graphs; when they could not be captured with the report, why, and a way to try again. */
function ReportGraphs({ report }: { report: HtmlReport }) {
  const [retry, setRetry] = useState(false)
  if (!report.graphs_url || !report.graphs_zip_url) return null
  if (report.graphs_error && !retry) {
    return (
      <div style={{ display: 'grid', gap: '10px', justifyItems: 'start' }}>
        <Notice tone="warning">The graphs were not captured: {report.graphs_error}</Notice>
        <Btn small variant="ghost" onClick={() => setRetry(true)}><RefreshCw size={13} /> Try again</Btn>
      </div>
    )
  }
  return <GraphDownloads base={report.graphs_url} zipUrl={report.graphs_zip_url} />
}

export default function HtmlReportPanel({ file, onFile }: { file: File | null; onFile: (f: File) => void }) {
  const [jmeter, setJmeter] = useState<JmeterInfo | null>(null)
  const [reports, setReports] = useState<HtmlReport[] | null>(null)
  const [title, setTitle] = useState('')
  const [granularity, setGranularity] = useState('')
  const [apdexT, setApdexT] = useState('')
  const [apdexF, setApdexF] = useState('')
  const [labelFilter, setLabelFilter] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [upload, setUpload] = useState<{ sent: number; total: number } | null>(null)
  const [current, setCurrent] = useState<HtmlReport | null>(null)
  const [startedAt, setStartedAt] = useState(0)
  const [error, setError] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [openGraphs, setOpenGraphs] = useState<string | null>(null)

  const refresh = () => {
    listHtmlReports().then(r => setReports(r.reports)).catch(e => setError(String(e.message || e)))
  }
  useEffect(() => {
    refresh()
    getJmeterInfo().then(setJmeter).catch(() => setJmeter(null))
  }, [])

  // Follow the report being generated
  useEffect(() => {
    if (!current || !busy(current)) return
    const timer = setTimeout(async () => {
      try {
        const next = await getHtmlReport(current.id)
        setCurrent(next)
        if (!busy(next)) refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setCurrent(null)
      }
    }, 1000)
    return () => clearTimeout(timer)
  }, [current])

  // Reports still being generated after a reload (or in another tab): check again shortly
  useEffect(() => {
    if (!reports?.some(r => busy(r) && r.id !== current?.id)) return
    const timer = setTimeout(refresh, 3000)
    return () => clearTimeout(timer)
  }, [reports]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (f: File) => { onFile(f); setError(''); setCurrent(null) }

  const submit = async () => {
    if (!file) return
    setError(''); setCurrent(null)
    setUpload({ sent: 0, total: file.size })
    try {
      const started = await createHtmlReport(file, {
        title: title.trim(), apdexSatisfied: apdexT, apdexTolerated: apdexF, granularity, labelFilter: labelFilter.trim(),
      }, (sent, total) => setUpload({ sent, total }))
      setStartedAt(Date.now())
      setCurrent(started)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setUpload(null)
    }
  }

  const remove = async (id: string) => {
    setConfirmDelete(null)
    try {
      await deleteHtmlReport(id)
      if (current?.id === id) setCurrent(null)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const running = Boolean(upload) || busy(current)
  const noJmeter = jmeter != null && !jmeter.found
  const stem = file ? file.name.split('.')[0] : ''
  const elapsed = startedAt ? Math.max(0, Math.round((Date.now() - startedAt) / 1000)) : 0
  const done = current?.status === 'done' ? current : null
  const s = done?.summary

  return (
    <>
      <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: 1.6, maxWidth: '820px', marginBottom: '20px' }}>
        JMeter's standard HTML dashboard: Apdex, statistics per label, errors, and response time and throughput over
        time. Your local JMeter builds it, exactly as{' '}
        <code style={{ background: 'rgba(var(--fg-rgb),0.08)', padding: '1px 6px', borderRadius: 6, fontSize: '13px', whiteSpace: 'nowrap' }}>jmeter -g results.jtl -o report</code>{' '}
        would, so every number is JMeter's own. The five report graphs download as JPEGs.
      </p>

      {noJmeter && (
        <div style={{ marginBottom: '20px' }}>
          <Notice tone="warning">
            JMeter was not found, and it is what builds the report. Set its location in{' '}
            <Link to="/settings" style={{ color: 'inherit', fontWeight: 600 }}>Settings → JMeter path</Link>.
          </Notice>
        </div>
      )}

      <Card style={{ marginBottom: '24px' }}>
        <CardTitle>New HTML report</CardTitle>

        {done && s && (
          <div style={{ border: '1px solid rgba(var(--success-rgb),0.3)', background: 'rgba(var(--success-rgb),0.06)', borderRadius: '16px', padding: '18px 20px', marginBottom: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                <CheckCircle size={20} style={{ color: 'var(--success)', flexShrink: 0 }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>Report ready: {done.title}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    {done.source_file} · {fmtNum(s.labels)} labels{s.start_time ? ` · ${s.start_time} – ${s.end_time}` : ''}
                    {done.seconds != null ? ` · built in ${fmtNum(done.seconds)} s` : ''}
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                {done.view_url && <LinkBtn href={done.view_url} primary><ExternalLink size={14} /> Open report</LinkBtn>}
                {done.download_url && <LinkBtn href={done.download_url} download><Download size={14} /> Download ZIP</LinkBtn>}
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px' }}>
              <Stat label="Samples" value={fmtNum(s.samples)} />
              <Stat label="Error rate" value={fmtPct(s.error_pct)} sub={`${fmtNum(s.errors)} errors`} />
              <Stat label="Average" value={fmtMs(s.mean_ms)} />
              <Stat label="90th pct" value={fmtMs(s.p90_ms)} />
              <Stat label="95th pct" value={fmtMs(s.p95_ms)} />
              <Stat label="Throughput" value={tps(s.throughput)} />
            </div>
            {done.graphs_url && done.graphs_zip_url && (
              <div style={{ marginTop: '18px' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px' }}>Graphs</div>
                <ReportGraphs report={done} />
              </div>
            )}
          </div>
        )}

        <JtlDropzone file={file} onPick={pick} />

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginBottom: '14px' }}>
          <div>
            <FieldLabel hint="shown on the dashboard">Report title</FieldLabel>
            <input style={fieldStyle} value={title} onChange={e => setTitle(e.target.value)} maxLength={200}
              placeholder={stem || 'Checkout load test'} />
          </div>
          <div>
            <FieldLabel>Graph granularity</FieldLabel>
            <select style={fieldStyle} value={granularity} onChange={e => setGranularity(e.target.value)}>
              {GRANULARITY.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
          </div>
          <div>
            <FieldLabel hint="T, ms">Apdex satisfied</FieldLabel>
            <input style={fieldStyle} type="number" min={1} value={apdexT} onChange={e => setApdexT(e.target.value)} placeholder="500" />
          </div>
          <div>
            <FieldLabel hint="F, ms">Apdex tolerated</FieldLabel>
            <input style={fieldStyle} type="number" min={1} value={apdexF} onChange={e => setApdexF(e.target.value)}
              placeholder={apdexT && Number(apdexT) > 0 ? String(4 * Number(apdexT)) : '1500'} />
          </div>
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '14px' }}>
          Empty fields keep JMeter's defaults. With only T set, F is 4 × T, as Apdex defines it.
        </div>

        <button onClick={() => setAdvanced(v => !v)} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '4px', padding: 0, marginBottom: advanced ? '10px' : '18px' }}>
          {advanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Advanced
        </button>
        {advanced && (
          <div style={{ maxWidth: '480px', marginBottom: '18px' }}>
            <FieldLabel hint="Java regular expression; empty keeps every label">Only labels matching</FieldLabel>
            <input style={{ ...fieldStyle, fontFamily: 'var(--font-mono, ui-monospace, monospace)' }} value={labelFilter}
              onChange={e => setLabelFilter(e.target.value)} placeholder="^(Login|Search|Checkout).*" spellCheck={false} />
          </div>
        )}

        {upload && <UploadProgress name={file?.name ?? ''} sent={upload.sent} total={upload.total} />}
        {!upload && current && busy(current) && <JobProgress lines={current.progress} elapsed={elapsed} />}
        {current?.status === 'failed' && <div style={{ marginBottom: '14px' }}><Notice tone="danger">{current.error || 'The report could not be generated'}</Notice></div>}
        {error && <div style={{ marginBottom: '14px' }}><Notice tone="danger">{error}</Notice></div>}

        <Btn onClick={submit} disabled={!file || running || noJmeter}>
          {upload ? <><Spinner /> Uploading…</> : running ? <><Spinner /> Generating…</> : <><FileBarChart size={15} /> Generate report</>}
        </Btn>
        {running && !upload && (
          <span style={{ marginLeft: '12px', fontSize: '12px', color: 'var(--text-muted)' }}>
            JMeter reads the whole file, so a file of several GB takes a few minutes.
          </span>
        )}
      </Card>

      <Card>
        <CardTitle right={<Btn small variant="ghost" onClick={refresh}><RefreshCw size={13} /> Refresh</Btn>}>
          HTML reports
        </CardTitle>
        {reports == null ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '14px' }}>Loading…</div>
        ) : reports.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No reports yet. Upload a JTL above to create the first one.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }}>Report</th>
                <th style={{ ...th, textAlign: 'left' }}>Generated</th>
                <th style={{ ...th, ...num }}>Samples</th>
                <th style={{ ...th, ...num }}>Errors</th>
                <th style={{ ...th, ...num }}>95th pct</th>
                <th style={{ ...th, ...num }}>Throughput</th>
                <th style={{ ...th, textAlign: 'right' }} />
              </tr></thead>
              <tbody>
                {reports.map(r => (
                  <Fragment key={r.id}>
                  <tr>
                    <td style={{ ...td, minWidth: '220px' }}>
                      <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{r.title}</div>
                      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{r.source_file} · {mb(r.source_size)}</div>
                      {r.status === 'failed' && (
                        <div style={{ display: 'flex', gap: '6px', alignItems: 'flex-start', fontSize: '12px', color: 'var(--danger)', marginTop: '4px', maxWidth: '520px' }}>
                          <XCircle size={13} style={{ flexShrink: 0, marginTop: '2px' }} /> {r.error || 'Failed'}
                        </div>
                      )}
                      {busy(r) && (
                        <div style={{ display: 'flex', gap: '6px', alignItems: 'center', fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                          <Spinner size={12} /> Generating…
                        </div>
                      )}
                    </td>
                    <td style={{ ...td, whiteSpace: 'nowrap' }}>{fmtDate(r.created_at)}</td>
                    <td style={{ ...td, ...num }}>{r.summary ? fmtNum(r.summary.samples) : '—'}</td>
                    <td style={{ ...td, ...num }}>{r.summary ? fmtPct(r.summary.error_pct) : '—'}</td>
                    <td style={{ ...td, ...num }}>{fmtMs(r.summary?.p95_ms)}</td>
                    <td style={{ ...td, ...num }}>{tps(r.summary?.throughput)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      <div style={{ display: 'inline-flex', gap: '6px', alignItems: 'center' }}>
                        {confirmDelete === r.id ? (
                          <>
                            <span style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>Delete this report?</span>
                            <Btn small variant="danger" onClick={() => remove(r.id)}>Delete</Btn>
                            <Btn small variant="ghost" onClick={() => setConfirmDelete(null)}>Cancel</Btn>
                          </>
                        ) : (
                          <>
                            {r.graphs_url && (
                              <Btn small variant="ghost" onClick={() => setOpenGraphs(openGraphs === r.id ? null : r.id)}>
                                <BarChart3 size={13} /> Graphs {openGraphs === r.id ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                              </Btn>
                            )}
                            {r.view_url && <LinkBtn href={r.view_url}><ExternalLink size={13} /> Open</LinkBtn>}
                            {r.download_url && <LinkBtn href={r.download_url} download><Download size={13} /> ZIP</LinkBtn>}
                            {!busy(r) && (
                              <button onClick={() => setConfirmDelete(r.id)} title="Delete report" aria-label={`Delete ${r.title}`}
                                style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '6px', display: 'inline-flex', borderRadius: '8px' }}>
                                <Trash2 size={15} />
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                  {openGraphs === r.id && r.graphs_url && (
                    <tr>
                      <td colSpan={7} style={{ ...td, padding: '14px 10px 18px' }}>
                        <ReportGraphs report={r} />
                      </td>
                    </tr>
                  )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
