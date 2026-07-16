<script setup lang="ts">
import { computed } from 'vue'
import {
  CheckCircle2,
  Clock3,
  ImageIcon,
  Moon,
  Newspaper,
  Octagon,
  Smartphone,
} from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    title?: string
    content?: string
    contentType?: string
    interactive?: boolean
    broadcast?: boolean
    notifyOnAllCompleted?: boolean
    compact?: boolean
  }>(),
  {
    title: '',
    content: '',
    contentType: 'text',
    interactive: false,
    broadcast: false,
    notifyOnAllCompleted: false,
    compact: false,
  },
)

const contentLabel = computed(() => {
  if (props.contentType === 'article') return '普通图文'
  if (props.contentType === 'image') return '普通图片 + 文字'
  return '普通文字'
})

const quickActions = [
  { label: '完成本次', icon: CheckCircle2 },
  { label: '推迟10分钟', icon: Clock3 },
  { label: '推迟30分钟', icon: Clock3 },
  { label: '今日忽略', icon: Moon },
  { label: '停止本次', icon: Octagon },
]
</script>

<template>
  <section class="delivery-preview" :class="{ compact }" aria-label="企业微信消息预览">
    <div class="preview-heading">
      <div>
        <span class="preview-kicker">WECOM DELIVERY</span>
        <h4>企业微信送达预览</h4>
      </div>
      <span class="mode-chip">
        <Newspaper v-if="contentType === 'article'" :size="13" />
        <ImageIcon v-else-if="contentType === 'image'" :size="13" />
        <Smartphone v-else :size="13" />
        {{ contentLabel }}
      </span>
    </div>

    <div class="phone-stage">
      <div class="message-bubble">
        <span class="sender-mark">N</span>
        <div class="message-copy">
          <strong>{{ interactive ? (broadcast ? '📣 全员持续提醒｜' : '🔁 持续提醒｜') : '' }}{{ title || '提醒标题' }}</strong>
          <p>{{ content || '提醒正文将在这里展示。' }}</p>
          <div v-if="contentType === 'image'" class="media-placeholder">
            <ImageIcon :size="18" />
            <span>图片消息</span>
          </div>
          <div v-else-if="contentType === 'article'" class="article-placeholder">
            <Newspaper :size="18" />
            <span>图文封面与跳转链接</span>
          </div>
          <p v-if="interactive" class="interaction-hint">
            {{ broadcast ? '📣【全员持续提醒｜需要每个人确认】' : '🔁【持续提醒｜需要你确认完成】' }}<br>
            这不是一次性通知；{{ broadcast ? '未完成的成员' : '在你完成前，系统' }}会继续收到催办。<br>
            完成后请尽快点击底部【快捷操作】→【完成本次】。<br>
            <template v-if="broadcast && notifyOnAllCompleted">
              所有登记接收人完成后，系统会广播“所有人都完成”。
            </template>
            <template v-else-if="!broadcast">
              菜单默认操作最近收到的一条交互式提醒。
            </template>
          </p>
        </div>
      </div>

      <div v-if="interactive" class="menu-dock">
        <div class="dock-series">
          <span>新建提醒</span>
          <span>我的提醒</span>
          <span class="selected">快捷操作</span>
        </div>
        <div class="dock-actions">
          <span v-for="entry in quickActions" :key="entry.label">
            <component :is="entry.icon" :size="13" />
            {{ entry.label }}
          </span>
        </div>
      </div>
      <div v-else class="passive-note">
        普通通知不会更新用户的“最近交互提醒”指针。
      </div>
    </div>
  </section>
</template>

<style scoped>
.delivery-preview {
  --preview-ink: #18251f;
  --preview-green: #1f6b4f;
  position: relative;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--preview-green) 28%, var(--border-subtle));
  border-radius: var(--radius-lg);
  background:
    radial-gradient(circle at 92% 8%, rgba(31, 107, 79, 0.14), transparent 34%),
    linear-gradient(145deg, #f8f7f0, #f0f4ef);
  color: var(--preview-ink);
}

.preview-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-bottom: 1px solid rgba(31, 107, 79, 0.14);
}

.preview-heading h4 {
  margin-top: 2px;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: var(--text-lg);
}

.preview-kicker {
  color: var(--preview-green);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.16em;
}

.mode-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 9px;
  border: 1px solid rgba(31, 107, 79, 0.22);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.74);
  color: var(--preview-green);
  font-size: var(--text-xs);
  font-weight: 700;
}

.phone-stage {
  padding: var(--space-4);
}

.message-bubble {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
}

.sender-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 7px;
  background: var(--preview-green);
  color: white;
  font-family: Georgia, serif;
  font-weight: 800;
  box-shadow: 3px 3px 0 rgba(24, 37, 31, 0.12);
}

.message-copy {
  position: relative;
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgba(24, 37, 31, 0.14);
  border-radius: 3px 12px 12px 12px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 12px 28px rgba(24, 37, 31, 0.08);
}

.message-copy strong {
  display: block;
  margin-bottom: 5px;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: var(--text-md);
}

.message-copy p {
  margin: 0;
  color: #526059;
  font-size: var(--text-xs);
  line-height: 1.75;
  white-space: pre-line;
}

.media-placeholder,
.article-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 54px;
  margin-top: var(--space-2);
  border: 1px dashed rgba(31, 107, 79, 0.3);
  background: rgba(31, 107, 79, 0.06);
  color: var(--preview-green);
  font-size: var(--text-xs);
}

.interaction-hint {
  margin-top: var(--space-3) !important;
  padding-top: var(--space-3);
  border-top: 1px dashed rgba(31, 107, 79, 0.25);
  color: var(--preview-green) !important;
  font-weight: 600;
}

.menu-dock {
  margin: var(--space-4) 0 0 42px;
  border: 1px solid rgba(24, 37, 31, 0.16);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(8px);
}

.dock-series {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.dock-series span {
  padding: 9px 5px;
  border-right: 1px solid rgba(24, 37, 31, 0.09);
  color: #526059;
  font-size: var(--text-xs);
  font-weight: 700;
  text-align: center;
}

.dock-series span:last-child {
  border-right: 0;
}

.dock-series .selected {
  background: rgba(31, 107, 79, 0.09);
  color: var(--preview-green);
}

.dock-actions {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border-top: 1px solid rgba(24, 37, 31, 0.12);
}

.dock-actions span {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 8px 3px;
  border-right: 1px solid rgba(24, 37, 31, 0.09);
  color: #526059;
  font-size: 9px;
  text-align: center;
}

.dock-actions span:last-child {
  border-right: 0;
}

.passive-note {
  margin: var(--space-3) 0 0 42px;
  color: #68736d;
  font-size: var(--text-xs);
}

.compact .preview-heading,
.compact .phone-stage {
  padding: var(--space-3);
}

@media (max-width: 620px) {
  .preview-heading {
    align-items: flex-start;
  }

  .menu-dock {
    margin-left: 0;
  }

  .dock-actions {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .dock-actions span:nth-child(3) {
    border-right: 0;
  }

  .dock-actions span:nth-child(n + 4) {
    border-top: 1px solid rgba(24, 37, 31, 0.09);
  }
}
</style>
