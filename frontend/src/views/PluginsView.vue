<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { api } from '@/lib/api';
import type { Plugin, Person } from '@/types';
import PageHeader from '@/components/PageHeader.vue';
import StatusBadge from '@/components/StatusBadge.vue';
import EmptyState from '@/components/EmptyState.vue';
import ConfirmDialog from '@/components/ConfirmDialog.vue';
import { useUiStore } from '@/stores/ui';

const ui = useUiStore();
const items = ref<Plugin[]>([]);
const people = ref<Person[]>([]);
const running = ref(new Set<string>());
const target = ref<Plugin>();
const editing = ref<any | null>(null);
const busy = ref(false);

const editForm = reactive({
  username: '',
  twscrape_fetch_limit: 40,
  interval_seconds: 180,
  include_replies: true,
  include_reposts: false,
  source: 'twscrape',
  feed_url: '',
  cover_image_url: '',
  fallback_cover_url: '',
  recipients: [] as string[],
  secrets: {} as Record<string, string>
});

async function load() {
  try {
    const data = await api.get<Plugin[] | { items: Plugin[] }>('/admin/plugins');
    items.value = Array.isArray(data) ? data : data.items;
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '插件加载失败', 'danger');
  }
}

async function loadPeople() {
  try {
    const data = await api.get<Person[] | { items: Person[] }>('/admin/people');
    people.value = Array.isArray(data) ? data : data.items;
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '接收人加载失败', 'danger');
  }
}

async function run(item: Plugin) {
  if (running.value.has(item.id)) return;
  running.value.add(item.id);
  try {
    await api.post(`/admin/plugins/${item.id}/run`);
    ui.toast(`${item.name} 已进入运行队列`, 'success');
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '运行失败', 'danger');
  } finally {
    window.setTimeout(() => running.value.delete(item.id), 1800);
  }
}

async function toggle() {
  if (!target.value) return;
  busy.value = true;
  const verb = target.value.enabled ? 'disable' : 'enable';
  try {
    await api.post(`/admin/plugins/${target.value.id}/${verb}`);
    ui.toast(`插件已${target.value.enabled ? '停用' : '启用'}`, 'success');
    target.value = undefined;
    await load();
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '操作失败', 'danger');
  } finally {
    busy.value = false;
  }
}

async function configure(item: Plugin) {
  try {
    const [details, secretsData] = await Promise.all([
      api.get<any>(`/admin/plugins/${item.id}`),
      api.get<any[]>(`/admin/plugins/${item.id}/secrets`),
    ]);

    editing.value = { ...item, secrets: secretsData };

    const conf = details.config || {};
    editForm.username = conf.username || '';
    editForm.twscrape_fetch_limit = conf.twscrape_fetch_limit || 40;

    const sched = details.schedule || item.schedule || {};
    editForm.interval_seconds = sched.seconds || 180;

    editForm.include_replies = conf.include_replies !== false;
    editForm.include_reposts = !!conf.include_reposts;
    editForm.source = conf.source || 'twscrape';
    editForm.feed_url = conf.feed_url || '';
    editForm.cover_image_url = conf.cover_image_url || '';
    editForm.fallback_cover_url = conf.fallback_cover_url || '';
    editForm.recipients = conf.recipients || [];

    editForm.secrets = {};
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '加载配置失败', 'danger');
  }
}

