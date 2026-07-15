<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity,
  BellRing,
  Users,
  KeyRound,
  Blocks,
  AlarmClock,
  Settings,
  Menu,
  LogOut,
  X,
  Bot,
  Cpu,
  ShieldCheck
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

const title = computed(() => String(route.meta.title ?? '控制台'))

interface NavItem {
  href: string
  label: string
  icon: any
}

interface NavGroup {
  groupName: string
  items: NavItem[]
}

const groups: NavGroup[] = [
  {
    groupName: '工作台',
    items: [
      { href: '/', label: '运行概览', icon: Activity }
    ]
  },
  {
    groupName: '消息中心',
    items: [
      { href: '/notifications', label: '通知与投递', icon: BellRing },
      { href: '/reminders', label: '提醒与催办', icon: AlarmClock }
    ]
  },
  {
    groupName: '自动化',
    items: [
      { href: '/plugins', label: '插件运行台', icon: Blocks }
    ]
  },
  {
    groupName: 'AI Gateway',
    items: [
      { href: '/ai/providers', label: 'Providers', icon: Bot },
      { href: '/ai/profiles', label: 'Profiles', icon: Cpu }
    ]
  },
  {
    groupName: '访问管理',
    items: [
      { href: '/people', label: '接收人', icon: Users },
      { href: '/api-clients', label: 'API Clients', icon: KeyRound }
    ]
  },
  {
    groupName: '系统',
    items: [
      { href: '/settings', label: '系统设置', icon: Settings }
    ]
  }
]

const currentGroup = computed(() => {
  const path = route.path
  const matched = groups.find(g =>
    g.items.some(item => {
      if (item.href === '/') {
        return path === '/'
      }
      return path.startsWith(item.href)
    })
  )
  return matched ? matched.groupName : '控制台'
})

const isActive = (href: string) => {
  if (href === '/') {
    return route.path === '/'
  }
  return route.path.startsWith(href)
}

async function logout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="app-shell">
    <!-- Sidebar -->
    <aside class="sidebar-aside" :class="{ 'sidebar--open': ui.sidebarOpen }">
      <div class="brand-area">
        <div class="logo-wrapper">
          <img src="/brand/logo-horizontal-reverse.svg" alt="Notify Hub" class="brand-logo">
          <small class="brand-version">OPERATIONS / 0.6.0</small>
        </div>
        <button class="close-nav-btn mobile-only" aria-label="关闭导航" @click="ui.sidebarOpen = false">
          <X :size="20" />
        </button>
      </div>

      <div class="nav-groups-container">
        <div v-for="group in groups" :key="group.groupName" class="nav-group">
          <div class="nav-group-title">
            {{ group.groupName }}
          </div>
          <nav class="nav-list">
            <RouterLink
              v-for="item in group.items"
              :key="item.href"
              :to="item.href"
              class="nav-item"
              :class="{ 'nav-item--active': isActive(item.href) }"
              @click="ui.sidebarOpen = false"
            >
              <component :is="item.icon" :size="16" class="nav-icon" />
              <span class="nav-label">{{ item.label }}</span>
            </RouterLink>
          </nav>
        </div>
      </div>

      <div class="sidebar-footer">
        <div class="status-indicator">
          <span class="pulse-dot" />
          <span class="status-text">核心服务在线</span>
        </div>
      </div>
    </aside>

    <!-- Mobile Scrim -->
    <div v-if="ui.sidebarOpen" class="sidebar-scrim" @click="ui.sidebarOpen = false" />

    <!-- Main Workspace -->
    <main class="main-workspace">
      <header class="workspace-header">
        <div class="header-left">
          <button class="menu-trigger-btn mobile-only" aria-label="打开导航" @click="ui.sidebarOpen = true">
            <Menu :size="20" />
          </button>
          
          <div class="breadcrumbs">
            <span class="crumb-parent">{{ currentGroup }}</span>
            <span class="crumb-separator">/</span>
            <span class="crumb-active">{{ title }}</span>
          </div>
        </div>

        <div class="header-right">
          <div class="system-status-badge">
            <ShieldCheck :size="14" class="status-shield-icon" />
            <span>Secure Node</span>
          </div>
          <button class="admin-profile-pill" title="退出登录" @click="logout">
            <span class="avatar-stub">A</span>
            <span class="admin-label">管理员</span>
            <LogOut :size="14" class="logout-icon" />
          </button>
        </div>
      </header>

      <div class="workspace-content">
        <div class="content-width">
          <RouterView />
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  background-color: var(--surface-app);
}

