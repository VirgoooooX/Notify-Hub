<script setup lang="ts">import{onMounted,ref}from'vue';import{useRoute,useRouter}from'vue-router';import{useAuthStore}from'@/stores/auth';const auth=useAuthStore(),route=useRoute(),router=useRouter(),username=ref('admin'),password=ref(''),confirmPassword=ref(''),busy=ref(false),error=ref('');onMounted(async()=>{try{await auth.bootstrap()}catch{auth.initialized=true}});async function submit(){if(!auth.initialized&&password.value!==confirmPassword.value){error.value='两次输入的密码不一致';return}busy.value=true;error.value='';try{if(auth.initialized)await auth.login(username.value,password.value);else await auth.initialize(username.value,password.value);await router.push(String(route.query.redirect??'/'))}catch(e){error.value=e instanceof Error?e.message:'无法进入控制台'}finally{busy.value=false}}</script>
<template>
  <main class="login-page">
    <section class="login-art">
      <div><img src="/brand/logo-horizontal-reverse.svg" alt="Notify Hub" class="brand-logo" style="height:48px;"></div><div>
        <p class="eyebrow">
          SELF-HOSTED SIGNAL CONTROL
        </p><h1>每一个信号，<br>都有迹可循。</h1><p>统一接收事件、调度提醒，并追踪每一次企业微信投递。可靠落库，清晰恢复。</p>
      </div><small class="mono">NOTIFY HUB / RELEASE 0.6.0</small>
    </section><section class="login-form-wrap">
      <form class="login-form" @submit.prevent="submit">
        <p class="eyebrow">
          SECURE ADMIN ACCESS
        </p><h2>{{ auth.initialized?'进入控制台':'初始化管理员' }}</h2><div class="field">
          <label for="username">管理员账号</label><input id="username" v-model="username" class="input" autocomplete="username" required>
        </div><div class="field">
          <label for="password">密码</label><input id="password" v-model="password" class="input" type="password" :autocomplete="auth.initialized?'current-password':'new-password'" minlength="12" required>
        </div><div v-if="!auth.initialized" class="field">
          <label for="confirm">确认密码</label><input id="confirm" v-model="confirmPassword" class="input" type="password" autocomplete="new-password" required>
        </div><p v-if="error" class="danger">
          {{ error }}
        </p><button class="btn btn--primary" :disabled="busy">
          {{ busy?'正在验证…':auth.initialized?'登录':'创建并登录' }}
        </button>
      </form>
    </section>
  </main>
</template>
