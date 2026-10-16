import { NavLink } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'

const linkStyle = (isActive: boolean): React.CSSProperties => ({
  padding: '8px 18px',
  borderRadius: '999px',
  textDecoration: 'none',
  fontSize: '14px',
  fontWeight: 500,
  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
  background: isActive ? 'rgba(255,255,255,0.1)' : 'transparent',
  transition: 'all 0.2s ease',
})

export default function Navigation() {
  return (
    <nav
      style={{
        background: 'rgba(16,15,15,0.72)',
        backdropFilter: 'blur(18px)',
        WebkitBackdropFilter: 'blur(18px)',
        borderBottom: '1px solid var(--border)',
        height: '72px',
        display: 'flex',
        alignItems: 'center',
        padding: '0 32px',
        gap: '24px',
        position: 'sticky',
        top: 0,
        zIndex: 100,
      }}
    >
      {/* Logo */}
      <NavLink to="/" style={{ display: 'flex', alignItems: 'center', gap: '12px', textDecoration: 'none' }}>
        <div
          style={{
            width: '36px',
            height: '36px',
            background: 'var(--accent-gradient)',
            borderRadius: '50%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: 600,
            fontSize: '13px',
            color: '#fff',
            letterSpacing: '-0.3px',
            flexShrink: 0,
            boxShadow: '0 6px 18px -6px rgba(var(--accent-rgb),0.8)',
          }}
        >
          PS
        </div>
        <span
          style={{
            fontWeight: 600,
            fontSize: '18px',
            color: 'var(--text-primary)',
            letterSpacing: '-0.02em',
          }}
        >
          ProjectStack
        </span>
      </NavLink>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Nav links, grouped in a glass pill like the reference */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          padding: '4px',
          borderRadius: '999px',
          border: '1px solid var(--border)',
          background: 'rgba(255,255,255,0.03)',
        }}
      >
        <NavLink to="/app" style={({ isActive }) => linkStyle(isActive)}>
          HAR → JMX
        </NavLink>
        <NavLink to="/analyze" style={({ isActive }) => linkStyle(isActive)}>
          Analyse Results
        </NavLink>
        <NavLink to="/settings" style={({ isActive }) => linkStyle(isActive)}>
          Settings
        </NavLink>
      </div>

      <a
        href="https://github.com"
        target="_blank"
        rel="noopener noreferrer"
        style={{
          padding: '9px 20px',
          borderRadius: '999px',
          textDecoration: 'none',
          fontSize: '14px',
          fontWeight: 500,
          color: '#fff',
          background: 'var(--accent-gradient)',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          boxShadow: '0 6px 20px -8px rgba(var(--accent-rgb),0.7)',
          transition: 'all 0.2s ease',
        }}
        onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-1px)' }}
        onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)' }}
      >
        Docs
        <ArrowUpRight size={15} />
      </a>
    </nav>
  )
}