async function saveConfig() {
  if (!editing.value) return;
  busy.value = true;
  try {
    const pluginId = editing.value.id;
    const configData: Record<string, any> = {
      username: editForm.username,
      include_replies: editForm.include_replies,
      include_reposts: editForm.include_reposts,
      recipients: editForm.recipients,
    };

    if (pluginId === 'codex_x_monitor') {
      configData.source = editForm.source;
      if (editForm.source === 'rss') {
        configData.feed_url = editForm.feed_url;
      } else if (editForm.source === 'twscrape') {
        configData.twscrape_fetch_limit = editForm.twscrape_fetch_limit;
      }
      if (editForm.cover_image_url) {
        configData.cover_image_url = editForm.cover_image_url;
      }
    } else if (pluginId === 'fabrizio_hwg_monitor') {
      configData.source = 'twscrape';
      configData.twscrape_fetch_limit = editForm.twscrape_fetch_limit;
      if (editForm.fallback_cover_url) {
        configData.fallback_cover_url = editForm.fallback_cover_url;
      }
    }

    await api.put(`/admin/plugins/${pluginId}/config`, {
      config: configData,
      schedule: {
        type: 'interval',
        seconds: Number(editForm.interval_seconds)
      }
    });

    for (const [secName, secVal] of Object.entries(editForm.secrets)) {
      if (secVal.trim()) {
        await api.put(`/admin/plugins/${pluginId}/secrets/${secName}`, {
          value: secVal.trim()
        });
      }
    }

    ui.toast('配置已更新', 'success');
    editing.value = null;
    await load();
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '保存配置失败', 'danger');
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  load();
  loadPeople();
});

const time = (v?: string) => v ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(v)) : '—';
</script>

