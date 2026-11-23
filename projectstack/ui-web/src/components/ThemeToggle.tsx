/** Switches between light and dark (the Settings page also offers "match the system"). */
import { Moon, Sun } from 'lucide-react'
import { useTheme } from '../theme'

export default function ThemeToggle() {
  const { theme, pref, toggle } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  const label = `Switch to ${next} mode${pref === 'system' ? ' (now following the system)' : ''}`
  return (
    <button type="button" onClick={toggle} aria-label={label} title={label}
      style={{
        width: '38px', height: '38px', borderRadius: '50%', flexShrink: 0, cursor: 'pointer',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        border: '1px solid var(--border)', background: 'rgba(var(--fg-rgb),0.04)', color: 'var(--text-secondary)',
      }}
      onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'rgba(var(--fg-rgb),0.09)' }}
      onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'rgba(var(--fg-rgb),0.04)' }}>
      {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  )
}
