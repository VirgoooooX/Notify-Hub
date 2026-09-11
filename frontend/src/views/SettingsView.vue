<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  SlidersHorizontal,
  MessageSquare,
  Share2,
  Server,
  ExternalLink,
  ShieldCheck,
  Sparkles,
  Clock,
  Layers,
  Globe,
  Radio,
  ArrowRight
} from 'lucide-vue-next'
import { api } from '@/lib/api'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppSwitch from '@/components/ui/AppSwitch.vue'
import DescriptionList from '@/components/data/DescriptionList.vue'
import { useUiStore } from '@/stores/ui'
import type { AIProfile, WechatMpSettings, BrowserPublisherSettings } from '@/types'
import { APP_VERSION } from '@/lib/version'
import { DEFAULT_TIMEZONE } from '@/lib/time'
import { useSettingsStore } from '@/stores/settings'

type TabKey = 'general' | 'wecom' | 'publishing' | 'system'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const platform = useSettingsStore()

const activeTab = ref<TabKey>('general')
const busy = ref(false)
const testing = ref(false)
const publishingMenu = ref(false)
const parserProfiles = ref<AIProfile[]>([])

const timezonePresets = [
  { label: '中国标准时间 (Asia/Shanghai)', value: 'Asia/Shanghai' },
  { label: '世界标准时间 (UTC)', value: 'UTC' },
  { label: '美东时间 (America/New_York)', value: 'America/New_York' }
]

const settings = reactive({
  timezone: DEFAULT_TIMEZONE,
  retention_days: 90,
  default_reminder_parser_profile_id: '',
  xiaohongshu_publishing_enabled: false,
  version: APP_VERSION,
  wecom: {
    configured: false,
    corp_id_configured: false,
    agent_id_configured: false,
    secret_configured: false,
    callback_token_configured: false,
    aes_key_configured: false,
    api_base_url: 'https://qyapi.weixin.qq.com',
    using_proxy: false
  },
  wechat_mp: {
    publish_mode: 'browser' as WechatMpSettings['publish_mode'],
    effective_mode: 'browser' as WechatMpSettings['effective_mode'],
    legacy_api_credentials_configured: false,
    api_credentials_managed_by: 'browser_publisher' as WechatMpSettings['api_credentials_managed_by'],
    author: 'Notify Hub'
  },
  browser_publisher: {
    api_url: 'http://192.168.31.100:8790',
    access_token_configured: false,
    configured: false
  } as BrowserPublisherSettings
})

const test = reactive({
  recipient_id: '',
  message_type: 'text'
})

function switchTab(tab: TabKey) {
  activeTab.value = tab
  if (router && route) {
    router.replace({ query: { ...(route.query ?? {}), tab } }).catch(() => {})
  }
}

function setTimezone(tz: string) {
  settings.timezone = tz
}

onMounted(async () => {
  const tabQuery = route?.query?.tab as string | undefined
  if (tabQuery && ['general', 'wecom', 'publishing', 'system'].includes(tabQuery)) {
    activeTab.value = tabQuery as TabKey
  }

  try {
    const [platformSettings, profiles] = await Promise.all([
      api.get<Record<string, unknown>>('/admin/settings'),
      api.get<AIProfile[]>('/admin/ai/profiles')
    ])
    Object.assign(settings, platformSettings)
    platform.setTimezone(
      typeof settings.timezone === 'string' ? settings.timezone : DEFAULT_TIMEZONE,
    )
    settings.timezone = platform.timezone
    settings.default_reminder_parser_profile_id =
      typeof platformSettings.default_reminder_parser_profile_id === 'string'
        ? platformSettings.default_reminder_parser_profile_id
        : ''
    parserProfiles.value = profiles.filter(
      (profile) => profile.capability === 'extract' && profile.enabled
    )
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '设置加载失败', 'danger')
  }
})

