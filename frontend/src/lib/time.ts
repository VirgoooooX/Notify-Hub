/**
 * Time values crossing the API boundary are UTC instants.  A value without an
 * offset is legacy data and is interpreted as UTC deliberately; it must never
 * be handed to `new Date()` (which makes the browser timezone part of the
 * contract).
 */
export const DEFAULT_TIMEZONE = 'UTC'

const OFFSET_PATTERN = /(?:Z|[+-]\d{2}:?\d{2})$/i
const LOCAL_INPUT_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/

export function isValidTimezone(timezone: string | null | undefined): timezone is string {
  if (!timezone?.trim()) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format()
    return true
  } catch {
    return false
  }
}

export function resolveTimezone(
  timezone: string | null | undefined,
  fallback = DEFAULT_TIMEZONE,
): string {
  return isValidTimezone(timezone) ? timezone : isValidTimezone(fallback) ? fallback : DEFAULT_TIMEZONE
}

/** Parse RFC3339 instants, treating offset-less legacy values as UTC. */
export function parseInstant(value: string | null | undefined): Date | null {
  if (!value?.trim()) return null
  const raw = value.trim()
  const normalized = OFFSET_PATTERN.test(raw) ? raw : `${raw}Z`
  const timestamp = Date.parse(normalized)
  return Number.isNaN(timestamp) ? null : new Date(timestamp)
}

export function formatInstant(
  value: string | null | undefined,
  timezone = DEFAULT_TIMEZONE,
  options: Intl.DateTimeFormatOptions = { dateStyle: 'short', timeStyle: 'short' },
): string {
  const instant = parseInstant(value)
  if (!instant) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    ...options,
    timeZone: resolveTimezone(timezone),
  }).format(instant)
}

function zonedParts(timestamp: number, timezone: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(timestamp))
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]))
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
  }
}

function timezoneOffsetMs(timestamp: number, timezone: string): number {
  const parts = zonedParts(timestamp, timezone)
  const asUtc = Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second,
  )
  return asUtc - Math.floor(timestamp / 1000) * 1000
}

/** Convert a datetime-local wall value in an explicit IANA zone to an instant. */
export function localDateTimeToInstant(
  value: string | null | undefined,
  timezone: string,
): string | null {
  if (!value) return null
  const match = LOCAL_INPUT_PATTERN.exec(value)
  if (!match) return null
  const [, year, month, day, hour, minute, second = '0', milliseconds = '0'] = match
  const nominal = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
    Number(milliseconds.padEnd(3, '0')),
  )
  if (!Number.isFinite(nominal)) return null
  if (!isValidTimezone(timezone)) return null
  const zone = timezone
  const wall = {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: Number(hour),
    minute: Number(minute),
    second: Number(second),
  }
  const offsets = new Set<number>()
  for (const delta of [-172_800_000, -86_400_000, -21_600_000, 0, 21_600_000, 86_400_000, 172_800_000]) {
    offsets.add(timezoneOffsetMs(nominal + delta, zone))
  }
  const candidates = [...offsets]
    .map((offset) => nominal - offset)
    .filter((candidate) => {
      const parts = zonedParts(candidate, zone)
      return (
        parts.year === wall.year &&
        parts.month === wall.month &&
        parts.day === wall.day &&
        parts.hour === wall.hour &&
        parts.minute === wall.minute &&
        parts.second === wall.second &&
        candidate % 1000 === Number(milliseconds.padEnd(3, '0'))
      )
    })
    .sort((left, right) => left - right)
  // No candidate means the wall time falls in a DST gap.  For a repeated wall
  // time choose the earliest instant (the IANA fold=0 interpretation).
  if (!candidates.length) return null
  return new Date(candidates[0]).toISOString()
}

/** Format an instant for a datetime-local control in an explicit IANA zone. */
export function instantToLocalDateTime(
  value: string | null | undefined,
  timezone = DEFAULT_TIMEZONE,
): string {
  const instant = parseInstant(value)
  if (!instant) return ''
  const parts = zonedParts(instant.getTime(), resolveTimezone(timezone))
  return [
    `${String(parts.year).padStart(4, '0')}-${String(parts.month).padStart(2, '0')}-${String(parts.day).padStart(2, '0')}`,
    `${String(parts.hour).padStart(2, '0')}:${String(parts.minute).padStart(2, '0')}`,
  ].join('T')
}

export function nowInstant(): string {
  return new Date().toISOString()
}

export function nowLocalDateTime(timezone = DEFAULT_TIMEZONE, offsetMinutes = 0): string {
  const instant = parseInstant(nowInstant())
  if (!instant) return ''
  return instantToLocalDateTime(
    new Date(instant.getTime() + offsetMinutes * 60_000).toISOString(),
    timezone,
  )
}
