<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, query } from '@/lib/api'
import type { Notification, Page } from '@/types'
import { useApplicationProfiles } from '@/composables/useApplicationProfiles'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppCard from '@/components/ui/AppCard.vue'
import DataTable from '@/components/data/DataTable.vue'
import TableToolbar from '@/components/data/TableToolbar.vue'
import LoadingState from '@/components/feedback/LoadingState.vue'
import { useUiStore } from '@/stores/ui'
import { formatInstant } from '@/lib/time'
import { useSettingsStore } from '@/stores/settings'

const ui = useUiStore()
const settings = useSettingsStore()
const { profiles, profileLabel, loadProfiles } = useApplicationProfiles()
const page = ref(1)
const status = ref('')
const keyword = ref('')
const profileFilter = ref('')
const loading = ref(false)
const result = ref<Page<Notification>>({
  items: [],
  page: 1,
  page_size: 20,
  total: 0
})

async function load() {
  loading.value = true
  try {
    result.value = await api.get<Page<Notification>>(
      '/admin/notifications' +
        query({
          page: page.value,
          page_size: 20,
          status: status.value,
          keyword: keyword.value,
          profile_id: profileFilter.value
        })
    )
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '通知加载失败', 'danger')
  } finally {
    loading.value = false
  }
}

let timer = 0
watch([status, keyword, profileFilter], () => {
  page.value = 1
  clearTimeout(timer)
  timer = window.setTimeout(load, 250)
})

onMounted(async () => {
  void settings.load()
  try {
    await loadProfiles()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Profile 加载失败', 'danger')
  }
  void load()
})

const time = (v: string) => formatInstant(v, settings.timezone)
</script>

<template>
  <PageHeader
    title="通知与投递"
    description="查看 Event → Notification → Delivery 的完整链路，并处理进入 dead 的任务。"
  />

  <AppCard padding="md">
    <TableToolbar>
      <template #left>
        <AppInput
          v-model="keyword"
          placeholder="搜索标题、正文或事件 ID"
          class="search-input"
        />
        <AppSelect v-model="status" class="status-select">
          <option value="">
            全部状态
          </option>
          <option value="succeeded">
            成功
          </option>
          <option value="retry_wait">
            等待重试
          </option>
          <option value="dead">
            已终止
          </option>
          <option value="processing">
            处理中
          </option>
        </AppSelect>
        <AppSelect v-model="profileFilter" class="profile-select">
          <option value="">
            全部 Profiles
          </option>
          <option v-for="profile in profiles" :key="profile.id" :value="profile.id">
            {{ profile.name }} · {{ profile.key }}
          </option>
        </AppSelect>
      </template>
    </TableToolbar>

    <LoadingState v-if="loading" message="LOADING DELIVERIES..." />
    
    <EmptyState v-else-if="!result.items.length" />
    
    <template v-else>
      <DataTable>
        <template #headers>
          <th>通知</th>
          <th>Profile</th>
          <th>消息类型</th>
          <th>优先级</th>
          <th>状态</th>
          <th>创建时间</th>
        </template>
        <tr v-for="item in result.items" :key="item.id">
          <td>
            <div class="notification-cell">
              <RouterLink class="link" :to="`/notifications/${item.id}`">
                {{ item.title }}
              </RouterLink>
              <span class="mono muted item-id">{{ item.id }}</span>
            </div>
          </td>
          <td>
            <span class="profile-label">{{ profileLabel(item.profile_id) }}</span>
          </td>
          <td>
            <span class="mono">{{ item.message_type }}</span>
          </td>
          <td>
            <span class="priority-label">{{ item.priority }}</span>
          </td>
          <td>
            <StatusBadge :status="item.status ?? 'pending'" />
          </td>
          <td>
            <span class="time-label">{{ time(item.created_at) }}</span>
          </td>
        </tr>
      </DataTable>

      <PaginationBar
        :page="page"
        :page-size="20"
        :total="result.total"
        @change="page = $event; load()"
      />
    </template>
  </AppCard>
</template>

<style scoped>
.search-input {
  max-width: 280px;
}

.status-select {
  max-width: 145px;
}

.profile-select {
  max-width: 190px;
}

.profile-label {
  color: var(--text-secondary);
  font-size: var(--text-xs);
}

.notification-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item-id {
  font-size: 11px;
}

.priority-label {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.time-label {
  color: var(--text-secondary);
}

@media (max-width: 600px) {
  .search-input, .status-select, .profile-select {
    max-width: 100%;
  }
}
</style>
