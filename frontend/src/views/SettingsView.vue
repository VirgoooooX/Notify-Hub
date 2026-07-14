<script setup lang="ts">import{onMounted,reactive,ref}from'vue';import{api}from'@/lib/api';import PageHeader from'@/components/PageHeader.vue';import StatusBadge from'@/components/StatusBadge.vue';import{useUiStore}from'@/stores/ui';const ui=useUiStore(),busy=ref(false),testing=ref(false),settings=reactive({timezone:'Asia/Shanghai',retention_days:90,version:'0.3.0',wecom:{configured:false,corp_id_configured:false,agent_id_configured:false,secret_configured:false,callback_token_configured:false,aes_key_configured:false,api_base_url:'https://qyapi.weixin.qq.com',using_proxy:false}}),test=reactive({recipient_id:'',message_type:'text'});onMounted(async()=>{try{Object.assign(settings,await api.get('/admin/settings'))}catch(e){ui.toast(e instanceof Error?e.message:'设置加载失败','danger')}});async function save(){busy.value=true;try{await api.patch('/admin/settings',{timezone:settings.timezone,retention_days:settings.retention_days});ui.toast('平台设置已保存','success')}catch(e){ui.toast(e instanceof Error?e.message:'保存失败','danger')}finally{busy.value=false}}async function sendTest(){testing.value=true;try{await api.post('/admin/channels/wecom/test',test);ui.toast('测试消息已进入正常投递队列','success')}catch(e){ui.toast(e instanceof Error?e.message:'测试失败','danger')}finally{testing.value=false}}</script>
<template>
  <PageHeader title="系统设置" description="企业微信凭据由只读 Secret 文件或环境变量注入，管理端仅显示配置状态。">
    <span class="mono muted">VERSION {{ settings.version }}</span>
  </PageHeader><section class="grid detail-grid">
    <form class="panel" @submit.prevent="save">
      <div class="panel-title">
        <h2>平台设置</h2>
      </div><div class="field">
        <label>默认时区</label><input v-model="settings.timezone" class="input">
      </div><div class="field" style="margin-top:14px">
        <label>历史保留天数</label><input v-model.number="settings.retention_days" class="input" type="number" min="7" max="3650">
      </div><div class="panel-title" style="margin-top:28px">
        <h2>企业微信凭据</h2><StatusBadge :status="settings.wecom.configured?'active':'disabled'" />
      </div><div class="warning-box">
        为避免运行时配置与实际渠道状态分叉，凭据只能通过部署目录的只读 Secret 文件或环境变量更新，并在重启后生效。
      </div><div class="definition-list" style="margin-top:12px">
        <dt>Corp ID</dt><dd>{{ settings.wecom.corp_id_configured?'已配置':'未配置' }}</dd>
        <dt>Agent ID</dt><dd>{{ settings.wecom.agent_id_configured?'已配置':'未配置' }}</dd>
        <dt>应用 Secret</dt><dd>{{ settings.wecom.secret_configured?'已配置':'未配置' }}</dd>
        <dt>回调 Token / AES Key</dt><dd>{{ settings.wecom.callback_token_configured&&settings.wecom.aes_key_configured?'已配置':'未完整配置' }}</dd>
        <dt>API 地址</dt><dd class="mono">
          {{ settings.wecom.api_base_url }}
        </dd>
        <dt>代理模式</dt><dd>
          {{ settings.wecom.using_proxy?'自定义 HTTPS 代理':'企业微信官方直连' }}
        </dd>
      </div><button class="btn btn--primary" :disabled="busy" style="margin-top:18px">
        {{ busy?'保存中…':'保存设置' }}
      </button>
    </form><article class="panel">
      <div class="panel-title">
        <h2>发送连通性测试</h2><span class="mono muted">NORMAL DELIVERY PATH</span>
      </div><p class="muted">
        测试消息也会创建 system.channel_test 事件，并显示在通知投递历史中。
      </p><form @submit.prevent="sendTest">
        <div class="field">
          <label>内部接收人 ID</label><input v-model="test.recipient_id" class="input" required placeholder="person_vigoss">
        </div><div class="field" style="margin-top:14px">
          <label>消息类型</label><select v-model="test.message_type" class="select">
            <option value="text">
              文本
            </option><option value="article">
              图文
            </option>
          </select>
        </div><button class="btn btn--primary" :disabled="testing" style="margin-top:18px">
          {{ testing?'正在提交…':'发送测试消息' }}
        </button>
      </form><div class="warning-box" style="margin-top:22px">
        图片和语音请通过媒体管理与通知接口验收；这里仅验证文本/图文渠道连通性。
      </div>
    </article>
  </section>
</template>
