<script setup lang="ts">import{onMounted,reactive,ref}from'vue';import{api}from'@/lib/api';import type{ApiClient}from'@/types';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import EmptyState from'@/components/EmptyState.vue';import ConfirmDialog from'@/components/ConfirmDialog.vue';import{useUiStore}from'@/stores/ui';const navigator=window.navigator,ui=useUiStore(),items=ref<ApiClient[]>([]),show=ref(false),secret=ref(''),target=ref<ApiClient>(),action=ref<'rotate'|'revoke'>('revoke'),busy=ref(false),form=reactive({name:'',allowed_event_types:'',rate_limit_per_minute:60,allow_broadcast:false});async function load(){try{const data=await api.get<ApiClient[]|{items:ApiClient[]}>('/admin/api-clients');items.value=Array.isArray(data)?data:data.items}catch(e){ui.toast(e instanceof Error?e.message:'Client 加载失败','danger')}}async function create(){busy.value=true;try{const data=await api.post<ApiClient&{api_key:string}>('/admin/api-clients',{name:form.name,allowed_event_types:form.allowed_event_types.split(',').map(v=>v.trim()).filter(Boolean),rate_limit_per_minute:form.rate_limit_per_minute,allow_broadcast:form.allow_broadcast});secret.value=data.api_key;show.value=false;ui.toast('Client 已创建，请立即保存 Key','success');await load()}catch(e){ui.toast(e instanceof Error?e.message:'创建失败','danger')}finally{busy.value=false}}function ask(item:ApiClient,next:'rotate'|'revoke'){target.value=item;action.value=next}async function confirm(){if(!target.value)return;busy.value=true;try{const data=await api.post<{api_key?:string}>(`/admin/api-clients/${target.value.id}/${action.value==='rotate'?'rotate-key':'revoke'}`);if(data?.api_key)secret.value=data.api_key;ui.toast(action.value==='rotate'?'Key 已轮换':'Client 已吊销','success');target.value=undefined;await load()}catch(e){ui.toast(e instanceof Error?e.message:'操作失败','danger')}finally{busy.value=false}}onMounted(load)</script>
<template>
  <PageHeader title="API Clients" description="每个外部来源独立授权、限流与轮换；Key 只在创建或轮换后显示一次。">
    <button class="btn btn--primary" @click="show=!show">
      创建 Client
    </button>
  </PageHeader><div v-if="secret" class="secret-once" style="margin-bottom:16px">
    <strong>仅显示一次：请立即复制并安全保存</strong><code>{{ secret }}</code><button class="btn btn--ghost btn--small" @click="navigator.clipboard.writeText(secret);ui.toast('已复制','success')">
      复制 Key
    </button><button class="btn btn--ghost btn--small" @click="secret=''">
      我已保存
    </button>
  </div><section v-if="show" class="panel" style="margin-bottom:16px">
    <form class="grid" style="grid-template-columns:1fr 1.5fr 130px;align-items:end" @submit.prevent="create">
      <div class="field">
        <label>名称</label><input v-model="form.name" class="input" required>
      </div><div class="field">
        <label>允许事件类型（逗号分隔）</label><input v-model="form.allowed_event_types" class="input" placeholder="home.alert, nas.health">
      </div><div class="field">
        <label>每分钟限额</label><input v-model.number="form.rate_limit_per_minute" class="input" type="number" min="1" max="10000">
      </div><label><input v-model="form.allow_broadcast" type="checkbox"> 允许广播（高危）</label><button class="btn btn--primary" :disabled="busy">
        创建
      </button>
    </form>
  </section><EmptyState v-if="!items.length" /><div v-else class="table-wrap">
    <table>
      <thead><tr><th>名称</th><th>Key 前缀</th><th>事件权限</th><th>限流</th><th>状态</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in items" :key="item.id">
          <td><strong>{{ item.name }}</strong><br><span class="mono muted">{{ item.id }}</span></td><td class="mono">
            {{ item.key_prefix }}••••
          </td><td>{{ item.allowed_event_types?.join(', ')||'未限定' }}<span v-if="item.allow_broadcast" class="danger"> · 可广播</span></td><td>{{ item.rate_limit_per_minute??'—' }} / min</td><td><StatusBadge :status="item.status" /></td><td>
            <button class="btn btn--ghost btn--small" @click="ask(item,'rotate')">
              轮换
            </button> <button class="btn btn--ghost btn--small" @click="ask(item,'revoke')">
              吊销
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div><ConfirmDialog :open="Boolean(target)" :title="action==='rotate'?'轮换 API Key？':'吊销 Client？'" :description="action==='rotate'?'旧 Key 将立即失效，新 Key 只显示一次。':'该 Client 将无法继续提交事件，此操作会进入审计日志。'" :confirm-text="action==='rotate'?'确认轮换':'确认吊销'" danger :busy="busy" @cancel="target=undefined" @confirm="confirm" />
</template>