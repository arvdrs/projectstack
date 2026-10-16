/**
 * Server metrics for a run, read from AWS CloudWatch or Azure Monitor for the run's
 * window: pick the resources (found in the account, or added by id), fetch, and see
 * them under the test's own response times on one time axis. Server findings join
 * the run's findings list.
 */
import { useEffect, useMemo, useState } from 'react'
import { Cloud, Plus, RefreshCw, Search, Server, Trash2 } from 'lucide-react'
import {
  discoverCloud, fetchServerMetrics, getCloudCatalog, getCloudTargets, getServerMetrics,
  type CloudCatalog, type CloudProvider, type CloudResource, type RunAnalysis, type ServerMetrics,
} from '../../api/client'
import MultiTimeline, { type Panel } from './MultiTimeline'
import { Btn, Card, CardTitle, Notice, SeverityBadge, Spinner, fieldStyle, fmtDate, fmtMs, fmtNum, fmtOffset } from './ui'

const COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)', 'var(--series-6)', 'var(--series-7)', 'var(--series-8)']
const ROLES = ['app', 'database', 'cache', 'load balancer', 'other']
const GROUP_TITLES: Record<string, string> = {
  cpu_pct: 'CPU (%)', limit_pct: 'Share of service limits (%)', mem_pct: 'Memory used (%)', mem_free: 'Available memory',
  mem_bytes: 'Memory (working set)', credits: 'CPU credits', connections: 'Connections', latency: 'Server latency (ms)',
  errors: 'Errors', requests: 'Requests', throttles: 'Throttled calls', network: 'Network', iops: 'Disk / I/O operations',
  queue: 'Queues', capacity: 'Capacity (instances, tasks, replicas)', evictions: 'Cache evictions', cpu_cores: 'CPU (cores)',
  health_pct: 'Backend availability (%)',
}
const GROUP_ORDER = Object.keys(GROUP_TITLES)

function fmtValue(v: number, unit: string): string {
  if (unit === '%') return `${v.toFixed(v < 10 ? 1 : 0)}%`
  if (unit === 'ms') return fmtMs(v)
  if (unit === 'bytes' || unit === 'bytes/s') {
    const sizes: [number, string][] = [[2 ** 30, 'GB'], [2 ** 20, 'MB'], [2 ** 10, 'KB']]
    const hit = sizes.find(([s]) => Math.abs(v) >= s)
    const text = hit ? `${(v / hit[0]).toFixed(1)} ${hit[1]}` : `${fmtNum(v)} B`
    return unit === 'bytes/s' ? `${text}/s` : text
  }
  if (unit === '/min' || unit === '/s') return `${fmtNum(v, 1)}${unit}`
  if (unit === 'cores') return `${fmtNum(v, 2)} cores`
  return fmtNum(v, Math.abs(v) < 10 ? 2 : 0)
}

export default function ServerMetricsCard({ analysis, onAttached }: { analysis: RunAnalysis; onAttached: () => void }) {
  const runId = analysis.run.run_id
  const [data, setData] = useState<ServerMetrics | null>(null)
  const [picking, setPicking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const load = () => getServerMetrics(runId).then(setData).catch(e => setError(String(e instanceof Error ? e.message : e)))
  useEffect(() => { setData(null); load() }, [runId]) // eslint-disable-line react-hooks/exhaustive-deps

  const fetchNow = async (resources: CloudResource[]) => {
    setBusy(true); setError(''); setNote('')
    try {
      const res = await fetchServerMetrics(runId, resources)
      setPicking(false)
      setNote(`${res.series} metric series fetched; ${res.findings} server finding(s).` +
        (res.missing.length ? ` ${res.missing.length} metric(s) had no data.` : '') +
        (res.warnings.length ? ` ${res.warnings.join(' ')}` : ''))
      await load()
      onAttached()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card style={{ marginBottom: '20px' }}>
      <CardTitle right={data?.available && !picking ? (
        <div style={{ display: 'flex', gap: '8px' }}>
          <Btn small variant="ghost" onClick={() => fetchNow(data.resources)} disabled={busy}>{busy ? <Spinner size={13} /> : <RefreshCw size={13} />} Fetch again</Btn>
          <Btn small variant="ghost" onClick={() => setPicking(true)}><Server size={13} /> Change resources</Btn>
        </div>
      ) : undefined}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}><Cloud size={16} /> Server metrics</span>
      </CardTitle>

      {error && <div style={{ marginBottom: '12px' }}><Notice tone="danger">{error}</Notice></div>}
      {note && <div style={{ marginBottom: '12px' }}><Notice tone="info">{note}</Notice></div>}

      {data && !data.available && !picking && (
        <div>
          <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: '14px' }}>
            Read CPU, memory, connections, latency, errors and capacity of the servers behind this test from
            <strong style={{ color: 'var(--text-primary)' }}> AWS CloudWatch</strong> or <strong style={{ color: 'var(--text-primary)' }}>Azure Monitor</strong>,
            for this run's time window, and see them next to the test's response times. Detected problems (CPU at its limit,
            exhausted CPU credits, memory pressure, connection limits, 5xx from the load balancer…) join the findings, linked
            to the slowdowns they coincide with. Read-only; sign-in is set up in Settings.
          </div>
          <Btn onClick={() => setPicking(true)}><Server size={14} /> Choose resources</Btn>
        </div>
      )}

      {picking && (
        <ResourcePicker scenario={analysis.run.scenario} initial={data?.resources ?? []} busy={busy}
          onCancel={() => setPicking(false)} onFetch={fetchNow} />
      )}

      {data?.available && !picking && <ServerCharts data={data} analysis={analysis} />}
      {!data && !error && <div style={{ color: 'var(--text-muted)', fontSize: '13px', display: 'flex', gap: 8 }}><Spinner size={13} /> Loading…</div>}
    </Card>
  )
}

