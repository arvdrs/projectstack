/**
 * Reports → Aggregate Report: upload a JMeter result file (JTL) and get JMeter's Aggregate
 * Report for it as two tables, the APIs and the transactions. Each table downloads as the
 * CSV JMeter's "Save Table Data" writes, and both together as an Excel workbook. Built by
 * ProjectStack with JMeter's own rules (runner/aggregate_report.py); JMeter is not needed.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, CheckCircle, Download, Eye, FileSpreadsheet, RefreshCw, Search, Table2, Trash2, XCircle } from 'lucide-react'
import {
  createAggregateReport, deleteAggregateReport, getAggregateReport, listAggregateReports,
  type AggregateReport, type AggregateRow, type AggregateTable,
} from '../../api/client'
import { Btn, Card, CardTitle, FieldLabel, Notice, Spinner, fieldStyle, fmtDate, fmtNum, num, td, th } from '../results/ui'
import { JobProgress, JtlDropzone, LinkBtn, UploadProgress, busy, mb } from './shared'

type Key = 'label' | 'samples' | 'average' | 'median' | 'p90' | 'p95' | 'p99' | 'min' | 'max' | 'error_rate' | 'throughput' | 'received_kbps' | 'sent_kbps'
const KEYS: Key[] = ['label', 'samples', 'average', 'median', 'p90', 'p95', 'p99', 'min', 'max', 'error_rate', 'throughput', 'received_kbps', 'sent_kbps']
const HEADINGS = ['Label', '# Samples', 'Average', 'Median', '90% Line', '95% Line', '99% Line', 'Min', 'Max', 'Error %', 'Throughput', 'Received KB/sec', 'Sent KB/sec']

/** A cell as JMeter's Aggregate Report table shows it (the server formats the decimals the way JMeter does). */
function shown(r: AggregateRow, k: Key): string {
  switch (k) {
    case 'label': return r.label
    case 'error_rate': return r.shown?.error ?? `${(r.error_rate * 100).toFixed(2)}%`
    case 'throughput': return r.shown?.throughput ?? (r.throughput == null ? '∞/sec' : `${r.throughput.toFixed(1)}/sec`)
    case 'received_kbps': return r.shown?.received ?? r.received_kbps.toFixed(2)
    case 'sent_kbps': return r.shown?.sent ?? r.sent_kbps.toFixed(2)
    default: return String(r[k])
  }
}
const sortValue = (r: AggregateRow, k: Key): number | string =>
  k === 'label' ? r.label.toLowerCase() : k === 'throughput' ? (r.throughput ?? Infinity) : r[k]

function Check({ checked, onChange, children, hint }: {
  checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode; hint: React.ReactNode
}) {
  return (
    <label style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', cursor: 'pointer', maxWidth: '640px' }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
        style={{ marginTop: '3px', accentColor: 'var(--accent)', width: '15px', height: '15px', flexShrink: 0 }} />
      <span>
        <span style={{ fontSize: '14px', color: 'var(--text-primary)', fontWeight: 500 }}>{children}</span>
        <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.5, marginTop: '2px' }}>{hint}</span>
      </span>
    </label>
  )
}

