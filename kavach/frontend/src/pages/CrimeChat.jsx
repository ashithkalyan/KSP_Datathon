import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Send, Mic, MicOff, FileDown, Trash2,
  ChevronDown, ChevronRight, AlertCircle, Zap, HelpCircle,
  Network as NetIcon, User, History, Plus, MessageSquare, ShieldCheck
} from 'lucide-react'
import { sendChatMessage, getChatHistory, getChatSessions, exportChatHistoryPdf } from '../services/api'
import Header from '../components/Header'
import MiniNetworkGraph from '../components/MiniNetworkGraph'
import { useLanguage } from '../i18n/LanguageContext'

const STARTERS = {
  en: [
    'Show me repeat offenders in Bengaluru with 3+ convictions',
    'List all murder cases in Mysuru from 2023 onwards',
    'Which police station has the highest theft cases?',
    'Show high-risk accused in the Hubballi Drug Syndicate',
    'Find all cybercrime cases with property value above 1 lakh',
    'Show gang-affiliated accused with EXTREME risk score',
  ],
  kn: [
    'ಬೆಂಗಳೂರಿನಲ್ಲಿ 3+ ಶಿಕ್ಷೆಗಳೊಂದಿಗೆ ಪುನರಾವರ್ತಿತ ಅಪರಾಧಿಗಳನ್ನು ತೋರಿಸಿ',
    '2023 ರಿಂದ ಮೈಸೂರಿನಲ್ಲಿ ಎಲ್ಲಾ ಕೊಲೆ ಪ್ರಕರಣಗಳನ್ನು ಪಟ್ಟಿ ಮಾಡಿ',
    'ಯಾವ ಪೊಲೀಸ್ ಠಾಣೆಯಲ್ಲಿ ಅತಿ ಹೆಚ್ಚು ಕಳ್ಳತನ ಪ್ರಕರಣಗಳಿವೆ?',
    'ಗ್ಯಾಂಗ್ ಸಂಬಂಧಿತ ಆರೋಪಿಗಳನ್ನು EXTREME ಅಪಾಯದ ಅಂಕದೊಂದಿಗೆ ತೋರಿಸಿ',
  ],
}

const RISK_COLORS = { EXTREME: '#C0392B', HIGH: '#E67E22', MEDIUM: '#F39C12', LOW: '#0F7A5A' }

function RiskBadge({ risk }) {
  return (
    <span className={`risk-badge risk-${risk}`}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: RISK_COLORS[risk], flexShrink: 0 }} />
      {risk}
    </span>
  )
}

