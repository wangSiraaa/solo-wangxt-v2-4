import { useState } from 'react'
import { api } from './api'

const EMPTY = {
  name: '',
  ra_deg: '',
  dec_deg: '',
  magnitude: '',
  filter_name: 'V',
  exposure_seconds: 300,
  readout_seconds: 45,
  requested_frames: 3,
  priority: 3,
  min_altitude_deg: 30,
  min_moon_separation_deg: 40,
}

export default function ProposalPanel({ proposals, targets, onChanged }) {
  const [p, setP] = useState({ code: '', pi_name: '', title: '' })
  const [sel, setSel] = useState('')
  const [t, setT] = useState(EMPTY)
  const [err, setErr] = useState('')

  const createProposal = async () => {
    setErr('')
    try {
      await api.createProposal(p)
      setP({ code: '', pi_name: '', title: '' })
      onChanged()
    } catch (e) {
      setErr(String(e))
    }
  }

  const addTarget = async () => {
    setErr('')
    try {
      const body = {
        ...t,
        ra_deg: Number(t.ra_deg),
        dec_deg: Number(t.dec_deg),
        magnitude: t.magnitude === '' ? null : Number(t.magnitude),
        exposure_seconds: Number(t.exposure_seconds),
        readout_seconds: Number(t.readout_seconds),
        requested_frames: Number(t.requested_frames),
        priority: Number(t.priority),
        min_altitude_deg: Number(t.min_altitude_deg),
        min_moon_separation_deg: Number(t.min_moon_separation_deg),
      }
      await api.addTarget(sel, body)
      setT(EMPTY)
      onChanged()
    } catch (e) {
      setErr(String(e))
    }
  }

  return (
    <section className="panel">
      <h2>① 观测提案与目标录入</h2>
      <div className="form-row">
        <input placeholder="提案编号 P2026-..." value={p.code}
          onChange={(e) => setP({ ...p, code: e.target.value })} />
        <input placeholder="PI 姓名" value={p.pi_name}
          onChange={(e) => setP({ ...p, pi_name: e.target.value })} />
        <input placeholder="标题" value={p.title}
          onChange={(e) => setP({ ...p, title: e.target.value })} />
        <button onClick={createProposal}>新建提案</button>
      </div>

      <table className="data-table">
        <thead>
          <tr><th>提案</th><th>PI</th><th>目标</th></tr>
        </thead>
        <tbody>
          {proposals.map((pr) => (
            <tr key={pr.id}>
              <td>{pr.code}</td><td>{pr.pi_name}</td>
              <td>{pr.targets.map((x) => x.name).join('，') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>向提案添加目标</h3>
      <div className="form-row">
        <select value={sel} onChange={(e) => setSel(e.target.value)}>
          <option value="">选择提案…</option>
          {proposals.map((pr) => <option key={pr.id} value={pr.id}>{pr.code}</option>)}
        </select>
        <input placeholder="目标名" value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} />
        <input placeholder="RA °" type="number" value={t.ra_deg} onChange={(e) => setT({ ...t, ra_deg: e.target.value })} />
        <input placeholder="Dec °" type="number" value={t.dec_deg} onChange={(e) => setT({ ...t, dec_deg: e.target.value })} />
        <select value={t.filter_name} onChange={(e) => setT({ ...t, filter_name: e.target.value })}>
          {['U', 'B', 'V', 'R', 'I', 'g', 'r', 'i', 'z', 'J', 'H', 'K'].map((f) => <option key={f}>{f}</option>)}
        </select>
      </div>
      <div className="form-row">
        <label>曝光秒 <input type="number" value={t.exposure_seconds} onChange={(e) => setT({ ...t, exposure_seconds: e.target.value })} /></label>
        <label>读出秒 <input type="number" value={t.readout_seconds} onChange={(e) => setT({ ...t, readout_seconds: e.target.value })} /></label>
        <label>申请帧数 <input type="number" value={t.requested_frames} onChange={(e) => setT({ ...t, requested_frames: e.target.value })} /></label>
        <label>优先级 <input type="number" min="1" max="9" value={t.priority} onChange={(e) => setT({ ...t, priority: e.target.value })} /></label>
        <label>最低高度° <input type="number" value={t.min_altitude_deg} onChange={(e) => setT({ ...t, min_altitude_deg: e.target.value })} /></label>
        <label>最小月距° <input type="number" value={t.min_moon_separation_deg} onChange={(e) => setT({ ...t, min_moon_separation_deg: e.target.value })} /></label>
        <button disabled={!sel} onClick={addTarget}>添加目标</button>
      </div>

      <details>
        <summary>全部目标（{targets.length}）</summary>
        <table className="data-table">
          <thead><tr><th>名称</th><th>RA</th><th>Dec</th><th>滤镜</th><th>曝光s</th><th>申请</th><th>已采</th></tr></thead>
          <tbody>
            {targets.map((x) => (
              <tr key={x.id}>
                <td>{x.name}</td><td>{x.ra_deg.toFixed(2)}</td><td>{x.dec_deg.toFixed(2)}</td>
                <td>{x.filter_name}</td><td>{x.exposure_seconds}</td>
                <td>{x.requested_frames}</td><td>{x.acquired_frames}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      {err && <div className="error">{err}</div>}
    </section>
  )
}
