<script setup lang="ts">import{onMounted,ref}from'vue';import{api}from'@/lib/api';import type{Plugin}from'@/types';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import EmptyState from'@/components/EmptyState.vue';import ConfirmDialog from'@/components/ConfirmDialog.vue';import{useUiStore}from'@/stores/ui';const ui=useUiStore(),items=ref<Plugin[]>([]),running=ref(new Set<string>()),target=ref<Plugin>(),busy=ref(false);async function load(){try{const data=await api.get<Plugin[]|{items:Plugin[]}>('/admin/plugins');items.value=Array.isArray(data)?data:data.items}catch(e){ui.toast(e instanceof Error?e.message:'插件加载失败','danger')}}async function run(item:Plugin){if(running.value.has(item.id))return;running.value.add(item.id);try{await api.post(`/admin/plugins/${item.id}/run`);ui.toast(`${item.name} 已进入运行队列`,'success')}catch(e){ui.toast(e instanceof Error?e.message:'运行失败','danger')}finally{window.setTimeout(()=>running.value.delete(item.id),1800)}}async function toggle(){if(!target.value)return;busy.value=true;const verb=target.value.enabled?'disable':'enable';try{await api.post(`/admin/plugins/${target.value.id}/${verb}`);ui.toast(`插件已${target.value.enabled?'停用':'启用'}`,'success');target.value=undefined;await load()}catch(e){ui.toast(e instanceof Error?e.message:'操作失败','danger')}finally{busy.value=false}}onMounted(load);const time=(v?:string)=>v?new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'</script>
<template>
  <PageHeader title="插件运行台" description="插件只发现事件；投递、去重与 Secret 始终由核心平台掌控。" /><EmptyState v-if="!items.length" /><section v-else class="grid entity-grid">
    <article v-for="item in items" :key="item.id" class="entity-card">
      <header><div><h3>{{ item.name }} <small class="muted">{{ item.version }}</small></h3><span class="mono muted">{{ item.id }}</span></div><StatusBadge :status="item.status" /></header><p>{{ item.description }}</p><div class="entity-meta">
        <div><span>调度</span><strong class="mono">{{ item.schedule??'手动' }}</strong></div><div><span>上次运行</span><strong>{{ time(item.last_run_at) }}</strong></div><div><span>下次运行</span><strong>{{ time(item.next_run_at) }}</strong></div><div><span>连续失败</span><strong :class="{'danger':item.consecutive_failures}">{{ item.consecutive_failures??0 }}</strong></div><div v-for="secretItem in item.secrets??[]" :key="secretItem.name">
          <span>{{ secretItem.name }}</span><strong>{{ secretItem.configured?'已配置':'未配置' }}</strong>
        </div>
      </div><footer>
        <button class="btn btn--primary btn--small" :disabled="running.has(item.id)" @click="run(item)">
          {{ running.has(item.id)?'已排队':'立即运行' }}
        </button><button class="btn btn--ghost btn--small" @click="target=item">
          {{ item.enabled?'停用':'启用' }}
        </button>
      </footer>
    </article>
  </section><ConfirmDialog :open="Boolean(target)" :title="`${target?.enabled?'停用':'启用'}插件？`" :description="target?.enabled?'将停止后续调度；当前已入队运行不会被强制中断。':'插件会按已保存配置恢复持久化调度。'" :busy="busy" @cancel="target=undefined" @confirm="toggle" />
</template>