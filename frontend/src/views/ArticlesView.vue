<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Copy, ExternalLink, Eye, Newspaper, RotateCcw, Undo2 } from 'lucide-vue-next'
import { api, query } from '@/lib/api'
import type { MpArticle, MpArticleConfig, Page } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import LoadingState from '@/components/feedback/LoadingState.vue'
import DataTable from '@/components/data/DataTable.vue'
import TableToolbar from '@/components/data/TableToolbar.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppDialog from '@/components/ui/AppDialog.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppStatus from '@/components/ui/AppStatus.vue'
import { useUiStore } from '@/stores/ui'
import { formatInstant } from '@/lib/time'
import { useSettingsStore } from '@/stores/settings'

const ui = useUiStore()
const settings = useSettingsStore()

const page = ref(1)
const status = ref('')
const loading = ref(false)
const config = ref<MpArticleConfig | null>(null)
const result = ref<Page<MpArticle>>({ items: [], page: 1, page_size: 20, total: 0 })
const selected = ref<MpArticle | null>(null)
const detailLoading = ref(false)
const detailOpen = ref(false)
const acting = ref(false)

const statusLabel: Record<string, string> = {
  draft: '草稿',
  ready: '待发布',
  published: '已发布',
  ignored: '已忽略',
}

const aiStatusLabel: Record<string, string> = {
  rules_summary: '规则摘要',
  ai_summarized: 'AI 摘要',
  fallback_summary: '摘要回退',
}

function aiLabel(value: string | null): string {
  return value ? (aiStatusLabel[value] ?? value) : '—'
}

function modeBanner(): { tone: 'info' | 'success'; text: string } | null {
  if (!config.value) return null
  if (config.value.effective_mode === 'library') {
    return {
      tone: 'info',
      text: '当前为文章库模式：文章已由 Notify-Hub 生成并排版。复制公众号格式或使用浏览器导入脚本填入后台，最终发布由你确认。',
    }
  }
  if (config.value.effective_mode === 'draft') {
    return { tone: 'info', text: '当前为草稿模式：文章已通过官方 API 保存到公众号草稿箱。' }
  }
  return { tone: 'success', text: '当前为自动发布模式：文章通过官方 API 建草稿并提交发布。' }
}

async function load() {
  loading.value = true
  try {
    result.value = await api.get<Page<MpArticle>>(
      '/admin/articles' +
        query({
          page: page.value,
          page_size: 20,
          status: status.value,
        }),
    )
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '文章列表加载失败', 'danger')
  } finally {
    loading.value = false
  }
}

async function loadConfig() {
  try {
    config.value = await api.get<MpArticleConfig>('/admin/articles/config')
  } catch {
    config.value = null
  }
}

watch(status, () => {
  if (page.value === 1) {
    void load()
  } else {
    page.value = 1
  }
})

watch(page, () => {
  void load()
})
async function openArticle(item: MpArticle) {
  detailLoading.value = true
  detailOpen.value = true
  try {
    selected.value = await api.get<MpArticle>(`/admin/articles/${item.id}`)
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '文章详情加载失败', 'danger')
    detailOpen.value = false
  } finally {
    detailLoading.value = false
  }
}

function updateRow(next: MpArticle) {
  const index = result.value.items.findIndex((item) => item.id === next.id)
  if (index >= 0) result.value.items[index] = next
  selected.value = next
}

async function act(action: 'publish' | 'ignore' | 'restore') {
  if (!selected.value) return
  acting.value = true
  try {
    const next = await api.post<MpArticle>(`/admin/articles/${selected.value.id}/${action}`)
    updateRow(next)
    const label = action === 'publish' ? '已标记发布' : action === 'ignore' ? '已忽略' : '已恢复为待发布'
    ui.toast(`${label}：${next.title}`, 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '操作失败', 'danger')
  } finally {
    acting.value = false
  }
}

