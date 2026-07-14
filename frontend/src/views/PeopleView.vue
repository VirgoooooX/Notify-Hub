<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { api } from '@/lib/api';
import type { Person } from '@/types';
import PageHeader from '@/components/PageHeader.vue';
import StatusBadge from '@/components/StatusBadge.vue';
import EmptyState from '@/components/EmptyState.vue';
import { useUiStore } from '@/stores/ui';

const ui = useUiStore();
const items = ref<Person[]>([]);
const show = ref(false);
const busy = ref(false);

const form = reactive({
  name: '',
  user_id: '',
  is_default: false
});

// For inline binding forms
const bindForms = reactive<Record<string, string>>({});

async function load() {
  try {
    const data = await api.get<Person[] | { items: Person[] }>('/admin/people');
    items.value = Array.isArray(data) ? data : data.items;
    // Initialize bindForms state
    items.value.forEach(p => {
      if (!(p.id in bindForms)) {
        bindForms[p.id] = '';
      }
    });
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '接收人加载失败', 'danger');
  }
}

async function create() {
  busy.value = true;
  try {
    const person = await api.post<Person>('/admin/people', {
      name: form.name,
      is_default: form.is_default
    });
    if (form.user_id.trim()) {
      await api.post(`/admin/people/${person.id}/wecom-identities`, {
        user_id: form.user_id.trim()
      });
    }
    show.value = false;
    Object.assign(form, { name: '', user_id: '', is_default: false });
    ui.toast('接收人已创建', 'success');
    await load();
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '创建失败', 'danger');
  } finally {
    busy.value = false;
  }
}

async function removePerson(id: string) {
  if (!window.confirm('确认删除该接收人吗？相关企业微信关联也将一并解绑。')) return;
  try {
    await api.delete(`/admin/people/${id}`);
    ui.toast('接收人已删除', 'success');
    await load();
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '删除失败', 'danger');
  }
}

async function removeIdentity(personId: string, identityId: string) {
  if (!window.confirm('确定解绑此企业微信身份吗？')) return;
  try {
    await api.delete(`/admin/people/${personId}/wecom-identities/${identityId}`);
    ui.toast('已解绑企业微信身份', 'success');
    await load();
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '解绑失败', 'danger');
  }
}

async function bindIdentity(personId: string) {
  const userId = bindForms[personId]?.trim();
  if (!userId) return;
  try {
    await api.post(`/admin/people/${personId}/wecom-identities`, {
      user_id: userId
    });
    bindForms[personId] = '';
    ui.toast('绑定成功', 'success');
    await load();
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '绑定失败', 'danger');
  }
}

async function toggleDefault(person: Person) {
  try {
    await api.patch(`/admin/people/${person.id}`, {
      is_default: !person.is_default
    });
    ui.toast('默认接收人设置已更新', 'success');
    await load();
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '更新失败', 'danger');
  }
}

onMounted(load);
</script>

<template>
  <PageHeader title="接收人" description="内部 Person 与企业微信 UserID 分离管理；空接收人永远不会隐式广播。">
    <button class="btn btn--primary" @click="show=!show">
      添加接收人
    </button>
  </PageHeader>

  <section v-if="show" class="panel" style="margin-bottom:16px">
    <div class="panel-title">
      <h2>新接收人</h2>
    </div>
    <form class="grid" style="grid-template-columns:1fr 1fr auto;align-items:end" @submit.prevent="create">
      <div class="field">
        <label>显示名称</label>
        <input v-model="form.name" class="input" required placeholder="例如：夏振东">
      </div>
      <div class="field">
        <label>企业微信 UserID (选填)</label>
        <input v-model="form.user_id" class="input" placeholder="例如：XiaZhendong">
      </div>
      <div class="flex" style="gap: 12px; align-items: center; height: 38px;">
        <label><input v-model="form.is_default" type="checkbox"> 默认接收人</label>
        <button class="btn btn--primary" :disabled="busy">保存</button>
      </div>
    </form>
  </section>

  <div class="warning-box" style="margin-bottom:16px">
    广播到 @all 是独立高危权限，不会由“默认接收人”设置自动启用。
  </div>

  <EmptyState v-if="!items.length" />

  <section v-else class="grid entity-grid">
    <article v-for="person in items" :key="person.id" class="entity-card" style="display: flex; flex-direction: column; justify-content: space-between; position: relative;">
      <div>
        <header style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
          <div>
            <h3 style="margin: 0; font-size: 16px;">{{ person.name }}</h3>
            <span class="mono muted" style="font-size: 11px;">{{ person.id }}</span>
          </div>
          <div class="flex" style="gap: 8px; align-items: center;">
            <StatusBadge :status="person.enabled===false?'disabled':'active'" />
            <button class="btn btn--danger btn--sm" style="padding: 2px 6px; font-size: 11px;" @click="removePerson(person.id)">
              删除
            </button>
          </div>
        </header>
        
        <p style="margin: 8px 0; font-size: 13px; color: var(--color-text-muted);">
          <span style="cursor: pointer; text-decoration: underline;" @click="toggleDefault(person)">
            {{ person.is_default ? '⭐ 默认通知接收人' : '普通接收人 (点击设为默认)' }}
          </span>
        </p>

        <div class="entity-meta" style="margin-top: 12px; border-top: 1px solid var(--color-border); padding-top: 8px;">
          <div v-for="identity in person.wecom_identities??[]" :key="identity.id" class="flex" style="justify-content: space-between; align-items: center; margin-bottom: 4px; font-size: 13px;">
            <span class="mono" style="font-weight: 500;">WeCom: {{ identity.user_id }}</span>
            <div class="flex" style="gap: 6px; align-items: center;">
              <span class="muted" style="font-size: 11px;">{{ identity.verified?'已验证':'手工关联' }}</span>
              <button class="btn btn--sm" style="padding: 0 4px; color: var(--color-danger); background: transparent; border: none; font-size: 14px; cursor: pointer;" title="解绑身份" @click="removeIdentity(person.id, identity.id)">
                ×
              </button>
            </div>
          </div>
          
          <div v-if="!person.wecom_identities?.length" style="font-size: 13px; color: var(--color-text-muted); margin-bottom: 8px;">
            尚未关联企业微信身份
          </div>
        </div>
      </div>

      <!-- Quick inline bind form -->
      <div v-if="!person.wecom_identities?.length" style="margin-top: 12px;">
        <form class="flex" style="gap: 8px;" @submit.prevent="bindIdentity(person.id)">
          <input v-model="bindForms[person.id]" class="input input--sm" placeholder="绑定企业微信 UserID" required style="flex: 1; padding: 4px 8px; font-size: 12px; height: 28px;">
          <button class="btn btn--sm btn--primary" style="height: 28px; padding: 0 10px; font-size: 12px;">绑定</button>
        </form>
      </div>
    </article>
  </section>
</template>