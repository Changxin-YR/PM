<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Download,FileCheck2,FileImage,FileText,Flag,RefreshCw,Upload} from 'lucide-vue-next'
type Proof={evidenceId:string;evidenceRef:string;originalName:string;mimeType:string;sizeBytes:number;sha256:string;revision:number;status:string;canVerify:boolean;description:string;linkedResourceType:string;linkedResourceId:string}
type Milestone={milestoneId:string;name:string;revision:number;actualDate?:string}
const props=defineProps<{projectId:string;stage:string;api:(p:string,o?:RequestInit)=>Promise<any>}>()
const path=`projects/${props.projectId}/evidence`,items=ref<Proof[]>([]),milestones=ref<Milestone[]>([]),canComplete=ref(false),rev=ref(1),page=ref(1),total=ref(0),busy=ref(false),error=ref(''),notice=ref('')
const kinds:Record<string,string>={milestone:'里程碑',requirement:'需求',charter:'立项卡',review:'评审',risk:'风险',report:'报告',cost_posting:'成本凭证',decision:'裁决',project:'项目成本来源'}
const originalExtensions=['.pdf','.png','.jpg','.jpeg','.docx'],sourceLimit=16*1024
const mode=ref<''|'upload'|'verify'|'complete'>(''),chosen=ref<Proof|null>(null),file=ref<File|null>(null),kind=ref('milestone'),target=ref(''),note=ref(''),day=ref(''),checked=ref(false)
let key='',signature='',expected=0
async function load(n=page.value){const [list,ms]=await Promise.all([props.api(`${path}?page=${n}`),props.api(`projects/${props.projectId}/milestones`)]);items.value=list.data.items;rev.value=list.data.collectionRevision;page.value=n;total.value=list.data.total;milestones.value=ms.data.items;canComplete.value=ms.data.canForecast&&['executing','delivering'].includes(props.stage)}
async function refresh(n=page.value){busy.value=true;error.value='';try{await load(n)}catch(e){items.value=[];canComplete.value=false;error.value=String(e)}finally{busy.value=false}}
function milestone(r:Proof){return r.linkedResourceType==='milestone'?milestones.value.find(m=>m.milestoneId===r.linkedResourceId):undefined}
function begin(action:'upload'|'verify'|'complete',r:Proof|null=null){mode.value=action;chosen.value=r;note.value='';day.value='';kind.value='milestone';target.value='';checked.value=false;file.value=null;key='';error.value='';notice.value='';expected=action==='upload'?rev.value:action==='verify'?r!.revision:milestone(r!)!.revision}
function changeKind(){target.value='';file.value=null;key='';error.value=''}
function selectFile(ev:Event){file.value=(ev.target as HTMLInputElement).files?.[0]??null;key=''}
async function save(){busy.value=true;error.value='';try{let url:string,body:FormData|string,sig:string;const r=chosen.value
 if(mode.value==='upload'){
   const upload=file.value,projectSource=kind.value==='project',resourceId=projectSource?props.projectId:target.value
   if(!upload||upload.size===0||upload.size>(projectSource?sourceLimit:20*1024*1024))throw Error(projectSource?'成本来源 JSON 必须大于零且不超过 16 KiB。':'文件必须大于零且不超过 20 MiB。')
   const name=upload.name.toLowerCase()
   if(projectSource&&!name.endsWith('.json'))throw Error('项目成本来源只接受 JSON 文件。')
   if(!projectSource&&(upload.type==='application/json'||!originalExtensions.some(ext=>name.endsWith(ext))))throw Error('此关联类型只接受 PDF、图片或 DOCX 文件。')
   url=path;body=new FormData()
   body.append('file',projectSource?upload.slice(0,upload.size,'application/json'):upload,upload.name)
   body.append('linkedResourceType',kind.value);body.append('linkedResourceId',resourceId);body.append('description',note.value)
   sig=JSON.stringify([kind.value,resourceId,note.value,upload.name,upload.size])
 }
 else if(mode.value==='verify'&&r){if(!checked.value)throw Error('请先核对原文件。');url=`evidence/${r.evidenceId}/verify`;body=JSON.stringify({reason:note.value.trim(),sha256:r.sha256});sig=body}
 else if(mode.value==='complete'&&r){url=`milestones/${r.linkedResourceId}/complete`;body=JSON.stringify({actualDate:day.value,evidenceRef:r.evidenceRef,...(note.value.trim()?{reason:note.value.trim()}:{})});sig=body}else return
 sig=url+expected+sig;if(!key||signature!==sig){key=crypto.randomUUID();signature=sig}
 await props.api(url,{method:'POST',headers:{'Idempotency-Key':key,'If-Match':String(expected)},body});notice.value=mode.value==='complete'?'里程碑已完成':mode.value==='verify'?'证据已核验':'附件已上传';mode.value='';await load(1)
 }catch(e){error.value=String(e)}finally{busy.value=false}}
