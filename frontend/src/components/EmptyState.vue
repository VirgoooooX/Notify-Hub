<script setup lang="ts">import{Inbox}from'lucide-vue-next';defineProps<{title?:string;description?:string}>()</script>
<template>
  <div class="empty">
    <Inbox :size="28" /><strong>{{ title??'暂无记录' }}</strong><span>{{ description??'当前筛选条件下没有数据。' }}</span>
  </div>
</template>