function legacyCopy(html: string, text: string) {
  if (typeof document.execCommand !== 'function') return false
  const holder = document.createElement('div')
  holder.contentEditable = 'true'
  holder.style.position = 'fixed'
  holder.style.left = '-9999px'
  holder.style.top = '0'
  holder.innerHTML = html
  document.body.appendChild(holder)
  const range = document.createRange()
  range.selectNodeContents(holder)
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
  const ok = document.execCommand('copy')
  holder.remove()
  if (ok) return true
  const area = document.createElement('textarea')
  area.value = text
  document.body.appendChild(area)
  area.select()
  const plainOk = document.execCommand('copy')
  area.remove()
  return plainOk
}

async function copyWechatFormat() {
  if (!selected.value) return
  const html = selected.value.content_html
  const text = selected.value.content
  try {
    if (navigator.clipboard?.write && 'ClipboardItem' in window) {
      await navigator.clipboard.write([
        new window.ClipboardItem({
          'text/html': new window.Blob([html], { type: 'text/html' }),
          'text/plain': new window.Blob([text], { type: 'text/plain' }),
        }),
      ])
      ui.toast('公众号格式已复制，打开公众号后台 Ctrl+V', 'success')
      return
    }
  } catch {
    // Fall through to the legacy copy path.
  }
  const ok = legacyCopy(html, text)
  ui.toast(ok ? '公众号格式已复制（兼容模式）' : '复制失败，请手动选择正文复制', ok ? 'success' : 'danger')
}

function openMpEditor() {
  const url = config.value?.mp_editor_url ?? 'https://mp.weixin.qq.com'
  window.open(url, '_blank', 'noopener,noreferrer')
}

const time = (v: string) => formatInstant(v, settings.timezone)

onMounted(() => {
  void settings.load()
  void loadConfig()
  void load()
})
</script>

<template>
  <PageHeader title="公众号文章" description="AI 已生成正文并完成公众号排版，最终发布由你确认；官方 API 路径同样保留。">
    <template #default>
      <AppButton variant="secondary" @click="openMpEditor">
        <ExternalLink :size="14" />
        打开公众号后台
      </AppButton>
    </template>
  </PageHeader>

  <AppAlert v-if="modeBanner()" :variant="modeBanner()!.tone" class="mode-banner">
    {{ modeBanner()!.text }}
  </AppAlert>

  <AppCard padding="md">
    <TableToolbar>
      <template #left>
        <AppSelect v-model="status" class="status-select" aria-label="文章状态筛选">
          <option value="">
            全部状态
          </option>
          <option value="ready">
            待发布
          </option>
          <option value="draft">
            草稿
          </option>
          <option value="published">
            已发布
          </option>
          <option value="ignored">
            已忽略
          </option>
        </AppSelect>
      </template>
    </TableToolbar>

    <LoadingState v-if="loading" message="LOADING ARTICLES..." />

    <EmptyState
      v-else-if="!result.items.length"
      title="暂无文章"
      description="插件命中并生成公众号文章后，会出现在这里。"
    />

    <template v-else>
      <DataTable>
        <template #headers>
          <th>封面</th>
          <th>标题</th>
          <th>状态</th>
          <th>AI 摘要</th>
          <th>作者</th>
          <th>创建时间</th>
          <th>操作</th>
        </template>
        <tr v-for="item in result.items" :key="item.id">
          <td>
            <img
              v-if="item.cover_url"
              :src="item.cover_url"
              class="cover-thumb"
              alt=""
            >
            <span v-else class="cover-placeholder">
              <Newspaper :size="16" />
            </span>
          </td>
          <td>
            <div class="title-cell">
              <button type="button" class="link-button" @click="openArticle(item)">
                {{ item.title }}
              </button>
              <span v-if="item.source_url" class="source-hint">{{ item.source_url }}</span>
            </div>
          </td>
          <td>
            <AppStatus :status="item.status" :label="statusLabel[item.status] ?? item.status" />
          </td>
          <td>
            <span class="muted">{{ aiLabel(item.ai_status) }}</span>
          </td>
          <td>
            <span class="muted">{{ item.author }}</span>
          </td>
          <td>
            <span class="time-label">{{ time(item.created_at) }}</span>
          </td>
          <td>
            <div class="row-actions">
              <AppButton size="sm" @click="openArticle(item)">
                <Eye :size="14" />
                查看
              </AppButton>
            </div>
          </td>
        </tr>
      </DataTable>

      <PaginationBar
        :page="page"
        :page-size="20"
        :total="result.total"
        @change="page = $event"
      />
    </template>
  </AppCard>

  <AppDialog v-model="detailOpen" title="文章预览">
    <div v-if="detailLoading" class="detail-loading">
      <LoadingState message="LOADING ARTICLE..." />
    </div>
    <div v-else-if="selected" class="article-detail">
      <div class="detail-meta">
        <AppStatus :status="selected.status" :label="statusLabel[selected.status] ?? selected.status" />
        <span v-if="selected.ai_profile" class="meta-chip">
          摘要：{{ aiLabel(selected.ai_status) }} · {{ selected.ai_profile }}
        </span>
      </div>
      <h2 class="detail-title">
        {{ selected.title }}
      </h2>
      <p v-if="selected.digest" class="detail-digest">
        {{ selected.digest }}
      </p>
      <p class="detail-author">
        作者：{{ selected.author }}
      </p>
      <!-- eslint-disable-next-line vue/no-v-html -- content_html is generated by the server from escaped content -->
      <div class="html-preview" v-html="selected.content_html" />
    </div>
    <template #footer>
      <template v-if="selected">
        <AppButton variant="secondary" :disabled="acting" @click="copyWechatFormat">
          <Copy :size="14" />
          复制公众号格式
        </AppButton>
        <AppButton
          v-if="selected.status === 'draft' || selected.status === 'ready'"
          variant="primary"
          :disabled="acting"
          @click="act('publish')"
        >
          标记已发布
        </AppButton>
        <AppButton
          v-if="selected.status === 'draft' || selected.status === 'ready'"
          variant="danger"
          :disabled="acting"
          @click="act('ignore')"
        >
          忽略
        </AppButton>
        <AppButton
          v-else-if="selected.status === 'ignored'"
          variant="secondary"
          :disabled="acting"
          @click="act('restore')"
        >
          <Undo2 :size="14" />
          恢复
        </AppButton>
        <AppButton
          v-else-if="selected.status === 'published'"
          variant="secondary"
          :disabled="acting"
          @click="act('restore')"
        >
          <RotateCcw :size="14" />
          恢复为待发布
        </AppButton>
      </template>
    </template>
  </AppDialog>
