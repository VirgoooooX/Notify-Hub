<script setup lang="ts">defineProps<{page:number;pageSize:number;total:number}>();const emit=defineEmits<{change:[page:number]}>()</script>
<template>
  <footer class="pagination">
    <span>共 {{ total }} 条 · 第 {{ page }} / {{ Math.max(1,Math.ceil(total/pageSize)) }} 页</span><div>
      <button class="btn btn--ghost btn--small" :disabled="page<=1" @click="emit('change',page-1)">
        上一页
      </button><button class="btn btn--ghost btn--small" :disabled="page*pageSize>=total" @click="emit('change',page+1)">
        下一页
      </button>
    </div>
  </footer>
</template>