function ServerCharts({ data, analysis }: { data: ServerMetrics; analysis: RunAnalysis }) {
  const panels = useMemo(() => {
    const out: Panel[] = [{
      title: 'Test p95 response time (ms)', fmt: v => fmtMs(v), floor: 10,
      series: [{ name: 'test p95', color: 'var(--text-secondary)', points: analysis.timeline.points.map(p => ({ t: p.t_offset_s + analysis.timeline.bucket_s / 2, v: p.p95_ms })) }],
    }]
    const groups = new Map<string, typeof data.series>()
    for (const s of data.series) {
      const key = `${s.kind}|${s.unit}`
      groups.set(key, [...(groups.get(key) ?? []), s])
    }
    const keys = [...groups.keys()].sort((a, b) => GROUP_ORDER.indexOf(a.split('|')[0]) - GROUP_ORDER.indexOf(b.split('|')[0]))
    for (const key of keys) {
      const list = groups.get(key)!.slice(0, COLORS.length)
      const [kind, unit] = key.split('|')
      const manyMetrics = new Set(list.map(s => s.metric)).size > 1
      const title = `${GROUP_TITLES[kind] ?? kind}${['errors', 'requests', 'throttles', 'evictions'].includes(kind) && unit ? ` (${unit === '/min' ? 'per minute' : unit === '/s' ? 'per second' : unit})` : ''}`
      out.push({
        title, subtitle: list.length === 1 ? list[0].resource : undefined, fmt: v => fmtValue(v, unit), floor: unit === '%' ? 10 : 1,
        series: list.map((s, i) => ({
          name: manyMetrics ? `${s.resource} · ${s.label}` : s.resource, color: COLORS[i],
          points: s.points.map(p => ({ t: p.t_offset_s, v: p.value })),
        })),
      })
    }
    return out
  }, [data, analysis])

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
        {data.resources.length} resource(s) · fetched {fmtDate(data.fetched_at)} · {(data.summary?.period_s ?? 60) / 60}-minute values
        {data.warnings.length > 0 && <> · {data.warnings.join(' ')}</>}
      </div>
      <MultiTimeline panels={panels} durationS={data.duration_s}
        band={data.steady ? { from: data.steady.start_offset_s, to: data.steady.end_offset_s, label: 'Steady state (statistics window)' } : undefined} />

      {data.findings.length > 0 && (
        <div>
          <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>Server findings</div>
          {data.findings.map(f => (
            <div key={f.n} style={{ display: 'flex', gap: '10px', alignItems: 'baseline', fontSize: '13.5px', lineHeight: 1.6 }}>
              <span style={{ color: 'var(--text-muted)' }}>#{f.n}</span><SeverityBadge severity={f.severity} />
              <span>{f.title}{f.start_offset_s != null && <span style={{ color: 'var(--text-muted)' }}> · {fmtOffset(f.start_offset_s)}</span>}</span>
            </div>
          ))}
        </div>
      )}

      {data.summary && (
        <details>
          <summary style={{ cursor: 'pointer', fontSize: '13px', color: 'var(--text-secondary)' }}>Averages and peaks per resource</summary>
          <div style={{ overflowX: 'auto', marginTop: '8px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead><tr>{['Resource', 'Metric', 'Steady-state average', 'Peak', 'Peak at'].map(h => (
                <th key={h} style={{ padding: '6px 10px', color: 'var(--text-muted)', fontWeight: 500, textAlign: h.includes('average') || h === 'Peak' ? 'right' : 'left', borderBottom: '1px solid var(--border)' }}>{h}</th>
              ))}</tr></thead>
              <tbody>{data.summary.resources.flatMap(r => r.metrics.map(m => (
                <tr key={`${r.name}${m.metric}`}>
                  <td style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', color: 'var(--text-primary)' }}>{r.name}</td>
                  <td style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>{m.label}</td>
                  <td style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{m.steady_avg == null ? '—' : fmtValue(m.steady_avg, m.unit)}</td>
                  <td style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtValue(m.max, m.unit)}</td>
                  <td style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>{fmtOffset(m.max_offset_s)}</td>
                </tr>
              )))}</tbody>
            </table>
          </div>
        </details>
      )}
      {data.missing.length > 0 && (
        <details>
          <summary style={{ cursor: 'pointer', fontSize: '13px', color: 'var(--text-secondary)' }}>{data.missing.length} metric(s) without data</summary>
          <ul style={{ paddingLeft: '20px', marginTop: '6px', fontSize: '12.5px', color: 'var(--text-muted)', display: 'grid', gap: '2px' }}>
            {data.missing.map((m, i) => <li key={i}>{m.resource} · {m.metric}: {m.why}</li>)}
          </ul>
        </details>
      )}
    </div>
  )
}