<template>
  <PageHeader title="插件运行台" description="插件只发现事件；投递、去重与 Secret 始终由核心平台掌控。" />

  <section v-if="editing" class="panel" style="margin-bottom: 24px;">
    <div class="panel-title">
      <h2>配置插件 - {{ editing.name }}</h2>
    </div>
    <form class="grid" style="grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px;" @submit.prevent="saveConfig">
      <div class="field">
        <label>监控账号</label>
        <input v-model="editForm.username" class="input" required>
      </div>

      <div class="field">
        <label>抓取周期（分钟）</label>
        <input 
          :value="Math.round(editForm.interval_seconds / 60)" 
          @input="editForm.interval_seconds = Number(($event.target as HTMLInputElement).value) * 60" 
          class="input" 
          type="number" 
          min="1" 
          required
        >
      </div>

      <template v-if="editing.id === 'codex_x_monitor'">
        <div class="field">
          <label>数据源</label>
          <select v-model="editForm.source" class="select">
            <option value="rss">RSS</option>
            <option value="x_api">X API</option>
            <option value="twscrape">twscrape</option>
          </select>
        </div>

        <div v-if="editForm.source === 'rss'" class="field">
          <label>RSS Feed URL</label>
          <input v-model="editForm.feed_url" class="input" type="url" required>
        </div>

        <div v-if="editForm.source === 'twscrape'" class="field">
          <label>twscrape 抓取条数</label>
          <input v-model.number="editForm.twscrape_fetch_limit" class="input" type="number" min="10" max="100" required>
        </div>
      </template>

      <template v-if="editing.id === 'fabrizio_hwg_monitor'">
        <div class="field">
          <label>twscrape 抓取条数</label>
          <input v-model.number="editForm.twscrape_fetch_limit" class="input" type="number" min="10" max="100" required>
        </div>
      </template>

      <div class="field" style="grid-column: 1 / -1; display: flex; gap: 24px; align-items: center; margin-top: 8px;">
        <label style="display: flex; align-items: center; gap: 6px; font-weight: normal; cursor: pointer;">
          <input v-model="editForm.include_replies" type="checkbox"> 包含回复
        </label>
        <label style="display: flex; align-items: center; gap: 6px; font-weight: normal; cursor: pointer;">
          <input v-model="editForm.include_reposts" type="checkbox"> 包含转推
        </label>
      </div>

      <div v-if="editing.id === 'codex_x_monitor'" class="field" style="grid-column: 1 / -1;">
        <label>封面图片 URL (可选)</label>
        <input v-model="editForm.cover_image_url" class="input" type="url" placeholder="https://...">
      </div>
      <div v-if="editing.id === 'fabrizio_hwg_monitor'" class="field" style="grid-column: 1 / -1;">
        <label>回退默认封面 URL (可选)</label>
        <input v-model="editForm.fallback_cover_url" class="input" type="url" placeholder="https://...">
      </div>

      <div class="field" style="grid-column: 1 / -1;">
        <label>接收人</label>
        <div style="display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px;">
          <label v-for="person in people" :key="person.id" style="display: flex; align-items: center; gap: 6px; font-weight: normal; cursor: pointer;">
            <input type="checkbox" :value="person.id" v-model="editForm.recipients">
            {{ person.name }}
          </label>
        </div>
      </div>

      <div v-if="editing.secrets && editing.secrets.length" class="field" style="grid-column: 1 / -1;">
        <label>密码 / Cookie 配置</label>
        <div v-for="sec in editing.secrets" :key="sec.name" style="margin-top: 12px; padding: 12px; background: rgba(0,0,0,0.02); border-radius: 6px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <strong class="mono">{{ sec.name }}</strong>
            <span class="muted" style="font-size: 0.85em;">
              {{ sec.source === 'env' ? '已通过环境变量配置' : (sec.configured ? '已配置' : '未配置') }}
            </span>
          </div>
          <input 
            type="password" 
            class="input" 
            v-model="editForm.secrets[sec.name]" 
            :placeholder="sec.source === 'env' ? '已通过环境变量配置' : (sec.configured ? '若要更新，在此输入新值；留空不修改' : '请输入值')"
            :disabled="sec.source === 'env'"
          >
        </div>
      </div>

      <div style="grid-column: 1 / -1; display: flex; gap: 12px; margin-top: 16px;">
        <button class="btn btn--primary" type="submit" :disabled="busy">
          {{ busy ? '保存中…' : '保存配置' }}
        </button>
        <button class="btn btn--ghost" type="button" @click="editing = null">
          取消
        </button>
      </div>
    </form>
  </section>

  <EmptyState v-if="!items.length" />
  <section v-else class="grid entity-grid">
    <article v-for="item in items" :key="item.id" class="entity-card">
      <header>
        <div>
          <h3>{{ item.name }} <small class="muted">{{ item.version }}</small></h3>
          <span class="mono muted">{{ item.id }}</span>
        </div>
        <StatusBadge :status="item.status" />
      </header>
      <p>{{ item.description }}</p>
      <div class="entity-meta">
        <div><span>调度</span><strong class="mono">{{ item.schedule ?? '手动' }}</strong></div>
        <div><span>上次运行</span><strong>{{ time(item.last_run_at) }}</strong></div>
        <div><span>下次运行</span><strong>{{ time(item.next_run_at) }}</strong></div>
        <div><span>连续失败</span><strong :class="{ 'danger': item.consecutive_failures }">{{ item.consecutive_failures ?? 0 }}</strong></div>
        <div v-for="secretItem in item.secrets ?? []" :key="secretItem.name">
          <span>{{ secretItem.name }}</span>
          <strong>
            {{ secretItem.source === 'env' ? '已通过环境变量配置' : (secretItem.configured ? '已配置' : '未配置') }}
          </strong>
        </div>
      </div>
      <footer>
        <button class="btn btn--primary btn--small" :disabled="running.has(item.id)" @click="run(item)">
          {{ running.has(item.id) ? '已排队' : '立即运行' }}
        </button>
        <button class="btn btn--ghost btn--small" @click="configure(item)">
          配置
        </button>
        <button class="btn btn--ghost btn--small" @click="target = item">
          {{ item.enabled ? '停用' : '启用' }}
        </button>
      </footer>
    </article>
  </section>

  <ConfirmDialog 
    :open="Boolean(target)" 
    :title="`${target?.enabled ? '停用' : '启用'}插件？`" 
    :description="target?.enabled ? '将停止后续调度；当前已入队运行不会被强制中断。' : '插件会按已保存配置恢复持久化调度。'" 
    :busy="busy" 
    @cancel="target = undefined" 
    @confirm="toggle" 
  />
</template>