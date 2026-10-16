import { useState, useEffect } from 'react'
import { Eye, EyeOff, Save, CheckCircle, AlertCircle, Terminal, Sliders, Loader2, Cpu, Activity, Mail, ShieldCheck, Layers, Upload, Trash2, Cloud } from 'lucide-react'
import { getSettings, updateSettings, verifyGmail, getRulePacks, uploadRulePack, deleteRulePack, getJmeterInfo, checkCloud, type Settings, type RulePack, type JmeterInfo } from '../api/client'

// ---------------------------------------------------------------------------
// Provider definitions (mirrors llm_router.py PROVIDERS)
// ---------------------------------------------------------------------------

const PROVIDER_OPTIONS = [
  {
    id: 'groq',
    label: 'Groq',
    subtitle: 'Llama 3.3 70B',
    tier: 'free' as const,
    desc: 'Free tier · ~300 req/day',
    link: 'https://console.groq.com',
    linkText: 'console.groq.com',
    keyField: 'groq_api_key' as keyof Settings,
    placeholder: 'gsk_...',
  },
  {
    id: 'gemini',
    label: 'Gemini',
    subtitle: 'Flash',
    tier: 'free' as const,
    desc: 'Free tier · generous limits',
    link: 'https://aistudio.google.com',
    linkText: 'aistudio.google.com',
    keyField: 'gemini_api_key' as keyof Settings,
    placeholder: 'AIzaSy...',
  },
  {
    id: 'ollama',
    label: 'Ollama',
    subtitle: 'gemma4 (local)',
    tier: 'free' as const,
    desc: 'Runs locally with gemma4 — no API key needed',
    link: 'https://ollama.com',
    linkText: 'ollama.com',
    keyField: null,
    placeholder: null,
  },
  {
    id: 'claude',
    label: 'Claude',
    subtitle: 'Sonnet 4.6',
    tier: 'paid' as const,
    desc: 'Best quality · paid credits',
    link: 'https://console.anthropic.com',
    linkText: 'console.anthropic.com',
    keyField: 'anthropic_api_key' as keyof Settings,
    placeholder: 'sk-ant-...',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    subtitle: 'GPT-4o-mini',
    tier: 'paid' as const,
    desc: 'High quality · low cost',
    link: 'https://platform.openai.com',
    linkText: 'platform.openai.com',
    keyField: 'openai_api_key' as keyof Settings,
    placeholder: 'sk-...',
  },
]

// ---------------------------------------------------------------------------
// Tiny components
// ---------------------------------------------------------------------------

function Toast({ msg, type }: { msg: string; type: 'success' | 'error' }) {
  return (
    <div style={{
      position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999,
      display: 'flex', alignItems: 'center', gap: '10px',
      padding: '12px 18px', borderRadius: '12px',
      background: type === 'success' ? 'rgba(35,134,54,0.95)' : 'rgba(var(--danger-rgb),0.95)',
      border: `1px solid ${type === 'success' ? 'var(--accent)' : 'var(--danger)'}`,
      color: '#fff', fontSize: '14px', fontWeight: 500,
      boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
      animation: 'slideUp 0.3s ease-out',
    }}>
      {type === 'success' ? <CheckCircle size={16} /> : <AlertCircle size={16} />}
      {msg}
    </div>
  )
}

function Section({ icon: Icon, title, description, children }: {
  icon: React.ElementType
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div style={{
      background: 'var(--glass), var(--bg-elevated)', border: '1px solid var(--border)',
      borderRadius: '20px', padding: '32px', marginBottom: '20px',
      boxShadow: '0 20px 40px -24px rgba(0,0,0,0.6)',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '14px', marginBottom: '24px' }}>
        <div style={{
          width: '40px', height: '40px', borderRadius: '50%',
          background: 'rgba(var(--accent-rgb),0.12)', border: '1px solid rgba(var(--accent-rgb),0.25)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
        }}>
          <Icon size={16} color="var(--accent)" />
        </div>
        <div>
          <h2 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            {title}
          </h2>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: '4px 0 0' }}>
            {description}
          </p>
        </div>
      </div>
      {children}
    </div>
  )
}

