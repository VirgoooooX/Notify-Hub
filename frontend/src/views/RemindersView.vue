<script setup lang="ts">import{onMounted,reactive,ref}from'vue';import{api,query}from'@/lib/api';import type{Page,Reminder}from'@/types';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import EmptyState from'@/components/EmptyState.vue';import PaginationBar from'@/components/PaginationBar.vue';import{useUiStore}from'@/stores/ui';const ui=useUiStore(),items=ref<Page<Reminder>>({items:[],page:1,page_size:20,total:0}),page=ref(1),status=ref(''),show=ref(false),busy=ref(false),form=reactive({title:'',content:'',schedule_type:'once',at:'',rrule:'',timezone:'Asia/Shanghai',recipients:'',require_ack:false,ack_policy:'any',repeat_interval_seconds:300,max_attempts:12,stop_at:''});async function load(){try{items.value=await api.get('/admin/reminders'+query({page:page.value,page_size:20,status:status.value}))}catch(e){ui.toast(e instanceof Error?e.message:'提醒加载失败','danger')}}async function create(){busy.value=true;try{await api.post('/admin/reminders',{title:form.title,content:form.content,schedule:form.schedule_type==='once'?{type:'once',at:form.at}:{type:'recurring',rrule:form.rrule,timezone:form.timezone},recipients:form.recipients.split(',').map(v=>v.trim()).filter(Boolean),require_ack:form.require_ack,ack_policy:form.ack_policy,repeat:form.require_ack?{interval_seconds:form.repeat_interval_seconds,max_attempts:form.max_attempts,stop_at:form.stop_at||undefined}:undefined});show.value=false;ui.toast('提醒已创建','success');await load()}catch(e){ui.toast(e instanceof Error?e.message:'创建失败','danger')}finally{busy.value=false}}onMounted(load);const time=(v?:string)=>v?new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'</script>
<template>
  <PageHeader title="提醒与催办" description="单次、周期与持续催办共享可恢复调度；确认后立即停止后续投递。">
    <button class="btn btn--primary" @click="show=!show">
      创建提醒
    </button>
  </PageHeader><section v-if="show" class="panel" style="margin-bottom:16px">
    <div class="panel-title">
      <h2>新提醒</h2><span class="mono muted">SAFE LIMITS ENFORCED</span>
    </div><form class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr))" @submit.prevent="create">
      <div class="field">
        <label>标题</label><input v-model="form.title" class="input" required>
      </div><div class="field">
        <label>接收人 ID（逗号分隔）</label><input v-model="form.recipients" class="input" required>
      </div><div class="field">
        <label>调度类型</label><select v-model="form.schedule_type" class="select">
          <option value="once">
            单次
          </option><option value="recurring">
            周期 RRULE
          </option>
        </select>
      </div><div v-if="form.schedule_type==='once'" class="field">
        <label>触发时间</label><input v-model="form.at" class="input" type="datetime-local" required>
      </div><template v-else>
        <div class="field">
          <label>RRULE</label><input v-model="form.rrule" class="input" placeholder="FREQ=WEEKLY;BYDAY=MO;BYHOUR=9" required>
        </div><div class="field">
          <label>时区</label><input v-model="form.timezone" class="input">
        </div>
      </template><div class="field" style="grid-column:1/-1">
        <label>内容</label><textarea v-model="form.content" class="textarea" />
      </div><label><input v-model="form.require_ack" type="checkbox"> 持续催办，直到确认</label><template v-if="form.require_ack">
        <div class="field">
          <label>确认策略</label><select v-model="form.ack_policy" class="select">
            <option value="any">
              任一确认
            </option><option value="all">
              全部确认
            </option><option value="each">
              逐人确认
            </option>
          </select>
        </div><div class="field">
          <label>催办间隔（秒，至少 300）</label><input v-model.number="form.repeat_interval_seconds" class="input" type="number" min="300">
        </div><div class="field">
          <label>最多次数</label><input v-model.number="form.max_attempts" class="input" type="number" min="1" max="100">
        </div><div class="field">
          <label>停止时间</label><input v-model="form.stop_at" class="input" type="datetime-local">
        </div>
      </template><button class="btn btn--primary" :disabled="busy">
        创建提醒
      </button>
    </form>
  </section><div class="filters">
    <select v-model="status" class="select" @change="page=1;load()">
      <option value="">
        全部状态
      </option><option value="active">
        运行中
      </option><option value="awaiting_ack">
        待确认
      </option><option value="paused">
        已暂停
      </option><option value="completed">
        已完成
      </option><option value="cancelled">
        已取消
      </option>
    </select>
  </div><EmptyState v-if="!items.items.length" /><div v-else class="table-wrap">
    <table>
      <thead><tr><th>提醒</th><th>调度</th><th>催办进度</th><th>下次触发</th><th>状态</th></tr></thead><tbody>
        <tr v-for="item in items.items" :key="item.id">
          <td>
            <RouterLink class="link" :to="`/reminders/${item.id}`">
              {{ item.title }}
            </RouterLink><br><span class="mono muted">{{ item.id }}</span>
          </td><td>{{ item.schedule_type }}{{ item.timezone?` · ${item.timezone}`:'' }}</td><td>{{ item.require_ack?`${item.attempt_count??0} / ${item.max_attempts??12}`:'无需确认' }}</td><td>{{ time(item.next_run_at) }}</td><td><StatusBadge :status="item.status" /></td>
        </tr>
      </tbody>
    </table>
  </div><PaginationBar :page="page" :page-size="20" :total="items.total" @change="page=$event;load()" />
</template>
