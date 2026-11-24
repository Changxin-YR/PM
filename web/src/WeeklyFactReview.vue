<script setup lang="ts">
import {onUnmounted, ref, watch} from 'vue'
import {CalendarDays, GitMerge, RefreshCw} from 'lucide-vue-next'

type Value = string|number|boolean
type Fact = {factId: string; revision: number; subject: string; predicate: string; typedValue: Value; quote: string; sourceRef: string}
type Status = 'same'|'changed'|'current_unverified'|'no_previous_fact'|'scan_limited'|'outside_reporting_week'
type Comparison = {
  status: Status; previousStart: string; previousEnd: string
  previous?: {asOf: string; typedValue: Value; quote: string; factRef: string; reportRef: string; evidenceRef?: string}
}
type Entry = {fact: Fact; comparison: Comparison; canRequestClarification: boolean}
type Page = {items: Entry[]; page: number; pageSize: number; total: number}
const props = defineProps<{reportId: string; refreshVersion: number; api: (path: string, options?: RequestInit) => Promise<any>}>()
const emit = defineEmits<{requested: []}>()
const expanded = ref(false), result = ref<Page>(), busy = ref(false), error = ref(''), notice = ref('')
const target = ref<Entry>(), dialog = ref(false), reason = ref(''), confirmed = ref(false), saving = ref(false), formError = ref('')
const statuses: Record<Status, string> = {
  same: '与上周一致', changed: '与上周相比有变化', current_unverified: '本周来源尚不可核验',
  no_previous_fact: '上周无可读且可核验的同类事实', scan_limited: '上周记录超过查询上限',
  outside_reporting_week: '事实时点超出本报告周',
}
const failures: Record<string, string> = {
  WEEKLY_FACT_REQUIRES_CURRENT_PUBLICATION: '请重新打开本周最新正式发布的周报。',
  WEEKLY_FACT_COMPARISON_CHANGED: '上周对照或依据已变化，请刷新后重新核对。',
  WEEKLY_FACT_CLARIFICATION_UNAVAILABLE: '该事实已有澄清或同一时点存在多条记录，请使用现有澄清流程。',
  WEEKLY_FACT_CONFIRMATION_REQUIRED: '请填写原因并确认转办。',
  ARCHIVED_READ_ONLY: '项目归档后只可查看。',
}
let seq = 0, epoch = 0
let attempt: {signature: string; key: string} | undefined

function problem(e: unknown) {
  const text = e instanceof Error ? e.message : String(e)
  return Object.entries(failures).find(([code])=>text.includes(code))?.[1]??text
}
async function load(page = result.value?.page??1) {
  const current = ++seq
  busy.value = true
  error.value = ''
  try {
    const {data}: {data: Page} = await props.api(`reports/${props.reportId}/facts/weekly-comparison?page=${page}`)
    if (current === seq) result.value = data
  } catch (e) {
    if (current === seq) {
      result.value = undefined
      error.value = problem(e)
    }
  } finally {
    if (current === seq) busy.value = false
  }
}
function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) void load()
}
function begin(entry: Entry) {
  target.value = entry
  reason.value = ''
  confirmed.value = false
  formError.value = ''
  attempt = undefined
  dialog.value = true
}
async function submit() {
  const entry = target.value, previous = entry?.comparison.previous
  if (!entry || !previous || saving.value || !confirmed.value || reason.value.length<5) return
  const stamp = epoch
  const body = JSON.stringify({previousFactRef: previous.factRef, reason: reason.value, confirmed: confirmed.value})
  const signature = `${entry.fact.factId}@${entry.fact.revision}/${body}`
  if (attempt?.signature !== signature) attempt = {signature, key: crypto.randomUUID()}
  saving.value = true
  formError.value = ''
  try {
    await props.api(`facts/${entry.fact.factId}/weekly-clarification`, {
      method: 'POST', headers: {'Idempotency-Key': attempt.key, 'If-Match': String(entry.fact.revision)}, body,
    })
    if (stamp === epoch) {
      dialog.value = false
      notice.value = '已转入 Fact 澄清，并生成项目负责人待办。'
      emit('requested')
    }
  } catch (e) {
    if (stamp === epoch) formError.value = problem(e)
  } finally {
    if (stamp === epoch) saving.value = false
  }
}
watch(() => props.refreshVersion, () => { if (expanded.value) void load() })
onUnmounted(() => { ++seq; ++epoch })
</script>

