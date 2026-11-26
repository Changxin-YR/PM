<script setup lang="ts">
import {onUnmounted, ref, watch} from 'vue'
import {FileText, RefreshCw} from 'lucide-vue-next'

type Project = {projectId: string; code: string; name: string}
type Entry = {
  projectId: string; projectCode: string; projectName: string
  status: 'published' | 'no_readable_publication' | 'source_unavailable'
  reportId?: string; versionNo?: number; publishedAt?: string; sourceRef?: string
  milestoneClaims?: number; requirementClaims?: number; riskClaims?: number; declaredPersonDays?: string
}
type WeekPage = {weekStart: string; weekEnd: string; period: string; asOf: string; page: number; pageSize: number; total: number; items: Entry[]}
const props = withDefaults(defineProps<{api: (path: string) => Promise<any>; active?: boolean}>(), {active: true})
const emit = defineEmits<{ 'open-report': [projectId: string, reportId: string] }>()
const week = ref(''), project = ref(''), result = ref<WeekPage>(), busy = ref(false), error = ref('')
const projects = ref<Project[]>([]), searching = ref(false), searchError = ref('')
const states = {published: '正式发布', no_readable_publication: '未见可读的正式周报', source_unavailable: '发布依据不可用'}
const formatTime = (iso: string) => new Date(iso).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false})
let sequence = 0, searchSequence = 0