function ResultCard({ row, onViewNetwork, onViewProfile }) {
  if (row.accused_id) return (
    <div style={{
      background: '#fff', border: '1px solid #E2E8F0', borderRadius: 6,
      padding: '10px 12px', animation: 'fadeIn 0.25s ease-out',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
        <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#1E293B' }}>{row.name}</div>
        {row.risk_category && <RiskBadge risk={row.risk_category} />}
      </div>
      <div style={{ fontSize: '0.68rem', color: '#64748B', lineHeight: 1.6 }}>
        {row.age && <span>Age: {row.age} • </span>}
        {row.gender && <span>{row.gender} • </span>}
        {row.district && <span>{row.district}</span>}
        {row.prior_convictions > 0 && <div style={{ color: '#C0392B', fontWeight: 600, marginTop: 3 }}>⚠ {row.prior_convictions} prior conviction(s)</div>}
        {row.gang_affiliation && <div style={{ color: '#7E22CE', marginTop: 2 }}>🔗 {row.gang_affiliation}</div>}
        {row.modus_operandi && <div style={{ color: '#475569', marginTop: 3, fontSize: '0.65rem', fontStyle: 'italic' }}>MO: {row.modus_operandi?.slice(0,60)}…</div>}
      </div>
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button onClick={() => onViewProfile(row.accused_id)} style={{ fontSize: '0.65rem', padding: '3px 8px', border: '1px solid #E2E8F0', borderRadius: 4, background: '#F8FAFC', cursor: 'pointer', color: '#475569', display: 'flex', alignItems: 'center', gap: 4 }}>
          <User size={10} />Profile
        </button>
        <button onClick={() => onViewNetwork(row.accused_id)} style={{ fontSize: '0.65rem', padding: '3px 8px', border: '1px solid #BFDBFE', borderRadius: 4, background: '#EFF6FF', cursor: 'pointer', color: '#1D4ED8', display: 'flex', alignItems: 'center', gap: 4 }}>
          <NetIcon size={10} />Network
        </button>
      </div>
    </div>
  )

  if (row.fir_number) return (
    <div style={{
      background: '#fff', border: '1px solid #E2E8F0', borderRadius: 6,
      padding: '10px 12px', animation: 'fadeIn 0.25s ease-out',
    }}>
      <div style={{ fontFamily: 'monospace', fontSize: '0.72rem', color: '#1D4ED8', fontWeight: 700, marginBottom: 4 }}>
        {row.fir_number}
      </div>
      <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#1E293B', marginBottom: 4 }}>{row.crime_type || row.crime_description?.slice(0,50)}</div>
      <div style={{ fontSize: '0.68rem', color: '#64748B', lineHeight: 1.5 }}>
        {row.district && <span>{row.district}</span>}
        {row.police_station && <span> • {row.police_station}</span>}
        {row.registration_date && <span> • {row.registration_date}</span>}
        {row.property_value > 0 && <div style={{ marginTop: 2 }}>₹{row.property_value?.toLocaleString('en-IN')}</div>}
      </div>
      {row.status && (
        <div style={{ marginTop: 6 }}>
          <span className={`status-pill ${
            row.status === 'Under Investigation' ? 'status-open' :
            row.status === 'Charge-Sheeted' ? 'status-sheeted' :
            row.status === 'Closed' ? 'status-closed' : 'status-filed'
          }`}>{row.status}</span>
        </div>
      )}
    </div>
  )

  // Generic row
  return (
    <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 6, padding: '8px 10px', fontSize: '0.72rem', color: '#334155', animation: 'fadeIn 0.2s ease-out' }}>
      {Object.entries(row).slice(0, 5).map(([k, v]) => v && (
        <div key={k} style={{ display: 'flex', gap: 6, marginBottom: 2 }}>
          <span style={{ color: '#94A3B8', minWidth: 80, flexShrink: 0 }}>{k.replace(/_/g,' ')}:</span>
          <span style={{ fontWeight: 500 }}>{String(v).slice(0, 60)}</span>
        </div>
      ))}
    </div>
  )
}

