import { Fragment, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence, useInView, useReducedMotion } from 'framer-motion'
import {
  ArrowRight, ArrowUpRight, Plus, Zap, FileSearch, ShieldCheck, Cpu, Rocket, Workflow,
  CheckCircle, FileCode2, Lock, Gauge, Activity, Github, Container, LineChart, Send, Chrome, Bot, Cog,
} from 'lucide-react'
import './landing.css'

// ---------------------------------------------------------------------------
// Traffic waveform — a field of sine lines that reads as live load on a system
// ---------------------------------------------------------------------------

function TrafficCanvas() {
  const ref = useRef<HTMLCanvasElement>(null)
  const reduce = useReducedMotion()

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const LINES = 24
    let w = 0, h = 0, raf = 0, t = 0, visible = true

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = canvas.clientWidth
      h = canvas.clientHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    const draw = () => {
      ctx.clearRect(0, 0, w, h)
      for (let i = 0; i < LINES; i++) {
        const k = i / (LINES - 1)
        const y0 = h * 0.7 + (i - LINES / 2) * 7
        const highlight = i % 6 === 0
        ctx.beginPath()
        for (let x = 0; x <= w; x += 6) {
          const env = Math.pow(Math.sin(Math.PI * (x / w)), 1.6)
          const y = y0 + env * (
            Math.sin(x * 0.0055 + t * 0.8 + i * 0.22) * 46 +
            Math.sin(x * 0.0125 - t * 1.25 + i * 0.37) * 18 +
            Math.sin(x * 0.03 + t * 2.1 + k * 6) * 4
          )
          if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
        }
        ctx.strokeStyle = highlight
          ? `rgba(255, 255, 255, ${0.10 + 0.08 * Math.sin(t + i)})`
          : `rgba(222, 83, 83, ${0.06 + 0.22 * Math.sin(Math.PI * k)})`
        ctx.lineWidth = highlight ? 1.2 : 1
        ctx.stroke()
      }
    }

    const loop = () => {
      if (visible) { t += 0.012; draw() }
      raf = requestAnimationFrame(loop)
    }

    resize()
    window.addEventListener('resize', resize)
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting })
    io.observe(canvas)
    if (reduce) draw(); else loop()

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      io.disconnect()
    }
  }, [reduce])

  return <canvas ref={ref} className="lp-hero-canvas" aria-hidden="true" />
}

// ---------------------------------------------------------------------------
// Pipeline demo — HAR requests stream in, tokens get detected, then correlated
// into variables, then a JMX script and a load chart come out the other end.
// ---------------------------------------------------------------------------

const DEMO_REQUESTS = [
  { method: 'POST', path: '/api/auth/login', token: 'eyJhbGci…' },
  { method: 'GET', path: '/api/products?page=1' },
  { method: 'POST', path: '/api/cart', token: '8f3a-21c' },
  { method: 'GET', path: '/api/cart/', token: '8f3a-21c', tokenInPath: true },
  { method: 'POST', path: '/api/checkout', token: 'x9Tq…' },
]
const DEMO_VARS = ['${authToken}', '${cartId}', '${csrfToken}']
const STAGES = ['Parsing HAR', 'Detecting', 'Correlating', 'Generated']
const STAGE_MS = [2200, 1500, 1700, 4200]
const METHOD_COLOR: Record<string, string> = { GET: 'var(--info)', POST: 'var(--success)' }