/* Sidebar */
.sidebar-aside {
  position: fixed;
  inset: 0 auto 0 0;
  width: 240px;
  background-color: var(--surface-sidebar);
  color: var(--text-inverse);
  display: flex;
  flex-direction: column;
  z-index: var(--z-sidebar);
  border-right: 1px solid #2d322e;
}

.brand-area {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-6) var(--space-5);
  border-bottom: 1px solid #252926;
}

.logo-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.brand-logo {
  height: 28px;
  width: auto;
}

.brand-version {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #7d857f;
  letter-spacing: 0.05em;
}

.close-nav-btn {
  color: #7d857f;
  padding: var(--space-1);
  border-radius: var(--radius-sm);
  display: inline-flex;
}

.close-nav-btn:hover {
  color: #fff;
  background-color: #2c312d;
}

.nav-groups-container {
  flex: 1;
  overflow-y: overlay;
  padding: var(--space-5) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.nav-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.nav-group-title {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #555d57;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding-left: var(--space-3);
  font-weight: 600;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: #a0a7a1;
  padding: 8px var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  transition: all 120ms ease;
  border: 1px solid transparent;
}

.nav-item:hover {
  color: #ffffff;
  background-color: #222723;
}

.nav-item--active {
  color: #ffffff;
  background-color: var(--color-neutral-800);
  border-color: #383f3a;
  font-weight: 500;
}

.nav-icon {
  flex-shrink: 0;
}

.nav-label {
  flex: 1;
}

.nav-item--active::after {
  content: '';
  width: 5px;
  height: 5px;
  background-color: var(--action-primary);
  border-radius: 50%;
}

.sidebar-footer {
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid #252926;
  background-color: #171b18;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--status-success);
  box-shadow: 0 0 0 4px rgba(45, 117, 81, 0.2);
  animation: pulse-green 2s infinite;
}

.status-text {
  font-size: 11px;
  font-family: var(--font-sans);
  color: #7d857f;
}

/* Main Workspace */
.main-workspace {
  flex: 1;
  margin-left: 240px;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.workspace-header {
  height: 64px;
  padding: 0 var(--space-6);
  border-bottom: 1px solid var(--border-subtle);
  background-color: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: var(--z-header);
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.menu-trigger-btn {
  color: var(--text-secondary);
  padding: var(--space-1);
  border-radius: var(--radius-sm);
  display: inline-flex;
}

.menu-trigger-btn:hover {
  background-color: var(--surface-hover);
  color: var(--text-primary);
}

.breadcrumbs {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.crumb-parent {
  color: var(--text-secondary);
  font-weight: 500;
}

.crumb-separator {
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.crumb-active {
  color: var(--text-primary);
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.system-status-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 4px var(--space-2);
  background-color: #f0fdf4;
  color: var(--status-success);
  border: 1px solid #dcfce7;
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 500;
}

.status-shield-icon {
  color: var(--status-success);
}

.admin-profile-pill {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-pill);
  transition: all 120ms ease;
  cursor: pointer;
  border: 1px solid transparent;
}

.admin-profile-pill:hover {
  background-color: var(--surface-hover);
  border-color: var(--border-subtle);
}

.avatar-stub {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background-color: var(--color-neutral-800);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: bold;
}

.admin-label {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-weight: 500;
}

.logout-icon {
  color: var(--text-tertiary);
  transition: color 120ms ease;
}

.admin-profile-pill:hover .logout-icon {
  color: var(--status-danger);
}

.workspace-content {
  padding: var(--space-6);
  flex: 1;
}

@keyframes pulse-green {
  0% {
    box-shadow: 0 0 0 0px rgba(45, 117, 81, 0.4);
  }
  70% {
    box-shadow: 0 0 0 6px rgba(45, 117, 81, 0);
  }
  100% {
    box-shadow: 0 0 0 0px rgba(45, 117, 81, 0);
  }
}

@media (max-width: 760px) {
  .sidebar-aside {
    transform: translateX(-100%);
    transition: transform 180ms ease;
  }
  
  .sidebar--open {
    transform: translateX(0);
  }
  
  .sidebar-scrim {
    position: fixed;
    inset: 0;
    background-color: rgba(23, 27, 24, 0.53);
    z-index: 25;
  }
  
  .main-workspace {
    margin-left: 0;
  }
  
  .workspace-header {
    padding: 0 var(--space-4);
  }
  
  .workspace-content {
    padding: var(--space-4);
  }
  
  .system-status-badge {
    display: none;
  }
}
</style>