</template>

<style scoped>
.mode-banner {
  margin-bottom: var(--space-4);
}

.status-select {
  max-width: 150px;
}

.cover-thumb {
  width: 46px;
  height: 30px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid var(--border-subtle);
  display: block;
}

.cover-placeholder {
  width: 46px;
  height: 30px;
  border-radius: 4px;
  border: 1px dashed var(--border-default);
  color: var(--text-tertiary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.title-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-width: 360px;
}

.link-button {
  background: none;
  border: none;
  padding: 0;
  color: var(--action-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  white-space: normal;
}

.link-button:hover {
  text-decoration: underline;
}

.source-hint {
  font-size: 11px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 340px;
}

.muted {
  color: var(--text-secondary);
}

.time-label {
  color: var(--text-secondary);
  font-size: var(--text-xs);
}

.row-actions {
  display: flex;
  gap: var(--space-2);
}

.detail-loading {
  padding: var(--space-6) 0;
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.meta-chip {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  background-color: var(--surface-hover);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  padding: 2px 8px;
}

.detail-title {
  font-size: var(--text-xl);
  font-weight: 700;
  margin: 0 0 var(--space-2);
  color: var(--text-primary);
  line-height: var(--leading-tight);
}

.detail-digest {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  margin: 0 0 var(--space-2);
}

.detail-author {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  margin: 0 0 var(--space-4);
}

.html-preview {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: var(--space-5);
  background-color: #fff;
  color: #3f3f3f;
  overflow-wrap: break-word;
}

.html-preview :deep(img) {
  max-width: 100%;
  border-radius: 8px;
}

.html-preview :deep(a) {
  color: #576b95;
  text-decoration: none;
}

.source-link {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  overflow-wrap: break-word;
}

@media (max-width: 600px) {
  .status-select {
    max-width: 100%;
  }
}
</style>
