<script setup lang="ts">import{onMounted,ref}from'vue';import{api}from'@/lib/api';import type{Dashboard}from'@/types';import PageHeader from'@/components/PageHeader.vue';import EmptyState from'@/components/EmptyState.vue';import{useUiStore}from'@/stores/ui';const ui=useUiStore(),loading=ref(true),data=ref<Dashboard>({today_events:0,succeeded_deliveries:0,failed_deliveries:0,retry_wait:0,failed_plugins:0,recent_errors:[]});onMounted(async()=>{try{data.value=await api.get<Dashboard>('/admin/dashboard')}catch(e){ui.toast(e instanceof Error?e.message:'概览加载失败','danger')}finally{loading.value=false}});const stats=[['today_events','今日事件','较昨日实时累计'],['succeeded_deliveries','成功投递','今日已完成'],['failed_deliveries','失败投递','需要人工关注'],['retry_wait','等待重试','由 Worker 自动恢复'],['failed_plugins','异常插件','连续失败或降级']] as const;const time=(v:string)=>new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v))</script>
<template>
  <PageHeader title="运行概览" description="从事件接收到渠道投递，查看今天的系统脉搏。">
    <RouterLink class="btn btn--primary" to="/notifications">
      检查投递链路
    </RouterLink>
  </PageHeader><div v-if="loading" class="loading">
    LOADING SIGNALS…
  </div><template v-else>
    <section class="grid stats-grid">
      <article v-for="[key,label,hint] in stats" :key="key" class="stat-card">
        <span>{{ label }}</span><strong>{{ data[key] }}</strong><small>{{ hint }}</small>
      </article>
    </section><section class="grid split">
      <article class="panel">
        <div class="panel-title">
          <h2>投递态势</h2><span class="mono muted">TODAY / LIVE</span>
        </div><div class="code-box">
          EVENT ACCEPTED ━━━ NOTIFICATION ROUTED ━━━ DELIVERY CLAIMED ━━━ PROVIDER ACK<br><span style="color:#70bd8e">● 核心队列工作中</span>　<span style="color:#d69b4b">● {{ data.retry_wait }} 条等待退避</span>　<span style="color:#dd6b5c">● {{ data.failed_deliveries }} 条需要处理</span>
        </div>
      </article><article class="panel">
        <div class="panel-title">
          <h2>最近系统错误</h2><RouterLink class="link" to="/plugins">
            查看插件
          </RouterLink>
        </div><EmptyState v-if="!data.recent_errors.length" title="没有新错误" description="最近运行状态平稳。" /><ul v-else class="timeline">
          <li v-for="error in data.recent_errors" :key="error.id">
            <strong>{{ error.type??'系统错误' }}</strong><span>{{ time(error.occurred_at) }}</span><p>{{ error.message }}</p>
          </li>
        </ul>
      </article>
    </section>
  </template>
</template>