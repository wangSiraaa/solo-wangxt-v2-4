export function parse(t) {
  return new Date(t)
}

export function hhmm(d) {
  const z = (n) => String(n).padStart(2, '0')
  return `${z(d.getUTCHours())}:${z(d.getUTCMinutes())}`
}

export function hhmmss(d) {
  const z = (n) => String(n).padStart(2, '0')
  return `${z(d.getUTCHours())}:${z(d.getUTCMinutes())}:${z(d.getUTCSeconds())}`
}

export function isoLocalInput(d) {
  // datetime-local 需要“无时区”格式，统一以 UTC 展示输入
  const z = (n) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${z(d.getUTCMonth() + 1)}-${z(d.getUTCDate())}T${z(d.getUTCHours())}:${z(d.getUTCMinutes())}`
}

export function localInputToIso(v) {
  // datetime-local 值视为 UTC
  return new Date(v + 'Z').toISOString()
}

export function minutesBetween(a, b) {
  return (b.getTime() - a.getTime()) / 60000
}
