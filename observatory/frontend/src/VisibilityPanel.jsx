import { useState } from 'react'
import { parse, hhmm } from './time'

const DARK_CN = { civil: '民用蒙影', nautical: '航海蒙影', astronomical: '天文暗夜' }

export default function VisibilityPanel({ timeline }) {
  const [sel, setSel] = useState(timeline.visibility[0]?.target_id || '')
  const v = timeline.visibility.find((x) => x.target_id === sel)

  return (
    <div className="card visibility">
      <h3>目标可见区间（Astropy 实算）</h3>
      <select value={sel} onChange={(e) => setSel(e.target.value)}>
        {timeline.visibility.map((x) => <option key={x.target_id} value={x.target_id}>{x.name}</option>)}
      </select>
      {v && (
        <div className="vis-body">
          <p className="muted">
            滤镜 {v.filter}（要求 {DARK_CN[v.dark_level]}）· 最低高度 {v.min_altitude}°
            · 最小月距 {v.min_moon_sep}° · 月面照亮 {Math.round(v.moon_illumination * 100)}%
          </p>
          <WinRow label="高度角窗口" wins={v.alt_windows} cls="alt" />
          <WinRow label="月距窗口" wins={v.moon_windows} cls="moon" />
          <WinRow label={`暗天光窗口（${DARK_CN[v.dark_level]}）`} wins={v.dark_windows} cls="dark" />
          <WinRow label="综合可执行窗口" wins={v.feasible} cls="feas" strong />
          {v.feasible.length === 0 && (
            <p className="error">该目标本夜不可执行：见下方原因表。</p>
          )}
        </div>
      )}
    </div>
  )
}

function WinRow({ label, wins, strong }) {
  return (
    <div className={`win-row ${strong ? 'strong' : ''}`}>
      <span className="win-label">{label}</span>
      <span className="win-vals">
        {wins.length === 0 && <em className="muted">整夜不满足</em>}
        {wins.map(([a, b], i) => (
          <span key={i} className="win-chip">{hhmm(parse(a))} – {hhmm(parse(b))} UTC</span>
        ))}
      </span>
    </div>
  )
}