function PipelineDemo() {
  const reduce = useReducedMotion()
  const [stage, setStage] = useState(reduce ? 3 : 0)
  const [cycle, setCycle] = useState(0)

  useEffect(() => {
    if (reduce) return
    const id = setTimeout(() => {
      if (stage === STAGES.length - 1) { setStage(0); setCycle(c => c + 1) }
      else setStage(stage + 1)
    }, STAGE_MS[stage])
    return () => clearTimeout(id)
  }, [stage, reduce])

  return (
    <div className="lp-demo">
      <div className="lp-demo-head">
        <i /><i /><i />
        <span className="lp-demo-title">checkout_flow.har</span>
        <AnimatePresence mode="wait">
          <motion.span key={stage} className="lp-demo-stage"
            initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.25 }}>
            {STAGES[stage]}
          </motion.span>
        </AnimatePresence>
      </div>

      <div key={cycle}>
        {DEMO_REQUESTS.map((r, i) => {
          const flagged = stage >= 1 && !!r.token
          return (
            <motion.div key={i} className={`lp-req${flagged ? ' has-token' : ''}`}
              initial={reduce ? false : { opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.28, duration: 0.4, ease: 'easeOut' }}>
              <span className="lp-method" style={{ color: METHOD_COLOR[r.method] }}>{r.method}</span>
              <span className="lp-path">
                {r.path}
                {r.tokenInPath && (stage >= 2
                  ? <span className="lp-token">{'${cartId}'}</span>
                  : <span style={{ color: flagged ? '#fff' : undefined }}>{r.token}</span>)}
              </span>
              {r.token && !r.tokenInPath && flagged && (
                <motion.span className="lp-token" initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }}>
                  {r.token}
                </motion.span>
              )}
              <span className="lp-status">200</span>
            </motion.div>
          )
        })}

        <div className="lp-vars">
          {stage >= 2 && DEMO_VARS.map((v, i) => (
            <motion.span key={v} className="lp-var"
              initial={reduce ? false : { opacity: 0, y: 10, scale: 0.9 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ delay: i * 0.18, type: 'spring', stiffness: 260, damping: 18 }}>
              <CheckCircle size={13} /> {v}
            </motion.span>
          ))}
        </div>

        <AnimatePresence>
          {stage >= 3 && (
            <motion.div initial={reduce ? false : { opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.4 }}>
              <div className="lp-out">
                <div className="lp-out-icon"><FileCode2 size={18} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '14px', fontWeight: 600 }}>checkout_flow.jmx</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>5 samplers · 3 extractors · ready to run</div>
                </div>
                <CheckCircle size={18} style={{ color: 'var(--success)' }} />
              </div>
              <div className="lp-chart">
                <div className="lp-chart-head"><span>Response time · p95</span><b>182 ms</b></div>
                <svg viewBox="0 0 300 70" preserveAspectRatio="none" aria-hidden="true">
                  <defs>
                    <linearGradient id="lpFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0" stopColor="#de5353" stopOpacity="0.35" />
                      <stop offset="1" stopColor="#de5353" stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  <motion.path d="M0,60 L20,52 L40,55 L60,40 L80,44 L100,30 L120,34 L140,22 L160,28 L180,18 L200,24 L220,16 L240,20 L260,12 L280,17 L300,10 L300,70 L0,70 Z"
                    fill="url(#lpFill)" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1, duration: 1 }} />
                  <path className="line" d="M0,60 L20,52 L40,55 L60,40 L80,44 L100,30 L120,34 L140,22 L160,28 L180,18 L200,24 L220,16 L240,20 L260,12 L280,17 L300,10"
                    fill="none" stroke="#de5353" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
                </svg>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="lp-float lp-float-1">
        <Zap size={16} style={{ color: 'var(--accent)' }} />
        <div>6 passes<small>correlation engine</small></div>
      </div>
      <div className="lp-float lp-float-2">
        <Lock size={15} style={{ color: 'var(--success)' }} />
        <div>100% local<small>nothing leaves your machine</small></div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function CountUp({ to, prefix = '', suffix = '' }: { to: number; prefix?: string; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, margin: '-80px' })
  const reduce = useReducedMotion()
  const [n, setN] = useState(reduce ? to : 0)

  useEffect(() => {
    if (!inView || reduce) return
    let raf = 0
    const start = performance.now()
    const tick = (now: number) => {
      const p = Math.min((now - start) / 1400, 1)
      setN(Math.round(to * (1 - Math.pow(1 - p, 3))))
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [inView, to, reduce])

  return <span ref={ref}>{prefix}{n}<em>{suffix}</em></span>
}

const reveal = {
  initial: { opacity: 0, y: 28 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: '-80px' },
  transition: { duration: 0.6, ease: [0.22, 1, 0.36, 1] },
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <div className="lp-eyebrow"><span className="lp-eyebrow-dot" />{children}</div>
}

function SpotlightCard({ children, delay }: { children: React.ReactNode; delay: number }) {
  return (
    <motion.div className="lp-card" {...reveal} transition={{ ...reveal.transition, delay }}
      onMouseMove={e => {
        const r = e.currentTarget.getBoundingClientRect()
        e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`)
        e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`)
      }}>
      {children}
    </motion.div>
  )
}

