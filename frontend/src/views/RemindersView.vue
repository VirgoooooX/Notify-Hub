<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, query } from '@/lib/api'
import type { Page, Person, Reminder } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'
import AppCard from '@/components/ui/AppCard.vue'
import DataTable from '@/components/data/DataTable.vue'
import TableToolbar from '@/components/data/TableToolbar.vue'
import { useUiStore } from '@/stores/ui'
import InteractiveReminderPreview from '@/components/reminders/InteractiveReminderPreview.vue'
import { useAsyncAction } from '@/composables/useAsyncAction'
import { defaultReminderForm, reminderCreatePayload } from '@/features/reminders/reminderForm'

const ui = useUiStore()
const items = ref<Page<Reminder>>({
  items: [],
  page: 1,
  page_size: 20,
  total: 0
})
const page = ref(1)
const status = ref('')
const show = ref(false)
const { pending: busy, run: runCreate } = useAsyncAction()
const broadcastAudienceCount = ref(0)

const form = reactive(defaultReminderForm())

const previewTriggers = ref<string[]>([])
const mediaPreviewUrl = ref('')
const mediaUploading = ref(false)
const isInteractive = computed(() => form.require_ack)

watch(
  () => [form.broadcast, form.require_ack],
  ([broadcast, requireAck]) => {
    if (!broadcast || !requireAck) {
      form.notify_on_all_completed = false
    }
  }
)

watch(
  () => form.notify_on_all_completed,
  (enabled) => {
    if (enabled) form.ack_policy = 'all'
  }
)

interface SchedulePreview {
  triggers: string[]
}

interface MediaAssetUpload {
  id: string
  kind: string
  mime_type: string
}

async function uploadImage(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  mediaUploading.value = true
  try {
    const data = new globalThis.FormData()
    data.append('kind', 'image')
    data.append('file', file)
    const asset = await api.upload<MediaAssetUpload>('/admin/media', data)
    form.media_asset_id = asset.id
    if (mediaPreviewUrl.value) globalThis.URL.revokeObjectURL(mediaPreviewUrl.value)
    mediaPreviewUrl.value = globalThis.URL.createObjectURL(file)
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '图片上传失败', 'danger')
    input.value = ''
  } finally {
    mediaUploading.value = false
  }
}

watch(
  () => [form.interval_minutes, form.cron_expression, form.timezone, form.schedule_type, form.start_at, form.end_at],
  async () => {
    if (form.schedule_type === 'once') {
      previewTriggers.value = []
      return
    }
    try {
      const res = await api.post<SchedulePreview>('/admin/reminders/preview', {
        type: form.schedule_type,
        interval_seconds: form.schedule_type === 'interval' ? form.interval_minutes * 60 : undefined,
        cron_expression: form.schedule_type === 'cron' ? form.cron_expression : undefined,
        timezone: form.timezone,
        start_at: form.start_at ? new Date(form.start_at).toISOString() : undefined,
        end_at: form.end_at ? new Date(form.end_at).toISOString() : undefined,
        count: 5
      })
      previewTriggers.value = res.triggers || []
    } catch (e) {
      previewTriggers.value = []
    }
  }
)

async function load() {
  try {
    items.value = await api.get('/admin/reminders' + query({ page: page.value, page_size: 20, status: status.value }))
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '提醒加载失败', 'danger')
  }
}

async function loadBroadcastAudience() {
  try {
    const data = await api.get<Person[] | { items: Person[] }>('/admin/people')
    const people = Array.isArray(data) ? data : data.items
    broadcastAudienceCount.value = people.filter(
      (person) =>
        person.enabled !== false &&
        person.wecom_identities?.some((identity) => identity.active !== false)
    ).length
  } catch {
    broadcastAudienceCount.value = 0
  }
}

async function create() {
  try {
    if ((form.content_type === 'image' || form.content_type === 'article') && !form.media_asset_id) {
      throw new Error(`${form.content_type === 'article' ? '图文' : '图片'}提醒需要先上传一张图片`)
    }
    await runCreate(() => api.post('/admin/reminders', reminderCreatePayload(form)))
    show.value = false
    ui.toast('提醒已创建', 'success')
    if (mediaPreviewUrl.value) globalThis.URL.revokeObjectURL(mediaPreviewUrl.value)
    mediaPreviewUrl.value = ''
    Object.assign(form, defaultReminderForm())
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '创建失败', 'danger')
  }
}

