const BASE = '/api'

async function req(path, { method = 'GET', body } = {}) {
  const res = await fetch(BASE + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${text}`)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  listSites: () => req('/sites'),
  createSite: (b) => req('/sites', { method: 'POST', body: b }),
  listProposals: () => req('/proposals'),
  createProposal: (b) => req('/proposals', { method: 'POST', body: b }),
  addTarget: (pid, b) => req(`/proposals/${pid}/targets`, { method: 'POST', body: b }),
  listTargets: () => req('/targets'),
  listNights: () => req('/nights'),
  createNight: (b) => req('/nights', { method: 'POST', body: b }),
  addNightTargets: (id, target_ids) =>
    req(`/nights/${id}/targets`, { method: 'POST', body: { target_ids } }),
  timeline: (id) => req(`/nights/${id}/timeline`),
  makePlan: (id) => req(`/nights/${id}/plan`, { method: 'POST' }),
  versions: (id) => req(`/nights/${id}/versions`),
  addWeather: (id, b) => req(`/nights/${id}/weather`, { method: 'POST', body: b }),
  simulateWeather: (id, b) =>
    req(`/nights/${id}/simulate-weather`, { method: 'POST', body: b }),
  addOverride: (id, b) => req(`/nights/${id}/overrides`, { method: 'POST', body: b }),
  complete: (id, at_utc) =>
    req(`/nights/${id}/complete`, { method: 'POST', body: { at_utc } }),
  finish: (id) => req(`/nights/${id}/finish`, { method: 'POST' }),
}