async function save() {
  busy.value = true
  try {
    await api.patch('/admin/settings', {
      timezone: settings.timezone,
      retention_days: settings.retention_days,
      default_reminder_parser_profile_id:
        settings.default_reminder_parser_profile_id || null,
      xiaohongshu_publishing_enabled: settings.xiaohongshu_publishing_enabled
    })
    platform.setTimezone(settings.timezone)
    ui.toast('设置已保存并生效', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '保存失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function sendTest() {
  testing.value = true
  try {
    await api.post('/admin/channels/wecom/test', test)
    ui.toast('测试消息已进入正常投递队列', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '测试失败', 'danger')
  } finally {
    testing.value = false
  }
}

async function publishReminderMenu() {
  publishingMenu.value = true
  try {
    await api.post('/admin/wecom/menu/publish')
    ui.toast('三系列提醒菜单已发布到企业微信应用', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '菜单发布失败', 'danger')
  } finally {
    publishingMenu.value = false
  }
}
</script>

<template>
  <PageHeader
    title="系统设置"
    eyebrow="NOTIFY HUB / SETTINGS"
    description="管理全局时区、生命周期策略、企业微信通知与外部内容发布节点调度。"
  >
    <span class="mono muted version-badge">VERSION {{ settings.version }}</span>
  </PageHeader>

  <!-- Navigation Tabs Bar -->
  <nav class="settings-tabs-nav" aria-label="设置分区导航" role="tablist">
    <button
      id="tab-general-btn"
      type="button"
      role="tab"
      class="tab-item"
      :class="{ active: activeTab === 'general' }"
      :aria-selected="activeTab === 'general'"
      @click="switchTab('general')"
    >
      <SlidersHorizontal class="tab-icon" :size="16" />
      <span class="tab-label">平台常规</span>
    </button>

    <button
      id="tab-wecom-btn"
      type="button"
      role="tab"
      class="tab-item"
      :class="{ active: activeTab === 'wecom' }"
      :aria-selected="activeTab === 'wecom'"
      @click="switchTab('wecom')"
    >
      <MessageSquare class="tab-icon" :size="16" />
      <span class="tab-label">企业微信渠道</span>
      <span
        class="status-indicator-dot"
        :class="settings.wecom.configured ? 'dot-success' : 'dot-warning'"
        :title="settings.wecom.configured ? '已就绪' : '凭据未全'"
      />
    </button>

    <button
      id="tab-publishing-btn"
      type="button"
      role="tab"
      class="tab-item"
      :class="{ active: activeTab === 'publishing' }"
      :aria-selected="activeTab === 'publishing'"
      @click="switchTab('publishing')"
    >
      <Share2 class="tab-icon" :size="16" />
      <span class="tab-label">内容发布与节点</span>
      <span
        class="status-indicator-dot"
        :class="settings.browser_publisher.configured ? 'dot-success' : 'dot-warning'"
        :title="settings.browser_publisher.configured ? '发布器已连接' : '发布器未就绪'"
      />
    </button>

    <button
      id="tab-system-btn"
      type="button"
      role="tab"
      class="tab-item"
      :class="{ active: activeTab === 'system' }"
      :aria-selected="activeTab === 'system'"
      @click="switchTab('system')"
    >
      <Server class="tab-icon" :size="16" />
      <span class="tab-label">系统与运维</span>
    </button>
  </nav>

  <!-- TAB 1: 平台常规 (General) -->
  <section v-show="activeTab === 'general'" class="tab-panel" aria-labelledby="tab-general-btn">
    <form class="panel-form" @submit.prevent="save">
      <div class="cards-grid">
        <!-- 基础运行配置 -->
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <Clock class="header-icon" :size="18" />
                <h3 class="panel-title">
                  基础运行参数
                </h3>
              </div>
              <span class="panel-badge-tag">RUNTIME</span>
            </div>
          </template>

          <div class="field">
            <label for="settings-timezone-input">默认时区 (Timezone)</label>
            <AppInput id="settings-timezone-input" v-model="settings.timezone" placeholder="Asia/Shanghai" />
            <div class="preset-chips mt-2">
              <span class="preset-title">常用预设：</span>
              <button
                v-for="tz in timezonePresets"
                :key="tz.value"
                type="button"
                class="chip-btn"
                :class="{ active: settings.timezone === tz.value }"
                @click="setTimezone(tz.value)"
              >
                {{ tz.value }}
              </button>
            </div>
            <span class="field-help mt-1">影响定时提醒计算、报表汇总以及系统日志展示的时间基准。</span>
          </div>

          <div class="field mt-5">
            <label for="settings-retention-input">临时媒体保留天数 (Retention Days)</label>
            <AppInput
              id="settings-retention-input"
              v-model.number="settings.retention_days"
              type="number"
              min="7"
              max="3650"
            />
            <span class="field-help">仅自动清理外部抓取、插件生成及一次性测试的临时媒体；用户主动上传并关联提醒的图片不受影响，长期保留。</span>
          </div>
        </AppCard>

        <!-- 智能能力扩展 -->
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <Sparkles class="header-icon" :size="18" />
                <h3 class="panel-title">
                  智能与语义提取
                </h3>
              </div>
              <span class="panel-badge-tag">AI FALLBACK</span>
            </div>
          </template>

          <div class="field">
            <label for="settings-parser-profile-select">提醒自然语言解析 Profile</label>
            <AppSelect id="settings-parser-profile-select" v-model="settings.default_reminder_parser_profile_id">
              <option value="">
                禁用 AI fallback (仅支持确定性结构语法)
              </option>
              <option v-for="profile in parserProfiles" :key="profile.id" :value="profile.id">
                {{ profile.name }} · {{ profile.model }}
              </option>
            </AppSelect>
            <span class="field-help mt-2">
              当快捷添加提醒输入的不是标准固定格式时，系统调用该 Extract 策略智能提取时间实体与核心任务内容。可在 AI Gateway 中配置更多 Profile。
            </span>
          </div>

          <div class="quick-link-box mt-5">
            <div class="quick-link-copy">
              <strong>需要调整 AI Provider 或模型参数？</strong>
              <span>前往 AI Gateway 配置接入点、凭据与模型温度等策略。</span>
            </div>
            <RouterLink to="/ai/profiles" class="quick-link-action">
              打开 Profiles
              <ArrowRight :size="14" />
            </RouterLink>
          </div>
        </AppCard>
      </div>

      <!-- 保存操作栏 -->
      <div class="save-bar mt-5">
        <div class="save-bar-info">
          <span>保存后平台时区与媒体生命周期配置将即时生效并同步到后台服务。</span>
        </div>
        <AppButton id="btn-save-general-settings" type="submit" variant="primary" :loading="busy">
          保存常规设置
        </AppButton>
      </div>
    </form>
  </section>

  <!-- TAB 2: 企业微信渠道 (WeCom) -->
  <section v-show="activeTab === 'wecom'" class="tab-panel" aria-labelledby="tab-wecom-btn">
    <AppAlert variant="info" class="info-alert mb-4">
      企业微信是 Notify Hub 核心通知与即时待办通道。为保证线上运行环境的一致性与安全性，凭据由只读 Secret 文件或环境变量注入，管理界面仅做就绪审计，避免配置分叉。
    </AppAlert>

    <div class="tab-grid">
      <!-- 左列：凭据就绪清单与网络设置 -->
      <div class="column-stack">
        <!-- 凭据状态卡片 -->
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <ShieldCheck class="header-icon" :size="18" />
                <h3 class="panel-title">
                  凭据就绪检查清单
                </h3>
              </div>
              <StatusBadge :status="settings.wecom.configured ? 'active' : 'disabled'" />
            </div>
          </template>

          <div class="checklist-section">
            <div class="checklist-title">
              Readiness Checklist (Read-Only Audit)
            </div>
            <ul class="readiness-list">
              <li class="checklist-item">
                <span class="check-icon" :class="{ success: settings.wecom.corp_id_configured }">
                  {{ settings.wecom.corp_id_configured ? '✓' : '✗' }}
                </span>
                <div class="item-meta">
                  <span class="item-label">企业 ID (Corp ID)</span>
                  <span class="item-sub">NOTIFY_HUB_WECOM_CORP_ID</span>
                </div>
                <span class="item-status" :class="{ 'status-ok': settings.wecom.corp_id_configured }">
                  {{ settings.wecom.corp_id_configured ? '已配置' : '未配置' }}
                </span>
              </li>

              <li class="checklist-item">
                <span class="check-icon" :class="{ success: settings.wecom.agent_id_configured }">
                  {{ settings.wecom.agent_id_configured ? '✓' : '✗' }}
                </span>
                <div class="item-meta">
                  <span class="item-label">应用 Agent ID</span>
                  <span class="item-sub">NOTIFY_HUB_WECOM_AGENT_ID</span>
                </div>
                <span class="item-status" :class="{ 'status-ok': settings.wecom.agent_id_configured }">
                  {{ settings.wecom.agent_id_configured ? '已配置' : '未配置' }}
                </span>
              </li>

              <li class="checklist-item">
                <span class="check-icon" :class="{ success: settings.wecom.secret_configured }">
                  {{ settings.wecom.secret_configured ? '✓' : '✗' }}
                </span>
                <div class="item-meta">
                  <span class="item-label">应用 Secret</span>
                  <span class="item-sub">NOTIFY_HUB_WECOM_SECRET</span>
                </div>
                <span class="item-status" :class="{ 'status-ok': settings.wecom.secret_configured }">
                  {{ settings.wecom.secret_configured ? '已配置' : '未配置' }}
                </span>
              </li>

              <li class="checklist-item">
                <span class="check-icon" :class="{ success: settings.wecom.callback_token_configured }">
                  {{ settings.wecom.callback_token_configured ? '✓' : '✗' }}
                </span>
                <div class="item-meta">
                  <span class="item-label">回调 Token</span>
                  <span class="item-sub">NOTIFY_HUB_WECOM_CALLBACK_TOKEN</span>
                </div>
                <span class="item-status" :class="{ 'status-ok': settings.wecom.callback_token_configured }">
                  {{ settings.wecom.callback_token_configured ? '已配置' : '未配置' }}
                </span>
              </li>

              <li class="checklist-item">
                <span class="check-icon" :class="{ success: settings.wecom.aes_key_configured }">
                  {{ settings.wecom.aes_key_configured ? '✓' : '✗' }}
                </span>
                <div class="item-meta">
                  <span class="item-label">回调 AES Key</span>
                  <span class="item-sub">NOTIFY_HUB_WECOM_CALLBACK_AES_KEY</span>
                </div>
                <span class="item-status" :class="{ 'status-ok': settings.wecom.aes_key_configured }">
                  {{ settings.wecom.aes_key_configured ? '已配置' : '未配置' }}
                </span>
              </li>
            </ul>
          </div>

          <DescriptionList class="details-list">
            <dt>API 服务端点</dt>
            <dd class="mono">
              {{ settings.wecom.api_base_url }}
            </dd>
            <dt>网络代理状态</dt>
            <dd>{{ settings.wecom.using_proxy ? '已启用自定义 HTTPS 代理' : '未启用代理 (直连企业微信官方)' }}</dd>
          </DescriptionList>
        </AppCard>
      </div>

      <!-- 右列：连通性测试与应用菜单发布 -->
      <div class="column-stack">
        <!-- 连通性测试 -->
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <Radio class="header-icon" :size="18" />
                <h3 class="panel-title">
                  发送连通性测试
                </h3>
              </div>
              <span class="mono muted title-info">NORMAL DELIVERY PATH</span>
            </div>
          </template>

          <p class="desc-text">
            向指定内部接收人发送一条真实消息，验证凭据有效性、Token 缓存及投递管道健康度。
          </p>

          <form @submit.prevent="sendTest">
            <div class="field">
              <label for="wecom-test-recipient">内部接收人 UserID</label>
              <AppInput
                id="wecom-test-recipient"
                v-model="test.recipient_id"
                required
                placeholder="例如: person_vigoss 或 企业微信内通讯录账号"
              />
            </div>
            <div class="field mt-3">
              <label for="wecom-test-type">测试消息类型</label>
              <AppSelect id="wecom-test-type" v-model="test.message_type">
                <option value="text">
                  文本消息 (Text)
                </option>
                <option value="article">
                  图文卡片 (Article)
                </option>
              </AppSelect>
            </div>

            <div class="form-actions mt-4">
              <AppButton id="btn-send-wecom-test" type="submit" variant="primary" :loading="testing">
                发送测试消息
              </AppButton>
            </div>
          </form>

          <AppAlert variant="warning" class="warning-alert mt-4">
            图片和语音媒体请通过提醒或通知接口真实验收；测试消息会入库并在投递历史中留下审计记录。
          </AppAlert>
        </AppCard>

        <!-- 三系列菜单发布 -->
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <Layers class="header-icon" :size="18" />
                <h3 class="panel-title">
                  企业微信三系列应用菜单
                </h3>
              </div>
              <span class="mono muted title-info">WECOM MENU / GLOBAL</span>
            </div>
          </template>

          <p class="desc-text">
            一键同步发布 Notify Hub 的三系列工作流菜单到企业微信应用底部，支持即时交互与待办闭环。
          </p>

          <div class="menu-tree" aria-label="企业微信三系列菜单结构">
            <section>
              <strong>新建提醒</strong>
              <span>快速文字提醒</span>
              <span>图文提醒</span>
              <span>打开完整创建页</span>
            </section>
            <section>
              <strong>我的提醒</strong>
              <span>等待我完成</span>
              <span>今天的提醒</span>
              <span>全部提醒</span>
            </section>
            <section>
              <strong>快捷操作</strong>
              <span>完成本次</span>
              <span>推迟10分钟</span>
              <span>推迟30分钟</span>
              <span>今日忽略</span>
              <span>停止本次</span>
            </section>
          </div>

          <AppAlert variant="warning" class="warning-alert mt-4">
            发布将覆盖企业微信后台现有自定义菜单；【快捷操作】仅作用于当前用户最近一次收到的交互式提醒任务。
          </AppAlert>

          <div class="form-actions mt-4">
            <AppButton
              id="btn-publish-wecom-menu"
              variant="primary"
              :loading="publishingMenu"
              :disabled="!settings.wecom.configured"
              @click="publishReminderMenu"
            >
              发布三系列菜单
            </AppButton>
          </div>
        </AppCard>
      </div>
    </div>
  </section>

  <!-- TAB 3: 内容发布与节点 (Publishing & Nodes) -->
  <section v-show="activeTab === 'publishing'" class="tab-panel" aria-labelledby="tab-publishing-btn">
    <!-- ADR-033 架构说明 -->
    <AppAlert variant="info" class="info-alert mb-4">
      <strong>统一发布网关架构 (ADR-033)：</strong>
      Notify Hub 作为调度中枢，专注于文章生成、分发调度与安全总闸；实际发布执行与微信公众号/小红书的第三方密钥由外部 Browser Publisher 容器独立托管，Notify Hub 本身不持有或读取第三方发布密钥。
    </AppAlert>

    <div class="tab-grid">
      <!-- 左侧：外部发布器节点监控 -->
      <div class="column-stack">
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <Globe class="header-icon" :size="18" />
                <h3 class="panel-title">
                  外部发布器服务节点 (Browser Publisher)
                </h3>
              </div>
              <StatusBadge :status="settings.browser_publisher.configured ? 'active' : 'disabled'" />
            </div>
          </template>

          <p class="desc-text">
            Browser Publisher 是独立运行的浏览器自动化与 API 发布网关，管理无头 Chromium、会话维持和任务防风控排队。
          </p>

          <DescriptionList class="details-list">
            <dt>服务 API 地址</dt>
            <dd class="mono">
              {{ settings.browser_publisher.api_url }}
            </dd>
            <dt>发布器鉴权 Token</dt>
            <dd>
              <span :class="settings.browser_publisher.access_token_configured ? 'text-success font-semibold' : 'text-danger'">
                {{ settings.browser_publisher.access_token_configured ? '已配置 (Bearer Auth 保护)' : '未配置' }}
              </span>
            </dd>
            <dt>执行保护机制</dt>
            <dd>单串行 Chromium Worker 独占锁保护 / SQLite WAL 持久化检查点</dd>
            <dt>平台风控策略</dt>
            <dd>小红书强制 1800 秒防风控冷却 / 遇滑块自动暂停告警等待人工恢复</dd>
          </DescriptionList>

          <div class="form-actions mt-4">
            <a
              :href="settings.browser_publisher.api_url"
              target="_blank"
              rel="noopener noreferrer"
              class="app-btn-secondary-link"
            >
              打开发布器 Web 控制台
              <ExternalLink :size="14" />
            </a>
          </div>
        </AppCard>

        <!-- 微信公众号调度策略 -->
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <Layers class="header-icon" :size="18" />
                <h3 class="panel-title">
                  微信公众号发布调度
                </h3>
              </div>
              <StatusBadge
                :status="settings.wechat_mp.effective_mode === 'browser' ? 'active' : 'neutral'"
              />
            </div>
          </template>

          <DescriptionList class="details-list">
            <dt>当前调度模式</dt>
            <dd>
              <span v-if="settings.wechat_mp.effective_mode === 'browser'" class="font-semibold text-primary">
                委托 Browser Publisher（官方 API 建草稿 + Playwright 最终发表）
              </span>
              <span v-else-if="settings.wechat_mp.effective_mode === 'library'" class="font-semibold text-secondary">
                仅存本地文章库 (需人工复制排版或确认)
              </span>
              <span v-else class="font-semibold">
                {{ settings.wechat_mp.effective_mode }}
              </span>
            </dd>
            <dt>API 密钥归属</dt>
            <dd>
              <span v-if="settings.wechat_mp.api_credentials_managed_by === 'browser_publisher'" class="badge-soft-green">
                Browser Publisher 容器独占 (ADR-033 推荐)
              </span>
              <span v-else class="badge-soft-neutral">
                Notify Hub（旧 direct API 兼容模式）
              </span>
            </dd>
            <dt>默认作者签名</dt>
            <dd>{{ settings.wechat_mp.author }}</dd>
            <dt>留言与通知规则</dt>
            <dd>固定开启留言 (need_open_comment=1)；发表前固定关闭群通知</dd>
          </DescriptionList>

          <div class="form-actions mt-4">
            <RouterLink to="/articles" class="app-btn-secondary-link">
              前往公众号文章库
              <ArrowRight :size="14" />
            </RouterLink>
          </div>
        </AppCard>
      </div>

      <!-- 右侧：小红书平台总闸与发布规范 -->
      <div class="column-stack">
        <AppCard padding="md">
          <template #header>
            <div class="panel-header-wrap">
              <div class="title-with-icon">
                <ShieldCheck class="header-icon" :size="18" />
                <h3 class="panel-title">
                  小红书平台安全总闸
                </h3>
              </div>
              <StatusBadge :status="settings.xiaohongshu_publishing_enabled ? 'active' : 'disabled'" />
            </div>
          </template>

          <p class="desc-text">
            小红书采用“平台总闸 + 插件意图”两层开关风控。平台总闸是最高优先级的物理熔断开关，防止插件自动化产生意外群发。
          </p>

          <div class="platform-toggle mt-3">
            <div class="platform-toggle-copy">
              <label for="xhs-master-toggle" class="toggle-label">小红书平台发布总开关</label>
              <span class="field-help">
                {{ settings.xiaohongshu_publishing_enabled ? '当前总闸已开启：插件生成的图文笔记将正常进入投递队列。' : '当前总闸已关闭：系统将安全拦截所有小红书发布任务，排队中任务也会安全取消。' }}
              </span>
            </div>
            <AppSwitch
              id="xhs-master-toggle"
              v-model="settings.xiaohongshu_publishing_enabled"
            />
          </div>

          <AppAlert v-if="!settings.xiaohongshu_publishing_enabled" variant="warning" class="info-alert mt-4">
            当前已关闭小红书平台发布。公众号文章不受影响。
          </AppAlert>

          <div class="rules-card mt-4">
            <div class="rules-card-title">
              小红书发帖风控硬性规则
            </div>
            <ul class="rules-list">
              <li><strong>封面规格</strong>：3:4 竖版动态霓虹排版大图（支持原生 Emoji）；</li>
              <li><strong>图片张数</strong>：单篇笔记严格限制在 1 ~ 18 张图片之间；</li>
              <li><strong>标题长度</strong>：笔记标题严格限制在 20 个字符以内；</li>
              <li><strong>冷却间隔</strong>：Browser Publisher 强制保证两次发帖间隔不少于 30 分钟。</li>
            </ul>
          </div>

          <!-- 保存小红书总闸修改 -->
          <div class="form-actions mt-5">
            <AppButton
              id="btn-save-publishing-settings"
              type="button"
              variant="primary"
              :loading="busy"
              @click="save"
            >
              保存发布总闸设置
            </AppButton>
          </div>
        </AppCard>
      </div>
    </div>
  </section>

  <!-- TAB 4: 系统与运维 (System & Ops) -->
  <section v-show="activeTab === 'system'" class="tab-panel" aria-labelledby="tab-system-btn">
    <div class="cards-grid">
      <!-- 系统版本与运行状态 -->
      <AppCard padding="md">
        <template #header>
          <div class="panel-header-wrap">
            <div class="title-with-icon">
              <Server class="header-icon" :size="18" />
              <h3 class="panel-title">
                运行环境与规格
              </h3>
            </div>
            <span class="panel-badge-tag">ENGINE</span>
          </div>
        </template>

        <DescriptionList class="details-list">
          <dt>系统版本</dt>
          <dd class="mono">
            v{{ settings.version }}
          </dd>
          <dt>数据持久化</dt>
          <dd>SQLite 3 + WAL 日志模式 (开启 busy_timeout=30s 与级联外键约束)</dd>
          <dt>当前默认时区</dt>
          <dd class="mono">
            {{ settings.timezone }}
          </dd>
          <dt>临时媒体保留</dt>
          <dd>{{ settings.retention_days }} 天 (过期由 MediaCleanupWorker 自动安全回收)</dd>
          <dt>广播授权控制</dt>
          <dd>默认禁止隐式 @all 广播（须显式声明 NOTIFY_HUB_ALLOW_BROADCAST）</dd>
        </DescriptionList>
      </AppCard>

      <!-- 架构准则与规范索引 -->
      <AppCard padding="md">
        <template #header>
          <div class="panel-header-wrap">
            <div class="title-with-icon">
              <ShieldCheck class="header-icon" :size="18" />
              <h3 class="panel-title">
                核心架构原则 (ADR 索引)
              </h3>
            </div>
            <span class="panel-badge-tag">GUARDRAILS</span>
          </div>
        </template>

        <div class="guardrails-list">
          <div class="guardrail-item">
            <strong>1. 只读凭据隔离机制</strong>
            <p>企业微信与关键通道 Secret 仅通过环境变量或 Secret 文件注入，管理端不回显明文，防止配置分叉与泄露。</p>
          </div>
          <div class="guardrail-item">
            <strong>2. 202 异步持久化保证</strong>
            <p>所有外部事件在响应 HTTP 202 前必须持久化落库；调度状态由数据库保证，重启后未完投递自动恢复。</p>
          </div>
          <div class="guardrail-item">
            <strong>3. ADR-031 / ADR-033 解耦原则</strong>
            <p>Notify Hub 专注于事件调度分工；第三方发布执行器（Browser Publisher）负责具体的浏览器会话和草稿 API。</p>
          </div>
        </div>
      </AppCard>
    </div>

    <!-- 跨模块运维快捷入口 -->
    <AppCard padding="md" class="mt-4">
      <template #header>
        <h3 class="panel-title">
          系统功能快捷入口
        </h3>
      </template>

      <div class="quick-nav-grid">
        <RouterLink to="/people" class="nav-tile">
          <div class="nav-tile-icon">
            <MessageSquare :size="20" />
          </div>
          <div class="nav-tile-text">
            <strong>内部接收人</strong>
            <span>管理通讯录用户与默认投递对象</span>
          </div>
        </RouterLink>

        <RouterLink to="/api-clients" class="nav-tile">
          <div class="nav-tile-icon">
            <ShieldCheck :size="20" />
          </div>
          <div class="nav-tile-text">
            <strong>API Clients</strong>
            <span>外部系统接入凭证与速率限制审计</span>
          </div>
        </RouterLink>

        <RouterLink to="/plugins" class="nav-tile">
          <div class="nav-tile-icon">
            <Layers :size="20" />
          </div>
          <div class="nav-tile-text">
            <strong>插件运行台</strong>
            <span>Codex X Monitor 等自动化插件配置</span>
          </div>
        </RouterLink>

        <RouterLink to="/ai/profiles" class="nav-tile">
          <div class="nav-tile-icon">
            <Sparkles :size="20" />
          </div>
          <div class="nav-tile-text">
            <strong>AI Gateway</strong>
            <span>管理模型 Provider 与提取/摘要策略</span>
          </div>
        </RouterLink>
      </div>
    </AppCard>
  </section>
</template>

<style scoped>
.version-badge {
  font-size: var(--text-xs);
  padding: 4px 8px;
  background-color: var(--color-neutral-100);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
}

/* 导航 Tabs 样式 */
.settings-tabs-nav {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-6);
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: var(--space-2);
  overflow-x: auto;
}

.tab-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 140ms ease;
  white-space: nowrap;
}