function Field({ label, help, children }: {
  label: string
  help?: string
  children: React.ReactNode
}) {
  return (
    <div style={{ marginBottom: '20px' }}>
      <label style={{
        display: 'block', fontSize: '13px', fontWeight: 500,
        color: 'var(--text-secondary)', marginBottom: '8px',
      }}>
        {label}
      </label>
      {children}
      {help && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px', lineHeight: 1.5 }}>
          {help}
        </p>
      )}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '10px 14px', borderRadius: '12px',
  background: 'var(--bg-surface)', border: '1px solid var(--border)',
  color: 'var(--text-primary)', fontSize: '14px', outline: 'none',
  fontFamily: 'inherit', transition: 'border-color 0.15s',
}

const numInputStyle: React.CSSProperties = { ...inputStyle, width: '100px' }

// ---------------------------------------------------------------------------
// ApiKeyField — reusable key input with show/hide toggle + status badge
// ---------------------------------------------------------------------------

function ApiKeyField({
  value, onChange, placeholder, label, help, link, linkText,
}: {
  value: string
  onChange: (v: string) => void
  placeholder: string
  label: string
  help?: string
  link?: string
  linkText?: string
}) {
  const [show, setShow] = useState(false)
  const hasKey = !!value && !value.includes('...')
  const ismasked = value.includes('...')

  return (
    <Field label={label} help={help}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <div style={{ position: 'relative', flex: 1 }}>
          <input
            type={show ? 'text' : 'password'}
            value={value}
            onChange={e => onChange(e.target.value)}
            placeholder={placeholder}
            style={{ ...inputStyle, paddingRight: '40px' }}
            onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
            onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
            autoComplete="new-password"
          />
          <button
            type="button"
            onClick={() => setShow(!show)}
            style={{
              position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--text-muted)', padding: '4px', display: 'flex', alignItems: 'center',
            }}
          >
            {show ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
        </div>
        {(hasKey || ismasked) && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '5px',
            padding: '6px 12px', borderRadius: '999px',
            background: 'rgba(var(--success-rgb),0.12)', border: '1px solid rgba(var(--success-rgb),0.3)',
            color: 'var(--success)', fontSize: '12px', fontWeight: 500, whiteSpace: 'nowrap',
          }}>
            <CheckCircle size={12} /> Set
          </div>
        )}
      </div>
      {link && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px' }}>
          Get a free key at{' '}
          <span style={{ color: 'var(--accent)' }}>{linkText}</span>
        </p>
      )}
    </Field>
  )
}

// ---------------------------------------------------------------------------
// Cloud sign-in check
// ---------------------------------------------------------------------------