function ResourcePicker({ scenario, initial, busy, onCancel, onFetch }: {
  scenario: string; initial: CloudResource[]; busy: boolean; onCancel: () => void; onFetch: (r: CloudResource[]) => void
}) {
  const [catalog, setCatalog] = useState<CloudCatalog | null>(null)
  const [provider, setProvider] = useState<CloudProvider>(initial[0]?.provider ?? 'aws')
  const [types, setTypes] = useState<string[]>([])
  const [region, setRegion] = useState('')
  const [found, setFound] = useState<CloudResource[] | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [selected, setSelected] = useState<CloudResource[]>(initial)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const [manual, setManual] = useState({ type: '', id: '', name: '' })

  useEffect(() => {
    getCloudCatalog().then(setCatalog).catch(() => {})
    if (!initial.length) getCloudTargets(scenario).then(r => { if (r.resources.length) setSelected(r.resources) }).catch(() => {})
  }, [scenario]) // eslint-disable-line react-hooks/exhaustive-deps

  const typesFor = (catalog?.types ?? []).filter(t => t.provider === provider)
  const byCategory = Object.entries(catalog?.categories ?? {}).map(([cat, title]) => ({ cat, title, types: typesFor.filter(t => t.category === cat) }))
  const isSelected = (r: CloudResource) => selected.some(s => s.provider === r.provider && s.id === r.id)
  const toggle = (r: CloudResource) => setSelected(prev => isSelected(r) ? prev.filter(s => !(s.provider === r.provider && s.id === r.id)) : [...prev, r])
  const titleOf = (r: CloudResource) => catalog?.types.find(t => t.provider === r.provider && t.type === r.type)?.title ?? r.type

  const search = async () => {
    setSearching(true); setError(''); setFound(null)
    try {
      const res = await discoverCloud(provider, types, region.trim())
      setFound(res.resources); setWarnings(res.warnings)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSearching(false)
    }
  }

  const chip = (active: boolean): React.CSSProperties => ({
    padding: '5px 12px', borderRadius: '999px', fontSize: '12.5px', cursor: 'pointer', whiteSpace: 'nowrap',
    border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`, color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
    background: active ? 'rgba(var(--accent-rgb),0.12)' : 'transparent',
  })

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div style={{ display: 'flex', gap: '6px' }}>
        {(['aws', 'azure'] as const).map(p => (
          <button key={p} onClick={() => { setProvider(p); setTypes([]); setFound(null) }} style={chip(provider === p)}>{p === 'aws' ? 'AWS CloudWatch' : 'Azure Monitor'}</button>
        ))}
      </div>

      <div>
        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>1 · Find resources in your account</div>
        <div style={{ display: 'grid', gap: '8px', marginBottom: '10px' }}>
          {byCategory.map(c => c.types.length > 0 && (
            <div key={c.cat} style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '190px' }}>{c.title}</span>
              {c.types.map(t => (
                <button key={t.type} style={chip(types.includes(t.type))}
                  onClick={() => setTypes(prev => prev.includes(t.type) ? prev.filter(x => x !== t.type) : [...prev, t.type])}>{t.title}</button>
              ))}
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <input style={{ ...fieldStyle, maxWidth: '240px' }} value={region} onChange={e => setRegion(e.target.value)}
            placeholder={provider === 'aws' ? 'Region (default from Settings)' : 'Location filter (optional)'} />
          <Btn small onClick={search} disabled={searching}>{searching ? <Spinner size={13} /> : <Search size={13} />} Find {types.length ? 'these' : 'all'} resources</Btn>
        </div>
        {error && <div style={{ marginTop: '10px' }}><Notice tone="danger">{error}</Notice></div>}
        {warnings.map((w, i) => <div key={i} style={{ marginTop: '8px' }}><Notice tone="warning">{w}</Notice></div>)}
        {found && (
          found.length === 0 ? <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '10px' }}>No resources found{types.length ? ' of these types' : ''}.</div> : (
            <div style={{ marginTop: '10px', maxHeight: '260px', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: '12px' }}>
              {found.map(r => (
                <label key={`${r.provider}${r.id}`} style={{ display: 'flex', gap: '10px', alignItems: 'center', padding: '8px 12px', borderBottom: '1px solid var(--border)', cursor: 'pointer', fontSize: '13px' }}>
                  <input type="checkbox" checked={isSelected(r)} onChange={() => toggle(r)} />
                  <span style={{ color: 'var(--text-primary)', flex: 1, wordBreak: 'break-word' }}>{r.name}</span>
                  <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{titleOf(r)}{r.region ? ` · ${r.region}` : ''}</span>
                </label>
              ))}
            </div>
          )
        )}
      </div>

      <div>
        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>…or add one by its ID</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(150px, 220px) minmax(0, 1fr) minmax(120px, 200px) auto', gap: '8px' }}>
          <select style={fieldStyle} value={manual.type} onChange={e => setManual(m => ({ ...m, type: e.target.value }))}>
            <option value="">Type…</option>
            {typesFor.map(t => <option key={t.type} value={t.type}>{t.title}</option>)}
          </select>
          <input style={fieldStyle} value={manual.id} onChange={e => setManual(m => ({ ...m, id: e.target.value }))}
            placeholder={provider === 'aws' ? 'i-0abc… · db-name · app/my-alb/123 · cluster/service' : '/subscriptions/…/resourceGroups/…/providers/…'} />
          <input style={fieldStyle} value={manual.name} onChange={e => setManual(m => ({ ...m, name: e.target.value }))} placeholder="Name (optional)" />
          <Btn small variant="ghost" disabled={!manual.type || !manual.id.trim()} onClick={() => {
            toggle({ provider, type: manual.type, id: manual.id.trim(), name: manual.name.trim() || manual.id.trim(), region: region.trim(), role: '' })
            setManual({ type: '', id: '', name: '' })
          }}><Plus size={13} /> Add</Btn>
        </div>
      </div>

      <div>
        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px' }}>2 · Resources to read ({selected.length}) <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>· saved for scenario “{scenario}”</span></div>
        {selected.length === 0 ? <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>None yet.</div> : (
          <div style={{ display: 'grid', gap: '6px' }}>
            {selected.map(r => (
              <div key={`${r.provider}${r.id}`} style={{ display: 'flex', gap: '10px', alignItems: 'center', fontSize: '13px', flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--text-primary)', minWidth: '180px', flex: 1, wordBreak: 'break-word' }}>{r.name || r.id}</span>
                <span style={{ color: 'var(--text-muted)' }}>{r.provider.toUpperCase()} · {titleOf(r)}</span>
                <select style={{ ...fieldStyle, width: '150px', padding: '6px 10px' }} value={r.role}
                  onChange={e => setSelected(prev => prev.map(s => s.provider === r.provider && s.id === r.id ? { ...s, role: e.target.value } : s))}>
                  <option value="">Role…</option>
                  {ROLES.map(x => <option key={x} value={x}>{x}</option>)}
                </select>
                <button onClick={() => toggle(r)} title="Remove" style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: '10px' }}>
        <Btn onClick={() => onFetch(selected)} disabled={!selected.length || busy}>{busy ? <><Spinner /> Fetching…</> : <><Cloud size={14} /> Fetch metrics for this run</>}</Btn>
        <Btn variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Btn>
      </div>
    </div>
  )
}
