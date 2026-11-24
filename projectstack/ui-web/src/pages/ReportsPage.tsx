/**
 * Reports: JMeter's reports for a result file (JTL) someone already has, a standalone feature
 * next to HAR → JMX and results analysis. Two tabs share the chosen file:
 *   /reports/html       JMeter's HTML dashboard (and its five graphs as JPEGs)
 *   /reports/aggregate  JMeter's Aggregate Report as two tables, APIs and transactions
 */
import { useState } from 'react'
import { Link, Navigate, NavLink, useParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { FileBarChart, Table2 } from 'lucide-react'
import HtmlReportPanel from '../components/reports/HtmlReportPanel'
import AggregateReportPanel from '../components/reports/AggregateReportPanel'

const fade = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0, transition: { duration: 0.3 } } }
const TABS = [
  { id: 'html', label: 'HTML Report', sub: "JMeter's dashboard and graphs", Icon: FileBarChart },
  { id: 'aggregate', label: 'Aggregate Report', sub: 'APIs and transactions tables', Icon: Table2 },
] as const

export default function ReportsPage() {
  const { tab } = useParams()
  const [file, setFile] = useState<File | null>(null)        // kept when switching tabs
  if (!TABS.some(t => t.id === tab)) return <Navigate to="/reports/html" replace />

  return (
    <div style={{ maxWidth: '1440px', margin: '0 auto', padding: '40px 24px 64px' }}>
      <motion.div {...fade}>
        <h1 style={{ fontSize: '34px', fontWeight: 600, lineHeight: 1.15, display: 'flex', alignItems: 'center', gap: '14px', marginBottom: '10px' }}>
          <span style={{ width: '4px', height: '30px', borderRadius: '4px', background: 'var(--accent-gradient)', flexShrink: 0 }} />
          Reports
        </h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: '15px', lineHeight: 1.6, maxWidth: '760px', marginBottom: '8px' }}>
          Upload a JMeter result file (JTL) and get JMeter's own reports for it: the HTML dashboard, or the Aggregate
          Report for the APIs and the transactions. Nothing leaves this machine.
        </p>
        <p style={{ color: 'var(--text-muted)', fontSize: '13px', marginBottom: '24px' }}>
          Want a verdict against an SLA, findings and run comparisons instead?{' '}
          <Link to="/analyze" style={{ color: 'var(--accent)', textDecoration: 'none', fontWeight: 500 }}>Analyse the results →</Link>
        </p>

        <nav role="tablist" aria-label="Report type" style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '22px' }}>
          {TABS.map(({ id, label, sub, Icon }) => (
            <NavLink key={id} to={`/reports/${id}`} role="tab" aria-selected={tab === id}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: '12px', padding: '12px 18px', borderRadius: '16px', textDecoration: 'none',
                minWidth: '240px', flex: '0 1 auto',
                border: `1px solid ${isActive ? 'rgba(var(--accent-rgb),0.55)' : 'var(--border)'}`,
                background: isActive ? 'rgba(var(--accent-rgb),0.08)' : 'rgba(var(--fg-rgb),0.02)',
                boxShadow: isActive ? '0 8px 24px -14px rgba(var(--accent-rgb),0.8)' : 'none',
              })}>
              <span style={{
                width: '34px', height: '34px', borderRadius: '10px', display: 'grid', placeItems: 'center', flexShrink: 0,
                background: tab === id ? 'var(--accent-gradient)' : 'rgba(var(--fg-rgb),0.06)', color: tab === id ? '#fff' : 'var(--text-secondary)',
              }}>
                <Icon size={17} />
              </span>
              <span>
                <span style={{ display: 'block', fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>{label}</span>
                <span style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)' }}>{sub}</span>
              </span>
            </NavLink>
          ))}
        </nav>

        {tab === 'html'
          ? <HtmlReportPanel file={file} onFile={setFile} />
          : <AggregateReportPanel file={file} onFile={setFile} />}
      </motion.div>
      <style>{'@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }'}</style>
    </div>
  )
}
