import { hhmm, parse } from './time'

const TRIGGER_LABEL = {
  initial: '初始', weather: '天气重排', manual: '人工调整', completion: '夜末结算',
}

function reasonZh(r) {
  return r.split(':')[0].replace('_', ' ')
}

export default function VersionPanel({ timeline, index, onPick }) {
  return (
    <div className="card versions">
      <h3>计划版本与不可执行解释</h3>
      <div className="version-tabs">
        {timeline.versions.map((v, i) => (
          <button key={v.id}
                  className={`vtab ${i === index ? 'active' : ''} trigger-${v.trigger}`}
                  onClick={() => onPick(i)}>
            v{v.version} · {TRIGGER_LABEL[v.trigger] || v.trigger}
          </button>
        ))}
      </div>
      {timeline.versions.length === 0 && <p className="muted">尚未生成计划。</p>}
      {timeline.versions[index] && (
        <VersionDetail version={timeline.versions[index]} timeline={timeline} />
      )}
    </div>
  )
}

function VersionDetail({ version, timeline }) {
  const framesByTarget = {}
  for (const f of timeline.frames) {
    framesByTarget[f.target_id] = (framesByTarget[f.target_id] || 0) + 1
  }
  const tname = (id) => timeline.targets.find((t) => t.id === id)?.name

  return (
    <div>
      <p className="reason-line">原因：{version.reason || '—'}</p>
      {version.adjustments?.length > 0 && (
        <ul className="adj-list">
          {version.adjustments.map((a) => (
            <li key={a.id}>
              <span className={`badge badge-${a.kind}`}>
                {{ pin: '置顶', drop: '移出', reorder: '调序', override_constraint: '覆盖约束' }[a.kind]}
              </span>
              {tname(a.target_id)} — {a.reason} <span className="muted">({a.author})</span>
            </li>
          ))}
        </ul>
      )}

      {version.unscheduled?.length > 0 ? (
        <table className="unsched">
          <thead>
            <tr><th>目标</th><th>未满足帧数</th><th>不可执行原因</th></tr>
          </thead>
          <tbody>
            {version.unscheduled.map((u, i) => (
              <tr key={i}>
                <td>{u.name}</td>
                <td>{u.frames_lost}</td>
                <td><ReasonText raw={u.reason} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted">所有目标帧均已排入（或已采满）。</p>}

      <div className="frame-summary">
        <b>已采集帧（占用申请额度）：{timeline.frames.length}</b>
        {timeline.targets.map((t) => (
          <span key={t.id} className="frame-chip">
            {t.name}: {framesByTarget[t.id] || 0}/{t.requested_frames}
          </span>
        ))}
      </div>
      <p className="muted small">
        版本生成于 {hhmm(parse(version.created_at))} UTC；已完成的历史动作冻结保留，
        中断曝光不产生帧、不占额度。
      </p>
    </div>
  )
}

const REASON_CN = {
  altitude: '高度角不足',
  moon: '月距不足',
  'altitude+moon': '高度角与月距窗口不相交',
  twilight: '晨昏蒙影（暗天光等级）窗口不满足',
  weather: '天气不可用',
  window_full: '可行窗口被高优先级目标占满',
  award_exhausted: '提案帧额度用尽',
  award_zero: '无可用额度',
  manual_drop: '人工移出',
}

function ReasonText({ raw }) {
  const [code, ...rest] = raw.split(':')
  const label = REASON_CN[code.trim()] || code
  const detail = rest.join(':').trim()
  return <><span className="reason-code">{label}</span>{detail && <span className="muted"> — {detail}</span>}</>
}