.tab-item:hover {
  color: var(--text-primary);
  background-color: var(--surface-hover);
}

.tab-item.active {
  color: var(--text-primary);
  background-color: var(--surface-panel);
  border-color: var(--border-default);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  font-weight: 600;
}

.tab-icon {
  opacity: 0.8;
}

.tab-item.active .tab-icon {
  opacity: 1;
  color: var(--action-primary);
}

.status-indicator-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

.dot-success {
  background-color: var(--status-success);
}

.dot-warning {
  background-color: var(--status-warning);
}

/* Tab Panels 布局 */
.tab-panel {
  display: flex;
  flex-direction: column;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.tab-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.column-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

@media (max-width: 900px) {
  .cards-grid,
  .tab-grid {
    grid-template-columns: 1fr;
  }
}

/* 卡片与标题 */
.panel-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.title-with-icon {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.header-icon {
  color: var(--text-secondary);
}

.panel-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
  color: var(--text-primary);
}

.panel-badge-tag {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--text-tertiary);
  padding: 2px 6px;
  background: var(--surface-hover);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
}

.title-info {
  font-size: 10px;
  font-family: var(--font-mono);
}

/* 表单与字段 */
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-weight: 600;
}

.field-help {
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

/* 预设标签 Chips */
.preset-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.preset-title {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.chip-btn {
  font-size: 11px;
  font-family: var(--font-mono);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--surface-hover);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 120ms ease;
}

.chip-btn:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.chip-btn.active {
  background: var(--color-neutral-800);
  color: var(--text-inverse);
  border-color: var(--color-neutral-800);
}

/* 平台开关 */
.platform-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background-color: var(--surface-hover);
}

