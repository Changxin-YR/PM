<script lang="ts">
export type RationaleCheck = {
  kind: 'conflict' | 'consistent' | 'unknown'
  selectedAnchorCode: string
  findings: {criterion: string; label: string; kind: 'conflict' | 'supports';
    rationaleQuote: string; sourceRef: string; sourceQuote: string}[]
}
</script>

<script setup lang="ts">
defineProps<{check: RationaleCheck; anchor?: string}>()
const labels = {conflict: '理由与锚点存在明确冲突', consistent: '已核对的立项材料支持本项锚点', unknown: '无法判断，请人工核对'}
</script>

<template>
  <div class="rationale-check" :data-state="check.kind" aria-live="polite">
    <strong>{{ labels[check.kind] }}</strong>
    <p v-if="anchor">所选锚点 {{ anchor }}</p>
    <ul v-if="check.findings.length">
      <li v-for="finding in check.findings" :key="finding.criterion">
        <span>{{ finding.kind === 'conflict' ? '冲突' : '支持' }} · {{ finding.label }}</span>
        <p>理由原话「{{ finding.rationaleQuote }}」</p>
        <p>材料原话「{{ finding.sourceQuote }}」</p>
        <code>{{ finding.sourceRef }}</code>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.rationale-check{font-size:13px;line-height:1.5;background:var(--pmo-bg);border:1px solid var(--pmo-line);
  border-inline-start:3px solid var(--el-color-info);padding:8px 12px;border-radius:5px;overflow-wrap:anywhere}
.rationale-check[data-state=conflict]{border-inline-start-color:var(--el-color-warning)}
.rationale-check[data-state=consistent]{border-inline-start-color:var(--el-color-success)}
.rationale-check p{margin:3px 0}
.rationale-check ul{margin:6px 0 0;padding-left:18px}
.rationale-check li+li{margin-top:6px}
</style>
