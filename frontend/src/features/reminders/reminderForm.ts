import {
  DEFAULT_TIMEZONE,
  isValidTimezone,
  nowInstant,
  resolveTimezone,
} from '@/lib/time'

export function defaultReminderForm(timezone = DEFAULT_TIMEZONE) {
  return {
    title: '',
    content: '',
    content_type: 'text',
    media_asset_id: '',
    url: '',
    schedule_type: 'once',
    at: '',
    interval_minutes: 60,
    cron_expression: '0 9 * * 1-5',
    start_at: '',
    end_at: '',
    misfire_policy: 'fire_once',
    timezone: resolveTimezone(timezone),
    recipients: '',
    broadcast: false,
    notify_on_all_completed: false,
    require_ack: false,
    ack_policy: 'any',
    repeat_interval_seconds: 300,
    max_attempts: 12,
    stop_at: '',
  }
}

export function reminderCreatePayload(form: ReturnType<typeof defaultReminderForm>) {
  const timezone = form.timezone.trim()
  if (!isValidTimezone(timezone)) throw new Error('请输入有效的 IANA 时区')
  // Keep datetime-local values as wall-clock strings. The API receives the
  // selected IANA timezone alongside them and is the sole DST authority.
  const scheduledAt = form.at || undefined
  const stopAt = form.stop_at || undefined
  return {
    title: form.title,
    content: form.content,
    content_type: form.content_type,
    media_asset_id: form.media_asset_id || undefined,
    url: form.url || undefined,
    schedule:
      form.schedule_type === 'once'
        ? { type: 'once', at: scheduledAt, timezone }
        : form.schedule_type === 'interval'
          ? {
              type: 'interval',
              interval_seconds: form.interval_minutes * 60,
              start_at: form.start_at
                ? form.start_at
                : nowInstant(),
              end_at: form.end_at || undefined,
              timezone,
              misfire_policy: form.misfire_policy,
            }
          : {
              type: 'cron',
              cron_expression: form.cron_expression,
              start_at: form.start_at || undefined,
              end_at: form.end_at || undefined,
              timezone,
              misfire_policy: form.misfire_policy,
            },
    recipients: form.broadcast
      ? []
      : form.recipients
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
    broadcast: form.broadcast,
    notify_on_all_completed: form.notify_on_all_completed,
    require_ack: form.require_ack,
    ack_policy: form.ack_policy,
    repeat: form.require_ack
      ? {
          interval_seconds: form.repeat_interval_seconds,
          max_attempts: form.max_attempts,
          stop_at: stopAt,
        }
      : undefined,
  }
}
