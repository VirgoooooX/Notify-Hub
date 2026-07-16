<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/lib/api'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import DescriptionList from '@/components/data/DescriptionList.vue'
import { useUiStore } from '@/stores/ui'
import type { AIProfile } from '@/types'
import { APP_VERSION } from '@/lib/version'

const ui = useUiStore()
const busy = ref(false)
const testing = ref(false)
const publishingMenu = ref(false)
const parserProfiles = ref<AIProfile[]>([])

const settings = reactive({
  timezone: 'Asia/Shanghai',
  retention_days: 90,
  default_reminder_parser_profile_id: '',
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
  }
})

const test = reactive({
  recipient_id: '',
  message_type: 'text'
})

onMounted(async () => {
  try {
    const [platformSettings, profiles] = await Promise.all([
      api.get<Record<string, unknown>>('/admin/settings'),
      api.get<AIProfile[]>('/admin/ai/profiles')
    ])
    Object.assign(settings, platformSettings)
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
        settings.default_reminder_parser_profile_id || null
    })
    ui.toast('平台设置已保存', 'success')
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
  <PageHeader title="系统设置" description="企业微信凭据由只读 Secret 文件或环境变量注入，管理端仅显示配置状态。">
    <span class="mono muted version-badge">VERSION {{ settings.version }}</span>
  </PageHeader>

  <section class="grid detail-grid">
    <form class="settings-form" @submit.prevent="save">
      <AppCard padding="md">
        <template #header>
          <h3 class="panel-title">
            平台设置
          </h3>
        </template>
        <div class="field">
          <label>默认时区</label>
          <AppInput v-model="settings.timezone" />
        </div>
        <div class="field mt-4">
          <label>历史保留天数</label>
          <AppInput v-model.number="settings.retention_days" type="number" min="7" max="3650" />
        </div>
        <div class="field mt-4">
          <label>提醒自然语言解析 Profile</label>
          <AppSelect v-model="settings.default_reminder_parser_profile_id">
            <option value="">
              禁用 AI fallback
            </option>
            <option v-for="profile in parserProfiles" :key="profile.id" :value="profile.id">
              {{ profile.name }} · {{ profile.model }}
            </option>
          </AppSelect>
        </div>
        
        <div class="panel-header-wrap mt-6">
          <h3 class="panel-title">
            企业微信凭据配置
          </h3>
          <StatusBadge :status="settings.wecom.configured ? 'active' : 'disabled'" />
        </div>

        <AppAlert variant="info" class="info-alert">
          为避免运行时配置与实际渠道状态分叉，凭据只能通过部署目录的只读 Secret 文件或环境变量更新，并在重启后生效。
        </AppAlert>

        <div class="checklist-section">
          <div class="checklist-title">
            Readiness Checklist
          </div>
          <ul class="readiness-list">
            <li class="checklist-item">
              <span class="check-icon" :class="{ success: settings.wecom.corp_id_configured }">
                {{ settings.wecom.corp_id_configured ? '✓' : '✗' }}
              </span>
              <span class="item-label">Corp ID</span>
              <span class="item-status">{{ settings.wecom.corp_id_configured ? '已配置' : '未配置' }}</span>
            </li>
            <li class="checklist-item">
              <span class="check-icon" :class="{ success: settings.wecom.agent_id_configured }">
                {{ settings.wecom.agent_id_configured ? '✓' : '✗' }}
              </span>
              <span class="item-label">Agent ID</span>
              <span class="item-status">{{ settings.wecom.agent_id_configured ? '已配置' : '未配置' }}</span>
            </li>
            <li class="checklist-item">
              <span class="check-icon" :class="{ success: settings.wecom.secret_configured }">
                {{ settings.wecom.secret_configured ? '✓' : '✗' }}
              </span>
              <span class="item-label">应用 Secret</span>
              <span class="item-status">{{ settings.wecom.secret_configured ? '已配置' : '未配置' }}</span>
            </li>
            <li class="checklist-item">
              <span class="check-icon" :class="{ success: settings.wecom.callback_token_configured }">
                {{ settings.wecom.callback_token_configured ? '✓' : '✗' }}
              </span>
              <span class="item-label">Callback Token</span>
              <span class="item-status">{{ settings.wecom.callback_token_configured ? '已配置' : '未配置' }}</span>
            </li>
            <li class="checklist-item">
              <span class="check-icon" :class="{ success: settings.wecom.aes_key_configured }">
                {{ settings.wecom.aes_key_configured ? '✓' : '✗' }}
              </span>
              <span class="item-label">AES Key</span>
              <span class="item-status">{{ settings.wecom.aes_key_configured ? '已配置' : '未配置' }}</span>
            </li>
          </ul>
        </div>

        <DescriptionList class="details-list">
          <dt>API 地址</dt>
          <dd class="mono">
            {{ settings.wecom.api_base_url }}
          </dd>
          <dt>代理状态</dt>
          <dd>{{ settings.wecom.using_proxy ? '已启用自定义 HTTPS 代理' : '未启用代理 (直连企业微信官方)' }}</dd>
        </DescriptionList>

        <div class="form-actions mt-5">
          <AppButton type="submit" variant="primary" :loading="busy">
            保存设置
          </AppButton>
        </div>
      </AppCard>
    </form>

    <article class="test-panel">
      <AppCard padding="md">
        <template #header>
          <div class="panel-header-wrap">
            <h3 class="panel-title">
              发送连通性测试
            </h3>
            <span class="mono muted title-info">NORMAL DELIVERY PATH</span>
          </div>
        </template>
        
        <p class="desc-text">
          测试消息也会创建 system.channel_test 事件，并显示在通知投递历史中。
        </p>
        
        <form @submit.prevent="sendTest">
          <div class="field">
            <label>内部接收人 ID</label>
            <AppInput v-model="test.recipient_id" required placeholder="person_vigoss" />
          </div>
          <div class="field mt-4">
            <label>消息类型</label>
            <AppSelect v-model="test.message_type">
              <option value="text">
                文本
              </option>
              <option value="article">
                图文
              </option>
            </AppSelect>
          </div>
          
          <div class="form-actions mt-5">
            <AppButton type="submit" variant="primary" :loading="testing">
              发送测试消息
            </AppButton>
          </div>
        </form>

        <AppAlert variant="warning" class="warning-alert mt-5">
          图片和语音请通过媒体管理与通知接口验收；这里仅验证文本/图文渠道连通性。
        </AppAlert>
      </AppCard>

      <AppCard padding="md" class="menu-publish-card">
        <template #header>
          <div class="panel-header-wrap">
            <h3 class="panel-title">
              企业微信三系列菜单
            </h3>
            <span class="mono muted title-info">WECOM MENU / GLOBAL</span>
          </div>
        </template>
        <p class="desc-text">
          发布后，企业微信应用底部会显示“新建提醒 / 我的提醒 / 快捷操作”三个系列。该操作会覆盖应用当前菜单，并写入管理员审计日志。
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
        <AppAlert variant="warning" class="warning-alert mt-5">
          只有【快捷操作】会操作当前 UserID 最近一次成功收到的交互式提醒；目标失效后不会回退到更早任务。创建页和列表页链接还需要配置公开访问地址。
        </AppAlert>
        <div class="form-actions mt-5">
          <AppButton
            variant="primary"
            :loading="publishingMenu"
            :disabled="!settings.wecom.configured"
            @click="publishReminderMenu"
          >
            发布三系列菜单
          </AppButton>
        </div>
      </AppCard>
    </article>
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

.menu-publish-card {
  margin-top: var(--space-4);
}

.menu-tree {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.menu-tree section {
  display: flex;
  overflow: hidden;
  flex-direction: column;
  border: 1px solid rgba(31, 107, 79, 0.2);
  border-radius: var(--radius-md);
}

.menu-tree strong,
.menu-tree span {
  padding: 9px 11px;
  background: #f8faf6;
  font-size: var(--text-xs);
}

.menu-tree strong {
  background: rgba(31, 107, 79, 0.1);
  color: #1f6b4f;
  letter-spacing: 0.04em;
}

.menu-tree span {
  border-top: 1px solid rgba(31, 107, 79, 0.1);
}

@media (max-width: 700px) {
  .menu-tree {
    grid-template-columns: 1fr;
  }
}

.panel-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.panel-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.title-info {
  font-size: 10px;
}

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

.desc-text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--leading-normal);
  margin: 0 0 var(--space-4) 0;
}

.info-alert {
  margin-top: var(--space-3);
  margin-bottom: var(--space-4);
}

.warning-alert {
  margin-top: var(--space-4);
}

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
  gap: var(--space-2);
}

.checklist-item {
  display: flex;
  align-items: center;
  font-size: var(--text-sm);
}

.check-icon {
  width: var(--space-5);
  height: var(--space-5);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  margin-right: var(--space-2);
  font-weight: bold;
  font-size: 11px;
  background-color: #fee2e2;
  color: var(--status-danger);
}

.check-icon.success {
  background-color: #dcfce7;
  color: var(--status-success);
}

.item-label {
  flex: 1;
  color: var(--text-primary);
  font-weight: 500;
}

.item-status {
  color: var(--text-secondary);
  font-size: var(--text-xs);
}

.details-list {
  border-top: 1px solid var(--border-subtle);
  padding-top: var(--space-4);
  margin-top: var(--space-4);
}

.mt-4 {
  margin-top: var(--space-4);
}

.mt-5 {
  margin-top: var(--space-5);
}

.mt-6 {
  margin-top: var(--space-6);
}
</style>
