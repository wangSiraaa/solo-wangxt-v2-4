import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import ProposalPanel from './ProposalPanel'
import NightPanel from './NightPanel'
import './styles.css'

export default function App() {
  const [proposals, setProposals] = useState([])
  const [targets, setTargets] = useState([])
  const [tick, setTick] = useState(0)

  const refresh = useCallback(async () => {
    const [ps, ts] = await Promise.all([api.listProposals(), api.listTargets()])
    setProposals(ps)
    setTargets(ts)
  }, [])

  useEffect(() => { refresh() }, [refresh, tick])

  const targetsById = Object.fromEntries(targets.map((t) => [t.id, t]))

  return (
    <div className="app">
      <header>
        <h1>🔭 NightPlan 夜间观测排程</h1>
        <p className="muted">
          提案（坐标 / 曝光 / 滤镜）→ 可执行夜间计划。系统依据高度角、月距、天文晨昏解释可行性；
          设备准备与滤镜切换占用时间；天气缩短窗口后只重排未开始曝光，已采集帧不重复占用额度。
        </p>
      </header>

      <ProposalPanel proposals={proposals} targets={targets} onChanged={() => setTick((x) => x + 1)} />
      <NightPanel targets={targets} targetsById={targetsById} onChangedTick={() => setTick((x) => x + 1)} />

      <footer className="muted">
        FastAPI + Astropy + PostgreSQL · React。时间轴刻度为 UTC（北京时间 = UTC+8）。
      </footer>
    </div>
  )
}
