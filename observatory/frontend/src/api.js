const BASE = '/api'

async function req(path, options = {}) {
  const r = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!r.ok) {
    let detail = r.statusText
    try {
      const j = await r.json()
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* ignore */ }
    throw new Error(`${r.status}: ${detail}`)
  }
  return r.status === 204 ? null : r.json()
}

export const api = {
  meta: () => req('/meta'),
  listProposals: () => req('/proposals'),
  createProposal: (body) => req('/proposals', { method: 'POST', body: JSON.stringify(body) }),
  listNights: () => req('/nights'),
  createNight: (body) => req('/nights', { method: 'POST', body: JSON.stringify(body) }),
  timeline: (id) => req(`/nights/${id}/timeline`),
  visibility: (id) => req(`/nights/${id}/visibility`),
  addWeather: (id, body) => req(`/nights/${id}/weather`, { method: 'POST', body: JSON.stringify(body) }),
  generatePlan: (id, body) => req(`/nights/${id}/plans/generate`, {
    method: 'POST', body: JSON.stringify(body),
  }),
  advance: (versionId, body) => req(`/plans/${versionId}/advance`, {
    method: 'POST', body: JSON.stringify(body),
  }),
  adjust: (versionId, body) => req(`/plans/${versionId}/adjustments`, {
    method: 'POST', body: JSON.stringify(body),
  }),
}
