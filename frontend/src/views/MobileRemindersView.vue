<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, BellRing, CalendarClock, CheckCircle2, ImagePlus, Plus } from 'lucide-vue-next'
import { captureMobileEntry, hasMobileEntry, mobileRequest } from '@/lib/mobileApi'

interface MobileReminder {
  id: string
  title: string
  content: string
  content_type: 'text' | 'image' | 'article'
  status: string
  next_run_at?: string
  scheduled_at?: string
  timezone: string
  require_ack: boolean
  repeat_interval_seconds?: number
  max_attempts?: number
  occurrences?: Array<{
    id: string
    scheduled_for: string
    status: string
    completed_at?: string
    recipient?: { status: string; notify_count: number; acknowledged_at?: string }
  }>
}

const route = useRoute()
const router = useRouter()
const busy = ref(false)
const error = ref('')
const reminders = ref<MobileReminder[]>([])
const detail = ref<MobileReminder | null>(null)
const previewUrl = ref('')
const mediaId = ref<string | null>(null)
const scope = computed(() => String(route.query.scope ?? 'active'))
const mode = computed(() => {
  if (route.name === 'mobile-reminder-new') return 'new'
  if (route.name === 'mobile-reminder-detail') return 'detail'
  return 'active'
})

const initialDate = new Date(Date.now() + 60 * 60 * 1000)
initialDate.setSeconds(0, 0)
const form = reactive({
  title: '',
  content: '',
  contentType: (route.query.content === 'article' ? 'article' : 'text') as
    | 'text'
    | 'image'
    | 'article',
  runAt: toLocalInput(initialDate),
  requireAck: false,
  repeatMinutes: 5,
  maxAttempts: 6,
})

function toLocalInput(value: Date) {
  const offset = value.getTimezoneOffset() * 60_000
  return new Date(value.getTime() - offset).toISOString().slice(0, 16)
}