function CloudCheckButton({ provider, save }: { provider: 'aws' | 'azure'; save: () => Promise<unknown> }) {
  const [state, setState] = useState<{ busy: boolean; ok?: boolean; msg?: string }>({ busy: false })
  const run = async () => {
    setState({ busy: true })
    try {
      await save()
      const info = await checkCloud(provider)
      const who = provider === 'aws'
        ? `Signed in to AWS account ${info.account} as ${info.identity} (${info.region || 'no region set'})`
        : `Signed in to Azure${info.subscription ? `, subscription ${info.subscription} readable` : ' (add a subscription ID to find resources)'}`
      setState({ busy: false, ok: true, msg: who })
    } catch (e) {
      setState({ busy: false, ok: false, msg: e instanceof Error ? e.message : String(e) })
    }
  }
  return (
    <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
      <button type="button" onClick={run} disabled={state.busy} style={{
        display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '8px 16px', borderRadius: '999px', fontSize: '13px',
        border: '1px solid var(--border-strong)', background: 'rgba(255,255,255,0.05)', color: 'var(--text-primary)', cursor: 'pointer',
      }}>
        {state.busy ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Cloud size={13} />} Save and test {provider === 'aws' ? 'AWS' : 'Azure'} sign-in
      </button>
      {state.msg && (
        <span style={{ fontSize: '13px', color: state.ok ? 'var(--success)' : 'var(--danger)', lineHeight: 1.5 }}>
          {state.ok ? '✓ ' : '✕ '}{state.msg}
        </span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Correlation rule packs
// ---------------------------------------------------------------------------

const RULE_PACK_EXAMPLE = `{
  "id": "acme",
  "name": "Acme Portal",
  "rules": [
    { "id": "acme-token", "name": "Form token", "type": "css",
      "selector": "input[name=acme_token]", "attr": "value", "var": "C_ACME_TOKEN" },
    { "id": "acme-sid", "name": "Session id in script", "type": "regex",
      "regex": "sessionId = '([^']+)'", "var": "C_ACME_SID" }
  ]
}`

function RulePacksSection() {
  const [packs, setPacks] = useState<RulePack[]>([])
  const [draft, setDraft] = useState('')
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  const load = () => getRulePacks().then(r => setPacks(r.packs)).catch(() => setPacks([]))
  useEffect(() => { load() }, [])

  const importPack = async () => {
    setMsg(null)
    let pack: unknown
    try { pack = JSON.parse(draft) } catch { setMsg({ text: 'Not valid JSON', ok: false }); return }
    try {
      const res = await uploadRulePack(pack)
      setMsg({ text: `Imported rule pack "${res.id}"`, ok: true })
      setDraft('')
      load()
    } catch (e) {
      setMsg({ text: String(e), ok: false })
    }
  }

  const readFile = (f: File) => f.text().then(setDraft)

  const remove = async (id: string) => {
    try { await deleteRulePack(id); load() } catch (e) { setMsg({ text: String(e), ok: false }) }
  }

  return (
    <Section icon={Layers} title="Correlation Rule Packs"
      description="Framework rules applied on every correlation run. Import your own to teach the engine an in-house framework.">
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '20px' }}>
        {packs.map(p => (
          <div key={p.id} style={{ border: '1px solid var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 14px', cursor: 'pointer' }}
              onClick={() => setOpen(o => o === p.id ? null : p.id)}>
              <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>{p.name}</span>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{p.rule_count} rule{p.rule_count !== 1 ? 's' : ''}</span>
              <span style={{ fontSize: '10px', fontWeight: 700, padding: '1px 6px', borderRadius: '4px', letterSpacing: '0.04em',
                background: p.source === 'user' ? 'rgba(var(--info-rgb),0.12)' : 'rgba(255,255,255,0.05)',
                color: p.source === 'user' ? 'var(--info)' : 'var(--text-muted)', border: '1px solid var(--border)' }}>
                {p.source === 'user' ? 'CUSTOM' : 'BUILT-IN'}
              </span>
              <span style={{ flex: 1, fontSize: '12px', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.description}</span>
              {p.source === 'user' && (
                <button type="button" title="Delete rule pack" onClick={e => { e.stopPropagation(); remove(p.id) }}
                  style={{ background: 'transparent', border: 'none', color: 'var(--danger)', cursor: 'pointer', padding: '2px' }}>
                  <Trash2 size={14} />
                </button>
              )}
            </div>
            {open === p.id && (
              <div style={{ borderTop: '1px solid var(--border)', padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {p.rules.map((r, i) => (
                  <div key={i} style={{ fontSize: '12px', display: 'flex', gap: '10px' }}>
                    <span style={{ fontFamily: 'monospace', color: 'var(--accent)', minWidth: '220px' }}>${'{'}{r.var}{'}'}</span>
                    <span style={{ color: 'var(--text-secondary)' }}>{r.name ?? r.id}</span>
                    <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace' }}>{r.type}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <Field label="Import a rule pack (JSON)" help="Rule types: css (selector + attr), regex (one capture group; where: body | headers), json (path from $), boundary (lb + rb).">
        <textarea value={draft} onChange={e => setDraft(e.target.value)} placeholder={RULE_PACK_EXAMPLE} rows={9}
          style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '12px', resize: 'vertical' }} />
      </Field>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <button type="button" onClick={importPack} disabled={!draft.trim()}
          style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '8px 18px', borderRadius: '999px', border: 'none',
            background: 'var(--accent-gradient)', color: '#fff', fontSize: '13px', cursor: draft.trim() ? 'pointer' : 'not-allowed', opacity: draft.trim() ? 1 : 0.5 }}>
          <Upload size={13} /> Import pack
        </button>
        <label style={{ fontSize: '13px', color: 'var(--accent)', cursor: 'pointer' }}>
          or load a .json file
          <input type="file" accept=".json,application/json" style={{ display: 'none' }}
            onChange={e => { const f = e.target.files?.[0]; if (f) readFile(f) }} />
        </label>
        {msg && <span style={{ fontSize: '12px', color: msg.ok ? 'var(--success)' : 'var(--danger)' }}>{msg.text}</span>}
      </div>
    </Section>
  )
}

export default function SettingsPage() {
  const [form, setForm] = useState<Settings>({
    ai_provider: 'ollama',
    anthropic_api_key: '',
    openai_api_key: '',
    groq_api_key: '',
    gemini_api_key: '',
    ollama_url: 'http://localhost:11434',
    ollama_model: 'gemma4',
    jmeter_path: 'jmeter',
    default_threads: '10',
    default_rampup: '30',
    default_loops: '1',
    grafana_url: '',
    gmail_address: '',
    gmail_app_password: '',
  })
  const [verifying, setVerifying] = useState(false)
  const [gmailCheck, setGmailCheck] = useState<{ ok: boolean; msg: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)
  const [jmeter, setJmeter] = useState<JmeterInfo | null>(null)

  useEffect(() => { getJmeterInfo().then(setJmeter).catch(() => {}) }, [])

  useEffect(() => {
    getSettings()
      .then(s => setForm(f => ({ ...f, ...s })))
      .catch(() => {})
      .finally(() => {
        setLoading(false)
        // Deep link from the Share dialog: /settings#email
        if (window.location.hash === '#email') {
          setTimeout(() => document.getElementById('email')?.scrollIntoView({ behavior: 'smooth' }), 50)
        }
      })
  }, [])

  const handleVerifyGmail = async () => {
    setVerifying(true); setGmailCheck(null)
    try {
      await updateSettings(form)
      const res = await verifyGmail()
      setGmailCheck({ ok: true, msg: `Connected — reports will be sent from ${res.address}` })
    } catch (err) {
      setGmailCheck({ ok: false, msg: err instanceof Error ? err.message : String(err) })
    } finally {
      setVerifying(false)
    }
  }

  const showToast = (msg: string, type: 'success' | 'error') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3500)
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await updateSettings(form)
      getJmeterInfo().then(setJmeter).catch(() => {})
      showToast('Settings saved successfully', 'success')
    } catch (err) {
      showToast(`Failed to save: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  const set = <K extends keyof Settings>(key: K, val: Settings[K]) =>
    setForm(f => ({ ...f, [key]: val }))

  const selectedProvider = PROVIDER_OPTIONS.find(p => p.id === form.ai_provider) ?? PROVIDER_OPTIONS[0]

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh' }}>
        <Loader2 size={24} style={{ color: 'var(--text-muted)', animation: 'spin 1s linear infinite' }} />
      </div>
    )
  }

  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: '56px 24px' }}>
      {/* Header */}
      <div style={{ marginBottom: '40px' }}>
        <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--accent)', fontWeight: 600, marginBottom: '10px' }}>
          Configuration
        </div>
        <h1 style={{ fontSize: '44px', fontWeight: 600, lineHeight: 1.1, color: 'var(--text-primary)', margin: 0 }}>
          Settings
        </h1>
        <p style={{ fontSize: '16px', color: 'var(--text-secondary)', marginTop: '12px' }}>
          Choose an AI provider, configure API keys, and set test defaults.
        </p>
      </div>

      <form onSubmit={handleSave}>

        {/* ── AI Provider ──────────────────────────────────────────── */}
        <Section icon={Cpu} title="AI Provider" description="Select the LLM used for Pass 6 dynamic value detection">

          {/* Provider cards */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))',
            gap: '10px',
            marginBottom: '24px',
          }}>
            {PROVIDER_OPTIONS.map(p => {
              const isSelected = form.ai_provider === p.id
              const isFree = p.tier === 'free'
              return (
                <div
                  key={p.id}
                  onClick={() => set('ai_provider', p.id)}
                  style={{
                    padding: '14px 16px',
                    borderRadius: '10px',
                    border: `2px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
                    background: isSelected ? 'rgba(var(--accent-rgb),0.07)' : 'var(--bg-surface)',
                    cursor: 'pointer',
                    transition: 'border-color 0.15s, background 0.15s',
                    userSelect: 'none',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
                    <div>
                      <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {p.label}
                      </span>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginLeft: '6px' }}>
                        {p.subtitle}
                      </span>
                    </div>
                    <span style={{
                      fontSize: '9px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px',
                      letterSpacing: '0.5px',
                      background: isFree ? 'rgba(var(--accent-rgb),0.15)' : 'rgba(var(--warning-rgb),0.15)',
                      color: isFree ? 'var(--success)' : '#d2991a',
                      border: `1px solid ${isFree ? 'rgba(var(--accent-rgb),0.3)' : 'rgba(var(--warning-rgb),0.3)'}`,
                    }}>
                      {isFree ? 'FREE' : 'PAID'}
                    </span>
                  </div>
                  <p style={{ fontSize: '11px', color: 'var(--text-muted)', margin: 0 }}>{p.desc}</p>
                </div>
              )
            })}
          </div>

          {/* Conditional fields for selected provider */}
          {selectedProvider.id === 'ollama' ? (
            <>
              <Field
                label="Ollama Base URL"
                help="URL where your local Ollama server is running."
              >
                <input
                  type="text"
                  value={form.ollama_url}
                  onChange={e => set('ollama_url', e.target.value)}
                  placeholder="http://localhost:11434"
                  style={inputStyle}
                  onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
                />
              </Field>
              <Field label="Model Name" help="Must be pulled first: ollama pull gemma4">
                <input
                  type="text"
                  value={form.ollama_model}
                  onChange={e => set('ollama_model', e.target.value)}
                  placeholder="gemma4"
                  style={{ ...inputStyle, width: '200px' }}
                  onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
                />
              </Field>
              <div style={{
                padding: '12px 14px', borderRadius: '12px', marginTop: '4px',
                background: 'rgba(110,118,129,0.06)', border: '1px solid var(--border)',
                fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.7, fontFamily: 'var(--font-mono)',
              }}>
                # Install / update Ollama<br />
                curl -fsSL https://ollama.com/install.sh | sh<br />
                ollama pull gemma4
              </div>
            </>
          ) : selectedProvider.keyField ? (
            <ApiKeyField
              value={form[selectedProvider.keyField] as string}
              onChange={v => set(selectedProvider.keyField!, v)}
              placeholder={selectedProvider.placeholder!}
              label="API Key"
              link={selectedProvider.link}
              linkText={selectedProvider.linkText}
            />
          ) : null}

          {/* Info box */}
          <div style={{
            padding: '12px 14px', borderRadius: '12px', marginTop: selectedProvider.id === 'ollama' ? '16px' : '4px',
            background: 'rgba(var(--info-rgb),0.06)', border: '1px solid rgba(var(--info-rgb),0.2)',
            fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.6,
          }}>
            <strong style={{ color: 'var(--info)' }}>Tip:</strong> Without an AI provider, correlation still runs
            5 rule-based passes (JWT/Bearer, JSON keys, URL path IDs, headers/cookies, backward inference).
            AI adds extra detection for edge-case tokens the rules miss.
          </div>
        </Section>

        {/* ── JMeter Configuration ─────────────────────────────────── */}
        <Section icon={Terminal} title="JMeter Configuration" description="Path to the local JMeter executable">
          <Field
            label="JMeter Executable Path"
            help="The jmeter binary, its bin/ folder or the JMeter install folder — or just 'jmeter' to use the one on your PATH."
          >
            <input
              type="text"
              value={form.jmeter_path}
              onChange={e => set('jmeter_path', e.target.value)}
              placeholder="/opt/apache-jmeter/bin/jmeter"
              style={inputStyle}
              onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
            />
          </Field>

          {jmeter && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px', fontSize: '13px' }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', color: jmeter.found ? 'var(--text-secondary)' : 'var(--danger)' }}>
                {jmeter.found ? <CheckCircle size={14} style={{ color: 'var(--success)' }} /> : <AlertCircle size={14} />}
                {jmeter.found ? <>Tests run with <code>{jmeter.path}</code></> : 'JMeter not found — set the path above'}
              </div>
              {jmeter.configured_path && !jmeter.configured_valid && (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', color: 'var(--warning)' }}>
                  <AlertCircle size={14} /> No jmeter executable at <code>{jmeter.configured_path}</code>{jmeter.found ? ' — using the one above instead' : ''}
                </div>
              )}
              {jmeter.found && (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', color: 'var(--text-muted)' }}>
                  {jmeter.custom_thread_groups ? <CheckCircle size={14} style={{ color: 'var(--success)' }} /> : <AlertCircle size={14} />}
                  {jmeter.custom_thread_groups
                    ? 'Custom Thread Groups plugin installed — Stepping, Concurrency and Ultimate thread groups can run'
                    : 'Custom Thread Groups plugin (jpgc-casutg) not installed — only the standard Thread Group can run'}
                </div>
              )}
            </div>
          )}
          <div style={{
            padding: '12px 14px', borderRadius: '12px',
            background: 'rgba(110,118,129,0.06)', border: '1px solid var(--border)',
            fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.7, fontFamily: 'var(--font-mono)',
          }}>
            # macOS: brew install jmeter<br />
            # Ubuntu: sudo apt install jmeter<br />
            # Or download from apache.jmeter.net
          </div>
        </Section>

        {/* ── Default Test Parameters ──────────────────────────────── */}
        <Section icon={Sliders} title="Default Test Parameters" description="Pre-fill values in the Run Test step">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
            <Field label="Virtual Users">
              <input
                type="number" min="1" max="10000"
                value={form.default_threads}
                onChange={e => set('default_threads', e.target.value)}
                style={numInputStyle}
                onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
              />
            </Field>
            <Field label="Ramp-up (seconds)">
              <input
                type="number" min="0" max="3600"
                value={form.default_rampup}
                onChange={e => set('default_rampup', e.target.value)}
                style={numInputStyle}
                onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
              />
            </Field>
            <Field label="Loops">
              <input
                type="number" min="1" max="1000"
                value={form.default_loops}
                onChange={e => set('default_loops', e.target.value)}
                style={numInputStyle}
                onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
              />
            </Field>
          </div>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>
            These values pre-fill the Run Test form. You can override them per run.
          </p>
        </Section>

        {/* ── Cloud monitoring (server metrics) ────────────────────── */}
        <div id="cloud" style={{ scrollMarginTop: '96px' }}>
        <Section icon={Cloud} title="Cloud Monitoring" description="Read server metrics from AWS CloudWatch and Azure Monitor into results analysis">
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', lineHeight: 1.6, marginBottom: '18px' }}>
            Read-only. Leave the keys empty to use this machine's own sign-in: an AWS CLI profile, SSO session or
            instance role, and <code>az login</code> or a managed identity for Azure. Permissions needed: AWS —
            CloudWatchReadOnlyAccess (plus ViewOnlyAccess to find resources); Azure — Monitoring Reader (plus Reader to find resources).
          </p>

          <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: '4px 0 12px' }}>AWS</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
            <Field label="Profile" help="From ~/.aws (blank: the default chain)">
              <input type="text" value={form.aws_profile ?? ''} onChange={e => set('aws_profile', e.target.value)} placeholder="default" style={inputStyle} />
            </Field>
            <Field label="Region" help="Where your resources run">
              <input type="text" value={form.aws_region ?? ''} onChange={e => set('aws_region', e.target.value)} placeholder="ap-south-1" style={inputStyle} />
            </Field>
          </div>
          <Field label="Access key ID" help="Optional — only for a dedicated read-only IAM user">
            <input type="text" value={form.aws_access_key_id ?? ''} onChange={e => set('aws_access_key_id', e.target.value)} placeholder="AKIA…" style={inputStyle} autoComplete="off" />
          </Field>
          <ApiKeyField label="Secret access key" value={form.aws_secret_access_key ?? ''} onChange={v => set('aws_secret_access_key', v)} placeholder="Only with an access key ID" />
          <ApiKeyField label="Session token" value={form.aws_session_token ?? ''} onChange={v => set('aws_session_token', v)} placeholder="Optional — for temporary credentials" />
          <CloudCheckButton provider="aws" save={() => updateSettings(form)} />

          <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: '24px 0 12px' }}>Azure</div>
          <Field label="Subscription ID" help="Needed to find resources; metrics are read from each resource's own subscription">
            <input type="text" value={form.azure_subscription_id ?? ''} onChange={e => set('azure_subscription_id', e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" style={inputStyle} />
          </Field>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
            <Field label="Tenant ID" help="Optional — for a service principal">
              <input type="text" value={form.azure_tenant_id ?? ''} onChange={e => set('azure_tenant_id', e.target.value)} style={inputStyle} />
            </Field>
            <Field label="Client ID" help="Optional — for a service principal">
              <input type="text" value={form.azure_client_id ?? ''} onChange={e => set('azure_client_id', e.target.value)} style={inputStyle} />
            </Field>
          </div>
          <ApiKeyField label="Client secret" value={form.azure_client_secret ?? ''} onChange={v => set('azure_client_secret', v)} placeholder="Only with a tenant and client ID" />
          <CloudCheckButton provider="azure" save={() => updateSettings(form)} />

          <label style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '22px', fontSize: '14px', color: 'var(--text-secondary)', cursor: 'pointer' }}>
            <input type="checkbox" checked={(form.cloud_auto_fetch ?? 'true') !== 'false'} onChange={e => set('cloud_auto_fetch', e.target.checked ? 'true' : 'false')} />
            Fetch server metrics automatically when a run is analysed (for scenarios that have resources chosen)
          </label>
        </Section>
        </div>

        {/* ── Grafana / Metrics ────────────────────────────────────── */}
        <Section icon={Activity} title="Grafana Integration" description="Stream live test metrics to a Grafana dashboard via InfluxDB Backend Listener">
          <Field
            label="InfluxDB Write URL"
            help="Grafana InfluxDB write endpoint, e.g. http://localhost:8086/write?db=jmeter. Leave blank to disable the backend listener."
          >
            <input
              type="text"
              value={form.grafana_url}
              onChange={e => set('grafana_url', e.target.value)}
              placeholder="http://localhost:8086/write?db=jmeter"
              style={inputStyle}
              onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
            />
          </Field>
          <div style={{
            padding: '12px 14px', borderRadius: '12px',
            background: 'rgba(110,118,129,0.06)', border: '1px solid var(--border)',
            fontSize: '13px', color: 'var(--text-muted)', lineHeight: 1.6,
          }}>
            When set, the generated JMX includes an InfluxDB Backend Listener that pushes per-request
            metrics to your Grafana instance in real time. Enable <strong>Transaction Grouping</strong> in
            the Generate step to see per-transaction latency in Grafana.
          </div>
        </Section>

        {/* ── Email (Gmail) ────────────────────────────────────────── */}
        <div id="email" style={{ scrollMarginTop: '96px' }}>
        <Section icon={Mail} title="Email (Gmail)" description="Share test reports and insights by email from the View Report step">
          <Field label="Gmail address">
            <input
              type="email"
              value={form.gmail_address}
              onChange={e => { set('gmail_address', e.target.value); setGmailCheck(null) }}
              placeholder="you@gmail.com"
              style={inputStyle}
              autoComplete="email"
              onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
            />
          </Field>
          <ApiKeyField
            label="App password"
            value={form.gmail_app_password}
            onChange={v => { set('gmail_app_password', v); setGmailCheck(null) }}
            placeholder="16-character app password"
            help="Not your Gmail password. Create one at myaccount.google.com/apppasswords (requires 2-Step Verification)."
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', marginTop: '4px' }}>
            <button
              type="button"
              onClick={handleVerifyGmail}
              disabled={verifying || !form.gmail_address || !form.gmail_app_password}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: '7px', padding: '9px 18px', borderRadius: '999px',
                border: '1px solid var(--border-strong)', background: 'rgba(255,255,255,0.05)', color: 'var(--text-primary)',
                fontSize: '13px', fontWeight: 500,
                cursor: verifying || !form.gmail_address || !form.gmail_app_password ? 'not-allowed' : 'pointer',
                opacity: verifying || !form.gmail_address || !form.gmail_app_password ? 0.5 : 1,
              }}
            >
              {verifying ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <ShieldCheck size={14} />}
              {verifying ? 'Verifying…' : 'Save & verify connection'}
            </button>
            {gmailCheck && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: gmailCheck.ok ? 'var(--success)' : 'var(--danger)' }}>
                {gmailCheck.ok ? <CheckCircle size={14} /> : <AlertCircle size={14} />} {gmailCheck.msg}
              </span>
            )}
          </div>
          <div style={{
            marginTop: '16px', padding: '12px 14px', borderRadius: '12px',
            background: 'rgba(110,118,129,0.06)', border: '1px solid var(--border)',
            fontSize: '13px', color: 'var(--text-muted)', lineHeight: 1.6,
          }}>
            Mail is sent through Gmail's SMTP server (smtp.gmail.com) from this machine. The app password is stored
            locally in <code>~/.projectstack_settings.json</code> and can be revoked any time from your Google Account.
            Verifying only signs in — it doesn't send anything.
          </div>
        </Section>
        </div>

        {/* ── Save button ──────────────────────────────────────────── */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: '8px' }}>
          <button
            type="submit"
            disabled={saving}
            style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              padding: '12px 28px', borderRadius: '999px', border: 'none',
              background: saving ? 'var(--accent-dim)' : 'var(--accent-gradient)',
              color: '#fff', fontWeight: 500, fontSize: '15px',
              cursor: saving ? 'not-allowed' : 'pointer',
              boxShadow: '0 6px 20px -8px rgba(var(--accent-rgb),0.7)',
              transition: 'all 0.2s',
            }}
            onMouseEnter={e => { if (!saving) (e.currentTarget as HTMLButtonElement).style.transform = 'translateY(-1px)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.transform = 'translateY(0)' }}
          >
            {saving ? <Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={15} />}
            {saving ? 'Saving…' : 'Save Settings'}
          </button>
        </div>
      </form>

      <div style={{ marginTop: '20px' }}>
        <RulePacksSection />
      </div>

      {toast && <Toast msg={toast.msg} type={toast.type} />}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
        @keyframes slideUp {
          from { transform: translateY(10px); opacity: 0 }
          to   { transform: translateY(0);    opacity: 1 }
        }
      `}</style>
    </div>
  )
}
