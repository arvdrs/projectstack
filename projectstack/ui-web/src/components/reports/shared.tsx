/**
 * Pieces shared by the Reports tabs (HTML Report, Aggregate Report): the JTL drop zone, the
 * upload and job progress lines, and links styled as buttons.
 */
import { useRef, useState } from 'react'
import { Activity, FileUp } from 'lucide-react'
import { Spinner } from '../results/ui'

export const ACCEPT = '.jtl,.csv,.xml,.gz'

export const mb = (n: number) => n >= 1_073_741_824 ? `${(n / 1_073_741_824).toFixed(2)} GB`
  : n >= 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`

export const busy = (r: { status: string } | null | undefined) => Boolean(r && (r.status === 'queued' || r.status === 'running'))

/** A link styled like a small Btn (Open in a new tab, Download). */
export function LinkBtn({ href, children, primary = false, download = false, label }: {
  href: string; children: React.ReactNode; primary?: boolean; download?: boolean; label?: string
}) {
  return (
    <a href={href} aria-label={label} {...(download ? { download: '' } : { target: '_blank', rel: 'noopener noreferrer' })}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '6px', borderRadius: '999px', fontWeight: 500, fontSize: '13px',
        padding: '7px 14px', whiteSpace: 'nowrap', textDecoration: 'none',
        ...(primary
          ? { background: 'var(--accent-gradient)', color: '#fff', boxShadow: '0 6px 20px -8px rgba(var(--accent-rgb),0.7)' }
          : { background: 'rgba(var(--fg-rgb),0.05)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)' }),
      }}>
      {children}
    </a>
  )
}

/** Drop a JTL here, or click to choose one. The chosen file is shared by both tabs. */
export function JtlDropzone({ file, onPick }: { file: File | null; onPick: (f: File) => void }) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const pick = (f: File | undefined | null) => { if (f) onPick(f) }
  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0]) }}
      role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
      style={{
        border: `1.5px dashed ${dragging ? 'var(--accent)' : 'var(--border-strong)'}`, borderRadius: '16px',
        padding: '28px', textAlign: 'center', cursor: 'pointer', marginBottom: '20px',
        background: dragging ? 'rgba(var(--accent-rgb),0.06)' : 'rgba(var(--fg-rgb),0.02)',
      }}>
      <FileUp size={26} style={{ color: file ? 'var(--accent)' : 'var(--text-muted)', margin: '0 auto 8px', display: 'block' }} />
      {file ? (
        <div style={{ fontSize: '15px', fontWeight: 500, overflowWrap: 'anywhere' }}>{file.name} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>· {mb(file.size)}</span></div>
      ) : (
        <div style={{ fontSize: '15px', fontWeight: 500 }}>Drop a JTL file here, or click to choose</div>
      )}
      <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
        CSV (with or without a header row) or XML results, optionally .gz
      </div>
      <input ref={inputRef} type="file" accept={ACCEPT} style={{ display: 'none' }} onChange={e => { pick(e.target.files?.[0]); e.target.value = '' }} />
    </div>
  )
}

export function UploadProgress({ name, sent, total }: { name: string; sent: number; total: number }) {
  return (
    <div style={{ marginBottom: '14px' }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
        <Spinner size={13} /> Uploading {name}: {mb(sent)} of {mb(total)} ({total ? Math.floor((sent / total) * 100) : 0}%)
      </div>
      <div style={{ height: '6px', borderRadius: '6px', background: 'rgba(var(--fg-rgb),0.08)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${total ? (sent / total) * 100 : 0}%`, background: 'var(--series-1)', transition: 'width 0.3s ease' }} />
      </div>
    </div>
  )
}

/** The last few progress lines of a job, the newest with a spinner and the time so far. */
export function JobProgress({ lines, elapsed }: { lines: string[]; elapsed: number }) {
  return (
    <div style={{ display: 'grid', gap: '4px', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
      {lines.slice(-5).map((p, i, arr) => (
        <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'center', opacity: i === arr.length - 1 ? 1 : 0.6 }}>
          {i === arr.length - 1 ? <Spinner size={13} /> : <Activity size={13} />} {p}
          {i === arr.length - 1 && elapsed > 2 && <span style={{ color: 'var(--text-muted)' }}>· {elapsed} s</span>}
        </div>
      ))}
    </div>
  )
}
