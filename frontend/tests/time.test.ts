import { describe, expect, it } from 'vitest'
import {
  formatInstant,
  instantToLocalDateTime,
  localDateTimeToInstant,
  parseInstant,
} from '@/lib/time'

describe('time contract', () => {
  it('treats offset-less legacy instants as UTC, not browser local time', () => {
    expect(parseInstant('2026-07-16T12:00:00')?.toISOString()).toBe('2026-07-16T12:00:00.000Z')
    expect(formatInstant('2026-07-16T12:00:00', 'Asia/Tokyo')).toContain('21:00')
  })

  it('round-trips wall time with an explicit zone', () => {
    const instant = localDateTimeToInstant('2026-07-16T12:00', 'America/New_York')
    expect(instant).toBe('2026-07-16T16:00:00.000Z')
    expect(instantToLocalDateTime(instant, 'America/New_York')).toBe('2026-07-16T12:00')
  })

  it('rejects DST gaps and picks the earliest fold instant', () => {
    expect(localDateTimeToInstant('2026-03-08T02:30', 'America/New_York')).toBeNull()
    expect(localDateTimeToInstant('2026-11-01T01:30', 'America/New_York')).toBe(
      '2026-11-01T05:30:00.000Z',
    )
    expect(localDateTimeToInstant('2026-08-09T09:30', 'Mars/Olympus')).toBeNull()
  })
})