.platform-toggle-copy {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.toggle-label {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.desc-text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--leading-normal);
  margin: 0 0 var(--space-4) 0;
}

.info-alert {
  margin-top: 0;
  margin-bottom: var(--space-4);
}

.warning-alert {
  margin-top: var(--space-4);
}

/* 检查清单 */
.checklist-section {
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background-color: var(--surface-hover);
  padding: var(--space-4);
  margin-bottom: var(--space-4);
}

.checklist-title {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-secondary);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: var(--space-3);
}

.readiness-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.checklist-item {
  display: flex;
  align-items: center;
  font-size: var(--text-sm);
}

.item-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.item-label {
  color: var(--text-primary);
  font-weight: 500;
  font-size: var(--text-sm);
}

.item-sub {
  color: var(--text-tertiary);
  font-size: 10px;
  font-family: var(--font-mono);
}

.item-status {
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: var(--font-mono);
}

.item-status.status-ok {
  color: var(--status-success);
  font-weight: 600;
}

.check-icon {
  width: var(--space-5);
  height: var(--space-5);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  margin-right: var(--space-3);
  font-weight: bold;
  font-size: 11px;
  background-color: #fee2e2;
  color: var(--status-danger);
  flex-shrink: 0;
}

.check-icon.success {
  background-color: #dcfce7;
  color: var(--status-success);
}

