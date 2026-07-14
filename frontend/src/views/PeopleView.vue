<script setup lang="ts">import{onMounted,reactive,ref}from'vue';import{api}from'@/lib/api';import type{Person}from'@/types';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import EmptyState from'@/components/EmptyState.vue';import{useUiStore}from'@/stores/ui';const ui=useUiStore(),items=ref<Person[]>([]),show=ref(false),busy=ref(false),form=reactive({name:'',user_id:'',is_default:false});async function load(){try{const data=await api.get<Person[]|{items:Person[]}>('/admin/people');items.value=Array.isArray(data)?data:data.items}catch(e){ui.toast(e instanceof Error?e.message:'接收人加载失败','danger')}}async function create(){busy.value=true;try{const person=await api.post<Person>('/admin/people',{name:form.name,is_default:form.is_default});await api.post(`/admin/people/${person.id}/wecom-identities`,{user_id:form.user_id});show.value=false;Object.assign(form,{name:'',user_id:'',is_default:false});ui.toast('接收人已创建','success');await load()}catch(e){ui.toast(e instanceof Error?e.message:'创建失败','danger')}finally{busy.value=false}}onMounted(load)</script>
<template>
  <PageHeader title="接收人" description="内部 Person 与企业微信 UserID 分离管理；空接收人永远不会隐式广播。">
    <button class="btn btn--primary" @click="show=!show">
      添加接收人
    </button>
  </PageHeader><section v-if="show" class="panel" style="margin-bottom:16px">
    <div class="panel-title">
      <h2>新接收人</h2>
    </div><form class="grid" style="grid-template-columns:1fr 1fr auto;align-items:end" @submit.prevent="create">
      <div class="field">
        <label>显示名称</label><input v-model="form.name" class="input" required>
      </div><div class="field">
        <label>企业微信 UserID</label><input v-model="form.user_id" class="input" required>
      </div><label><input v-model="form.is_default" type="checkbox"> 默认接收人</label><button class="btn btn--primary" :disabled="busy">
        保存
      </button>
    </form>
  </section><div class="warning-box" style="margin-bottom:16px">
    广播到 @all 是独立高危权限，不会由“默认接收人”设置自动启用。
  </div><EmptyState v-if="!items.length" /><section v-else class="grid entity-grid">
    <article v-for="person in items" :key="person.id" class="entity-card">
      <header><div><h3>{{ person.name }}</h3><span class="mono muted">{{ person.id }}</span></div><StatusBadge :status="person.enabled===false?'disabled':'active'" /></header><p>{{ person.is_default?'默认通知接收人':'普通接收人' }}</p><div class="entity-meta">
        <div v-for="identity in person.wecom_identities??[]" :key="identity.id">
          <span class="mono">{{ identity.user_id }}</span><span>{{ identity.verified?'已验证':'手工关联' }}</span>
        </div><div v-if="!person.wecom_identities?.length">
          <span>尚未关联企业微信身份</span>
        </div>
      </div>
    </article>
  </section>
</template>