async function load(page = 1, preserveCutoff = false) {
  const current = ++sequence
  const params = new URLSearchParams({page: String(page)})
  if (week.value) params.set('weekStart', week.value)
  if (project.value) params.set('projectId', project.value)
  if (preserveCutoff && result.value) params.set('asOf', result.value.asOf)
  if (!preserveCutoff) result.value = undefined
  busy.value = true
  error.value = ''
  try {
    const {data}: {data: WeekPage} = await props.api(`analytics/weekly-reports?${params}`)
    if (current === sequence) {
      result.value = data
      week.value = data.weekStart
    }
  } catch (e) {
    if (current === sequence) {
      result.value = undefined
      error.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    if (current === sequence) busy.value = false
  }
}

async function findProjects(keyword = '') {
  const current = ++searchSequence
  const params = new URLSearchParams({pageSize: '50', 'filters[keyword]': keyword.trim()})
  searching.value = true
  searchError.value = ''
  try {
    const {data}: {data: {items: Project[]}} = await props.api(`projects?${params}`)
    if (current === searchSequence) {
      const selected = projects.value.find(item => item.projectId === project.value)
      projects.value = selected && !data.items.some(item => item.projectId === selected.projectId)
        ? [selected, ...data.items] : data.items
    }
  } catch (e) {
    if (current === searchSequence) searchError.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (current === searchSequence) searching.value = false
  }
}

watch(() => props.active, active => {
  if (active) {
    void load()
    void findProjects()
  }
}, {immediate: true})
onUnmounted(() => { ++sequence; ++searchSequence })
</script>

<template>
  <section aria-label="组合周报" :aria-busy="busy">
    <div class="toolbar filters">
      <label>周起始日（周一）<input v-model="week" type="date" @change="load()"/></label>
      <el-select v-model="project" aria-label="筛选项目" placeholder="全部授权项目" class="project-filter"
        filterable remote clearable :remote-method="findProjects" :loading="searching" @change="load()">
        <el-option v-for="item in projects" :key="item.projectId" :value="item.projectId" :label="`${item.code} · ${item.name}`"/>
      </el-select>
      <el-button :disabled="busy" @click="week=''; load()">本周</el-button>
      <el-button :disabled="busy" aria-label="刷新组合周报" title="刷新组合周报" @click="load(result?.page)">
        <RefreshCw :size="15"/>
      </el-button>
    </div>
    <el-alert v-if="searchError" type="warning" :closable="false" :title="searchError"/>
    <el-alert v-if="error" type="error" :closable="false" :title="error"/>
    <div v-else v-loading="busy">
      <p v-if="result" class="period">
        <strong>{{result.weekStart}} 至 {{result.weekEnd}}</strong>
        <span>北京时间 · {{result.period}} · {{result.total}} 个授权项目</span>
        <span>查询时点 <time :datetime="result.asOf">{{formatTime(result.asOf)}}</time></span>
      </p>
      <el-table class="portfolio-table" :data="result?.items??[]" row-key="projectId"
        :empty-text="busy?'正在读取…':'该范围内没有授权项目'">
        <el-table-column label="项目" min-width="220"><template #default="{row}">
          <strong>{{row.projectName}}</strong><small class="support">{{row.projectCode}}</small>
        </template></el-table-column>
        <el-table-column label="周报状态" min-width="187"><template #default="{row}">
          <el-tag :type="row.status==='published'?'success':row.status==='source_unavailable'?'warning':'info'">
            {{states[row.status as Entry['status']]}}
          </el-tag>
          <small v-if="row.publishedAt" class="support">第 {{row.versionNo}} 版 · {{formatTime(row.publishedAt)}}</small>
        </template></el-table-column>
        <el-table-column label="里程碑 / 需求 / 风险申报" min-width="185"><template #default="{row}">
          {{row.status==='published'?`${row.milestoneClaims} / ${row.requirementClaims} / ${row.riskClaims}`:'—'}}
        </template></el-table-column>
        <el-table-column label="申报人天" min-width="104" align="right"><template #default="{row}">{{row.declaredPersonDays??'—'}}</template></el-table-column>
        <el-table-column label="来源" min-width="128"><template #default="{row}">
          <el-button v-if="row.reportId" text type="primary" :title="row.sourceRef" @click="emit('open-report', row.projectId, row.reportId)">
            <FileText :size="15"/>查看周报
          </el-button><span v-else>—</span>
        </template></el-table-column>
      </el-table>
      <div class="mobile-portfolio">
        <ul v-if="result?.items.length" class="portfolio-cards">
          <li v-for="row in result.items" :key="row.projectId">
            <div class="card-heading">
              <div><strong>{{row.projectName}}</strong><small class="support">{{row.projectCode}}</small></div>
              <el-tag :type="row.status==='published'?'success':row.status==='source_unavailable'?'warning':'info'">
                {{states[row.status]}}
              </el-tag>
            </div>
            <template v-if="row.reportId">
              <small v-if="row.publishedAt" class="support">第 {{row.versionNo}} 版 · {{formatTime(row.publishedAt)}}</small>
              <dl class="card-counts">
                <div><dt>里程碑申报</dt><dd>{{row.milestoneClaims}}</dd></div>
                <div><dt>需求申报</dt><dd>{{row.requirementClaims}}</dd></div>
                <div><dt>风险申报</dt><dd>{{row.riskClaims}}</dd></div>
                <div><dt>申报人天</dt><dd>{{row.declaredPersonDays}}</dd></div>
              </dl>
              <el-button text type="primary" :title="row.sourceRef" @click="emit('open-report', row.projectId, row.reportId)">
                <FileText :size="15"/>查看周报
              </el-button>
            </template>
          </li>
        </ul>
        <el-empty v-else-if="!busy" description="该范围内没有授权项目" :image-size="58"/>
      </div>
      <el-pagination v-if="result&&result.total>result.pageSize" :current-page="result.page" :total="result.total"
        :page-size="result.pageSize" :disabled="busy" layout="prev,pager,next" @current-change="(n:number)=>load(n,true)"/>
    </div>
  </section>
</template>

<style scoped>
.filters{align-items:end;flex-wrap:wrap;gap:8px}
.project-filter{width:min(100%,270px)}
.period{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px;margin:8px 0;font-size:13px}
.period span,.support{color:var(--el-text-color-secondary)}
.support{display:block;margin-top:3px}
.mobile-portfolio{display:none}
@media(max-width:700px){
  .portfolio-table{display:none}
  .mobile-portfolio{display:block}
  .portfolio-cards{display:grid;gap:8px;margin:0;padding:0;list-style:none}
  .portfolio-cards li{padding:12px;border:1px solid var(--pmo-line);border-radius:var(--pmo-radius-panel);overflow-wrap:anywhere}
  .card-heading,.card-counts{display:flex;align-items:center;justify-content:space-between;gap:8px 12px;flex-wrap:wrap}
  .card-counts{font-size:12px}
  .card-counts dt{color:var(--el-text-color-secondary)}
  .card-counts dd{margin:2px 0 0;font-size:14px;font-weight:600}
}
</style>
