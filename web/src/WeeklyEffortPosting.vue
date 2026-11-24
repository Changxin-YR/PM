<script setup lang="ts">
import {onUnmounted, ref, watch} from 'vue'
import {FileCheck2, RefreshCw} from 'lucide-vue-next'

type Posting = {postingId: string; status: 'pending'|'verified'; amountSigned: string; sourceRef: string}
type Preview = {
  reportRef: string; declaredPersonDays: string; verifiedPersonDays: string; candidatePersonDays: string
  allowedPeriods: string[]; existing?: Posting; canPropose: boolean
}
const props = defineProps<{reportId: string; revision: number; api: (path: string, options?: RequestInit) => Promise<any>}>()
const preview = ref<Preview>(), period = ref(''), readBusy = ref(false), writeBusy = ref(false)
const dialog = ref(false), error = ref(''), notice = ref('')
const problems: Record<string, string> = {
  WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION: '周报发布依据已变化，请重新打开最新正式周报。',
  WEEKLY_EFFORT_PERIOD_INVALID: '该凭证月份不在本报告周的 UTC 月份范围内。',
  WEEKLY_EFFORT_BALANCE_CHANGED: '已核验人天或候选差额已变化，请刷新后重新确认。',
  WEEKLY_EFFORT_ALREADY_VERIFIED: '该周报版本已有核验凭证。',
  LEDGER_LOCKED: '项目当前阶段的凭证台账为只读。',
}
let sequence = 0, scope = 0
let attempt: {signature: string; key: string} | undefined
function problem(e: unknown) {
  const message = e instanceof Error ? e.message : String(e)
  return Object.entries(problems).find(([code])=>message.includes(code))?.[1]??message
}
async function load() {
  const seq = ++sequence
  readBusy.value = true
  error.value = ''
  try {
    const {data}: {data: Preview} = await props.api(`reports/${props.reportId}/effort-posting`)
    if (seq === sequence) {
      preview.value = data
      if (!data.allowedPeriods.includes(period.value)) period.value = data.allowedPeriods[0]??''
    }
  } catch (e) {
    if (seq === sequence) {
      preview.value = undefined
      error.value = problem(e)
    }
  } finally {
    if (seq === sequence) readBusy.value = false
  }
}
async function confirm() {
  if (!preview.value?.canPropose || !period.value || writeBusy.value) return
  const epoch = scope
  const body = JSON.stringify({period: period.value, expectedAmount: preview.value.candidatePersonDays})
  const signature = `${props.reportId}@${props.revision}/${body}`
  if (attempt?.signature !== signature) attempt = {signature, key: crypto.randomUUID()}
  writeBusy.value = true
  error.value = ''
  try {
    const {data}: {data: Posting} = await props.api(`reports/${props.reportId}/effort-posting`, {
      method: 'POST', headers: {'Idempotency-Key': attempt.key, 'If-Match': String(props.revision)}, body,
    })
    if (epoch === scope) {
      dialog.value = false
      notice.value = `待核验凭证 ${data.amountSigned} 人天已在成本台账登记。`
      attempt = undefined
      void load()
    }
  } catch (e) {
    if (epoch === scope) error.value = problem(e)
  } finally {
    if (epoch === scope) writeBusy.value = false
  }
}
watch([()=>props.reportId, ()=>props.revision], () => {
  ++scope
  attempt = undefined
  preview.value = undefined
  dialog.value = false
  writeBusy.value = false
  notice.value = ''
  void load()
}, {immediate: true})
onUnmounted(() => { ++sequence; ++scope })
</script>

<template>
  <section class="effort-candidate" aria-label="周报凭证候选" :aria-busy="readBusy">
    <div class="candidate-header">
      <h4>周报凭证候选</h4>
      <el-button aria-label="刷新周报凭证" :disabled="readBusy||writeBusy" text @click="load"><RefreshCw :size="15"/></el-button>
    </div>
    <el-alert v-if="error&&!dialog" type="error" :closable="false" :title="error"/>
    <p v-if="notice" role="status">{{notice}}</p>
    <p v-if="readBusy&&!preview">正在读取当周凭证…</p>
    <template v-if="preview">
      <dl class="candidate-amounts">
        <div><dt>申报总量</dt><dd>{{preview.declaredPersonDays}} 人天</dd></div>
        <div><dt>当前已核验净额</dt><dd>{{preview.verifiedPersonDays}} 人天</dd></div>
        <div><dt>可提报差额</dt><dd>{{preview.candidatePersonDays}} 人天</dd></div>
      </dl>
      <p v-if="preview.existing" class="candidate-muted">
        {{preview.existing.status==='verified'?'已核验凭证':'已登记候选'}} · {{preview.existing.amountSigned}} 人天 ·
        <code>cost-posting:{{preview.existing.postingId}}</code>
      </p>
      <p v-else-if="preview.candidatePersonDays==='0.00'" class="candidate-muted">本次申报无需新增凭证候选。</p>
      <el-button v-if="preview.canPropose" :disabled="readBusy" @click="error='';dialog=true">
        <FileCheck2 :size="15"/>提报待核验凭证
      </el-button>
    </template>
  </section>
  <el-dialog v-model="dialog" title="提报周报凭证" width="min(92vw, 500px)" :close-on-click-modal="false"
    :close-on-press-escape="!writeBusy" :show-close="!writeBusy">
    <el-alert v-if="error" type="error" :closable="false" :title="error"/>
    <p v-if="preview">将 {{preview.candidatePersonDays}} 人天提交至凭证台账，由独立核验人员确认。</p>
    <label>凭证归属月份（UTC）
      <select v-model="period" :disabled="writeBusy">
        <option v-for="month in preview?.allowedPeriods??[]" :key="month" :value="month">{{month}}</option>
      </select>
    </label>
    <template #footer>
      <el-button :disabled="writeBusy" @click="dialog=false">取消</el-button>
      <el-button type="primary" :disabled="!period" :loading="writeBusy" @click="confirm">确认提报</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.effort-candidate{margin-top:14px;padding:12px 16px;border:1px solid var(--el-border-color-light);border-radius:8px}
.candidate-header,.candidate-amounts{display:flex;align-items:center;gap:8px 24px;flex-wrap:wrap}
.candidate-header{justify-content:space-between}
.candidate-header h4,.candidate-amounts dd{margin:0}
.candidate-amounts dt,.candidate-muted{color:var(--el-text-color-secondary)}
.candidate-amounts dd{font-weight:600}
.candidate-muted{overflow-wrap:anywhere}
</style>