/* 详情列表 */
.details-list {
  border-top: 1px solid var(--border-subtle);
  padding-top: var(--space-3);
  margin-top: var(--space-3);
}

/* 三系列菜单树展示 */
.menu-tree {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.menu-tree section {
  display: flex;
  overflow: hidden;
  flex-direction: column;
  border: 1px solid rgba(45, 117, 81, 0.2);
  border-radius: var(--radius-md);
}

.menu-tree strong,
.menu-tree span {
  padding: 8px 10px;
  background: #f8faf6;
  font-size: var(--text-xs);
}

.menu-tree strong {
  background: rgba(45, 117, 81, 0.1);
  color: var(--status-success);
  letter-spacing: 0.02em;
}

.menu-tree span {
  border-top: 1px solid rgba(45, 117, 81, 0.1);
  color: var(--text-primary);
}

@media (max-width: 700px) {
  .menu-tree {
    grid-template-columns: 1fr;
  }
}

/* 规则卡片 */
.rules-card {
  border: 1px solid var(--border-subtle);
  background-color: var(--surface-hover);
  border-radius: var(--radius-sm);
  padding: var(--space-3) var(--space-4);
}

.rules-card-title {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: var(--space-2);
}

.rules-list {
  margin: 0;
  padding-left: var(--space-4);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  line-height: var(--leading-relaxed);
}

.rules-list li strong {
  color: var(--text-primary);
}

/* 快捷链接与动作条 */
.quick-link-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background-color: var(--surface-hover);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
}