/** One aggregate table: sortable by any column (TOTAL stays last), a label filter, and its CSV. */
function AggregateTableView({ title, note, table, csvUrl, empty, name }: {
  title: string; note: string; table: AggregateTable; csvUrl: string | null; empty: string; name: string
}) {
  const [sort, setSort] = useState<{ key: Key; desc: boolean } | null>(null)
  const [filter, setFilter] = useState('')
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const list = q ? table.rows.filter(r => r.label.toLowerCase().includes(q)) : [...table.rows]
    if (sort) {
      list.sort((a, b) => {
        const x = sortValue(a, sort.key), y = sortValue(b, sort.key)
        const c = x < y ? -1 : x > y ? 1 : 0
        return sort.desc ? -c : c
      })
    }
    return list
  }, [table, sort, filter])

  const clickSort = (key: Key) => setSort(s =>
    !s || s.key !== key ? { key, desc: key !== 'label' } : s.desc === (key !== 'label') ? { key, desc: !s.desc } : null)

  const cell = (r: AggregateRow, k: Key, total: boolean): React.CSSProperties => ({
    ...td, ...(k === 'label' ? { minWidth: '240px', maxWidth: '460px', overflowWrap: 'anywhere' } : num),
    ...(total ? { fontWeight: 600, color: 'var(--text-primary)' } : {}),
    ...(k === 'error_rate' && r.error_rate > 0 ? { color: 'var(--danger)' } : {}),
  })

  return (
    <section aria-label={`${title} aggregate report`} style={{ marginTop: '22px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
            {title} <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '13px' }}>· {table.rows.length} {table.rows.length === 1 ? 'label' : 'labels'}</span>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
            {filter.trim() && table.total ? `Showing ${rows.length} of ${table.rows.length}; TOTAL still covers every label` : note}
          </div>
        </div>
        {table.rows.length > 0 && (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ position: 'relative' }}>
              <Search size={13} style={{ position: 'absolute', left: '11px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
              <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter labels" aria-label={`Filter ${title} labels`}
                style={{ ...fieldStyle, width: '200px', padding: '7px 12px 7px 30px', fontSize: '13px', borderRadius: '999px' }} />
            </div>
            {csvUrl && <LinkBtn href={csvUrl} download label={`Download the ${title} table as CSV`}><Download size={13} /> Download CSV</LinkBtn>}
          </div>
        )}
      </div>
      {table.rows.length === 0 ? (
        <div style={{ fontSize: '14px', color: 'var(--text-muted)', border: '1px dashed var(--border-strong)', borderRadius: '12px', padding: '16px' }}>{empty}</div>
      ) : (
        <div style={{ overflow: 'auto', maxHeight: '70vh', border: '1px solid var(--border)', borderRadius: '12px' }}>
          <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label={`${name}: ${title}`}>
            <thead>
              <tr>
                {KEYS.map((k, i) => {
                  const active = sort?.key === k
                  return (
                    <th key={k} style={{ ...th, ...(k === 'label' ? { textAlign: 'left' } : num), position: 'sticky', top: 0, zIndex: 1, background: 'var(--bg-elevated)', padding: 0 }}
                      aria-sort={active ? (sort!.desc ? 'descending' : 'ascending') : 'none'}>
                      <button onClick={() => clickSort(k)} title={`Sort by ${HEADINGS[i]}`}
                        style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px', padding: '9px 10px', width: '100%', boxSizing: 'border-box',
                          justifyContent: k === 'label' ? 'flex-start' : 'flex-end', color: active ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                        {HEADINGS[i]}{active && (sort!.desc ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.label}>{KEYS.map(k => <td key={k} style={cell(r, k, false)}>{shown(r, k)}</td>)}</tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={KEYS.length} style={{ ...td, color: 'var(--text-muted)' }}>No label contains “{filter}”.</td></tr>
              )}
            </tbody>
            {table.total && (
              <tfoot>
                <tr>
                  {KEYS.map(k => (
                    <td key={k} style={{ ...cell(table.total!, k, true), position: 'sticky', bottom: 0, background: 'var(--bg-elevated)', borderTop: '1px solid var(--border-strong)', borderBottom: 'none' }}>
                      {shown(table.total!, k)}
                    </td>
                  ))}
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </section>
  )
}

/** What was left out or could not be read, and why. */
function Notes({ report }: { report: AggregateReport }) {
  const n = report.notes
  if (!n) return null
  const items: React.ReactNode[] = []
  const sub = n.subresults
  if (sub.labels.length && !sub.included) {
    items.push(<>Left out {sub.labels.length} sub-result {sub.labels.length === 1 ? 'label' : 'labels'} ({fmtNum(sub.samples)} samples): the
      redirects and embedded resources JMeter writes as rows of their own, such as <b>{sub.labels[0]}</b>. Their time is
      already part of their parent request. Turn on “List sub-results as APIs” to show them.</>)
  } else if (sub.labels.length) {
    items.push(<>{sub.labels.length} sub-result {sub.labels.length === 1 ? 'label is' : 'labels are'} listed as APIs (redirects and
      embedded resources, such as <b>{sub.labels[0]}</b>). Their time is also part of their parent request, so the APIs TOTAL counts it twice.</>)
  }
  const empty = n.empty_transactions
  if (empty.samples && !empty.included) {
    items.push(<>Left out {fmtNum(empty.samples)} empty transaction samples (“Number of samples in transaction : 0”, recorded as 0 ms)
      of {empty.labels.slice(0, 3).join(', ')}{empty.labels.length > 3 ? ` and ${empty.labels.length - 3} more` : ''}: the
      controller ran but none of its samplers did. JMeter's HTML report leaves them out too; its Aggregate Report counts them.</>)
  }
  if (n.negative_elapsed) {
    items.push(<>Left out {fmtNum(n.negative_elapsed)} {n.negative_elapsed === 1 ? 'sample' : 'samples'} with a negative elapsed time,
      which JMeter writes when a test stops in the middle of a Transaction Controller.</>)
  }
  if (n.unreadable_rows) {
    items.push(<>Skipped {fmtNum(n.unreadable_rows)} {n.unreadable_rows === 1 ? 'row' : 'rows'} that could not be read, usually a line
      cut off when the test was stopped.</>)
  }
  if (!items.length) return null
  return (
    <div style={{ display: 'grid', gap: '8px', marginTop: '16px' }}>
      {items.map((it, i) => <Notice key={i} tone="info"><span style={{ fontSize: '13px' }}>{it}</span></Notice>)}
    </div>
  )
}

export default function AggregateReportPanel({ file, onFile }: { file: File | null; onFile: (f: File) => void }) {
  const [reports, setReports] = useState<AggregateReport[] | null>(null)
  const [title, setTitle] = useState('')
  const [includeSub, setIncludeSub] = useState(false)
  const [includeEmpty, setIncludeEmpty] = useState(false)
  const [upload, setUpload] = useState<{ sent: number; total: number } | null>(null)
  const [current, setCurrent] = useState<AggregateReport | null>(null)
  const [view, setView] = useState<AggregateReport | null>(null)
  const [loadingView, setLoadingView] = useState<string | null>(null)
  const [startedAt, setStartedAt] = useState(0)
  const [error, setError] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const viewRef = useRef<HTMLDivElement>(null)

  const refresh = () => {
    listAggregateReports().then(r => setReports(r.reports)).catch(e => setError(String(e.message || e)))
  }
  useEffect(refresh, [])

  const open = async (id: string, scroll = true) => {
    setLoadingView(id)
    try {
      const r = await getAggregateReport(id, true)
      setView(r)
      if (scroll) setTimeout(() => viewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingView(null)
    }
  }

  // Follow the report being built; show it when it is ready
  useEffect(() => {
    if (!current || !busy(current)) return
    const timer = setTimeout(async () => {
      try {
        const next = await getAggregateReport(current.id)
        setCurrent(next)
        if (!busy(next)) {
          refresh()
          if (next.status === 'done') open(next.id)
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setCurrent(null)
      }
    }, 700)
    return () => clearTimeout(timer)
  }, [current]) // eslint-disable-line react-hooks/exhaustive-deps

  // Reports still being built after a reload (or in another tab): check again shortly
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
      const started = await createAggregateReport(file, {
        title: title.trim(), includeSubresults: includeSub, includeEmptyTransactions: includeEmpty,
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
      await deleteAggregateReport(id)
      if (current?.id === id) setCurrent(null)
      if (view?.id === id) setView(null)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const running = Boolean(upload) || busy(current)
  const stem = file ? file.name.split('.')[0] : ''
  const elapsed = startedAt ? Math.max(0, Math.round((Date.now() - startedAt) / 1000)) : 0
  const s = view?.summary

  return (
    <>
      <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: 1.6, maxWidth: '820px', marginBottom: '20px' }}>
        JMeter's Aggregate Report in two tables: the <b>APIs</b> (every sampler) and the <b>transactions</b> (Transaction
        Controllers). Each number is computed the way JMeter's Aggregate Report listener computes it. Each table
        downloads as the CSV its “Save Table Data” writes, and both download together as an Excel workbook.
      </p>

      <Card style={{ marginBottom: '24px' }}>
        <CardTitle>New aggregate report</CardTitle>
        <JtlDropzone file={file} onPick={pick} />

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 420px)', gap: '16px', marginBottom: '18px' }}>
          <div>
            <FieldLabel hint="used in the downloaded file names">Report title</FieldLabel>
            <input style={fieldStyle} value={title} onChange={e => setTitle(e.target.value)} maxLength={200}
              placeholder={stem || 'Checkout load test'} />
          </div>
        </div>
        <div style={{ display: 'grid', gap: '12px', marginBottom: '20px' }}>
          <Check checked={includeSub} onChange={setIncludeSub}
            hint={<>JMeter writes each redirect and embedded resource of a request as a row of its own (Login-0, Login-1 …), and their
              time is already part of the request. Left out by default.</>}>
            List sub-results as APIs
          </Check>
          <Check checked={includeEmpty} onChange={setIncludeEmpty}
            hint={<>Transaction Controller samples where none of the controller's samplers ran, recorded as 0 ms. JMeter's Aggregate
              Report counts them and its HTML report leaves them out. Left out by default.</>}>
            Count empty transactions
          </Check>
        </div>

        {upload && <UploadProgress name={file?.name ?? ''} sent={upload.sent} total={upload.total} />}
        {!upload && current && busy(current) && <JobProgress lines={current.progress} elapsed={elapsed} />}
        {current?.status === 'failed' && <div style={{ marginBottom: '14px' }}><Notice tone="danger">{current.error || 'The report could not be built'}</Notice></div>}
        {error && <div style={{ marginBottom: '14px' }}><Notice tone="danger">{error}</Notice></div>}

        <Btn onClick={submit} disabled={!file || running}>
          {upload ? <><Spinner /> Uploading…</> : running ? <><Spinner /> Building…</> : <><Table2 size={15} /> Create aggregate report</>}
        </Btn>
      </Card>

      {view && view.tables && (
        <div ref={viewRef} style={{ scrollMarginTop: '90px' }}>
          <Card style={{ marginBottom: '24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                <CheckCircle size={20} style={{ color: 'var(--success)', flexShrink: 0 }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>Aggregate report: {view.title}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', overflowWrap: 'anywhere' }}>
                    {view.source_file}{s ? ` · ${fmtNum(s.samples)} samples · ${new Date(s.start_ms).toLocaleString()} – ${new Date(s.end_ms).toLocaleString()}` : ''}
                    {view.seconds != null ? ` · built in ${fmtNum(view.seconds, view.seconds < 10 ? 1 : 0)} s` : ''}
                  </div>
                </div>
              </div>
              {view.downloads?.xlsx && (
                <LinkBtn href={view.downloads.xlsx} download primary label="Download both tables as an Excel workbook">
                  <FileSpreadsheet size={14} /> Download both (Excel)
                </LinkBtn>
              )}
            </div>
            <Notes report={view} />
            <AggregateTableView title="APIs" name={view.title} table={view.tables.apis} csvUrl={view.downloads?.apis_csv ?? null}
              note="Every sampler (HTTP requests and any other sampler), one row per label"
              empty="No API samples in this file." />
            <AggregateTableView title="Transactions" name={view.title} table={view.tables.transactions} csvUrl={view.downloads?.transactions_csv ?? null}
              note="Transaction Controllers, one row per controller"
              empty="This file has no Transaction Controller samples, so there is no transactions table." />
          </Card>
        </div>
      )}

      <Card>
        <CardTitle right={<Btn small variant="ghost" onClick={refresh}><RefreshCw size={13} /> Refresh</Btn>}>
          Aggregate reports
        </CardTitle>
        {reports == null ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '14px' }}>Loading…</div>
        ) : reports.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No aggregate reports yet. Upload a JTL above to create the first one.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={{ ...th, textAlign: 'left' }}>Report</th>
                <th style={{ ...th, textAlign: 'left' }}>Created</th>
                <th style={{ ...th, ...num }}>APIs</th>
                <th style={{ ...th, ...num }}>Transactions</th>
                <th style={{ ...th, ...num }}>Samples</th>
                <th style={{ ...th, textAlign: 'right' }} />
              </tr></thead>
              <tbody>
                {reports.map(r => (
                  <tr key={r.id} style={view?.id === r.id ? { background: 'rgba(var(--accent-rgb),0.05)' } : undefined}>
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
                          <Spinner size={12} /> Building…
                        </div>
                      )}
                    </td>
                    <td style={{ ...td, whiteSpace: 'nowrap' }}>{fmtDate(r.created_at)}</td>
                    <td style={{ ...td, ...num }}>{r.counts ? fmtNum(r.counts.apis) : '—'}</td>
                    <td style={{ ...td, ...num }}>{r.counts ? fmtNum(r.counts.transactions) : '—'}</td>
                    <td style={{ ...td, ...num }}>{r.summary ? fmtNum(r.summary.samples) : '—'}</td>
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
                            {r.status === 'done' && (
                              <Btn small variant="ghost" onClick={() => open(r.id)} disabled={loadingView === r.id}>
                                {loadingView === r.id ? <Spinner size={13} /> : <Eye size={13} />} View
                              </Btn>
                            )}
                            {r.downloads?.xlsx && <LinkBtn href={r.downloads.xlsx} download label={`Download ${r.title} as an Excel workbook`}><FileSpreadsheet size={13} /> Excel</LinkBtn>}
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
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
