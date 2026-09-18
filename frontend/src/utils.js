export const FILTER_COLORS = {
  U: '#6d5dfc',
  B: '#3b82f6',
  V: '#22c55e',
  R: '#ef4444',
  I: '#9333ea',
  g: '#22d3ee',
  r: '#f97316',
  i: '#a855f7',
  z: '#db2777',
  J: '#f59e0b',
  H: '#eab308',
  K: '#ca8a04',
}

export function filterColor(f) {
  return FILTER_COLORS[f] || '#64748b'
}

export function toDate(s) {
  return new Date(s)
}

export function hhmm(d) {
  return d.toISOString().slice(11, 16)
}

export function hhmmss(d) {
  return d.toISOString().slice(11, 19)
}

// datetime-local（UTC 视）<-> ISO
export function localInputValue(iso) {
  if (!iso) return ''
  return iso.slice(0, 16)
}

export function inputToIso(v) {
  // 输入框无时区后缀，按 UTC 处理
  return v ? v + ':00Z' : null
}

export const KIND_LABEL = {
  setup: '设备准备',
  filter_change: '滤镜切换',
  science: '科学曝光',
}

export const TRIGGER_LABEL = {
  initial: '初始计划',
  weather: '天气重排',
  manual: '人工调整',
}

export const STATUS_LABEL = {
  planned: '计划',
  completed: '已完成',
  interrupted: '中断',
}
