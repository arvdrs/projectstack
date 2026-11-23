/**
 * Light / dark appearance. The choice (dark, light, or follow the system) is kept per
 * browser in localStorage and applied as <html data-theme="light|dark">; index.css holds
 * the two palettes. index.html applies it before the first paint so pages never flash.
 */
import { useEffect, useState } from 'react'

export type ThemePref = 'dark' | 'light' | 'system'
export type Theme = 'dark' | 'light'

const KEY = 'ps-theme'
const EVENT = 'ps-theme-change'
const META_COLOR: Record<Theme, string> = { dark: '#100f0f', light: '#f4f3f1' }

export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'dark' || v === 'light' || v === 'system') return v
  } catch { /* storage blocked: use the default */ }
  return 'dark'
}

function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function resolveTheme(pref: ThemePref = getThemePref()): Theme {
  return pref === 'system' ? systemTheme() : pref
}

export function applyTheme(pref: ThemePref = getThemePref(), animate = false): Theme {
  const theme = resolveTheme(pref)
  const root = document.documentElement
  if (root.dataset.theme === theme) return theme
  const smooth = animate && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (smooth) root.classList.add('theme-switching')
  root.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META_COLOR[theme])
  if (smooth) window.setTimeout(() => root.classList.remove('theme-switching'), 260)
  return theme
}

export function setThemePref(pref: ThemePref): void {
  try { localStorage.setItem(KEY, pref) } catch { /* not remembered, still applied */ }
  applyTheme(pref, true)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: pref }))
}

/** The current preference and the theme in effect; re-renders when either changes (here, in another tab, or in the OS). */
export function useTheme(): { pref: ThemePref; theme: Theme; setPref: (p: ThemePref) => void; toggle: () => void } {
  const [pref, setPrefState] = useState<ThemePref>(getThemePref)
  const [theme, setTheme] = useState<Theme>(() => resolveTheme(pref))

  useEffect(() => {
    const sync = () => { const p = getThemePref(); setPrefState(p); setTheme(applyTheme(p, true)) }
    const onStorage = (e: StorageEvent) => { if (e.key === KEY) sync() }
    const media = window.matchMedia?.('(prefers-color-scheme: light)')
    window.addEventListener(EVENT, sync)
    window.addEventListener('storage', onStorage)
    media?.addEventListener?.('change', sync)
    return () => {
      window.removeEventListener(EVENT, sync)
      window.removeEventListener('storage', onStorage)
      media?.removeEventListener?.('change', sync)
    }
  }, [])

  return {
    pref, theme,
    setPref: setThemePref,
    toggle: () => setThemePref(resolveTheme(pref) === 'dark' ? 'light' : 'dark'),
  }
}
