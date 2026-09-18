import { useMemo } from 'react'
import { filterColor, hhmm, toDate, KIND_LABEL, STATUS_LABEL } from './utils'

const ROW_H = 34
const LABEL_W = 150
const HEADER_H = 28

/**
 * 夜间时间轴：
 *  - 深色底 = 天文黑夜窗口；红色斜纹 = 天气；
 *  - 每目标一行，准备/切换用浅色小条，科学曝光按滤镜着色；
 *  - 已完成实心、中断红色斜杠、计划虚线边框。
 */
export default function Timeline({ timeline, version, targetsById }) {
  const bounds = useMemo(() => {
    const d = timeline.dark_intervals
    if (!d?.length) return null
    return {
      start: toDate(d[0][0]),
      end: toDate(d[d.length - 1][1]),
    }
  }, [timeline])

  const targetIds = useMemo(() => {
    const ids = new Set()
    for (const b of version.blocks) if (b.target_id) ids.add(b.target_id)
    for (const tid of Object.keys(version.feasibility)) ids.add(Number(tid))
    return [...ids].sort((a, b) => a - b)
  }, [version])

  if (!bounds) return <div className="muted">该夜无天文黑夜窗口</div>

  const span = bounds.end - bounds.start
  const width = Math.max(900, span / 1000 / 60 * 8) // 每分钟 8px
  const x = (d) => ((toDate(d) - bounds.start) / span) * width
  const w = (a, b) => x(b) - x(a)

  const hours = []
  for (let t = new Date(bounds.start); t <= bounds.end; t = new Date(t.getTime() + 3600000))
    hours.push(t)

  const weatherRows = timeline.weather || []

  return (
    <div className="timeline-scroll">
      <div style={{ width: width + LABEL_W }}>
        <div style={{ display: 'flex', position: 'sticky', top: 0, zIndex: 3 }}>
          <div style={{ width: LABEL_W, flex: `0 0 ${LABEL_W}px`, height: HEADER_H }} />
          <div className="ruler" style={{ width, height: HEADER_H }}>
            {hours.map((h) => (
              <div key={h.toISOString()} className="tick" style={{ left: x(h) }}>
                {hhmm(h)}
              </div>
            ))}
          </div>
        </div>

        {targetIds.map((tid) => {
          const t = targetsById[tid]
          const f = version.feasibility[String(tid)]
          const blocks = version.blocks.filter((b) => b.target_id === tid)
          return (
            <div className="lane-row" key={tid}>
              <div className="lane-label" style={{ width: LABEL_W, flex: `0 0 ${LABEL_W}px` }}>
                <div className="lane-name" title={t ? `${t.ra_deg.toFixed(2)}, ${t.dec_deg.toFixed(2)}` : ''}>
                  {t ? t.name : `#${tid}`}
                </div>
                <div className="lane-sub">
                  {f ? (
                    <>
                      申请 {f.requested_frames} · 已采 {f.acquired_frames} · 本版 {f.scheduled_new_frames}
                    </>
                  ) : null}
                </div>
              </div>
              <div className="lane" style={{ width, height: ROW_H }}>
                {/* 黑夜底 */}
                {timeline.dark_intervals.map(([a, b], i) => (
                  <div key={i} className="dark-band" style={{ left: x(a), width: w(a, b) }} />
                ))}
                {/* 可见区间描边 */}
                {f?.visible_intervals.map(([a, b], i) => (
                  <div key={i} className="visible-band" style={{ left: x(a), width: w(a, b) }} />
                ))}
                {/* 天气 */}
                {weatherRows.map((wt, i) => (
                  <div key={i} className="weather-band" style={{ left: x(wt.start_utc), width: w(wt.start_utc, wt.end_utc) }} />
                ))}
                {blocks.map((b) => {
                  const left = x(b.start_utc)
                  const bw = Math.max(2, w(b.start_utc, b.end_utc))
                  const color = b.kind === 'science' ? filterColor(b.filter_name) : undefined
                  return (
                    <div
                      key={b.id}
                      className={`block block-${b.kind} block-${b.status}`}
                      style={{
                        left,
                        width: bw,
                        background: b.kind === 'science' ? color : undefined,
                        top: b.kind === 'science' ? 8 : 0,
                        height: b.kind === 'science' ? ROW_H - 16 : 6,
                      }}
                      title={
                        `${t?.name ?? ''} ${KIND_LABEL[b.kind]}${b.frame_seq ? ' #' + b.frame_seq : ''}
${hhmm(b.start_utc)}–${hhmm(b.end_utc)} UTC · ${STATUS_LABEL[b.status]}${b.note ? '\n' + b.note : ''}`
                      }
                    >
                      {b.kind === 'science' && bw > 26 ? `#${b.frame_seq}` : ''}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
