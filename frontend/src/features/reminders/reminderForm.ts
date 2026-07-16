export function defaultReminderForm() {
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
    timezone: 'Asia/Shanghai',
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
  const scheduledAt = form.at ? new Date(form.at).toISOString() : undefined
  const stopAt = form.stop_at ? new Date(form.stop_at).toISOString() : undefined
  return {
    title: form.title,
    content: form.content,
    content_type: form.content_type,
    media_asset_id: form.media_asset_id || undefined,
    url: form.url || undefined,
    schedule:
      form.schedule_type === 'once'
        ? { type: 'once', at: scheduledAt, timezone: form.timezone }
        : form.schedule_type === 'interval'
          ? {
              type: 'interval',
              interval_seconds: form.interval_minutes * 60,
              start_at: form.start_at
                ? new Date(form.start_at).toISOString()
                : new Date().toISOString(),
              end_at: form.end_at ? new Date(form.end_at).toISOString() : undefined,
              timezone: form.timezone,
              misfire_policy: form.misfire_policy,
            }
          : {
              type: 'cron',
              cron_expression: form.cron_expression,
              start_at: form.start_at ? new Date(form.start_at).toISOString() : undefined,
              end_at: form.end_at ? new Date(form.end_at).toISOString() : undefined,
              timezone: form.timezone,
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
