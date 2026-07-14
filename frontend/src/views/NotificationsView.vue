<script setup lang="ts">import{onMounted,ref,watch}from'vue';import{api,query}from'@/lib/api';import type{Notification,Page}from'@/types';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import EmptyState from'@/components/EmptyState.vue';import PaginationBar from'@/components/PaginationBar.vue';import{useUiStore}from'@/stores/ui';const ui=useUiStore(),page=ref(1),status=ref(''),keyword=ref(''),loading=ref(false),result=ref<Page<Notification>>({items:[],page:1,page_size:20,total:0});async function load(){loading.value=true;try{result.value=await api.get<Page<Notification>>('/admin/notifications'+query({page:page.value,page_size:20,status:status.value,keyword:keyword.value}))}catch(e){ui.toast(e instanceof Error?e.message:'通知加载失败','danger')}finally{loading.value=false}}let timer=0;watch([status,keyword],()=>{page.value=1;clearTimeout(timer);timer=window.setTimeout(load,250)});onMounted(load);const time=(v:string)=>new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'short'}).format(new Date(v))</script>
<template>
  <PageHeader title="通知与投递" description="查看 Event → Notification → Delivery 的完整链路，并处理进入 dead 的任务。" /><section class="panel">
    <div class="filters">
      <input v-model="keyword" class="input" placeholder="搜索标题、正文或事件 ID"><select v-model="status" class="select">
        <option value="">
          全部状态
        </option><option value="succeeded">
          成功
        </option><option value="retry_wait">
          等待重试
        </option><option value="dead">
          已终止
        </option><option value="processing">
          处理中
        </option>
      </select>
    </div><div v-if="loading" class="loading">
      LOADING DELIVERIES…
    </div><EmptyState v-else-if="!result.items.length" /><div v-else class="table-wrap">
      <table>
        <thead><tr><th>通知</th><th>消息类型</th><th>优先级</th><th>状态</th><th>创建时间</th></tr></thead><tbody>
          <tr v-for="item in result.items" :key="item.id">
            <td>
              <RouterLink class="link" :to="`/notifications/${item.id}`">
                {{ item.title }}
              </RouterLink><br><span class="mono muted">{{ item.id }}</span>
            </td><td>{{ item.message_type }}</td><td>{{ item.priority }}</td><td><StatusBadge :status="item.status??'pending'" /></td><td>{{ time(item.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div><PaginationBar :page="page" :page-size="20" :total="result.total" @change="page=$event;load()" />
  </section>
</template>