function displayTime(value?: string) {
  if (!value) return '尚未排期'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function statusText(value: string) {
  return (
    {
      active: '进行中',
      paused: '已暂停',
      completed: '已完成',
      acknowledged: '已完成',
      pending: '等待完成',
      expired: '已过期',
      cancelled: '已取消',
    }[value] ?? value
  )
}

async function initializeEntry() {
  captureMobileEntry(route.query.entry)
  if (route.query.entry) {
    const query = { ...route.query }
    delete query.entry
    await router.replace({ query })
  }
  if (!hasMobileEntry()) throw new Error('请从企业微信应用菜单重新打开提醒中心。')
}

async function load() {
  error.value = ''
  busy.value = true
  try {
    await initializeEntry()
    if (mode.value === 'active') {
      const response = await mobileRequest<{ items: MobileReminder[] }>(
        `/reminders?scope=${encodeURIComponent(scope.value)}`,
      )
      reminders.value = response.items
    } else if (mode.value === 'detail') {
      detail.value = await mobileRequest<MobileReminder>(`/reminders/${String(route.params.id)}`)
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '加载失败，请稍后重试。'
  } finally {
    busy.value = false
  }
}

async function uploadImage(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (previewUrl.value) globalThis.URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = globalThis.URL.createObjectURL(file)
  const data = new globalThis.FormData()
  data.append('file', file)
  busy.value = true
  error.value = ''
  try {
    const uploaded = await mobileRequest<{ id: string }>('/media', { method: 'POST', body: data })
    mediaId.value = uploaded.id
    if (form.contentType === 'text') form.contentType = 'article'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '图片上传失败。'
  } finally {
    busy.value = false
  }
}

async function createReminder() {
  busy.value = true
  error.value = ''
  try {
    if ((form.contentType === 'image' || form.contentType === 'article') && !mediaId.value) {
      throw new Error('请先上传一张图片。')
    }
    const item = await mobileRequest<MobileReminder>('/reminders', {
      method: 'POST',
      body: JSON.stringify({
        title: form.title,
        content: form.content,
        content_type: form.contentType,
        media_asset_id: mediaId.value,
        schedule: {
          type: 'once',
          at: new Date(form.runAt).toISOString(),
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
        },
        require_ack: form.requireAck,
        repeat: form.requireAck
          ? {
              interval_seconds: form.repeatMinutes * 60,
              max_attempts: form.maxAttempts,
            }
          : undefined,
      }),
    })
    await router.push({ name: 'mobile-reminder-detail', params: { id: item.id } })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '创建失败，请检查填写内容。'
  } finally {
    busy.value = false
  }
}

watch(() => route.fullPath, load)
onMounted(load)
onBeforeUnmount(() => {
  if (previewUrl.value) globalThis.URL.revokeObjectURL(previewUrl.value)
})
</script>

<template>
  <main class="mobile-reminders">
    <div class="paper-glow" aria-hidden="true" />
    <header class="mobile-header">
      <button v-if="mode === 'detail'" class="icon-button" aria-label="返回" @click="router.back()">
        <ArrowLeft :size="20" />
      </button>
      <div>
        <p class="eyebrow">
          NOTIFY HUB · MOBILE
        </p>
        <h1>{{ mode === 'new' ? '记下一件事' : mode === 'detail' ? '提醒详情' : '我的提醒' }}</h1>
      </div>
      <RouterLink v-if="mode !== 'new'" class="new-button" to="/m/reminders/new" aria-label="新建提醒">
        <Plus :size="20" />
      </RouterLink>
    </header>

    <p v-if="error" class="error-card" role="alert">
      {{ error }}
    </p>

    <section v-if="mode === 'active'" class="list-section" aria-label="提醒列表">
      <nav class="scope-tabs" aria-label="提醒筛选">
        <RouterLink :to="{ path: '/m/reminders/active', query: { scope: 'active' } }">
          进行中
        </RouterLink>
        <RouterLink :to="{ path: '/m/reminders/active', query: { scope: 'awaiting_ack' } }">
          待完成
        </RouterLink>
        <RouterLink :to="{ path: '/m/reminders/active', query: { scope: 'today' } }">
          今天
        </RouterLink>
        <RouterLink :to="{ path: '/m/reminders/active', query: { scope: 'all' } }">
          全部
        </RouterLink>
      </nav>
      <div v-if="busy" class="empty-card">
        正在整理你的提醒…
      </div>
      <div v-else-if="!reminders.length" class="empty-card">
        <BellRing :size="28" />
        <strong>这里暂时很安静</strong>
        <span>新建一条提醒，届时我们会准时叫你。</span>
      </div>
      <RouterLink
        v-for="item in reminders"
        :key="item.id"
        class="reminder-card"
        :to="`/m/reminders/${item.id}`"
      >
        <span class="status-dot" :class="`status-${item.status}`" />
        <div class="card-copy">
          <strong>{{ item.title }}</strong>
          <span>{{ displayTime(item.next_run_at || item.scheduled_at) }}</span>
        </div>
        <span class="status-label">{{ statusText(item.status) }}</span>
      </RouterLink>
    </section>

    <form v-else-if="mode === 'new'" class="create-sheet" @submit.prevent="createReminder">
      <label>
        <span>提醒标题</span>
        <input v-model.trim="form.title" required maxlength="200" placeholder="例如：出门带药">
      </label>
      <label>
        <span>补充说明</span>
        <textarea v-model="form.content" maxlength="20000" rows="4" placeholder="留一点届时有用的信息" />
      </label>
      <fieldset>
        <legend>消息样式</legend>
        <div class="segmented">
          <button type="button" :class="{ selected: form.contentType === 'text' }" @click="form.contentType = 'text'">
            文字
          </button>
          <button type="button" :class="{ selected: form.contentType === 'article' }" @click="form.contentType = 'article'">
            图文
          </button>
          <button type="button" :class="{ selected: form.contentType === 'image' }" @click="form.contentType = 'image'">
            图片
          </button>
        </div>
      </fieldset>
      <label v-if="form.contentType !== 'text'" class="image-picker">
        <img v-if="previewUrl" :src="previewUrl" alt="待发送图片预览">
        <span v-else><ImagePlus :size="24" /> 选择 JPEG、PNG 或 WebP</span>
        <input type="file" accept="image/jpeg,image/png,image/webp" @change="uploadImage">
      </label>
      <label>
        <span>提醒时间</span>
        <input v-model="form.runAt" type="datetime-local" required>
      </label>
      <label class="check-row">
        <input v-model="form.requireAck" type="checkbox">
        <span><strong>需要我确认完成</strong><small>未完成时可以继续催办</small></span>
      </label>
      <div v-if="form.requireAck" class="repeat-grid">
        <label><span>每隔（分钟）</span><input v-model.number="form.repeatMinutes" type="number" min="5"></label>
        <label><span>最多提醒</span><input v-model.number="form.maxAttempts" type="number" min="1" max="12"></label>
      </div>
      <button class="primary-action" type="submit" :disabled="busy">
        <CalendarClock :size="20" /> {{ busy ? '正在保存…' : '安排提醒' }}
      </button>
    </form>

    <section v-else class="detail-sheet">
      <div v-if="busy" class="empty-card">
        正在读取提醒…
      </div>
      <template v-else-if="detail">
        <div class="detail-hero">
          <span class="status-pill">{{ statusText(detail.status) }}</span>
          <h2>{{ detail.title }}</h2>
          <p>{{ detail.content || '没有补充说明' }}</p>
        </div>
        <dl>
          <div><dt>下次提醒</dt><dd>{{ displayTime(detail.next_run_at || detail.scheduled_at) }}</dd></div>
          <div><dt>需要完成</dt><dd>{{ detail.require_ack ? '是' : '否' }}</dd></div>
          <div v-if="detail.repeat_interval_seconds">
            <dt>催办间隔</dt><dd>{{ detail.repeat_interval_seconds / 60 }} 分钟</dd>
          </div>
        </dl>
        <h3>最近执行</h3>
        <div v-if="!detail.occurrences?.length" class="empty-card compact">
          尚未执行
        </div>
        <div v-for="occurrence in detail.occurrences" :key="occurrence.id" class="occurrence-row">
          <CheckCircle2 :size="18" />
          <span>{{ displayTime(occurrence.scheduled_for) }}</span>
          <strong>{{ statusText(occurrence.recipient?.status || occurrence.status) }}</strong>
        </div>
      </template>
    </section>
  </main>
</template>

<style scoped>
.mobile-reminders { --ink:#1e2b25; --paper:#f4f0e5; --green:#1f6b4f; min-height:100dvh; color:var(--ink); background:var(--paper); padding:env(safe-area-inset-top) 18px calc(32px + env(safe-area-inset-bottom)); font-family:"Noto Serif SC","Songti SC",serif; position:relative; overflow:hidden }
.mobile-reminders::before { content:""; position:fixed; inset:0; opacity:.24; pointer-events:none; background-image:repeating-linear-gradient(0deg,transparent 0 27px,rgba(31,107,79,.14) 28px) }
.paper-glow { position:fixed; width:280px; height:280px; right:-120px; top:-100px; border-radius:50%; background:#e4b15a; filter:blur(70px); opacity:.22; pointer-events:none }
.mobile-header { min-height:104px; display:flex; align-items:center; justify-content:space-between; position:relative; z-index:1; border-bottom:2px solid var(--ink) }
.mobile-header h1 { margin:3px 0 0; font-size:30px; letter-spacing:-.04em }
.eyebrow { margin:0; color:var(--green); font:700 10px/1.2 ui-monospace,monospace; letter-spacing:.18em }
.icon-button,.new-button { width:42px; height:42px; display:grid; place-items:center; border:1px solid var(--ink); color:var(--ink); background:#fffaf0; box-shadow:3px 3px 0 var(--ink) }
.list-section,.create-sheet,.detail-sheet { position:relative; z-index:1; padding-top:20px }
.scope-tabs { display:grid; grid-template-columns:repeat(4,1fr); gap:6px; margin-bottom:18px }
.scope-tabs a { padding:10px 4px; text-align:center; color:var(--ink); text-decoration:none; font-size:13px; border:1px solid rgba(30,43,37,.28); background:rgba(255,255,255,.38) }
.scope-tabs a.router-link-exact-active { color:#fff; background:var(--green); border-color:var(--green) }
.reminder-card { display:flex; align-items:center; gap:12px; min-height:72px; margin:10px 0; padding:14px; color:inherit; text-decoration:none; background:#fffaf0; border:1px solid rgba(30,43,37,.25); box-shadow:4px 4px 0 rgba(30,43,37,.12) }
.status-dot { width:10px; height:10px; border:2px solid var(--paper); outline:1px solid var(--ink); border-radius:50%; background:#d39c3f }
.status-completed,.status-acknowledged { background:var(--green) }
.card-copy { display:flex; flex:1; min-width:0; flex-direction:column; gap:5px }.card-copy strong{font-size:16px}.card-copy span,.status-label{font:12px ui-monospace,monospace;color:#5a645f}.status-label{writing-mode:vertical-rl;letter-spacing:.08em}
.empty-card,.error-card { position:relative; z-index:2; display:flex; flex-direction:column; align-items:center; gap:8px; padding:28px 18px; text-align:center; border:1px dashed rgba(30,43,37,.45); background:rgba(255,250,240,.7) }.empty-card span{font-size:13px;color:#66706b}.empty-card.compact{padding:16px}.error-card{align-items:flex-start;color:#8d322a;border-style:solid;border-color:#8d322a}
.create-sheet { display:grid; gap:18px }.create-sheet label,.create-sheet fieldset{display:grid;gap:8px;border:0;padding:0;margin:0}.create-sheet label>span,.create-sheet legend{font-size:13px;font-weight:700}.create-sheet input,.create-sheet textarea{width:100%;box-sizing:border-box;border:1px solid rgba(30,43,37,.45);border-radius:0;background:#fffaf0;color:var(--ink);padding:13px;font:15px/1.5 inherit;outline:none}.create-sheet input:focus,.create-sheet textarea:focus{border-color:var(--green);box-shadow:3px 3px 0 rgba(31,107,79,.2)}
.segmented { display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.segmented button{border:1px solid rgba(30,43,37,.35);background:transparent;padding:11px;color:var(--ink)}.segmented button.selected{background:var(--ink);color:var(--paper)}
.image-picker{min-height:132px;place-items:center;border:1px dashed var(--green)!important;background:rgba(255,255,255,.38);overflow:hidden}.image-picker span{display:flex;align-items:center;gap:8px;color:var(--green)}.image-picker input{position:absolute;opacity:0;pointer-events:none}.image-picker img{width:100%;max-height:220px;object-fit:cover}
.check-row{grid-template-columns:24px 1fr!important;align-items:start;padding:14px;border:1px solid rgba(30,43,37,.3)!important;background:#fffaf0}.check-row input{width:18px;height:18px;padding:0}.check-row span{display:flex;flex-direction:column;gap:3px}.check-row small{font-weight:400;color:#65706a}.repeat-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.primary-action{display:flex;justify-content:center;align-items:center;gap:9px;padding:15px;border:1px solid var(--ink);background:var(--green);color:#fff;font:bold 16px inherit;box-shadow:5px 5px 0 var(--ink)}.primary-action:disabled{opacity:.55}
.detail-hero{padding:24px 0;border-bottom:1px solid rgba(30,43,37,.35)}.detail-hero h2{font-size:28px;margin:12px 0 8px}.detail-hero p{margin:0;color:#59635e;line-height:1.7}.status-pill{display:inline-block;padding:4px 9px;background:#dce9df;color:var(--green);font:700 11px ui-monospace,monospace}.detail-sheet dl{margin:0 0 26px}.detail-sheet dl div{display:flex;justify-content:space-between;padding:15px 0;border-bottom:1px solid rgba(30,43,37,.18)}.detail-sheet dt{color:#68716d}.detail-sheet dd{margin:0;font-weight:700}.detail-sheet h3{font-size:15px}.occurrence-row{display:grid;grid-template-columns:24px 1fr auto;align-items:center;padding:13px 0;border-bottom:1px solid rgba(30,43,37,.16);font-size:13px}.occurrence-row strong{color:var(--green)}
@media (min-width: 640px){.mobile-reminders{max-width:520px;margin:0 auto;box-shadow:0 0 0 1px rgba(30,43,37,.1),0 20px 70px rgba(30,43,37,.18)}}
</style>
