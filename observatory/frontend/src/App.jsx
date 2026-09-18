import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import ProposalForm from './ProposalForm'
import ControlPanel from './ControlPanel'
import Timeline from './Timeline'
import VersionPanel from './VersionPanel'
import VisibilityPanel from './VisibilityPanel'

export default function App() {
  const [nights, setNights] = useState([])
  const [activeNightId, setActiveNightId] = useState(null)
  const [timeline, setTimeline] = useState(null)
  const [proposals, setProposals] = useState([])
  const [versionIdx, setVersionIdx] = useState(0)
  const [toast, setToast] = useState('')
  const [err, setErr] = useState('')

  const notify = (m) => {
    setToast(m)
    setTimeout(() => setToast(''), 2600)
  }

  const loadNights = useCallback(async () => {
    const ns = await api.listNights()
    setNights(ns)
    setActiveNightId((cur) => cur || ns[0]?.id || null)
  }, [])

  const loadTimeline = useCallback(async () => {
    if (!activeNightId) { setTimeline(null); return }
    try {
      const tl = await api.timeline(activeNightId)
      setTimeline((prev) => {
        // 默认选最新版本
        setVersionIdx(tl.versions.length ? tl.versions.length - 1 : 0)
        return tl
      })
    } catch (e) { setErr(e.message) }
  }, [activeNightId])

  useEffect(() => { loadNights(); api.listProposals().then(setProposals).catch(() => {}) }, [loadNights])
  useEffect(() => { loadTimeline() }, [loadTimeline])

  const activeNight = nights.find((n) => n.id === activeNightId)

  return (
    <div className="app">
      <header>
        <h1>🔭 天文台夜间计划系统</h1>
        <p className="subtitle">
          观测提案 → 可执行夜间计划 · FastAPI + Astropy · 高度角 / 月距 / 晨昏窗口判定 · 演示环境，不连接真实望远镜
        </p>
        {nights.length > 0 && (
          <div className="night-switch">
            观测夜：
            <select value={activeNightId || ''} onChange={(e) => setActiveNightId(e.target.value)}>
              {nights.map((n) => <option key={n.id} value={n.id}>{n.local_date}（{n.note}）</option>)}
            </select>
          </div>
        )}
      </header>

      <main>
        <div className="col-left">
          <ProposalForm onCreated={() => api.listProposals().then(setProposals)} />
          {proposals.length > 0 && (
            <div className="card">
              <h3>已录提案（{proposals.length}）</h3>
              {proposals.map((p) => (
                <div key={p.id} className="proposal-item">
                  <b>{p.code}</b> <span className="muted">{p.pi} · {p.title}</span>
                  <span className="muted"> 额度 {p.awarded_frames} 帧 · {p.targets.length} 目标</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="col-mid">
          <ControlPanel
            nights={nights} activeNight={activeNight} timeline={timeline}
            onNightCreated={loadNights} reload={loadTimeline} notify={notify}
          />
          {timeline && (
            <div className="card">
              <h3>夜间时间轴与目标可见区间</h3>
              <Timeline timeline={timeline} selectedVersion={versionIdx} />
            </div>
          )}
          {err && <div className="card"><div className="error">{err}</div></div>}
        </div>

        <div className="col-right">
          {timeline && timeline.visibility.length > 0 && <VisibilityPanel timeline={timeline} />}
          {timeline && <VersionPanel timeline={timeline} index={versionIdx} onPick={setVersionIdx} />}
        </div>
      </main>

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