function AIMessage({ msg, onViewNetwork, onViewProfile, onSuggestionClick, t }) {
  const [showSQL, setShowSQL] = useState(false)
  const [showTrace, setShowTrace] = useState(false)
  const [showIdentity, setShowIdentity] = useState(false)
  const isClarification = !!msg.needs_clarification

  return (
    <div className="msg-row">
      <div className="msg-avatar avatar-ai" style={isClarification ? { background: '#7E22CE' } : undefined}>
        {isClarification ? '?' : 'AI'}
      </div>
      <div style={{ flex: 1, maxWidth: '72%' }}>
        <div
          className="msg-bubble bubble-ai"
          style={isClarification ? { background: '#FAF5FF', border: '1px solid #E9D5FF' } : undefined}
        >
          {/* Memory recall banner */}
          {msg.memory_recalled && (
            <div style={{
              display: 'flex', alignItems: 'flex-start', gap: 6,
              background: '#F5F3FF', border: '1px solid #E9D5FF',
              borderRadius: 5, padding: '6px 10px', marginBottom: 8,
              fontSize: '0.7rem', color: '#6B21A8',
            }}>
              <span>🧠</span>
              <span>Recalled from your {msg.memory_recalled.date} session: "{msg.memory_recalled.text}"</span>
            </div>
          )}

          {/* Intent label (or a "needs clarification" label instead) */}
          {isClarification ? (
            <div style={{ fontSize: '0.65rem', color: '#7E22CE', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
              <HelpCircle size={11} color="#7E22CE" />
              {t('chatClarifying')}
            </div>
          ) : msg.intent && (
            <div style={{ fontSize: '0.65rem', color: '#94A3B8', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Zap size={10} color="#C5A028" />
              {msg.intent}
            </div>
          )}

          {/* Interpretation */}
          <div style={{ fontSize: '0.82rem', lineHeight: 1.65, color: '#1E293B', whiteSpace: 'pre-wrap' }}>
            {msg.interpretation}
          </div>

          {/* Small inline network snapshot — only present when the brain
              actually found a connected network worth showing; a static
              glance-visual, not the full interactive Network page. */}
          {msg.network_snapshot && <MiniNetworkGraph snapshot={msg.network_snapshot} />}

          {/* Alias resolution note */}
          {msg.alias_matches?.length > 0 && msg.alias_matches.some(m => m.method !== 'exact') && (
            <div style={{
              marginTop: 8, padding: '7px 10px', background: '#EFF6FF',
              border: '1px solid #BFDBFE', borderRadius: 5, fontSize: '0.7rem', color: '#1D4ED8',
            }}>
              🔍 <strong>Name resolution:</strong>{' '}
              {msg.alias_matches.filter(m => m.method !== 'exact').slice(0, 3).map((m, i) => (
                <span key={i}>{i > 0 ? ', ' : ''}{m.name} <em>({m.method.replace('_', ' ')}, {Math.round(m.confidence * 100)}%)</em></span>
              ))}
            </div>
          )}

          {/* Insights */}
          {msg.insights && msg.insights !== msg.interpretation && (
            <div style={{
              marginTop: 10, padding: '8px 10px',
              background: '#FFFBEB', border: '1px solid #FDE68A',
              borderRadius: 5, fontSize: '0.72rem', color: '#78350F',
            }}>
              <strong>📊 Insight:</strong> {msg.insights}
            </div>
          )}

          {/* Result count — never rendered for clarification turns, since
              those never carry results by design (see brain.py) */}
          {msg.result_count > 0 && (
            <div style={{ marginTop: 8, fontSize: '0.68rem', color: '#64748B' }}>
              {msg.result_count} record{msg.result_count !== 1 ? 's' : ''} found
              {msg.result_count > 10 && ' (showing top 10 in panel)'}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
            {/* SQL pill */}
            {msg.sql_generated && (
              <div>
                <button className="sql-pill" onClick={() => setShowSQL(!showSQL)}>
                  <span style={{ color: '#C5A028', fontFamily: 'monospace' }}>⌗</span>
                  SQL Query
                  {showSQL ? <ChevronDown size={10}/> : <ChevronRight size={10}/>}
                </button>
              </div>
            )}
            {/* Explainability / pipeline trace pill */}
            {msg.pipeline_trace?.length > 0 && (
              <div>
                <button className="sql-pill" onClick={() => setShowTrace(!showTrace)}>
                  <span style={{ color: '#7E22CE' }}>◈</span>
                  {t('chatWhyThisAnswer')}
                  {showTrace ? <ChevronDown size={10}/> : <ChevronRight size={10}/>}
                </button>
              </div>
            )}
            {/* Identity reasoning trace pill — real audit data that was
                computed on every person-lookup but never surfaced */}
            {msg.identity_reasoning_trace && (
              <div>
                <button className="sql-pill" onClick={() => setShowIdentity(!showIdentity)}>
                  <ShieldCheck size={11} color="#0F7A5A" />
                  {t('chatIdentityConfidence')}
                  {showIdentity ? <ChevronDown size={10}/> : <ChevronRight size={10}/>}
                </button>
              </div>
            )}
          </div>
          {showSQL && <div className="sql-code">{msg.sql_generated}</div>}
          {showTrace && (
            <div style={{
              marginTop: 6, background: '#FAF5FF', border: '1px solid #E9D5FF',
              borderRadius: 4, padding: '8px 10px', fontSize: '0.68rem', color: '#581C87',
            }}>
              {msg.pipeline_trace.map((step, i) => (
                <div key={i} style={{ marginBottom: 3, display: 'flex', gap: 6 }}>
                  <span style={{ color: '#A855F7', flexShrink: 0 }}>{i + 1}.</span>
                  <span>{step}</span>
                </div>
              ))}
            </div>
          )}
          {showIdentity && msg.identity_reasoning_trace && (
            <div style={{
              marginTop: 6, background: '#F0FDF4', border: '1px solid #BBF7D0',
              borderRadius: 4, padding: '8px 10px', fontSize: '0.68rem', color: '#14532D',
            }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>
                {msg.identity_reasoning_trace.confidence_pct}
              </div>
              <div>{msg.identity_reasoning_trace.officer_summary || msg.identity_reasoning_trace.conclusion}</div>
            </div>
          )}
        </div>

        {/* Follow-up suggestions */}
        {msg.follow_up_suggestions?.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
            {msg.follow_up_suggestions.map((s, i) => (
              <button key={i} className="suggestion-chip" onClick={() => onSuggestionClick(s)}>
                <ChevronRight size={10} />
                {s}
              </button>
            ))}
          </div>
        )}

        <div style={{ fontSize: '0.6rem', color: '#CBD5E1', marginTop: 5 }}>{msg.timestamp}</div>
      </div>
    </div>
  )
}

function UserMessage({ text, time }) {
  return (
    <div className="msg-row user">
      <div className="msg-avatar avatar-user">YOU</div>
      <div>
        <div className="msg-bubble bubble-user">{text}</div>
        <div style={{ fontSize: '0.6rem', color: '#CBD5E1', marginTop: 4, textAlign: 'right' }}>{time}</div>
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="msg-row">
      <div className="msg-avatar avatar-ai">AI</div>
      <div className="msg-bubble bubble-ai" style={{ padding: '12px 16px' }}>
        <div className="typing-dots">
          <div className="typing-dot" />
          <div className="typing-dot" />
          <div className="typing-dot" />
        </div>
      </div>
    </div>
  )
}

export default function CrimeChat({ user }) {
  const { language, t } = useLanguage()
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  const [recording, setRecording] = useState(false)
  const [micNotice, setMicNotice] = useState(null)
  const [panelResults, setPanelResults] = useState([])
  const [panelTitle, setPanelTitle] = useState('Query Results')
  const [showHistory, setShowHistory] = useState(false)
  const [pastSessions, setPastSessions] = useState([])
  const [restoring, setRestoring] = useState(true)
  const [exporting, setExporting] = useState(false)
  const messagesEndRef = useRef(null)
  const recognitionRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const welcomeMessage = useCallback(() => ({
    type: 'ai',
    interpretation: language === 'kn'
      ? `ನಮಸ್ಕಾರ ${user?.full_name?.split(' ')[1] || 'ಅಧಿಕಾರಿ'}. ನಾನು ಕವಚ-AI, ನಿಮ್ಮ ಬುದ್ಧಿವಂತ ಅಪರಾಧ ವಿಶ್ಲೇಷಣಾ ಸಹಾಯಕ.\n\nFIR ದಾಖಲೆಗಳು, ಆರೋಪಿಗಳ ಪ್ರೊಫೈಲ್‌ಗಳು, ಅಪರಾಧ ಪ್ರವೃತ್ತಿಗಳು, ಗ್ಯಾಂಗ್ ಜಾಲಗಳು ಅಥವಾ ಪುನರಾವರ್ತಿತ ಅಪರಾಧಿಗಳ ಬಗ್ಗೆ ನೀವು ಸಹಜ ಭಾಷೆಯಲ್ಲಿ ಏನನ್ನಾದರೂ ಕೇಳಬಹುದು.`
      : `Namaskara ${user?.full_name?.split(' ')[1] || 'Officer'}. I am KAVACH-AI, your intelligent crime analytics assistant.\n\nYou can ask me anything about FIR records, accused profiles, crime trends, gang networks, or repeat offenders — in natural language. Try a query below or use your voice.`,
    intent: 'Welcome', sql_generated: null, insights: null,
    follow_up_suggestions: STARTERS[language].slice(0, 3), result_count: 0,
    timestamp: new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }),
    results: [],
  }), [user, language])

  // Restore the last active session on mount — fixes losing the whole
  // conversation on every page refresh. sessionStorage (not localStorage)
  // deliberately: a shared/kiosk machine shouldn't keep another officer's
  // conversation alive after the browser tab closes — and App.jsx clears
  // this key on every fresh login, so signing out and back in always
  // starts a clean conversation too.
  useEffect(() => {
    const savedId = sessionStorage.getItem('kavach_active_chat_session')
    if (savedId) {
      loadSession(savedId).finally(() => setRestoring(false))
    } else {
      setMessages([welcomeMessage()])
      setRestoring(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadSession(sid) {
    try {
      const data = await getChatHistory(sid)
      if (!data.history || data.history.length === 0) {
        setMessages([welcomeMessage()])
        setSessionId(null)
        sessionStorage.removeItem('kavach_active_chat_session')
        return
      }
      const rehydrated = data.history.map(turn => {
        const time = turn.timestamp
          ? new Date(turn.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
          : ''
        return turn.role === 'user'
          ? { type: 'user', text: turn.text, time }
          : { type: 'ai', interpretation: turn.text, intent: '', sql_generated: null,
              insights: null, follow_up_suggestions: [], result_count: 0, results: [], timestamp: time }
      })
      setMessages(rehydrated)
      setSessionId(sid)
      sessionStorage.setItem('kavach_active_chat_session', sid)
    } catch {
      setMessages([welcomeMessage()])
    }
    setShowHistory(false)
  }

  async function loadSessionsList() {
    try {
      const data = await getChatSessions()
      setPastSessions(data.sessions || [])
    } catch { /* history sidebar is a nice-to-have, fail quietly */ }
  }

  function startNewSession() {
    setMessages([welcomeMessage()])
    setSessionId(null)
    setPanelResults([])
    sessionStorage.removeItem('kavach_active_chat_session')
    setShowHistory(false)
  }

  const send = useCallback(async (text = input.trim()) => {
    if (!text || loading) return
    setInput('')
    setMicNotice(null)
    const time = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })

    setMessages(prev => [...prev, { type: 'user', text, time }])
    setLoading(true)

    try {
      const res = await sendChatMessage(text, sessionId, language)
      if (!sessionId) {
        setSessionId(res.session_id)
        sessionStorage.setItem('kavach_active_chat_session', res.session_id)
      }

      setMessages(prev => [...prev, {
        type: 'ai',
        ...res,
        timestamp: new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }),
      }])

      // Update side panel
      if (res.results?.length > 0) {
        setPanelResults(res.results.slice(0, 15))
        setPanelTitle(`${res.result_count} Result${res.result_count !== 1 ? 's' : ''} — ${res.intent || 'Query'}`)
      }

      // Text-to-speech — now symmetric across both languages (previously
      // this only ever fired for Kannada, so an officer working in
      // English never got a spoken reply at all, which is one plausible
      // reading of "voice doesn't work when the language changes").
      if (res.interpretation && window.speechSynthesis) {
        const utt = new SpeechSynthesisUtterance(res.interpretation)
        utt.lang = language === 'kn' ? 'kn-IN' : 'en-IN'
        window.speechSynthesis.speak(utt)
      }
    } catch (err) {
      setMessages(prev => [...prev, {
        type: 'ai',
        interpretation: 'Connection error. Ensure the KAVACH backend is running on port 8000.',
        intent: 'Error',
        sql_generated: null, insights: null, follow_up_suggestions: [],
        result_count: 0, results: [],
        timestamp: time,
      }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, sessionId, language])

  // Voice input — errors used to fail completely silently (setRecording(false)
  // with no feedback at all), which is almost certainly what "the mic doesn't
  // work" actually meant in practice: it wasn't that recognition never ran,
  // it's that a permission-denied / no-speech / unsupported-language failure
  // gave no sign anything had gone wrong. Every failure path now surfaces a
  // clear, translated message.
  const toggleVoice = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) { setMicNotice(t('chatMicError')); return }
    if (recording) {
      recognitionRef.current?.stop()
      setRecording(false)
      return
    }
    setMicNotice(null)
    const rec = new SR()
    rec.lang = language === 'kn' ? 'kn-IN' : 'en-IN'
    rec.interimResults = false
    rec.onresult = e => {
      const transcript = e.results[0][0].transcript
      setInput(transcript)
      setRecording(false)
      setTimeout(() => send(transcript), 100)
    }
    rec.onerror = (e) => {
      setRecording(false)
      if (e.error === 'not-allowed' || e.error === 'permission-denied') {
        setMicNotice(t('chatMicNotAllowed'))
      } else if (e.error === 'no-speech') {
        setMicNotice(t('chatMicNoSpeech'))
      } else if (e.error === 'language-not-supported' && language === 'kn') {
        setMicNotice(t('chatMicLangUnsupported'))
      } else {
        setMicNotice(t('chatMicError'))
      }
    }
    rec.onend = () => setRecording(false)
    recognitionRef.current = rec
    try {
      rec.start()
      setRecording(true)
    } catch {
      setMicNotice(t('chatMicError'))
    }
  }

  // Backend-generated PDF export (replaces the old client-side jsPDF
  // export, which had no way to embed a Kannada-capable font and would
  // have rendered Kannada chat turns as blank boxes — see
  // backend/services/pdf_export.py). Scoped to the current session; the
  // full "everything since this login" export happens automatically on
  // logout instead (see App.jsx).
  const exportPDF = async () => {
    if (!sessionId || exporting) return
    setExporting(true)
    try {
      const blob = await exportChatHistoryPdf('session', sessionId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `KAVACH-Session-${sessionId}-${Date.now()}.pdf`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('Export failed', e)
    } finally {
      setExporting(false)
    }
  }

  const clearChat = () => startNewSession()

  return (
    <>
      <Header title={t('navChat')} subtitle="Natural Language Crime Query" user={user} />

      <div className="chat-wrap">
        {/* History sidebar */}
        {showHistory && (
          <div style={{
            width: 260, borderRight: '1px solid #E2E8F0', background: '#F8FAFC',
            display: 'flex', flexDirection: 'column', flexShrink: 0,
          }}>
            <div style={{ padding: '12px 14px', borderBottom: '1px solid #E2E8F0', background: '#fff' }}>
              <button onClick={startNewSession} style={{
                width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                padding: '8px', background: '#0B1D3A', color: '#C5A028', border: 'none',
                borderRadius: 6, fontSize: '0.75rem', fontWeight: 600, cursor: 'pointer',
              }}>
                <Plus size={13} /> {t('chatNewChat')}
              </button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
              <div style={{ fontSize: '0.62rem', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.08em', padding: '6px 8px' }}>
                {t('chatHistory')}
              </div>
              {pastSessions.length === 0 ? (
                <div style={{ padding: '1.5rem 1rem', textAlign: 'center', fontSize: '0.7rem', color: '#94A3B8' }}>
                  No past conversations yet
                </div>
              ) : pastSessions.map(s => (
                <button key={s.session_id} onClick={() => loadSession(s.session_id)} style={{
                  display: 'block', width: '100%', textAlign: 'left', padding: '9px 10px',
                  background: s.session_id === sessionId ? '#EFF6FF' : '#fff',
                  border: `1px solid ${s.session_id === sessionId ? '#BFDBFE' : '#E2E8F0'}`,
                  borderRadius: 6, marginBottom: 6, cursor: 'pointer',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 3 }}>
                    <MessageSquare size={10} color="#94A3B8" />
                    <span style={{ fontSize: '0.6rem', color: '#94A3B8' }}>
                      {new Date(s.last_active).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })} · {s.turn_count} turns
                    </span>
                  </div>
                  <div style={{ fontSize: '0.72rem', color: '#1E293B', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.first_message || '(empty)'}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Main chat */}
        <div className="chat-main">
          {/* Sub-header bar */}
          <div style={{
            background: '#fff', borderBottom: '1px solid #E2E8F0',
            padding: '6px 1rem', display: 'flex', alignItems: 'center',
            gap: 10, flexShrink: 0,
          }}>
            <button
              onClick={() => { const next = !showHistory; setShowHistory(next); if (next) loadSessionsList() }}
              title="Conversation history"
              style={{
                display: 'flex', alignItems: 'center', gap: 5, padding: '4px 8px',
                background: showHistory ? '#0B1D3A' : '#F8FAFC', border: '1px solid #E2E8F0',
                borderRadius: 5, cursor: 'pointer', fontSize: '0.7rem', fontWeight: 600,
                color: showHistory ? '#C5A028' : '#475569',
              }}
            >
              <History size={12} /> {t('chatHistory')}
            </button>
            <div style={{
              width: 7, height: 7, borderRadius: '50%',
              background: '#0F7A5A',
              boxShadow: '0 0 0 2px rgba(15,122,90,0.25)',
            }}/>
            <span style={{ fontSize: '0.7rem', color: '#64748B' }}>
              {sessionId ? `Session: ${sessionId}` : 'Ready'} ●{' '}
              {language === 'en' ? 'English' : 'ಕನ್ನಡ'} mode
            </span>
            <div style={{ flex: 1 }} />
            <button onClick={exportPDF} disabled={!sessionId || exporting} title={!sessionId ? 'Send a message first' : undefined} style={{
              display: 'flex', alignItems: 'center', gap: 5,
              fontSize: '0.7rem', padding: '4px 10px',
              background: '#F8FAFC', border: '1px solid #E2E8F0',
              borderRadius: 5, cursor: (!sessionId || exporting) ? 'not-allowed' : 'pointer',
              color: '#475569', opacity: (!sessionId || exporting) ? 0.5 : 1,
            }}>
              <FileDown size={11} /> {exporting ? '…' : t('exportPdf')}
            </button>
            <button onClick={clearChat} style={{
              display: 'flex', alignItems: 'center', gap: 5,
              fontSize: '0.7rem', padding: '4px 10px',
              background: '#FEF2F2', border: '1px solid #FECACA',
              borderRadius: 5, cursor: 'pointer', color: '#991B1B',
            }}>
              <Trash2 size={11} /> Clear
            </button>
          </div>

          {/* Messages */}
          <div className="chat-messages">
            {restoring && (
              <div style={{ textAlign: 'center', padding: '2rem', color: '#94A3B8', fontSize: '0.75rem' }}>
                Restoring your conversation…
              </div>
            )}
            {/* Starters (show when only welcome message) */}
            {!restoring && messages.length <= 1 && (
              <div style={{ padding: '8px 0' }}>
                <p style={{ fontSize: '0.7rem', color: '#94A3B8', marginBottom: 12, textAlign: 'center' }}>
                  — Try one of these queries to get started —
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
                  {STARTERS[language].map((s, i) => (
                    <button key={i} className="suggestion-chip" onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              msg.type === 'user'
                ? <UserMessage key={i} text={msg.text} time={msg.time} />
                : <AIMessage
                    key={i} msg={msg} t={t}
                    onViewNetwork={id => window.open(`/network?focus=${id}`, '_self')}
                    onViewProfile={id => window.open(`/profiles?id=${id}`, '_self')}
                    onSuggestionClick={send}
                  />
            ))}

            {loading && <TypingIndicator />}
            <div ref={messagesEndRef} />
          </div>

          {/* Mic notice — replaces the old silent failure on any voice-input error */}
          {micNotice && (
            <div style={{
              margin: '0 1rem', padding: '6px 10px', background: '#FFFBEB',
              border: '1px solid #FDE68A', borderRadius: 5, fontSize: '0.68rem',
              color: '#78350F', display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <AlertCircle size={12} />
              {micNotice}
            </div>
          )}

          {/* Input bar */}
          <div className="chat-input-bar">
            <button
              className={`voice-btn${recording ? ' recording' : ''}`}
              onClick={toggleVoice}
              title={recording ? 'Stop recording' : `Voice input (${language === 'kn' ? 'Kannada' : 'English'})`}
            >
              {recording ? <MicOff size={15} /> : <Mic size={15} />}
            </button>

            <textarea
              ref={inputRef}
              className="chat-input"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
              placeholder={t('chatPlaceholder')}
              rows={1}
            />

            <button
              onClick={() => send()}
              disabled={!input.trim() || loading}
              style={{
                width: 38, height: 38, borderRadius: 6, border: 'none',
                background: input.trim() && !loading ? '#0B1D3A' : '#E2E8F0',
                color: input.trim() && !loading ? '#C5A028' : '#94A3B8',
                cursor: input.trim() && !loading ? 'pointer' : 'not-allowed',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'all 0.15s', flexShrink: 0,
              }}
            >
              <Send size={14} />
            </button>
          </div>
        </div>

        {/* Side panel */}
        <div className="chat-panel">
          <div style={{
            padding: '10px 14px', background: '#fff',
            borderBottom: '1px solid #E2E8F0', flexShrink: 0,
          }}>
            <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#1E293B' }}>
              {panelTitle}
            </div>
            <div style={{ fontSize: '0.62rem', color: '#94A3B8', marginTop: 2 }}>
              Click any card to explore
            </div>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: '10px' }}>
            {panelResults.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '2rem 1rem' }}>
                <div style={{ fontSize: '1.5rem', marginBottom: 8 }}>🔍</div>
                <div style={{ fontSize: '0.75rem', color: '#94A3B8', lineHeight: 1.6 }}>
                  Query results will appear here after you send a message
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {panelResults.map((r, i) => (
                  <ResultCard
                    key={i} row={r}
                    onViewNetwork={id => window.open(`/network?focus=${id}`, '_self')}
                    onViewProfile={id => window.open(`/profiles?id=${id}`, '_self')}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Disclaimer — honest about current data provenance (see
              backend/import_real_dataset.py): this stays up only while
              the system runs on placeholder data, not indefinitely. */}
          <div style={{
            padding: '8px 14px',
            borderTop: '1px solid #E2E8F0',
            fontSize: '0.6rem', color: '#94A3B8', lineHeight: 1.5,
            background: '#fff',
          }}>
            ⚠ {t('chatSynthDataNotice')}
          </div>
        </div>
      </div>
    </>
  )
}
