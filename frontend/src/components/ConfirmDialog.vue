<script setup lang="ts">defineProps<{open:boolean;title:string;description:string;confirmText?:string;danger?:boolean;busy?:boolean}>();const emit=defineEmits<{confirm:[];cancel:[]}>()</script>
<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="emit('cancel')">
      <section class="modal" role="alertdialog" aria-modal="true">
        <p class="eyebrow">
          REQUIRES CONFIRMATION
        </p><h2>{{ title }}</h2><p>{{ description }}</p><footer>
          <button class="btn btn--ghost" :disabled="busy" @click="emit('cancel')">
            取消
          </button><button class="btn" :class="danger?'btn--danger':'btn--primary'" :disabled="busy" @click="emit('confirm')">
            {{ busy?'处理中…':confirmText??'确认' }}
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>