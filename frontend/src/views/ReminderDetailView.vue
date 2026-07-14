<script setup lang="ts">import{onMounted,ref}from'vue';import{useRoute}from'vue-router';import{api}from'@/lib/api';import type{Reminder}from'@/types';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import ConfirmDialog from'@/components/ConfirmDialog.vue';import{useUiStore}from'@/stores/ui';const route=useRoute(),ui=useUiStore(),item=ref<Reminder>(),action=ref(''),busy=ref(false);async function load(){try{item.value=await api.get(`/admin/reminders/${route.params.id}`)}catch(e){ui.toast(e instanceof Error?e.message:'提醒加载失败','danger')}}async function execute(){if(!action.value)return;busy.value=true;try{await api.post(`/admin/reminders/${route.params.id}/${action.value}`);ui.toast('提醒状态已更新','success');action.value='';await load()}catch(e){ui.toast(e instanceof Error?e.message:'操作失败','danger')}finally{busy.value=false}}onMounted(load);const time=(v?:string)=>v?new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'medium'}).format(new Date(v)):'—';const labels:Record<string,string>={pause:'暂停',resume:'恢复',complete:'强制完成',cancel:'取消'}</script>
<template>
  <PageHeader title="催办时间线" eyebrow="REMINDER / AUDIT TRAIL">
    <RouterLink class="btn btn--ghost" to="/reminders">
      返回提醒
    </RouterLink>
  </PageHeader><div v-if="!item" class="loading">
    LOADING REMINDER…
  </div><section v-else class="grid detail-grid">
    <article class="panel">
      <div class="panel-title">
        <div><h2>{{ item.title }}</h2><span class="mono muted">{{ item.id }}</span></div><StatusBadge :status="item.status" />
      </div><p>{{ item.content }}</p><dl class="definition-list">
        <dt>调度类型</dt><dd>{{ item.schedule_type }}</dd><dt>时区</dt><dd>{{ item.timezone??'—' }}</dd><dt>下次触发</dt><dd>{{ time(item.next_run_at) }}</dd><dt>确认策略</dt><dd>{{ item.require_ack?item.ack_policy:'无需确认' }}</dd><dt>催办进度</dt><dd>{{ item.attempt_count??0 }} / {{ item.max_attempts??'—' }}</dd><dt>停止时间</dt><dd>{{ time(item.stop_at) }}</dd>
      </dl><footer style="display:flex;gap:8px;margin-top:18px">
        <button v-if="!['paused','completed','cancelled'].includes(item.status)" class="btn btn--ghost" @click="action='pause'">
          暂停
        </button><button v-if="item.status==='paused'" class="btn btn--primary" @click="action='resume'">
          恢复
        </button><button v-if="!['completed','cancelled'].includes(item.status)" class="btn btn--ghost" @click="action='complete'">
          强制完成
        </button><button v-if="!['completed','cancelled'].includes(item.status)" class="btn btn--danger" @click="action='cancel'">
          取消
        </button>
      </footer>
    </article><article class="panel">
      <div class="panel-title">
        <h2>收件人确认</h2>
      </div><div class="entity-meta">
        <div v-for="person in item.recipients??[]" :key="person.id">
          <span>{{ person.name??person.id }}</span><strong>{{ person.acknowledged_at?time(person.acknowledged_at):'等待确认' }}</strong>
        </div>
      </div>
    </article><article class="panel" style="grid-column:1/-1">
      <div class="panel-title">
        <h2>完整时间线</h2><span class="mono muted">IDEMPOTENT CALLBACKS</span>
      </div><ul class="timeline">
        <li v-for="entry in item.timeline??[]" :key="entry.id">
          <strong>{{ entry.type }}</strong><span>{{ time(entry.occurred_at) }}</span><p>{{ entry.message }}</p>
        </li>
      </ul>
    </article>
  </section><ConfirmDialog :open="Boolean(action)" :title="`${labels[action]??action}此提醒？`" :description="action==='cancel'?'尚未发送的投递将被取消，操作会写入审计日志。':'状态转换由服务端原子执行，重复操作不会产生额外投递。'" :danger="['cancel','complete'].includes(action)" :busy="busy" @cancel="action=''" @confirm="execute" />
</template>