function FaqItem({ q, a }: { q: string; a: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`lp-faq-item${open ? ' open' : ''}`}>
      <button className="lp-faq-q" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span>{q}</span>
        <span className="lp-faq-icon"><Plus size={16} /></span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }} style={{ overflow: 'hidden' }}>
            <p className="lp-faq-a">{a}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Content
// ---------------------------------------------------------------------------

const HEADLINE: { text: string; accent?: boolean }[] = [
  { text: 'Record' }, { text: 'once.' }, { text: 'Load test', accent: true }, { text: 'everything.' },
]

const stats = [
  { to: 6, suffix: '', label: 'Correlation passes, rule-based plus AI' },
  { to: 2, prefix: '<', suffix: ' min', label: 'From HAR file to a runnable script' },
  { to: 100, suffix: '%', label: 'Local execution — no cloud uploads' },
  { to: 5, suffix: '+', label: 'Export targets: JMX, k6, CI, Grafana, Postman' },
]

const capabilities = [
  { icon: Zap, tag: 'Multi-pass correlation', title: 'Never miss a dynamic token again', desc: 'JWTs, JSON keys, headers, cookies, path IDs and plain-text values are detected and wired up automatically, so scripts don\'t break in CI.' },
  { icon: FileSearch, tag: 'HAR parser', title: 'Skip manual request mapping', desc: 'Drop in a HAR file. Every request, header, cookie and payload is extracted, filtered and ready to script.' },
  { icon: ShieldCheck, tag: 'Privacy first', title: 'Your data stays on your machine', desc: 'No SaaS, no uploads. A built-in scanner flags PII, API keys and bearer tokens in recordings before you proceed.' },
  { icon: Cpu, tag: 'AI assist', title: 'AI catches what rules miss', desc: 'An optional LLM pass — Claude, OpenAI, Gemini, Groq or a local Ollama model — reviews edge-case values the rules leave behind.' },
  { icon: Rocket, tag: 'JMX generator', title: 'Recording to running in minutes', desc: 'JMeter 5.x scripts with extractors, variables, think times and transaction controllers — no hand edits needed.' },
  { icon: Workflow, tag: 'CI/CD export', title: 'Ship tests with your pipeline', desc: 'Generate GitHub Actions and Jenkins pipelines and k6 scripts alongside your JMX, bundled in a single ZIP.' },
]

const steps = [
  { title: 'Record & upload', desc: 'Export a HAR from Chrome DevTools → Network and drop it in.' },
  { title: 'Correlate', desc: 'The engine finds every token, session ID and CSRF value and links extractor to usage.' },
  { title: 'Generate', desc: 'Set the load profile and think times, preview, then export the JMX.' },
  { title: 'Run & analyse', desc: 'Execute with local JMeter and read throughput, p90/p95 and errors in the report.' },
]

const integrations = [
  { name: 'Apache JMeter', icon: Cog }, { name: 'k6', icon: Gauge }, { name: 'GitHub Actions', icon: Github },
  { name: 'Jenkins', icon: Container }, { name: 'Grafana', icon: LineChart }, { name: 'Postman', icon: Send },
  { name: 'Chrome DevTools', icon: Chrome }, { name: 'Claude · OpenAI · Ollama', icon: Bot },
]

