import { useState } from 'react'
import { api } from './api'

const FILTERS = ['U', 'B', 'V', 'g', 'R', 'r', 'I', 'i', 'L', 'z', 'Ha']

const blankTarget = () => ({
  name: '', ra_hours: '', dec_deg: '', filter: 'V',
  exposure_sec: 300, requested_frames: 1, min_altitude: '', min_moon_sep: '', priority: 100,
})

export default function ProposalForm({ onCreated }) {
  const [head, setHead] = useState({ code: '', pi: '', title: '', awarded_frames: 5 })
  const [targets, setTargets] = useState([blankTarget()])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const updateT = (i, k, v) => setTargets((ts) => ts.map((t, j) => j === i ? { ...t, [k]: v } : t))

  async function submit(e) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      const payload = {
        ...head,
        awarded_frames: Number(head.awarded_frames),
        targets: targets.map((t) => ({
          name: t.name,
          ra_hours: t.ra_hours === '' ? undefined : Number(t.ra_hours),
          dec_deg: Number(t.dec_deg),
          filter: t.filter,
          exposure_sec: Number(t.exposure_sec),
          requested_frames: Number(t.requested_frames),
          min_altitude: t.min_altitude === '' ? null : Number(t.min_altitude),
          min_moon_sep: t.min_moon_sep === '' ? null : Number(t.min_moon_sep),
          priority: Number(t.priority),
        })),
      }
      const p = await api.createProposal(payload)
      onCreated?.(p)
      setHead({ code: '', pi: '', title: '', awarded_frames: 5 })
      setTargets([blankTarget()])
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card proposal-form" onSubmit={submit}>
      <h3>① 申请人录入观测提案</h3>
      <div className="grid4">
        <label>提案编号
          <input value={head.code} required placeholder="PROP-2026B-017"
                 onChange={(e) => setHead({ ...head, code: e.target.value })} />
        </label>
        <label>PI
          <input value={head.pi} required placeholder="研究员姓名"
                 onChange={(e) => setHead({ ...head, pi: e.target.value })} />
        </label>
        <label>标题
          <input value={head.title} placeholder="项目标题"
                 onChange={(e) => setHead({ ...head, title: e.target.value })} />
        </label>
        <label>批准总帧数
          <input type="number" min="0" value={head.awarded_frames}
                 onChange={(e) => setHead({ ...head, awarded_frames: e.target.value })} />
        </label>
      </div>

      <div className="targets-head">
        <b>目标（坐标 / 曝光 / 滤镜）</b>
        <button type="button" className="btn-small"
                onClick={() => setTargets([...targets, blankTarget()])}>+ 添加目标</button>
      </div>

      {targets.map((t, i) => (
        <div className="target-row" key={i}>
          <input placeholder="名称" value={t.name} required
                 onChange={(e) => updateT(i, 'name', e.target.value)} />
          <input type="number" step="any" placeholder="RA (时)" value={t.ra_hours} required
                 title="赤经，小时（0–24）"
                 onChange={(e) => updateT(i, 'ra_hours', e.target.value)} />
          <input type="number" step="any" placeholder="Dec (°)" value={t.dec_deg} required
                 title="赤纬，度（-90–90）"
                 onChange={(e) => updateT(i, 'dec_deg', e.target.value)} />
          <select value={t.filter} onChange={(e) => updateT(i, 'filter', e.target.value)}>
            {FILTERS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <input type="number" placeholder="曝光秒" value={t.exposure_sec} min="1"
                 onChange={(e) => updateT(i, 'exposure_sec', e.target.value)} />
          <input type="number" placeholder="帧数" value={t.requested_frames} min="1"
                 onChange={(e) => updateT(i, 'requested_frames', e.target.value)} />
          <input type="number" step="any" placeholder="最低高度°（可选）" value={t.min_altitude}
                 onChange={(e) => updateT(i, 'min_altitude', e.target.value)} />
          <input type="number" step="any" placeholder="最小月距°（可选）" value={t.min_moon_sep}
                 onChange={(e) => updateT(i, 'min_moon_sep', e.target.value)} />
          <input type="number" placeholder="优先级" value={t.priority}
                 onChange={(e) => updateT(i, 'priority', e.target.value)} />
          {targets.length > 1 && (
            <button type="button" className="btn-del"
                    onClick={() => setTargets(targets.filter((_, j) => j !== i))}>×</button>
          )}
        </div>
      ))}

      {err && <div className="error">{err}</div>}
      <button className="btn primary" disabled={busy}>{busy ? '提交中…' : '提交提案'}</button>
    </form>
  )
}
