import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || ''

const api = axios.create({ baseURL: BASE, timeout: 30000 })

// Attach the session token to every request automatically once logged in —
// this is what makes the token-based auth real end-to-end, not just a
// login screen that isn't actually wired to anything downstream.
api.interceptors.request.use(config => {
  const token = sessionStorage.getItem('kavach_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// A session that's expired or been revoked comes back as 401 — force a
// clean re-login rather than leaving the UI silently broken.
api.interceptors.response.use(
  res => res,
  err => {
    if (err?.response?.status === 401 && !err.config?.url?.includes('/api/auth/')) {
      sessionStorage.removeItem('kavach_token')
      sessionStorage.removeItem('kavach_user')
      window.location.reload()
    }
    return Promise.reject(err)
  }
)

// ── Auth ──────────────────────────────────────────────────────────────────────
export const login = (username, password) =>
  api.post('/api/auth/login', { username, password }).then(r => r.data)

export const register = (username, password, role) =>
  api.post('/api/auth/register', { username, password, role }).then(r => r.data)

export const logout = (token) =>
  api.post('/api/auth/logout', null, { params: { token } }).then(r => r.data)

export const validateSession = (token) =>
  api.get('/api/auth/validate', { params: { token } }).then(r => r.data)

// ── Chat sessions (history sidebar) ──────────────────────────────────────────
// user_id is no longer passed from the client — the backend now derives it
// from the authenticated session token (see main.py's require_auth), which
// is also what fixed history bleeding between different logged-in officers.
export const getChatSessions = () =>
  api.get('/api/chat/sessions').then(r => r.data)

// One combined PDF of chat history — scope: 'login' (everything since this
// sign-in, used automatically right before logout), 'all' (entire history),
// or 'session' (a single conversation, pass sessionId).
export const exportChatHistoryPdf = (scope = 'login', sessionId = null) =>
  api.get('/api/chat/export', {
    params: { scope, ...(sessionId ? { session_id: sessionId } : {}) },
    responseType: 'blob',
  }).then(r => r.data)

// ── Explainability ────────────────────────────────────────────────────────────
export const getIdentityReasoning = (accusedId) =>
  api.get(`/api/accused/${accusedId}/reasoning`).then(r => r.data)

// ── New intelligence endpoints ────────────────────────────────────────────────
export const predictCrime = (district, crimeType, targetMonth) =>
  api.get('/api/predict', { params: { district, crime_type: crimeType, target_month: targetMonth } }).then(r => r.data)

export const findSimilarCases = (firNumber, topK = 5) =>
  api.get(`/api/similarity/${encodeURIComponent(firNumber)}`, { params: { top_k: topK } }).then(r => r.data)

export const getCaseTimeline = (firNumber) =>
  api.get(`/api/timeline/${encodeURIComponent(firNumber)}`).then(r => r.data)

export const getCaseRecommendations = (firNumber) =>
  api.get(`/api/recommendations/${encodeURIComponent(firNumber)}`).then(r => r.data)

export const getCaseSummary = (firNumber) =>
  api.get(`/api/case-summary/${encodeURIComponent(firNumber)}`).then(r => r.data)

export const ingestDocument = (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/api/ingest/document', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// Writes an investigator-confirmed draft into the live database — the
// second half of ingestion that was previously missing entirely (the
// extraction step above only ever produced a draft that went nowhere).
export const confirmIngest = (payload) =>
  api.post('/api/ingest/confirm', payload).then(r => r.data)

const MOCK_DASHBOARD = {
  kpis: {
    total_firs: 524, open_cases: 142, total_accused: 689,
    arrested: 412, high_risk_offenders: 38, repeat_offenders: 84,
    gang_members: 45, charge_sheeted: 298
  },
  recent_firs: [
    { fir_number: 'FIR2024/00142', district: 'Bengaluru Urban', crime_type: 'Robbery', status: 'Under Investigation', police_station: 'Koramangala PS', registration_date: '2024-06-15' },
    { fir_number: 'FIR2024/00141', district: 'Mysuru', crime_type: 'Cybercrime', status: 'Charge-Sheeted', police_station: 'Mysuru North PS', registration_date: '2024-06-14' },
    { fir_number: 'FIR2024/00140', district: 'Mangaluru', crime_type: 'Theft', status: 'Under Investigation', police_station: 'Mangaluru Port PS', registration_date: '2024-06-12' },
    { fir_number: 'FIR2024/00139', district: 'Belagavi', crime_type: 'Assault', status: 'Closed', police_station: 'Belagavi City PS', registration_date: '2024-06-10' },
    { fir_number: 'FIR2024/00138', district: 'Hubballi-Dharwad', crime_type: 'Chain Snatching', status: 'Under Investigation', police_station: 'Hubballi Rural PS', registration_date: '2024-06-08' },
  ],
  crime_distribution: [
    { crime_type: 'Theft', count: 148 },
    { crime_type: 'Vehicle Theft', count: 96 },
    { crime_type: 'Assault', count: 72 },
    { crime_type: 'Cybercrime', count: 64 },
    { crime_type: 'Robbery', count: 52 },
    { crime_type: 'Chain Snatching', count: 48 },
    { crime_type: 'Burglary', count: 44 }
  ],
  district_distribution: [
    { district: 'Bengaluru Urban', count: 184 },
    { district: 'Mysuru', count: 78 },
    { district: 'Hubballi-Dharwad', count: 62 },
    { district: 'Mangaluru', count: 54 },
    { district: 'Belagavi', count: 48 },
    { district: 'Kalaburagi', count: 42 },
    { district: 'Davanagere', count: 32 },
    { district: 'Shivamogga', count: 24 }
  ],
  monthly_trend_2024: [
    { month: '1', count: 38 }, { month: '2', count: 42 }, { month: '3', count: 48 },
    { month: '4', count: 45 }, { month: '5', count: 52 }, { month: '6', count: 58 },
    { month: '7', count: 61 }, { month: '8', count: 55 }, { month: '9', count: 49 },
    { month: '10', count: 44 }, { month: '11', count: 40 }, { month: '12', count: 36 }
  ],
  trend_year: 2024
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
export const getDashboardOverview = () =>
  api.get('/api/dashboard/overview').then(r => r.data).catch(() => MOCK_DASHBOARD)

// ── Chat ──────────────────────────────────────────────────────────────────────
export const sendChatMessage = (message, sessionId, language = 'en') =>
  api.post('/api/chat', { message, session_id: sessionId, language }).then(r => r.data)

export const getChatHistory = sessionId =>
  api.get(`/api/chat/history/${sessionId}`).then(r => r.data)

export const clearChatHistory = sessionId =>
  api.delete(`/api/chat/history/${sessionId}`).then(r => r.data)

// ── FIR ───────────────────────────────────────────────────────────────────────
export const searchFIRs = params =>
  api.get('/api/fir', { params }).then(r => r.data)

export const getFIRDetail = firNumber =>
  api.get(`/api/fir/${encodeURIComponent(firNumber)}`).then(r => r.data)

// ── Accused ───────────────────────────────────────────────────────────────────
export const searchAccused = params =>
  api.get('/api/accused', { params }).then(r => r.data)

export const getAccusedProfile = id =>
  api.get(`/api/accused/${id}`).then(r => r.data)

export const getAccusedNetwork = (id, depth = 2) =>
  api.get(`/api/accused/${id}/network`, { params: { depth } }).then(r => r.data)

// ── Analytics ─────────────────────────────────────────────────────────────────
export const getCrimeTrends = params =>
  api.get('/api/analytics/trends', { params }).then(r => r.data)

export const getHotspots = params =>
  api.get('/api/analytics/hotspots', { params }).then(r => r.data)

export const getDemographics = () =>
  api.get('/api/analytics/demographics').then(r => r.data)

export const getDistrictSummary = () =>
  api.get('/api/analytics/district-summary').then(r => r.data)

// ── Network Graph ─────────────────────────────────────────────────────────────
export const getFullNetworkGraph = (limit = 80) =>
  api.get('/api/network/graph', { params: { limit } }).then(r => r.data)

export const getGangs = () =>
  api.get('/api/network/gangs').then(r => r.data)

// ── Translation ───────────────────────────────────────────────────────────────
export const translateText = (text, targetLanguage = 'kn') =>
  api.post('/api/translate', { text, target_language: targetLanguage }).then(r => r.data)

// ── Meta ─────────────────────────────────────────────────────────────────────
export const getDistricts = () =>
  api.get('/api/meta/districts').then(r => r.data)

export const getCrimeTypes = () =>
  api.get('/api/meta/crime-types').then(r => r.data)

// With real IDs (unlike getCrimeTypes above, which is name-only and used
// for chat-query filtering) — needed to commit a confirmed ingestion draft.
export const getPoliceStations = (district = null) =>
  api.get('/api/meta/police-stations', { params: district ? { district } : {} }).then(r => r.data)

export const getCrimeSubheads = () =>
  api.get('/api/meta/crime-subheads').then(r => r.data)

export const getCaseStatuses = () =>
  api.get('/api/meta/case-statuses').then(r => r.data)

export const healthCheck = () =>
  api.get('/api/health').then(r => r.data)
