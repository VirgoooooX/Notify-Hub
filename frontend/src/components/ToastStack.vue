<script setup lang="ts">import{useUiStore}from'@/stores/ui';const ui=useUiStore()</script>
<template>
  <div class="toast-stack" aria-live="polite">
    <button v-for="item in ui.toasts" :key="item.id" class="toast" :class="`toast--${item.tone}`" @click="ui.remove(item.id)">
      {{ item.message }}
    </button>
  </div>
</template>