onMounted(()=>refresh(1))
</script>
<template><section class="panel evidence-panel">
<div class="toolbar"><strong>证据附件 · {{total}}</strong><el-button :disabled="busy||!!mode||stage==='archived'" @click="begin('upload')"><Upload :size="15"/>上传附件</el-button><el-button title="刷新附件" aria-label="刷新附件" :disabled="busy||!!mode" @click="refresh()"><RefreshCw :size="15"/></el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/><span role="status">{{notice}}</span>
<form v-if="mode" @submit.prevent="save"><template v-if="mode==='upload'">
  <label>关联类型<select v-model="kind" :disabled="busy" @change="changeKind" required><option v-for="(label,k) in kinds" :key="k" :value="k">{{label}}</option></select></label>
  <label v-if="kind==='milestone'">关联里程碑<select v-model="target" required><option value="" disabled>请选择</option><option v-for="m in milestones" :value="m.milestoneId" :key="m.milestoneId">{{m.name}}</option></select></label>
  <label v-else-if="kind==='project'">关联项目<input :value="projectId" readonly/></label>
  <label v-else>关联资源编号<input v-model.trim="target" required></label>
  <label>{{kind==='project'?'成本来源 JSON':'证据文件'}}<input :key="kind" type="file" required :disabled="busy" :accept="kind==='project'?'.json,application/json':originalExtensions.join(',')" @change="selectFile"></label>
  <label>附件说明<textarea v-model="note" maxlength="500"/></label>
</template>
<template v-else-if="mode==='verify'&&chosen"><a :href="`/api/v1/evidence/${chosen.evidenceId}?download=true`" download><Download :size="15"/>下载核对原文件</a><code>{{chosen.sha256}}</code><label>核验说明<textarea v-model="note" required minlength="4" maxlength="1000"/></label><label class="flag"><input type="checkbox" v-model="checked" required>已核对原文件与关联资料</label></template>
<template v-else-if="chosen"><p>{{milestone(chosen)?.name}} · {{chosen.originalName}}</p><label>实际完成日期<input type="date" v-model="day" required></label><label>完成说明<textarea v-model="note" minlength="4" maxlength="1000"/></label></template><div class="actions"><el-button :disabled="busy" @click="mode=''">取消</el-button><el-button native-type="submit" type="primary" :loading="busy">{{mode==='upload'?'提交附件':mode==='verify'?'确认核验附件':'确认里程碑完成'}}</el-button></div></form>
<el-table :data="items" v-loading="busy" row-key="evidenceId" empty-text="暂无证据附件">
  <el-table-column type="expand" width="44">
    <template #default="{row}">
      <dl class="evidence-detail"><dt>业务引用</dt><dd>{{row.evidenceRef}}</dd><dt>SHA-256</dt><dd>{{row.sha256}}</dd><dt>资源编号</dt><dd>{{row.linkedResourceId}}</dd><dt>说明</dt><dd>{{row.description||'—'}}</dd></dl>
    </template>
  </el-table-column>
  <el-table-column label="文件" min-width="210">
    <template #default="{row}"><span class="file-name"><component :is="row.mimeType?.startsWith('image/')?FileImage:FileText" :size="17" aria-hidden="true"/>{{row.originalName}}</span></template>
  </el-table-column>
  <el-table-column label="关联资料" min-width="132"><template #default="{row}">{{milestone(row)?.name??kinds[row.linkedResourceType]}}</template></el-table-column>
  <el-table-column label="状态" width="108">
    <template #default="{row}"><el-tag size="small" :type="row.status==='verified'?'success':'info'">{{row.status==='verified'?'已核验':'待核验'}}</el-tag></template>
  </el-table-column>
  <el-table-column label="操作" width="153"><template #default="{row}">
    <div class="evidence-actions">
      <a :href="`/api/v1/evidence/${row.evidenceId}?download=true`" download title="下载原文件" aria-label="下载原文件"><Download :size="16"/>下载</a>
      <el-button v-if="row.canVerify" text aria-label="核验附件" title="核验附件" :disabled="!!mode||busy||stage==='archived'" @click="begin('verify',row)"><FileCheck2 :size="16"/></el-button>
      <el-button v-if="canComplete&&row.status==='verified'&&milestone(row)&&!milestone(row)?.actualDate" text aria-label="登记里程碑完成" title="登记里程碑完成" :disabled="!!mode||busy" @click="begin('complete',row)"><Flag :size="16"/></el-button>
    </div>
  </template></el-table-column>
</el-table>
<el-pagination v-if="total>20" :disabled="busy||!!mode" :current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="refresh"/>
</section></template>

<style scoped>
.evidence-panel form {display:grid;gap:12px;margin:0 0 16px;padding:16px;background:var(--pmo-bg);border:1px solid var(--pmo-line);border-radius:8px}
.evidence-panel .toolbar {flex-wrap:wrap}
.flag,.file-name,.evidence-actions {display:flex;align-items:center;gap:8px}
.flag input {width:auto}
a {display:inline-flex;align-items:center;gap:6px;color:var(--pmo-primary-hover)}
.file-name svg {color:var(--pmo-primary-hover);flex:none}
.evidence-actions {gap:4px;flex-wrap:wrap}
.evidence-actions :deep(.el-button) {padding-inline:8px}
code,dd,.file-name {overflow-wrap:anywhere}
.evidence-detail {display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 16px;margin:0;padding:12px 20px}
.evidence-detail dt {color:var(--pmo-secondary)}
.evidence-detail dd {margin:0;white-space:pre-wrap}
</style>