onMounted(() => {
  void Promise.all([load(), loadBroadcastAudience()])
})

const time = (v?: string) =>
  v
    ? new Intl.DateTimeFormat('zh-CN', {
        dateStyle: 'short',
        timeStyle: 'short'
      }).format(new Date(v))
    : '—'

const scheduleLabel = (item: Reminder) => {
  if (item.schedule_type === 'once') return '单次'
  if (item.schedule_type === 'interval') {
    const seconds = Number(item.schedule_config?.seconds ?? 0)
    return seconds ? `每 ${Math.round(seconds / 60)} 分钟` : '固定间隔'
  }
  if (item.schedule_type === 'cron') return 'Cron'
  return '周期'
}

const contentLabel = (type?: string) => {
  if (type === 'image') return '图片 + 文字'
  if (type === 'article') return '普通图文'
  return '普通文字'
}
</script>

<template>
  <PageHeader title="提醒与催办" description="普通消息负责送达，底部【快捷操作】菜单负责完成、推迟与停止。">
    <AppButton variant="primary" @click="show = !show">
      {{ show ? '取消创建' : '创建提醒' }}
    </AppButton>
  </PageHeader>

  <section class="interaction-brief" aria-label="交互式提醒说明">
    <div class="brief-index mono">
      01 / DELIVERY CONTRACT
    </div>
    <div>
      <strong>交互不再藏在消息按钮里</strong>
      <p>交互式提醒会以普通文字、图文或图片消息送达；菜单始终操作该 UserID 最近成功收到的一条交互式提醒。</p>
    </div>
    <div class="brief-rule">
      <span>成功送达才更新</span>
      <span>新提醒覆盖旧指针</span>
      <span>失效后不自动回退</span>
    </div>
  </section>

  <AppCard v-if="show" padding="md" class="create-card">
    <template #header>
      <div class="panel-header-wrap">
        <h3 class="panel-title">
          创建提醒
        </h3>
        <span class="mono muted">MESSAGE → DELIVERY → MENU</span>
      </div>
    </template>
    <form class="grid form-grid" @submit.prevent="create">
      <div class="form-step full-width">
        <span class="form-step-index mono">01</span>
        <div>
          <strong>选择发送对象</strong>
          <p>指定成员与企业微信 @all 二选一；这里只决定发给谁。</p>
        </div>
      </div>
      <div v-if="!form.broadcast" class="field">
        <label>接收人 ID（逗号分隔）</label>
        <AppInput v-model="form.recipients" required />
      </div>
      <div class="field audience-mode-field" :class="{ broadcast: form.broadcast }">
        <AppCheckbox v-model="form.broadcast">
          企业微信 @all 全员广播
        </AppCheckbox>
        <span class="field-help">仅后台管理员可创建；微信菜单创建始终只针对当前个人。</span>
      </div>
      <div v-if="form.broadcast" class="broadcast-contract full-width" role="alert">
        <span class="broadcast-kicker mono">HIGH IMPACT · @ALL</span>
        <strong>向企业微信应用可见范围发送 @all 消息</strong>
        <p>
          当前会冻结 <b>{{ broadcastAudienceCount }}</b> 名已启用且绑定 UserID 的 Notify Hub 成员作为本次接收快照。
          <template v-if="form.require_ack">
            首次 @all 送达，随后只催办仍未完成的成员。
          </template>
          <template v-else>
            这是一条普通全员通知：只发送消息，不要求成员确认，也不建立完成统计。
          </template>
        </p>
      </div>

      <div class="form-step full-width">
        <span class="form-step-index mono">02</span>
        <div>
          <strong>设置调度与消息</strong>
          <p>单次、固定间隔和 Cron 都可以发送文本、图片或图文消息。</p>
        </div>
      </div>
      <div class="field">
        <label>标题</label>
        <AppInput v-model="form.title" required />
      </div>
      <div class="field">
        <label>调度类型</label>
        <AppSelect v-model="form.schedule_type">
          <option value="once">
            单次
          </option>
          <option value="interval">
            固定间隔
          </option>
          <option value="cron">
            Cron / 每日每周
          </option>
        </AppSelect>
      </div>
      <div v-if="form.schedule_type === 'once'" class="field">
        <label>触发时间</label>
        <AppInput v-model="form.at" type="datetime-local" required />
      </div>
      <template v-else>
        <div v-if="form.schedule_type === 'interval'" class="field">
          <label>执行间隔（分钟，至少 5）</label>
          <AppInput v-model.number="form.interval_minutes" type="number" min="5" required />
        </div>
        <div v-else class="field">
          <label>Cron（分 时 日 月 周）</label>
          <AppInput v-model="form.cron_expression" placeholder="0 9 * * 1-5" required />
        </div>
        <div class="field">
          <label>时区</label>
          <AppInput v-model="form.timezone" />
        </div>
        <div class="field">
          <label>开始时间（可选）</label>
          <AppInput v-model="form.start_at" type="datetime-local" />
        </div>
        <div class="field">
          <label>结束时间（可选）</label>
          <AppInput v-model="form.end_at" type="datetime-local" />
        </div>
        <div class="field">
          <label>错过执行</label>
          <AppSelect v-model="form.misfire_policy">
            <option value="fire_once">
              补发一次
            </option>
            <option value="skip">
              跳过
            </option>
          </AppSelect>
        </div>
      </template>

      <div v-if="form.schedule_type !== 'once' && previewTriggers.length" class="field full-width preview-triggers-box">
        <label>预计前5次触发时间</label>
        <div class="triggers-list">
          <span v-for="t in previewTriggers" :key="t" class="mono text-xs trigger-badge">
            {{ time(t) }}
          </span>
        </div>
      </div>

      <div class="field">
        <label>内容类型</label>
        <AppSelect v-model="form.content_type">
          <option value="text">
            文本
          </option>
          <option value="image">
            图片
          </option>
          <option value="article">
            图文 (Article)
          </option>
        </AppSelect>
        <small class="field-help">三种类型都会走普通企业微信消息，不生成模板卡片。</small>
      </div>
      <div v-if="form.content_type === 'image' || form.content_type === 'article'" class="field">
        <label>提醒图片</label>
        <input type="file" accept="image/jpeg,image/png,image/webp" :disabled="mediaUploading" @change="uploadImage">
        <span v-if="mediaUploading" class="mono text-xs muted">正在上传…</span>
        <span v-else-if="form.media_asset_id" class="mono text-xs muted">{{ form.media_asset_id }}</span>
        <img v-if="mediaPreviewUrl" :src="mediaPreviewUrl" class="media-preview" alt="提醒图片预览">
      </div>
      <div v-if="form.content_type === 'image' || form.content_type === 'article'" class="field full-width">
        <label>跳转链接 URL（可选）</label>
        <AppInput v-model="form.url" placeholder="未填写时打开封面图片" />
      </div>

      <div class="field full-width">
        <label>内容</label>
        <AppTextarea v-model="form.content" />
      </div>

      <div class="form-step full-width">
        <span class="form-step-index mono">03</span>
        <div>
          <strong>决定是否需要交互</strong>
          <p>普通通知只负责送达；交互式提醒会持续催办，并允许用户通过底部菜单完成或推迟。</p>
        </div>
      </div>
      <div class="field checkbox-field full-width">
        <AppCheckbox v-model="form.require_ack">
          设为交互式持续提醒
        </AppCheckbox>
        <span class="field-help">开启后，消息会附带底部菜单提示，并在成功送达后成为该用户的最近操作目标。</span>
      </div>

      <template v-if="form.require_ack">
        <div class="field">
          <label>确认策略</label>
          <AppSelect v-model="form.ack_policy" :disabled="form.notify_on_all_completed">
            <option value="any">
              任一确认
            </option>
            <option value="all">
              全部确认
            </option>
            <option value="each">
              逐人确认
            </option>
          </AppSelect>
          <small v-if="form.notify_on_all_completed" class="field-help">启用“全员完成后通知”时必须使用“全部确认”。</small>
        </div>
        <div class="field">
          <label>催办间隔（秒，至少 300）</label>
          <AppInput v-model.number="form.repeat_interval_seconds" type="number" min="300" />
        </div>
        <div class="field">
          <label>最多次数</label>
          <AppInput v-model.number="form.max_attempts" type="number" min="1" max="12" />
        </div>
        <div class="field">
          <label>停止时间</label>
          <AppInput v-model="form.stop_at" type="datetime-local" />
        </div>
      </template>

      <template v-if="form.broadcast && form.require_ack">
        <div class="form-step full-width">
          <span class="form-step-index mono">04</span>
          <div>
            <strong>设置广播完成反馈</strong>
            <p>这是 @all 交互式提醒的独立可选能力，不影响消息首次发送和日常催办。</p>
          </div>
        </div>
        <div class="field checkbox-field full-width completion-notice-field">
          <AppCheckbox v-model="form.notify_on_all_completed">
            所有人完成后，再发送一条 @all 通知
          </AppCheckbox>
          <span class="field-help">开启后会自动采用“全部确认”；只有快照成员全部真实完成才发送，停止、取消或过期不算完成。</span>
        </div>
      </template>

      <div class="full-width preview-section">
        <InteractiveReminderPreview
          :title="form.title"
          :content="form.content"
          :content-type="form.content_type"
          :interactive="isInteractive"
          :broadcast="form.broadcast"
          :notify-on-all-completed="form.notify_on_all_completed"
        />
      </div>
      
      <div class="form-submit-row full-width">
        <AppButton
          type="submit"
          variant="primary"
          :loading="busy"
          :disabled="form.broadcast && broadcastAudienceCount === 0"
        >
          立即创建
        </AppButton>
      </div>
    </form>
  </AppCard>

  <AppCard padding="md">
    <TableToolbar>
      <template #left>
        <AppSelect v-model="status" class="status-select" @change="page = 1; load()">
          <option value="">
            全部状态
          </option>
          <option value="active">
            运行中
          </option>
          <option value="awaiting_ack">
            待确认
          </option>
          <option value="paused">
            已暂停
          </option>
          <option value="completed">
            已完成
          </option>
          <option value="cancelled">
            已取消
          </option>
        </AppSelect>
      </template>
    </TableToolbar>

    <EmptyState v-if="!items.items.length" />
    
    <template v-else>
      <DataTable>
        <template #headers>
          <th>提醒</th>
          <th>调度</th>
          <th>送达与交互</th>
          <th>催办进度</th>
          <th>下次触发</th>
          <th>状态</th>
        </template>
        <tr v-for="item in items.items" :key="item.id">
          <td>
            <div class="reminder-cell">
              <RouterLink class="link" :to="`/reminders/${item.id}`">
                {{ item.title }}
              </RouterLink>
              <span class="mono muted item-id">{{ item.id }}</span>
            </div>
          </td>
          <td>
            <span class="mono">
              {{ scheduleLabel(item) }}{{ item.timezone ? ` · ${item.timezone}` : '' }}
            </span>
          </td>
          <td>
            <div class="delivery-cell">
              <span class="content-type-chip">{{ contentLabel(item.content_type) }}</span>
              <span v-if="item.broadcast" class="broadcast-chip">@all · 全员快照</span>
              <span v-if="item.interaction_mode === 'latest_menu' || item.require_ack" class="interactive-chip">
                底部菜单 · 最近一条
              </span>
              <span v-else class="passive-chip">普通通知</span>
            </div>
          </td>
          <td>
            <span>
              {{ item.require_ack ? `${item.attempt_count ?? 0} / ${item.max_attempts ?? 12}` : '无需确认' }}
            </span>
          </td>
          <td>
            <span class="time-label">{{ time(item.next_run_at) }}</span>
          </td>
          <td>
            <StatusBadge :status="item.status" />
          </td>
        </tr>
      </DataTable>

      <PaginationBar
        :page="page"
        :page-size="20"
        :total="items.total"
        @change="page = $event; load()"
      />
    </template>
  </AppCard>
