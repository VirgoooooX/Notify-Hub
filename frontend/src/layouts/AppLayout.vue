<script setup lang="ts">
import{computed}from'vue';import{useRoute,useRouter}from'vue-router';import{Activity,BellRing,Users,KeyRound,Blocks,AlarmClock,Settings,Menu,LogOut,X}from'lucide-vue-next';import{useAuthStore}from'@/stores/auth';import{useUiStore}from'@/stores/ui'
const route=useRoute(),router=useRouter(),auth=useAuthStore(),ui=useUiStore();const title=computed(()=>String(route.meta.title??'控制台'));const nav=[['/','概览',Activity],['/notifications','通知',BellRing],['/reminders','提醒',AlarmClock],['/plugins','插件',Blocks],['/people','接收人',Users],['/api-clients','API Clients',KeyRound],['/settings','设置',Settings]];async function logout(){await auth.logout();router.push('/login')}
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{'sidebar--open':ui.sidebarOpen}">
      <div class="brand">
        <img src="/brand/logo-horizontal-reverse.svg" alt="Notify Hub" class="brand-logo">
        <small class="brand-version">OPERATIONS / 0.3.0</small>
        <button class="icon-btn mobile-only" aria-label="关闭导航" @click="ui.sidebarOpen=false">
          <X />
        </button>
      </div><nav>
        <RouterLink v-for="[href,label,icon] in nav" :key="String(href)" :to="String(href)" @click="ui.sidebarOpen=false">
          <component :is="icon" :size="18" /><span>{{ label }}</span>
        </RouterLink>
      </nav><div class="sidebar-foot">
        <span class="pulse-dot" /> 核心服务在线
      </div>
    </aside><div v-if="ui.sidebarOpen" class="sidebar-scrim" @click="ui.sidebarOpen=false" /><main class="main">
      <header class="topbar">
        <button class="icon-btn mobile-only" aria-label="打开导航" @click="ui.sidebarOpen=true">
          <Menu />
        </button><div><span class="topbar-label">当前工作区</span><strong>{{ title }}</strong></div><button class="user-pill" @click="logout">
          <span>管理员</span><LogOut :size="16" />
        </button>
      </header><div class="content">
        <RouterView />
      </div>
    </main>
  </div>
</template>