<template>
  <section class="weekly-facts" aria-label="逐报告跨周 Fact 对照">
    <div class="weekly-bar">
      <el-button text :aria-expanded="expanded" @click="toggle"><CalendarDays :size="15"/>逐报告跨周对照</el-button>
      <el-button v-if="expanded" text aria-label="刷新跨周对照" :disabled="busy||saving" @click="load()"><RefreshCw :size="15"/></el-button>
    </div>
    <div v-if="expanded" v-loading="busy" :aria-busy="busy">
      <p class="muted">对照上周同类事实的可核验来源。发现变化后，可由项目经理决定是否转办澄清。</p>
      <p v-if="notice" role="status">{{notice}}</p>
      <el-alert v-if="error" type="error" :closable="false" :title="error"/>
      <p v-else-if="result&&!result.total" class="muted">本周报暂无 Fact 记录。</p>
      <article v-for="row in result?.items" :key="row.fact.factId" class="weekly-row">
        <div class="weekly-bar">
          <strong>{{row.fact.subject}} / {{row.fact.predicate}}</strong>
          <el-tag size="small" :type="row.comparison.status==='same'?'success':row.comparison.status==='changed'?'warning':'info'">
            {{statuses[row.comparison.status]}}
          </el-tag>
        </div>
        <p class="muted">上周 {{row.comparison.previousStart}} 至 {{row.comparison.previousEnd}}（北京时间）</p>
        <dl v-if="row.comparison.previous" class="sources">
          <div>
            <dt>本周 · {{row.fact.typedValue}}</dt><dd>{{row.fact.quote}}</dd>
            <dd><code>fact:{{row.fact.factId}}@{{row.fact.revision}}</code> · <code>{{row.fact.sourceRef}}</code></dd>
          </div>
          <div>
            <dt>上周 · {{row.comparison.previous.typedValue}}</dt><dd>{{row.comparison.previous.quote}}</dd>
            <dd><code>{{row.comparison.previous.factRef}}</code> · <code>{{row.comparison.previous.reportRef}}</code></dd>
            <dd v-if="row.comparison.previous.evidenceRef"><code>{{row.comparison.previous.evidenceRef}}</code></dd>
          </div>
        </dl>
        <el-button v-if="row.canRequestClarification" size="small" :disabled="busy||saving" @click="begin(row)">
          <GitMerge :size="15"/>转入澄清
        </el-button>
      </article>
      <el-pagination v-if="result&&result.total>result.pageSize" :current-page="result.page" :page-size="result.pageSize"
        :total="result.total" :disabled="busy||saving" layout="prev,pager,next" @current-change="load"/>
    </div>
  </section>
  <el-dialog v-model="dialog" title="转入 Fact 澄清" width="min(92vw, 520px)" :close-on-click-modal="false"
    :close-on-press-escape="!saving" :show-close="!saving">
    <form id="weekly-fact-clarification" class="clarification-form" @submit.prevent="submit">
      <el-alert v-if="formError" type="error" :closable="false" :title="formError"/>
      <strong v-if="target">{{target.fact.subject}} / {{target.fact.predicate}}</strong>
      <label>澄清原因<textarea v-model.trim="reason" required minlength="5" maxlength="2000" rows="3" :disabled="saving"/></label>
      <label class="approval"><input v-model="confirmed" type="checkbox" required :disabled="saving"/>确认将本周事实转入人工澄清</label>
    </form>
    <template #footer>
      <el-button :disabled="saving" @click="dialog=false">取消</el-button>
      <el-button type="primary" native-type="submit" form="weekly-fact-clarification"
        :disabled="!confirmed||reason.length<5" :loading="saving">确认转办</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.weekly-facts{border:1px solid var(--pmo-line);padding:12px;border-radius:8px}
.weekly-bar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.weekly-row{margin:8px 0;padding:12px;border:1px solid var(--pmo-line);border-radius:8px;overflow-wrap:anywhere}
.weekly-row dt{font-weight:600}
.weekly-row dd{margin:4px 0 0;white-space:pre-wrap}
.muted{color:var(--el-text-color-secondary)}
.sources{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(100%, 230px),1fr))}
.clarification-form,.clarification-form label{display:grid;gap:8px}
.clarification-form .approval{display:flex;align-items:center}
</style>
