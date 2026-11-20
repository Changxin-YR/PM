<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {RefreshCw,Send} from 'lucide-vue-next'

type Gate='charter'|'feasibility'|'closing'
type Draft={gateCheck:{gate:Gate;eligible:boolean;missing:{code:string;message:string;sourceRefs:string[]}[]};draftMaterials:string[]}
type Review={reviewId:string;type:Gate;status:'draft'|'pending'|'decided'}
type Api=(path:string,options?:RequestInit,receiveHeaders?:(headers:Headers)=>void)=>Promise<any>
const props=defineProps<{projectId:string;stageLabel:string;api:Api}>()
const emit=defineEmits<{stageRead:[projectId:string,stage:string,revision:number]}>()
const gates:Record<string,Gate>={drafting:'charter',designing:'feasibility',delivering:'closing'}
const labels={charter:'立项',feasibility:'落地',closing:'结项'}
const statuses={draft:'草稿',pending:'待评审',decided:'已裁决'}
const busy=ref(false),confirm=ref(false),error=ref(''),notice=ref(''),allowed=ref(false)
const draft=ref<Draft|null>(null),items=ref<Review[]>([]),total=ref(0),revision=ref('')
const gate=computed(()=>draft.value?.gateCheck.gate)
let attempt:{gate:Gate;revision:string;key:string}|null=null
const path=`projects/${props.projectId}`

async function read(){
  draft.value=null;allowed.value=false;revision.value=''
  const {project}=(await props.api(path)).data
  emit('stageRead',props.projectId,project.stage,project.revision)
  const {data:history}=await props.api(`${path}/reviews`)
  items.value=history.items;total.value=history.total
  const next=gates[project.stage]
  if(next){
    const {data}=await props.api(`${path}/reviews/prepare?gate=${next}`,{},headers=>{
      revision.value=headers.get('X-PMO-Project-Revision')||''
      allowed.value=headers.get('X-PMO-Can-Submit')==='true'
    })
    draft.value=data
  }
}

async function refresh(){
  busy.value=true;error.value=''
  try{await read()}catch(e){error.value=e instanceof Error?e.message:'未能读取评审状态。'}
  finally{busy.value=false}
}

async function submit(){
  const type=gate.value,rev=revision.value
  if(busy.value||!type||!rev||!allowed.value||!draft.value?.gateCheck.eligible)return
  if(!attempt||attempt.gate!==type||attempt.revision!==rev)attempt={gate:type,revision:rev,key:crypto.randomUUID()}
  busy.value=true;error.value='';let saved=false
  try{
    await props.api(`${path}/reviews`,{method:'POST',headers:{'If-Match':rev,'Idempotency-Key':attempt.key},body:JSON.stringify({reviewType:type})})
    saved=true;confirm.value=false;attempt=null;notice.value='已提交评审申请。'
    await read()
  }catch(e){error.value=saved?'申请已提交，但未能重读最新阶段。':e instanceof Error?e.message:'未能确认送审结果。'}
  finally{busy.value=false}
}
onMounted(refresh)
</script>

<template>
  <section class="reviews" aria-label="关口送审" v-loading="busy && !confirm">
    <div class="review-actions">
      <h3>当前阶段：{{stageLabel}}</h3>
      <el-button :disabled="busy" @click="refresh"><RefreshCw :size="14"/> 重读阶段与材料</el-button>
    </div>
    <el-alert v-if="notice" :title="notice" type="success" :closable="false" show-icon/>
    <el-alert v-if="error && !confirm" :title="error" type="error" :closable="false" show-icon/>
    <div v-if="gate && draft" class="gate-review">
      <h4>{{labels[gate]}}预检 · {{draft.gateCheck.eligible?'材料齐备':'存在缺口'}}</h4>
      <ul v-if="draft.gateCheck.missing.length" class="gate-gaps">
        <li v-for="(gap,i) in draft.gateCheck.missing" :key="i">{{gap.message}}
          <details v-if="gap.sourceRefs.length"><summary>出处</summary><code v-for="source in gap.sourceRefs" :key="source">{{source}}</code></details>
        </li>
      </ul>
      <details v-if="draft.draftMaterials.length"><summary>预检材料引用</summary><code v-for="source in draft.draftMaterials" :key="source">{{source}}</code></details>
      <el-button v-if="allowed" type="primary" :disabled="busy || !revision" @click="confirm=true"><Send :size="14"/> 提交{{labels[gate]}}评审</el-button>
    </div>
    <h4>评审记录 {{total>items.length?`（最近 ${items.length} / ${total} 条）`:''}}</h4>
    <el-table :data="items" row-key="reviewId" empty-text="暂无评审记录" style="width:100%">
      <el-table-column label="关口" min-width="70"><template #default="{row}">{{labels[row.type as Gate]}}</template></el-table-column>
      <el-table-column label="状态" min-width="84"><template #default="{row}">{{statuses[row.status as Review['status']]}}</template></el-table-column>
      <el-table-column prop="reviewId" label="评审编号" min-width="156" show-overflow-tooltip/>
    </el-table>
  </section>
  <el-dialog v-model="confirm" title="确认送审" width="min(420px,92vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" append-to-body>
    <p>提交{{gate?labels[gate]:''}}评审申请，项目将进入待评审阶段。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <template #footer><el-button :disabled="busy" @click="confirm=false">取消</el-button><el-button type="primary" :loading="busy" @click="submit">确认送审</el-button></template>
  </el-dialog>
</template>

<style scoped>
.review-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.reviews h3,.reviews h4{margin:.75rem 0}
.reviews :deep(.el-alert){margin:.5rem 0}
.gate-review{border-block:1px solid var(--el-border-color);margin:.5rem 0 1rem;padding-bottom:.75rem}
.gate-review>details,.gate-gaps li{margin-bottom:.5rem}
.gate-review code{display:block;overflow-wrap:anywhere;padding:.15rem .4rem}
.gate-gaps{padding-left:1.3rem}
</style>
