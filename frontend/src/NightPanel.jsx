import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import Timeline from './Timeline'
import { TRIGGER_LABEL, hhmm, localInputValue, inputToIso, toDate } from './utils'

export default function NightPanel({ targets, targetsById, onChangedTick }) {
  const [nights, setNights] = useState([])
  const [nightId, setNightId] = useState('')
  const [data, setData] = useState(null)
  const [versionIdx, setVersionIdx] = useState(0)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  // 天气 / 人工 / 执行表单
  const [weatherAt, setWeatherAt] = useState('2026-09-18T14:04')
  const [weatherUntil, setWeatherUntil] = useState('2026-09-18T15:20')
  const [pinTarget, setPinTarget] = useState('')
  const [pinStart, setPinStart] = useState('2026-09-18T16:00')
  const [pinEnd, setPinEnd] = useState('2026-09-18T16:12')
  const [pinSeq, setPinSeq] = useState(1)
  const [pinReason, setPinReason] = useState('')
  const [completeAt, setCompleteAt] = useState('2026-09-18T20:20')

  const loadNights = async () => {
    const rows = await api.listNights()
    setNights(rows)
    setNightId((cur) => cur || (rows[0]?.id ?? ''))
  }

  const loadTimeline = async () => {
    if (!nightId) return
    const d = await api.timeline(nightId)
    setData(d)
    setVersionIdx(d.versions.length - 1)
  }

  useEffect(() => { loadNights() }, [])
  useEffect(() => { loadTimeline() }, [nightId]) // eslint-disable-line

  const version = data?.versions?.[versionIdx]

  const run = async (label, fn) => {
    setErr(''); setMsg('')
    try {
      const r = await fn()
      setMsg(label + (r ? '：' + JSON.stringify(r.summary ?? r) : ''))
      await loadTimeline()
      onChangedTick?.()
    } catch (e) {
      setErr(String(e))
    }
  }

  const nightTargetIds = useMemo(() => {
    if (!data) return []
    return Object.keys(data.versions[0]?.feasibility ?? {})
      .map(Number).sort((a, b) => a - b)
  }, [data])

  if (!data) return <section className="panel"><h2>② 观测夜</h2><p className="muted">尚无观测夜，请先运行播种或创建。</p></section>

  return (
    <section className="panel">
      <h2>② 观测夜与夜间时间轴</h2>
      <div className="form-row">
        <select value={nightId} onChange={(e) => setNightId(e.target.value)}>
          {nights.map((n) => (
            <option key={n.id} value={n.id}>
              {n.night_date} · {data?.site?.name ?? ''} · {n.status}
            </option>
          ))}
        </select>
        {data.versions.length === 0 && (
          <button onClick={() => run('初始计划已生成', () => api.makePlan(nightId))}>
            生成初始夜间计划
          </button>
        )}
        <span className="muted">
          天文黑夜 {data.dark_intervals.map(([a, b]) =>
            `${hhmm(toDate(a))}–${hhmm(toDate(b))} UTC`).join('，')}
        </span>
      </div>

      {data.versions.length > 0 && (
        <>
          <div className="version-tabs">
            {data.versions.map((v, i) => (
              <button key={v.id}
                className={i === versionIdx ? 'tab active' : 'tab'}
                onClick={() => setVersionIdx(i)}>
                v{v.version} {TRIGGER_LABEL[v.trigger]}
              </button>
            ))}
          </div>

          {version && (
            <>
              <div className="version-meta">
                <strong>v{version.version} {TRIGGER_LABEL[version.trigger]}</strong>
                <span>由 {version.created_by} 于 {new Date(version.created_at).toLocaleString()} 生成</span>
                <span className="reason">原因：{version.reason}</span>
              </div>

              <Timeline timeline={data} version={version} targetsById={targetsById} />
              <Legend />

              <FeasibilityTable version={version} targetsById={targetsById} />

              <div className="controls-grid">
                <div className="control-card">
                  <h4>☁ 本地天气样本（缩短窗口后重排）</h4>
                  <label>中断时刻 <input type="datetime-local" value={weatherAt}
                    onChange={(e) => setWeatherAt(e.target.value)} /></label>
                  <label>恢复时刻 <input type="datetime-local" value={weatherUntil}
                    onChange={(e) => setWeatherUntil(e.target.value)} /></label>
                  <button onClick={() => run('天气重排完成', () => api.simulateWeather(nightId, {
                    at_utc: inputToIso(weatherAt),
                    weather: [{
                      start_utc: inputToIso(weatherAt),
                      end_utc: inputToIso(weatherUntil),
                      kind: 'cloud', source: 'sample', note: '前端录入的本地天气样本',
                    }],
                    note: `值班科学家录入天气样本 ${weatherAt.slice(11)}–${weatherUntil.slice(11)}，窗口缩短后重排未开始曝光`,
                  }))}>中断并重排</button>
                  <p className="hint">跨中断点的曝光标记中断且不计额度；其前已结束帧入账；之后未开始的曝光重新排布。</p>
                </div>

                <div className="control-card">
                  <h4>✋ 人工锁定一帧（保留原因）</h4>
                  <select value={pinTarget} onChange={(e) => setPinTarget(e.target.value)}>
                    <option value="">选择目标…</option>
                    {nightTargetIds.map((tid) =>
                      <option key={tid} value={tid}>{targetsById[tid]?.name}</option>)}
                  </select>
                  <div className="form-row">
                    <label>帧# <input type="number" min="1" style={{ width: 60 }}
                      value={pinSeq} onChange={(e) => setPinSeq(Number(e.target.value))} /></label>
                    <label>起 <input type="datetime-local" value={pinStart}
                      onChange={(e) => setPinStart(e.target.value)} /></label>
                    <label>止 <input type="datetime-local" value={pinEnd}
                      onChange={(e) => setPinEnd(e.target.value)} /></label>
                  </div>
                  <input placeholder="调整原因（必填，随版本保留）" value={pinReason}
                    onChange={(e) => setPinReason(e.target.value)} />
                  <button disabled={!pinTarget || !pinReason}
                    onClick={() => run('人工调整版本已生成', () => api.addOverride(nightId, {
                      action: 'pin', target_id: Number(pinTarget),
                      start_utc: inputToIso(pinStart), end_utc: inputToIso(pinEnd),
                      frame_seq: pinSeq, reason: pinReason,
                    }))}>锁定并重排</button>
                </div>

                <div className="control-card">
                  <h4>▶ 推进执行 / 结束夜</h4>
                  <label>模拟当前时刻 <input type="datetime-local" value={completeAt}
                    onChange={(e) => setCompleteAt(e.target.value)} /></label>
                  <button onClick={() => run('执行推进完成',
                    () => api.complete(nightId, inputToIso(completeAt)))}>
                    登记此前已结束的帧
                  </button>
                  <button className="secondary"
                    onClick={() => run('观测夜已结束', () => api.finish(nightId))}>
                    结束观测夜
                  </button>
                  <p className="hint">已采集帧台账共 {data.acquired_frames.length} 帧，重排不会重复占用申请额度。</p>
                  <ul className="frame-ledger">
                    {data.acquired_frames.map((f) => (
                      <li key={f.id}>
                        {hhmm(toDate(f.acquired_at))} {targetsById[f.target_id]?.name} #{f.frame_seq} {f.filter_name}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>

              <VersionDiff data={data} idx={versionIdx} />
            </>
          )}
        </>
      )}

      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}
    </section>
  )
}

function Legend() {
  return (
    <div className="legend">
      <span><i className="sw dark" />天文黑夜</span>
      <span><i className="sw visible" />目标可见区间</span>
      <span><i className="sw weather" />天气不可用</span>
      <span><i className="sw planned" />计划</span>
      <span><i className="sw completed" />已完成</span>
      <span><i className="sw interrupted" />中断（不计额度）</span>
    </div>
  )
}

function FeasibilityTable({ version, targetsById }) {
  const rows = Object.entries(version.feasibility)
  return (
    <details className="feasibility" open>
      <summary>为什么这些目标不可执行 / 未排满？（{rows.length} 个目标）</summary>
      <table className="data-table">
        <thead>
          <tr><th>目标</th><th>可行</th><th>申请</th><th>已采</th><th>本版新排</th><th>原因（高度角 / 月距 / 晨昏 / 窗口）</th></tr>
        </thead>
        <tbody>
          {rows.map(([tid, f]) => (
            <tr key={tid} className={f.feasible ? '' : 'row-bad'}>
              <td>{targetsById[tid]?.name ?? tid}</td>
              <td>{f.feasible ? '✅' : '❌'}</td>
              <td>{f.requested_frames}</td>
              <td>{f.acquired_frames}</td>
              <td>{f.scheduled_new_frames}</td>
              <td>{f.reasons.length ? f.reasons.join('；') : (f.total_in_plan_frames >= f.requested_frames ? '全部排下' : '—')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

function VersionDiff({ data, idx }) {
  const v = data.versions[idx]
  const diff = v.summary?.diff_from_previous
  if (!diff) return null
  return (
    <div className="diff-box">
      与上一版差异：科学块搬移 <b>{diff.moved}</b>，保持原位 <b>{diff.kept}</b>，
      新增 <b>{diff.new}</b>；本版完成块 {v.summary.completed_blocks}，
      中断块 {v.summary.interrupted_blocks}。
      {v.summary.targets_infeasible.length > 0 &&
        <span className="bad"> 不可行目标：{v.summary.targets_infeasible.join(', ')}</span>}
    </div>
  )
}