</template>

<style scoped>
.interaction-brief {
  display: grid;
  grid-template-columns: 150px minmax(240px, 1fr) minmax(320px, auto);
  gap: var(--space-5);
  align-items: center;
  margin-bottom: var(--space-4);
  padding: var(--space-4) var(--space-5);
  border: 1px solid rgba(31, 107, 79, 0.2);
  border-left: 4px solid #1f6b4f;
  background:
    linear-gradient(90deg, rgba(31, 107, 79, 0.08), transparent 45%),
    var(--surface-panel);
  box-shadow: var(--shadow-panel);
}

.brief-index {
  color: #1f6b4f;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.11em;
}

.interaction-brief strong {
  display: block;
  margin-bottom: 3px;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: var(--text-lg);
}

.interaction-brief p {
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: 1.7;
}

.brief-rule {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.brief-rule span,
.content-type-chip,
.broadcast-chip,
.interactive-chip,
.passive-chip {
  padding: 4px 7px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
}

.brief-rule span {
  border: 1px solid rgba(31, 107, 79, 0.18);
  background: rgba(31, 107, 79, 0.06);
  color: #1f6b4f;
}

.create-card {
  margin-bottom: var(--space-4);
}

.panel-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.panel-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.form-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.full-width {
  grid-column: 1 / -1;
}

.form-step {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-2);
  padding: var(--space-3) 0 var(--space-2);
  border-bottom: 1px solid var(--border-subtle);
}