const faqs = [
  { q: 'Do I need a cloud account or subscription?', a: 'No. ProjectStack runs on your machine. There are no accounts, no SaaS fees and no cloud uploads — your recordings and scripts stay local.' },
  { q: 'Which JMeter version does it target?', a: 'Generated JMX files work with JMeter 5.4 and above, including RegEx and JSON extractors, Groovy post-processors and constant timers.' },
  { q: 'How does the AI correlation work?', a: 'Five rule-based passes run first (JWT, JSON body, headers and cookies, plain text, backward inference). An optional LLM pass then reviews what\'s left, using Ollama locally or a configured provider.' },
  { q: 'Can I export to formats other than JMX?', a: 'Yes — k6 scripts, GitHub Actions workflows, Jenkins pipelines, Grafana dashboards, Postman collections, and ZIP bundles of all of them.' },
  { q: 'Does it handle sensitive data in HAR files?', a: 'A built-in scanner checks URLs and payloads for emails, card numbers, API keys, JWTs and bearer tokens before you continue.' },
]

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function LandingPage() {
  const navigate = useNavigate()
  const reduce = useReducedMotion()
  const [scrolled, setScrolled] = useState(false)
  const stepsRef = useRef<HTMLDivElement>(null)
  const stepsInView = useInView(stepsRef, { once: true, margin: '-120px' })

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  const launch = () => navigate('/app')

  return (
    <div className="lp">
      {/* ── Nav ── */}
      <nav className={`lp-nav${scrolled ? ' is-scrolled' : ''}`}>
        <div className="lp-wrap lp-nav-inner">
          <button className="lp-logo" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <span className="lp-logo-mark">PS</span>
            <span className="lp-logo-text">ProjectStack</span>
          </button>
          <div className="lp-links">
            <button onClick={() => scrollTo('capabilities')}>Capabilities</button>
            <button onClick={() => scrollTo('how-it-works')}>How it works</button>
            <button onClick={() => scrollTo('faqs')}>FAQs</button>
          </div>
          <button className="lp-btn lp-btn-primary lp-btn-sm" onClick={launch}>
            Launch app <ArrowUpRight size={15} />
          </button>
        </div>
      </nav>

      {/* ── Hero ── */}
      <section className="lp-hero">
        <TrafficCanvas />
        <div className="lp-orb" />
        <div className="lp-hero-fade" />

        <div className="lp-wrap lp-hero-grid">
          <div>
            <motion.div initial={reduce ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
              <Eyebrow>AI-powered performance engineering</Eyebrow>
            </motion.div>

            <h1 className="lp-h1">
              {HEADLINE.map((w, i) => (
                <Fragment key={i}>
                  <span className="w">
                    <motion.span className={w.accent ? 'accent' : undefined}
                      initial={reduce ? false : { y: '110%' }} animate={{ y: 0 }}
                      transition={{ delay: 0.15 + i * 0.1, duration: 0.8, ease: [0.22, 1, 0.36, 1] }}>
                      {w.text}
                    </motion.span>
                  </span>{' '}
                </Fragment>
              ))}
            </h1>

            <motion.p className="lp-lede" initial={reduce ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.65, duration: 0.6 }}>
              Turn a browser recording into a correlated, production-ready JMeter script.
              ProjectStack finds every dynamic token for you, then runs the test and reports on it.
            </motion.p>

            <motion.div className="lp-cta-row" initial={reduce ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.8, duration: 0.6 }}>
              <button className="lp-btn lp-btn-primary" onClick={launch}>Generate your first script <ArrowRight size={16} /></button>
              <button className="lp-btn lp-btn-ghost" onClick={() => scrollTo('how-it-works')}>See how it works</button>
            </motion.div>

            <motion.div className="lp-trust" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.1, duration: 0.6 }}>
              <span><Lock size={14} /> Runs locally</span>
              <span><Activity size={14} /> JMeter 5.x output</span>
              <span><CheckCircle size={14} /> No sign-up</span>
            </motion.div>
          </div>

          <motion.div className="lp-demo-col" initial={reduce ? false : { opacity: 0, y: 40, rotateX: 8 }} animate={{ opacity: 1, y: 0, rotateX: 0 }}
            transition={{ delay: 0.4, duration: 0.9, ease: [0.22, 1, 0.36, 1] }} style={{ perspective: 1200 }}>
            <PipelineDemo />
          </motion.div>
        </div>
      </section>

      {/* ── Integrations marquee ── */}
      <div className="lp-marquee" aria-label="Integrations">
        <div className="lp-marquee-track">
          {[...integrations, ...integrations].map((it, i) => (
            <span key={i} className="lp-marquee-item" aria-hidden={i >= integrations.length}>
              <it.icon size={20} /> {it.name}
            </span>
          ))}
        </div>
      </div>

      {/* ── Stats ── */}
      <section className="lp-section">
        <div className="lp-wrap">
          <div className="lp-stats">
            {stats.map((s, i) => (
              <motion.div key={s.label} className="lp-stat" {...reveal} transition={{ ...reveal.transition, delay: i * 0.08 }}>
                <div className="lp-stat-value"><CountUp to={s.to} prefix={s.prefix} suffix={s.suffix} /></div>
                <div className="lp-stat-label">{s.label}</div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Capabilities ── */}
      <section id="capabilities" className="lp-section" style={{ paddingTop: 0 }}>
        <div className="lp-wrap">
          <motion.div className="lp-section-head" {...reveal}>
            <div>
              <Eyebrow>Capabilities</Eyebrow>
              <h2 className="lp-h2">Stop losing hours to broken correlations</h2>
            </div>
            <p className="lp-sub">
              Every missed token is a failed test, and every failed test delays a release.
              ProjectStack handles the manual work so you can focus on the results.
            </p>
          </motion.div>

          <div className="lp-cards">
            {capabilities.map((c, i) => (
              <SpotlightCard key={c.title} delay={(i % 3) * 0.08}>
                <div className="lp-card-icon"><c.icon size={22} /></div>
                <div className="lp-card-tag">{c.tag}</div>
                <h3>{c.title}</h3>
                <p>{c.desc}</p>
              </SpotlightCard>
            ))}
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section id="how-it-works" className="lp-section" style={{ paddingTop: 0 }}>
        <div className="lp-wrap">
          <motion.div className="lp-section-head" {...reveal}>
            <div>
              <Eyebrow>How it works</Eyebrow>
              <h2 className="lp-h2">Four steps to a production-ready test</h2>
            </div>
            <p className="lp-sub">No complex setup and no cloud — just your HAR file and JMeter.</p>
          </motion.div>

          <div className="lp-steps" ref={stepsRef}>
            <div className="lp-steps-rail">
              <motion.div initial={{ scaleX: reduce ? 1 : 0 }} animate={{ scaleX: stepsInView ? 1 : reduce ? 1 : 0 }}
                transition={{ duration: 1.6, ease: [0.22, 1, 0.36, 1] }} />
            </div>
            {steps.map((s, i) => (
              <motion.div key={s.title} className="lp-step"
                initial={reduce ? false : { opacity: 0, y: 24 }} animate={stepsInView ? { opacity: 1, y: 0 } : undefined}
                transition={{ delay: 0.2 + i * 0.3, duration: 0.6 }}>
                <motion.div className="lp-step-num"
                  animate={stepsInView ? { borderColor: 'rgba(222,83,83,0.8)', boxShadow: '0 0 0 6px rgba(222,83,83,0.1)' } : undefined}
                  transition={{ delay: 0.2 + i * 0.3, duration: 0.5 }}>
                  {String(i + 1).padStart(2, '0')}
                </motion.div>
                <h3>{s.title}</h3>
                <p>{s.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ── FAQs ── */}
      <section id="faqs" className="lp-section" style={{ paddingTop: 0 }}>
        <div className="lp-wrap lp-faq-grid">
          <motion.div {...reveal}>
            <Eyebrow>FAQs</Eyebrow>
            <h2 className="lp-h2">Common questions</h2>
            <p className="lp-sub" style={{ marginTop: '20px' }}>Everything you need to know before you record your first flow.</p>
          </motion.div>
          <motion.div className="lp-faq" {...reveal} transition={{ ...reveal.transition, delay: 0.1 }}>
            {faqs.map(f => <FaqItem key={f.q} q={f.q} a={f.a} />)}
          </motion.div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="lp-wrap">
        <motion.div className="lp-cta" {...reveal}>
          <div className="lp-cta-rings" aria-hidden="true"><span /><span /><span /></div>
          <div style={{ position: 'relative' }}>
            <Eyebrow>Get started</Eyebrow>
            <h2 className="lp-h2" style={{ maxWidth: '760px', margin: '16px auto 20px' }}>
              Your first correlated script, in under two minutes
            </h2>
            <p className="lp-sub" style={{ maxWidth: '520px', margin: '0 auto 40px' }}>
              Upload a HAR file and get a clean JMeter script that's ready to run. Free, local, no sign-up.
            </p>
            <button className="lp-btn lp-btn-primary" onClick={launch}>Open ProjectStack <ArrowRight size={16} /></button>
          </div>
        </motion.div>
      </section>

      {/* ── Footer ── */}
      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-inner">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span className="lp-logo-mark" style={{ width: '30px', height: '30px', fontSize: '11px' }}>PS</span>
            <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>ProjectStack</span>
            <span>— HAR to JMeter, automated.</span>
          </div>
          <span>MIT License</span>
        </div>
      </footer>
    </div>
  )
}
