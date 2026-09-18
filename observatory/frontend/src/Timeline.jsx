import { useMemo, useState } from 'react'
import { parse, hhmm, minutesBetween } from './time'
import { FILTER_FALLBACK_COLORS } from './colors'

const KIND_LABEL = { setup: '设备准备', filter_change: '滤镜切换', exposure: '曝光' }
const STATUS_LABEL = {
  pending: '待执行', in_progress: '进行中', completed: '已完成', interrupted: '已中断',
}

export default function Timeline({ timeline, selectedVersion, onSelectTarget }) {
  const [hover, setHover] = useState(null)

  const { start, end, totalMin } = useMemo(() => {
    const s = timeline.summary
    const st = parse(s.sunset)
    const en = parse(s.sunrise)
    return { start: st, end: en, totalMin: minutesBetween(st, en) }
  }, [timeline])

  const x = (iso) => {
    const d = parse(iso)
    return (minutesBetween(start, d) / totalMin) * 100
  }

  const version = timeline.versions[selectedVersion] || timeline.versions[timeline.versions.length - 1]

  // 每个目标一条泳道（含无动作目标，便于看“未排入”解释）
  const lanes = useMemo(() => {
    return timeline.targets.map((t) => ({
      target: t,
      vis: timeline.visibility.find((v) => v.target_id === t.id),
      actions: version ? version.actions.filter((a) => a.target_id === t.id) : [],
    }))
  }, [timeline, version])

  const ticks = useMemo(() => {
    const arr = []
    const d = new Date(start)
    d.setUTCMinutes(0, 0, 0)
    if (d < start) d.setUTCHours(d.getUTCHours() + 1)
    while (d < end) {
      arr.push(new Date(d))
      d.setUTCHours(d.getUTCHours() + 1)
    }
    return arr
  }, [start, end])

  const targetName = (id) => timeline.targets.find((t) => t.id === id)?.name || '—'
  const filterColor = (f) => FILTER_FALLBACK_COLORS[f] || '#8aa0b4'

  return (
    <div className="timeline-wrap">
      <div className="timeline-axis">
        <span className="tw-bound">日落 {hhmm(parse(timeline.summary.sunset))} UTC</span>
        <div className="tw-scale">
          {ticks.map((t) => (
            <span key={t.toISOString()} className="tw-tick" style={{ left: `${x(t)}%` }}>
              {hhmm(t)}
            </span>
          ))}
        </div>
        <span className="tw-bound">日出 {hhmm(parse(timeline.summary.sunrise))} UTC</span>
      </div>

      <div className="timeline">
        {/* 天气层 */}
        <div className="lane lane-weather">
          <div className="lane-label">天气</div>
          <div className="lane-track">
            {timeline.weather.map((w) => {
              const l = x(w.starts_at)
              const r = x(w.ends_at)
              return (
                <div
                  key={w.id}
                  className={`wx ${w.usable ? 'wx-ok' : 'wx-bad'}`}
                  style={{ left: `${l}%`, width: `${Math.max(0, r - l)}%` }}
                  title={`${hhmm(parse(w.starts_at))}–${hhmm(parse(w.ends_at))} ${w.usable ? '可观测' : '关闭圆顶'}（云量 ${w.cloud_pct}%）${w.note ? '：' + w.note : ''}`}
                />
              )
            })}
          </div>
        </div>

        {/* 目标泳道 */}
        {lanes.map(({ target, vis, actions }) => (
          <div className="lane" key={target.id}>
            <button className="lane-label target" onClick={() => onSelectTarget?.(target)}
                    title={`${target.name} · ${target.filter} · ${target.exposure_sec}s × ${target.requested_frames}`}>
              <span className="filter-dot" style={{ background: filterColor(target.filter) }} />
              {target.name}
            </button>
            <div className="lane-track">
              {/* 高度角可行窗口（淡色底） */}
              {vis?.alt_windows.map(([a, b], i) => (
                <div key={'a' + i} className="win win-alt"
                     style={{ left: `${x(a)}%`, width: `${x(b) - x(a)}%` }} />
              ))}
              {/* 综合可行窗口（绿框） */}
              {vis?.feasible.map(([a, b], i) => (
                <div key={'f' + i} className="win win-feasible"
                     style={{ left: `${x(a)}%`, width: `${x(b) - x(a)}%` }} />
              ))}
              {actions.map((a) => {
                const l = x(a.starts_at)
                const w = Math.max(0.4, x(a.ends_at) - l)
                const cls = `act act-${a.kind} status-${a.status}`
                const bg = a.kind === 'exposure' ? filterColor(a.filter)
                  : a.kind === 'filter_change' ? '#6b7a99' : '#4d5b74'
                return (
                  <div
                    key={a.id}
                    className={cls}
                    style={{ left: `${l}%`, width: `${w}%`, background: a.status === 'interrupted' ? undefined : bg }}
                    onMouseEnter={() => setHover({ a, target: target.name })}
                    onMouseLeave={() => setHover(null)}
                  >
                    {a.kind === 'exposure' && <span className="act-tag">{a.filter}</span>}
                    {a.carries_frame && <span className="frame-mark" title="已采集帧">●</span>}
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      {hover && (
        <div className="hover-card">
          <b>{hover.target}</b> · {KIND_LABEL[hover.a.kind]}
          <div>{hhmm(parse(hover.a.starts_at))}–{hhmm(parse(hover.a.ends_at))} UTC · {STATUS_LABEL[hover.a.status]}</div>
          <div className="muted">{hover.a.detail}</div>
        </div>
      )}

      <div className="legend">
        <span><i className="sw win-alt" /> 高度角可行</span>
        <span><i className="sw win-feasible" /> 综合可执行（高度∩月距∩晨昏）</span>
        <span><i className="sw wx-bad" /> 天气关闭</span>
        <span><i className="sw" style={{ background: '#37b6a7' }} /> 曝光</span>
        <span><i className="sw" style={{ background: '#4d5b74' }} /> 设备准备</span>
        <span><i className="frame-mark">●</i> 已采集帧</span>
      </div>
    </div>
  )
}