.quick-link-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.quick-link-copy strong {
  font-size: var(--text-xs);
  color: var(--text-primary);
}

.quick-link-copy span {
  font-size: 11px;
  color: var(--text-secondary);
}

.quick-link-action {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--action-primary);
  text-decoration: none;
  font-weight: 600;
  white-space: nowrap;
}

.quick-link-action:hover {
  text-decoration: underline;
}

.app-btn-secondary-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--text-primary);
  background-color: var(--surface-hover);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  text-decoration: none;
  transition: all 120ms ease;
}

.app-btn-secondary-link:hover {
  background-color: var(--surface-selected);
  border-color: var(--border-strong);
}

/* 保存底栏 */
.save-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4);
  background: var(--surface-panel);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-panel);
}

.save-bar-info {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

/* 规范列表 */
.guardrails-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.guardrail-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.guardrail-item strong {
  font-size: var(--text-xs);
  color: var(--text-primary);
}

.guardrail-item p {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--text-secondary);
  line-height: var(--leading-normal);
}

/* 快捷运维导航网格 */
.quick-nav-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

@media (max-width: 800px) {
  .quick-nav-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.nav-tile {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  background-color: var(--surface-hover);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  text-decoration: none;
  transition: all 140ms ease;
}

.nav-tile:hover {
  background-color: var(--surface-selected);
  border-color: var(--border-strong);
  transform: translateY(-1px);
}

.nav-tile-icon {
  color: var(--action-primary);
  margin-top: 2px;
}

.nav-tile-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-tile-text strong {
  font-size: var(--text-xs);
  color: var(--text-primary);
}

.nav-tile-text span {
  font-size: 10px;
  color: var(--text-tertiary);
}

/* 徽章 */
.badge-soft-green {
  display: inline-block;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: 500;
  background-color: #dcfce7;
  color: var(--status-success);
  border-radius: var(--radius-sm);
}

.badge-soft-neutral {
  display: inline-block;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: 500;
  background-color: var(--surface-hover);
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
}

.text-success {
  color: var(--status-success);
}

.text-danger {
  color: var(--status-danger);
}

.font-semibold {
  font-weight: 600;
}

.mt-1 { margin-top: var(--space-1); }
.mt-2 { margin-top: var(--space-2); }
.mt-3 { margin-top: var(--space-3); }
.mt-4 { margin-top: var(--space-4); }
.mt-5 { margin-top: var(--space-5); }
.mb-4 { margin-bottom: var(--space-4); }
</style>
