<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppInput from '@/components/ui/AppInput.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import { APP_VERSION } from '@/lib/version'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const username = ref('admin')
const password = ref('')
const confirmPassword = ref('')
const busy = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    await auth.bootstrap()
  } catch {
    auth.initialized = true
  }
})

async function submit() {
  if (!auth.initialized && password.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  error.value = ''
  try {
    if (auth.initialized) {
      await auth.login(username.value, password.value)
    } else {
      await auth.initialize(username.value, password.value)
    }
    await router.push(String(route.query.redirect ?? '/'))
  } catch (e) {
    error.value = e instanceof Error ? e.message : '无法进入控制台'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-art">
      <div class="logo-area">
        <img src="/brand/logo-horizontal-reverse.svg" alt="Notify Hub" class="brand-logo">
      </div>
      <div class="art-content">
        <p class="art-eyebrow">
          SELF-HOSTED SIGNAL CONTROL
        </p>
        <h1 class="art-title">
          每一个信号，<br>都有迹可循。
        </h1>
        <p class="art-desc">
          统一接收事件、调度提醒，并追踪每一次企业微信投递。可靠落库，清晰恢复。
        </p>
      </div>
      <small class="art-footer mono">NOTIFY HUB / RELEASE {{ APP_VERSION }}</small>
    </section>

    <section class="login-form-wrap">
      <form class="login-form" @submit.prevent="submit">
        <p class="form-eyebrow">
          SECURE ADMIN ACCESS
        </p>
        <h2 class="form-title">
          {{ auth.initialized ? '进入控制台' : '初始化管理员' }}
        </h2>

        <div class="form-fields">
          <div class="field">
            <label for="username">管理员账号</label>
            <AppInput
              id="username"
              v-model="username"
              autocomplete="username"
              required
            />
          </div>

          <div class="field">
            <label for="password">密码</label>
            <AppInput
              id="password"
              v-model="password"
              type="password"
              :autocomplete="auth.initialized ? 'current-password' : 'new-password'"
              minlength="12"
              required
            />
          </div>

          <div v-if="!auth.initialized" class="field">
            <label for="confirm">确认密码</label>
            <AppInput
              id="confirm"
              v-model="confirmPassword"
              type="password"
              autocomplete="new-password"
              required
            />
          </div>
        </div>

        <AppAlert v-if="error" variant="danger" class="error-alert">
          {{ error }}
        </AppAlert>

        <div class="form-actions">
          <AppButton type="submit" variant="primary" :loading="busy" class="submit-btn">
            {{ auth.initialized ? '登录' : '创建并登录' }}
          </AppButton>
        </div>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(320px, 0.85fr) minmax(420px, 1.15fr);
  background-color: var(--color-neutral-900);
}

.login-art {
  color: white;
  padding: 7vw;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  position: relative;
  overflow: hidden;
}

.login-art::after {
  content: 'N';
  position: absolute;
  right: -0.1em;
  bottom: -0.38em;
  font: 500 45vw var(--font-mono);
  color: rgba(255, 255, 255, 0.02);
  user-select: none;
  pointer-events: none;
}

.logo-area {
  z-index: 1;
}

.brand-logo {
  height: 48px;
  width: auto;
}

.art-content {
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.art-eyebrow {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: 0.14em;
  color: var(--action-primary);
  margin: 0;
}

.art-title {
  font-size: clamp(36px, 5vw, 64px);
  font-weight: 700;
  line-height: 1.05;
  margin: 0;
  max-width: 650px;
}

.art-desc {
  color: #929b92;
  max-width: 440px;
  line-height: var(--leading-relaxed);
  margin: 0;
  font-size: var(--text-md);
}

.art-footer {
  z-index: 1;
  color: #71756d;
}

.login-form-wrap {
  background-color: var(--surface-panel);
  display: grid;
  place-items: center;
  padding: var(--space-8);
  clip-path: polygon(7% 0, 100% 0, 100% 100%, 0 100%);
}

.login-form {
  width: min(390px, 100%);
}

.form-eyebrow {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: 0.14em;
  color: var(--text-secondary);
  margin: 0 0 var(--space-2) 0;
}

.form-title {
  font-size: var(--text-3xl);
  font-weight: 700;
  margin: 0 0 var(--space-6) 0;
}

.form-fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
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

.error-alert {
  margin-top: var(--space-4);
}

.form-actions {
  margin-top: var(--space-5);
}

.submit-btn {
  width: 100%;
}

@media (max-width: 760px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .login-art {
    display: none;
  }
  .login-form-wrap {
    clip-path: none;
    min-height: 100vh;
    padding: var(--space-6);
  }
}
</style>
