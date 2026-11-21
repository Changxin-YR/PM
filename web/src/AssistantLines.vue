<script setup lang="ts">
defineProps<{items: {text:string;sourceRefs:string[];observedAt?:string|null;category?:string;projectId?:string}[]}>()
</script>

<template>
  <ul class="cited-list">
    <li v-for="(item,index) in items" :key="index">
      <el-tag v-if="item.category" size="small" effect="plain">{{item.category}}</el-tag>
      <span>{{item.text}}</span>
      <details v-if="item.sourceRefs.length">
        <summary>来源<span v-if="item.observedAt"> · {{new Date(item.observedAt).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}}</span></summary>
        <code v-for="source in item.sourceRefs" :key="source">{{source}}</code>
      </details>
    </li>
  </ul>
</template>

<style scoped>
.cited-list{list-style:none;margin:0;padding:0}
.cited-list li{padding:7px 0;border-bottom:1px solid var(--el-border-color-lighter);overflow-wrap:anywhere}
.cited-list .el-tag{margin-right:8px}
summary{cursor:pointer;color:var(--el-text-color-secondary);font-size:12px;margin-top:4px}
code{display:block;margin:2px 0;color:var(--el-text-color-secondary);overflow-wrap:anywhere}
</style>
