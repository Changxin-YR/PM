<script lang="ts">
export type RequirementExceptionItem={requirementId:string;revision:number;baselineVersion:number;requirementHash:string;deviation:string}
export type RequirementException={exceptionId:string;revision:number;signedBy:string;signedAt:string;expiresAt:string;
  verifiedBy:string;evidenceRef:string;evidenceSha256:string;receiptHash:string;items:RequirementExceptionItem[]}
</script>

<script setup lang="ts">
defineProps<{receipt:RequirementException;title:string}>()
</script>

<template>
  <details open class="receipt">
    <summary>{{title}}</summary>
    <ul>
      <li v-for="item in receipt.items" :key="item.requirementId">
        <p>{{item.deviation}}</p>
        <code>{{item.requirementId}} · 需求版本 {{item.revision}} · 基线 v{{item.baselineVersion}}</code>
      </li>
    </ul>
    <dl>
      <dt>客户代表</dt><dd>{{receipt.signedBy}} · {{new Date(receipt.signedAt).toLocaleString('zh-CN')}}</dd>
      <dt>有效截至</dt><dd>{{new Date(receipt.expiresAt).toLocaleString('zh-CN')}}</dd>
      <dt>原件核验</dt><dd>{{receipt.verifiedBy}} · <a :href="`/api/v1/evidence/${receipt.evidenceRef.slice(9)}?download=true`" download>下载核验原件</a></dd>
      <dt>原件 SHA-256</dt><dd><code>{{receipt.evidenceSha256}}</code></dd>
      <dt>回执 SHA-256</dt><dd><code>{{receipt.receiptHash}}</code></dd>
    </dl>
  </details>
</template>

<style scoped>
.receipt{padding:.6rem 0;border-block:1px solid var(--el-border-color);overflow-wrap:anywhere}
summary{font-weight:600;cursor:pointer}
ul{padding-left:1.25rem}
li p{margin:.5rem 0 .2rem;white-space:pre-wrap}
dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:.3rem .7rem;margin:.65rem 0 .15rem;font-size:.88rem}
dt,code{color:var(--el-text-color-secondary)}
dd{margin:0}
a{color:var(--el-color-primary)}
</style>