.form-step-index {
  display: grid;
  flex: 0 0 34px;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #1f6b4f;
  color: white;
  font-size: 11px;
  font-weight: 800;
}

.form-step strong {
  display: block;
  font-family: "Noto Serif SC", "Songti SC", serif;
}

.form-step p {
  margin: 2px 0 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-weight: 600;
}

.field-help {
  color: var(--text-tertiary);
  font-size: 10px;
  line-height: 1.55;
}

.checkbox-field {
  flex-direction: row;
  align-items: center;
}

.completion-notice-field {
  padding: var(--space-4);
  border: 1px solid rgba(31, 107, 79, 0.22);
  background: rgba(31, 107, 79, 0.05);
}

.form-submit-row {
  margin-top: var(--space-2);
}

.preview-section {
  margin-top: var(--space-2);
}

.status-select {
  max-width: 145px;
}

.reminder-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item-id {
  font-size: 11px;
}

.time-label {
  color: var(--text-secondary);
}

.delivery-cell {
  display: flex;
  min-width: 138px;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
}

.content-type-chip {
  border: 1px solid var(--border-subtle);
  background: var(--surface-hover);
  color: var(--text-primary);
}

.interactive-chip {
  background: rgba(31, 107, 79, 0.1);
  color: #1f6b4f;
}

