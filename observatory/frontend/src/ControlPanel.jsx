import { useState } from 'react'
import { api } from './api'
import { isoLocalInput, localInputToIso, parse, hhmm } from './time'

const DEFAULT_SITES = {
  基特峰: { lat: 31.95, lon: -111.6, height_m: 2100, timezone: 'America/Phoenix' },
  帕瑞纳: { lat: -24.63, lon: -70.4, height_m: 2635, timezone: 'America/Santiago' },
  拉帕尔马: { lat: 28.76, lon: -17.86, height_m: 2396, timezone: 'Atlantic/Canary' },
}

function defaultClock(isoSunrise) {
  // 默认推进时刻 = 日出前 2 分钟
  const d = new Date(isoSunrise)
  d.setUTCMinutes(d.getUTCMinutes() - 2)
  return isoLocalInput(d)
}

export default function ControlPanel({
  nights, activeNight, onNightCreated, timeline, reload, notify,
}) {
  const [date, setDate] = useState('2026-09-18')
  const [site, setSite] = useState('基特峰')
  const [err, setErr] = useState('')

  const [wxStart, setWxStart] = useState('')
  const [wxDuration, setWxDuration] = useState(60)
  const [wxUsable, setWxUsable] = useState(false)
  const [wxCloud, setWxCloud] = useState(90)
  const [wxNote, setWxNote] = useState('')

  const [clock, setClock] = useState('')
  const [reason, setReason] = useState('')

  const latest = timeline?.versions?.[timeline.versions.length - 1]

  const run = async (fn) => {
    setErr('')
    try { await fn(); await reload() } catch (e) { setErr(e.message) }
  }

  async function createNight(e) {
    e.preventDefault()
    const s = DEFAULT_SITES[site]
    await run(() => api.createNight({ local_date: date, ...s, note: site }))
    onNightCreated?.()
    notify('观测夜已创建')
  }

  function syncedStart() {
    if (!timeline) return ''
    if (wxStart) return wxStart
    const st = timeline.summary.sunset ? parse(timeline.summary.sunset) : new Date()
    return isoLocalInput(new Date(st.getTime() + 3 * 3600_000))
  }

  async function addWeather(e) {
    e.preventDefault()
    const starts = new Date(localInputToIso(wxStart))
    await run(() => api.addWeather(activeNight.id, {
      starts_at: starts.toISOString(),
      ends_at: new Date(starts.getTime() + wxDuration * 60000).toISOString(),
      usable: wxUsable, cloud_pct: Number(wxCloud), note: wxNote,
    }))
    notify('天气样本已记录')
  }

  async function generate() {
    await run(() => api.generatePlan(activeNight.id, { trigger: 'initial', reason: reason || '初始自动排程' }))
    notify('已生成初始计划 v1')
  }

  async function advance(reschedule) {
    if (!latest) return
    const asOf = localInputToIso(clock)
    const body = {
      as_of: asOf, reschedule,
      reason: reschedule
        ? (reason || '天气更新：缩短可用窗口，重排未开始曝光')
        : '推进执行（夜末结算）',
    }
    const r = await run(() => api.advance(latest.id, body))
    if (r) notify(reschedule ? '已推进并重排（新版本）' : '已推进执行')
  }

  async function adjust(kind) {
    if (!latest || !selectedTargetId) return
    const label = { pin: '置顶优先', drop: '移出本夜' }[kind]
    await run(() => api.adjust(latest.id, {
      target_id: selectedTargetId, kind,
      reason: reason || `值班科学家${label}（未填写具体原因）`,
    }))
    notify(`已登记人工调整：${label}`)
  }

  const [selectedTargetId, setSelectedTargetId] = useState('')

  return (
    <div className="card control-panel">
      <h3>② 值班科学家控制台</h3>

      <form onSubmit={createNight} className="row">
        <label>观测夜
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <label>站点
          <select value={site} onChange={(e) => setSite(e.target.value)}>
            {Object.keys(DEFAULT_SITES).map((k) => <option key={k}>{k}</option>)}
          </select>
        </label>
        <button className="btn">建立观测夜</button>
      </form>

      {activeNight && timeline && (
        <>
          <div className="twilight">
            {[
              ['日落', timeline.summary.sunset],
              ['民用蒙影', timeline.summary.civil_start],
              ['航海蒙影', timeline.summary.nautical_start],
              ['天文暗夜', timeline.summary.astro_start],
              ['天文晨光', timeline.summary.astro_end],
              ['日出', timeline.summary.sunrise],
            ].map(([k, v]) => (
              <span key={k} className="tw-chip">{k} {v ? hhmm(parse(v)) : '—'}</span>
            ))}
            <span className="tw-chip moon">月相 {timeline.visibility?.[0]
              ? Math.round(timeline.visibility[0].moon_illumination * 100) + '%' : '—'}</span>
          </div>

          <form onSubmit={addWeather} className="wx-form">
            <div className="section-title">③ 本地天气样本（不连真实望远镜）</div>
            <div className="row wrap">
              <label>起始 (UTC)
                <input type="datetime-local" value={syncedStart()}
                       onChange={(e) => setWxStart(e.target.value)} />
              </label>
              <label>持续(分)
                <input type="number" min="5" value={wxDuration}
                       onChange={(e) => setWxDuration(e.target.value)} />
              </label>
              <label>云量%
                <input type="number" min="0" max="100" value={wxCloud}
                       onChange={(e) => setWxCloud(e.target.value)} />
              </label>
              <label className="chk">
                <input type="checkbox" checked={wxUsable}
                       onChange={(e) => setWxUsable(e.target.checked)} /> 可观测
              </label>
              <input className="grow" placeholder="备注，如：卷云过境，关闭圆顶"
                     value={wxNote} onChange={(e) => setWxNote(e.target.value)} />
              <button className="btn warn">{wxUsable ? '记录晴好' : '记录关闭圆顶'}</button>
            </div>
          </form>

          <div className="section-title">④ 计划与执行模拟</div>
          <div className="row wrap">
            <button className="btn primary" disabled={!!latest} onClick={generate}>
              生成初始计划
            </button>
            <label>模拟时刻 (UTC)
              <input type="datetime-local"
                     value={clock || (timeline.summary.sunrise ? defaultClock(timeline.summary.sunrise) : '')}
                     onChange={(e) => setClock(e.target.value)} />
            </label>
            <button className="btn" disabled={!latest} onClick={() => advance(true)}>
              推进到此刻并重排
            </button>
            <button className="btn" disabled={!latest} onClick={() => advance(false)}>
              推进到此刻（夜末结算）
            </button>
          </div>
          <input className="reason" placeholder="本次操作/人工调整原因（会随计划版本保留）"
                 value={reason} onChange={(e) => setReason(e.target.value)} />

          <div className="section-title">⑤ 人工调整（保留原因与版本）</div>
          <div className="row">
            <select value={selectedTargetId} onChange={(e) => setSelectedTargetId(e.target.value)}>
              <option value="">选择目标…</option>
              {timeline.targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <button className="btn" disabled={!selectedTargetId} onClick={() => adjust('pin')}>
              置顶优先
            </button>
            <button className="btn danger" disabled={!selectedTargetId} onClick={() => adjust('drop')}>
              移出本夜
            </button>
          </div>
        </>
      )}

      {err && <div className="error">{err}</div>}
    </div>
  )
}
