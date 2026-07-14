<script setup lang="ts">import{onMounted,ref}from'vue';import{useRoute}from'vue-router';import{api}from'@/lib/api';import type{Notification,Delivery}from'@/types';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import ConfirmDialog from'@/components/ConfirmDialog.vue';import{useUiStore}from'@/stores/ui';const route=useRoute(),ui=useUiStore(),item=ref<Notification>(),target=ref<Delivery>(),busy=ref(false);async function load(){try{item.value=await api.get<Notification>(`/admin/notifications/${route.params.id}`);for(const d of item.value.deliveries??[])d.attempts=await api.get(`/admin/deliveries/${d.id}/attempts`)}catch(e){ui.toast(e instanceof Error?e.message:'详情加载失败','danger')}}async function retry(){if(!target.value)return;busy.value=true;try{await api.post(`/admin/deliveries/${target.value.id}/retry`);ui.toast('投递已重新排队','success');target.value=undefined;await load()}catch(e){ui.toast(e instanceof Error?e.message:'重试失败','danger')}finally{busy.value=false}}onMounted(load);const time=(v?:string)=>v?new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'medium'}).format(new Date(v)):'—'</script>
<template>
  <PageHeader title="通知详情" eyebrow="DELIVERY TRACE" description="供应商响应已归一化，敏感响应正文不会显示。">
    <RouterLink class="btn btn--ghost" to="/notifications">
      返回列表
    </RouterLink>
  </PageHeader><div v-if="!item" class="loading">
    LOADING TRACE…
  </div><template v-else>
    <section class="grid detail-grid">
      <article class="panel">
        <div class="panel-title">
          <h2>{{ item.title }}</h2><StatusBadge :status="item.status??'pending'" />
        </div><p>{{ item.content }}</p><dl class="definition-list">
          <dt>Notification ID</dt><dd class="mono">
            {{ item.id }}
          </dd><dt>消息类型</dt><dd>{{ item.message_type }}</dd><dt>优先级</dt><dd>{{ item.priority }}</dd><dt>创建时间</dt><dd>{{ time(item.created_at) }}</dd>
        </dl>
      </article><article class="panel">
        <div class="panel-title">
          <h2>来源事件</h2>
        </div><pre class="code-box">{{ JSON.stringify(item.event??{},null,2) }}</pre>
      </article>
    </section><section v-for="delivery in item.deliveries??[]" :key="delivery.id" class="panel" style="margin-top:16px">
      <div class="panel-title">
        <div><h2>{{ delivery.recipient_name??delivery.recipient_id??'未知接收人' }}</h2><span class="mono muted">{{ delivery.id }}</span></div><div style="display:flex;gap:10px;align-items:center">
          <StatusBadge :status="delivery.status" /><button v-if="delivery.status==='dead'" class="btn btn--danger btn--small" @click="target=delivery">
            手工重试
          </button>
        </div>
      </div><div v-if="delivery.last_error_message" class="warning-box">
        <strong>{{ delivery.last_error_code }}</strong> · {{ delivery.last_error_message }}
      </div><ul class="timeline" style="margin-top:20px">
        <li v-for="attempt in delivery.attempts??[]" :key="attempt.id">
          <strong>第 {{ attempt.attempt_no }} 次尝试 · {{ attempt.status }}</strong><span>{{ time(attempt.started_at) }} → {{ time(attempt.finished_at) }}</span><p v-if="attempt.error_message">
            {{ attempt.error_code }} · {{ attempt.error_message }}
          </p>
        </li>
      </ul>
    </section>
  </template><ConfirmDialog :open="Boolean(target)" title="重新投递这条消息？" description="该操作会把 dead 投递重置为 pending，并记录一条管理员审计日志。" confirm-text="确认重新排队" danger :busy="busy" @cancel="target=undefined" @confirm="retry" />
</template>