.broadcast-chip {
  border: 1px solid rgba(177, 69, 47, 0.25);
  background: rgba(177, 69, 47, 0.09);
  color: #9a3f2c;
}

.audience-mode-field {
  justify-content: center;
  padding: var(--space-3);
  border: 1px solid var(--border-subtle);
  background: var(--surface-hover);
}

.audience-mode-field.broadcast {
  border-color: rgba(177, 69, 47, 0.3);
  background: rgba(177, 69, 47, 0.06);
}

.broadcast-contract {
  position: relative;
  overflow: hidden;
  padding: var(--space-4) var(--space-5);
  border: 1px solid rgba(177, 69, 47, 0.34);
  border-left: 5px solid #b1452f;
  background: linear-gradient(110deg, rgba(177, 69, 47, 0.1), rgba(255, 248, 235, 0.76));
}

.broadcast-contract::after {
  position: absolute;
  top: -22px;
  right: 20px;
  color: rgba(177, 69, 47, 0.08);
  content: '@ALL';
  font-family: var(--font-mono);
  font-size: 72px;
  font-weight: 900;
}

.broadcast-contract strong,
.broadcast-contract p,
.broadcast-kicker {
  position: relative;
  z-index: 1;
}

.broadcast-kicker {
  display: block;
  margin-bottom: 5px;
  color: #9a3f2c;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.14em;
}

.broadcast-contract strong {
  font-family: "Noto Serif SC", "Songti SC", serif;
}

.broadcast-contract p {
  max-width: 820px;
  margin: 7px 0 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: 1.7;
}

.passive-chip {
  background: rgba(113, 117, 109, 0.09);
  color: var(--text-secondary);
}

.preview-triggers-box {
  background: var(--bg-surface-secondary, rgba(0,0,0,0.02));
  padding: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px dashed var(--border-color);
}

.triggers-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: 4px;
}

.trigger-badge {
  background: var(--bg-surface-tertiary, rgba(0,0,0,0.05));
  padding: 2px 8px;
  border-radius: 4px;
  color: var(--text-secondary);
}

.media-preview {
  width: min(100%, 320px);
  max-height: 180px;
  object-fit: cover;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}

@media (max-width: 600px) {
  .interaction-brief {
    grid-template-columns: 1fr;
  }

  .brief-rule {
    justify-content: flex-start;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }
  .status-select {
    max-width: 100%;
  }
}
</style>
