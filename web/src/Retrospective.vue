<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {FileCheck2,Pencil,Plus,RefreshCw,Save,Search} from 'lucide-vue-next'
import ReviewCalibration,{calibrationLabels,type Calibration,type CalibrationKey,type Deviation} from './ReviewCalibration.vue'

type CostDiscussion={type:'money'|'effort';attribution:'estimate'|'execution'|'scope';sourceRef:string}
type State={projectId:string;stage:string;kind:'normal'|'terminated'|null;fromStage:'closed'|'terminated'|null;
  retrospectiveId:string|null;revision:number;answers:string[];questions:string[];completedAt:string|null;canSave:boolean;
  costDiscussion?:CostDiscussion[];calibrationDeviations?:Partial<Record<CalibrationKey,Deviation>>;calibrationGap?:string}
const attributionLabels={estimate:'估算',execution:'执行',scope:'范围'}
type LessonContent={category:string;what:string;rootCause:string;advice:string}
type Lesson=LessonContent & {lessonId:string;projectId:string;revision:number;status:'draft'|'confirmed';
  createdBy:string;createdAt:string;confirmedBy:string|null;confirmedAt:string|null;canConfirm:boolean}
type LessonList={items:Lesson[];total:number}
type SearchHit=LessonContent & {lessonId:string;projectId:string;sourceRefs:string[];confirmedAt:string}
type SearchPage={items:SearchHit[];page:number;pageSize:number;total:number;asOf:string}
type SearchCriteria={keyword:string;category:string}
type Intent={kind:'answers';revision:number}|{kind:'draft'}|{kind:'confirm';lessonId:string;revision:number}

const props=defineProps<{projectId:string;projectRevision?:number;requestedDraft?:boolean;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{draftOpened:[]}>()
const fields=[{name:'category',label:'教训分类'},{name:'what',label:'发生了什么'},
  {name:'rootCause',label:'根因'},{name:'advice',label:'建议'}] as const
const prompts={answers:{title:'填写项目复盘',submit:'保存复盘',icon:Save},draft:{title:'新增教训草稿',submit:'保存教训草稿',icon:Save},
  confirm:{title:'独立确认教训',submit:'确认教训',icon:FileCheck2}}
const placeholders=new Set(['todo','tbd','n/a','na','none','null','unknown','待补','待填写','待定','未知','无','暂无','不适用','略'])
const state=ref<State|null>(null),lessons=ref<LessonList|null>(null),loading=ref(false),saving=ref(false),dialog=ref(false)
const intent=ref<Intent|null>(null),answers=ref<string[]>([]),draft=ref<LessonContent>({category:'',what:'',rootCause:'',advice:''})
const draftSource=ref<number>()
const notice=ref(''),error=ref(''),formError=ref('')
const keyword=ref(''),category=ref(''),searching=ref(false),searchError=ref(''),hits=ref<SearchPage|null>(null)
const criteria=ref<SearchCriteria|null>(null),pageSize=20
const busy=computed(()=>loading.value||saving.value)
const questions=computed(()=>state.value?.questions??[])
const calibration=ref<Calibration>(),deviations=ref<Partial<Record<CalibrationKey,Deviation>>>({})
const requiredDeviations=computed(()=>{
  if(state.value?.kind!=='normal')return []
  const rows=calibration.value?.dimensions??{}
  return (Object.keys(rows) as CalibrationKey[]).filter(key=>rows[key]?.requiresRetrospective&&rows[key]?.current)
    .map(key=>({key,revision:rows[key]!.current!.revision}))
})
const selectedLesson=computed(()=>{
  const action=intent.value
  return action?.kind==='confirm'?lessons.value?.items.find(item=>item.lessonId===action.lessonId):undefined
})
let attempt:{signature:string;key:string}|undefined

function time(iso:string){return new Date(iso).toLocaleString('zh-CN',{hour12:false})}
function message(e:unknown,fallback:string){return e instanceof TypeError?'无法连接服务，请检查网络。':e instanceof Error?e.message:fallback}
function plain(value:string){return !!value.trim()&&!value.includes('\0')}
function explicit(value:string){return plain(value)&&/[\p{L}\p{N}]/u.test(value)&&!placeholders.has(value.trim().replace(/[.。!！?？]+$/u,'').trim().toLowerCase())}
function base(){return `projects/${props.projectId}`}
async function read(){
  const [retro,list]:[{data:State},{data:LessonList}]=await Promise.all([props.api(`${base()}/retrospective`),props.api(`${base()}/lessons`)])
  state.value=retro.data;lessons.value=list.data
}
async function refresh(){
  if(busy.value)return
  loading.value=true;error.value=''
  try{await read()}catch(e){state.value=null;lessons.value=null;error.value=message(e,'未能读取复盘和教训。')}finally{loading.value=false}
}
onMounted(refresh)
watch(()=>props.projectRevision,()=>void refresh())

function begin(kind:Intent['kind'],lesson?:Lesson,fromAnswers=false){
  const retro=state.value
  if(busy.value||!retro)return
  if(kind==='confirm'){
    if(lesson?.canConfirm!==true||lesson.status!=='draft')return
    intent.value={kind,lessonId:lesson.lessonId,revision:lesson.revision}
  }else{
    if(retro.canSave!==true)return
    if(kind==='answers'){
      intent.value={kind,revision:retro.revision};answers.value=retro.questions.map((_,i)=>retro.answers[i]??'')
      deviations.value=Object.fromEntries(requiredDeviations.value.map(({key,revision})=>{
        const previous=retro.calibrationDeviations?.[key]
        return [key,previous?.resultRevision===revision?{...previous}:{resultRevision:revision,explanation:'',improvement:''}]
      }))
    }
    else{
      if(fromAnswers&&!retro.completedAt)return
      intent.value={kind}
      draftSource.value=fromAnswers?retro.revision:undefined
      draft.value={category:'',what:fromAnswers?(retro.answers[0]??''):'',
        rootCause:fromAnswers?(retro.answers[1]??''):'',advice:fromAnswers?(retro.answers[3]??''):''}
    }
  }
  formError.value='';notice.value='';attempt=undefined;dialog.value=true
}
watch([()=>props.requestedDraft,state,busy],([requested,retro,wait])=>{
  if(!requested||!retro||retro.projectId!==props.projectId||wait)return
  if(!dialog.value&&retro.canSave&&retro.completedAt)begin('draft',undefined,true)
  emit('draftOpened')
},{immediate:true})
const command=computed(()=>{
  const action=intent.value,retro=state.value
  if(!action||!retro)return null
  if(action.kind==='confirm'){
    const item=selectedLesson.value
    if(item?.canConfirm!==true||item.status!=='draft'||item.revision!==action.revision)return null
    return {path:`lessons/${item.lessonId}/confirm`,method:'POST',revision:action.revision,payload:{}}
  }
  // Saving answers and drafting a lesson share the same PM and retrospective-entry checks.
  if(retro.canSave!==true)return null
  if(action.kind==='answers'){
    if(retro.revision!==action.revision||!retro.questions.every((_,i)=>explicit(answers.value[i]??'')))return null
    const rows=requiredDeviations.value
    if(rows.some(({key,revision})=>{
      const row=deviations.value[key]
      return !row||row.resultRevision!==revision||row.explanation.trim().length<4||row.improvement.trim().length<4
    }))return null
    return {path:`${base()}/retrospective`,method:'PUT',revision:action.revision,payload:{answers:answers.value.map(value=>value.trim()),
      ...(rows.length?{calibrationDeviations:deviations.value}:{})}}
  }
  const {category,what,rootCause,advice}=draft.value
  if(![category,what,rootCause,advice].every(plain)||Array.from(category.trim()).length>20)return null
  return {path:`${base()}/lessons`,method:'POST',payload:{category:category.trim(),what:what.trim(),rootCause:rootCause.trim(),advice:advice.trim(),
    ...(draftSource.value===undefined?{}:{sourceRetrospectiveRevision:draftSource.value})}}
})

async function save(){
  const action=intent.value,write=command.value
  if(busy.value||!action||!write)return
  const body=JSON.stringify(write.payload),signature=JSON.stringify([write.path,write.method,write.revision,body])
  if(attempt?.signature!==signature)attempt={signature,key:crypto.randomUUID()}
  const headers:Record<string,string>={'Idempotency-Key':attempt.key}
  if(write.revision!==undefined)headers['If-Match']=String(write.revision)
  saving.value=true;formError.value='';error.value=''
  let saved=false
  try{
    await props.api(write.path,{method:write.method,headers,body})
    saved=true;dialog.value=false;attempt=undefined
    notice.value=action.kind==='answers'?'复盘答复已保存。':action.kind==='draft'?'教训草稿已登记，待独立确认。':'教训已独立确认。'
    await read()
    if(action.kind==='confirm'&&criteria.value)void loadSearch(1,criteria.value)
  }catch(e){
    if(saved){state.value=null;lessons.value=null;error.value='操作已提交，但未能重读复盘和教训。'}
    else formError.value=message(e,'未能确认提交结果。')
  }finally{saving.value=false}
}

async function loadSearch(page:number,query:SearchCriteria){
  if(searching.value)return
  searching.value=true;searchError.value='';criteria.value=query
  try{
    const params=new URLSearchParams({page:String(page),pageSize:String(pageSize)})
    if(query.keyword)params.set('keyword',query.keyword)
    if(query.category)params.set('category',query.category)
    const {data}:{data:SearchPage}=await props.api(`lessons/search?${params}`)
    hits.value=data
  }catch(e){hits.value=null;searchError.value=message(e,'未能检索已确认教训。')}finally{searching.value=false}
}
function search(){void loadSearch(1,{keyword:keyword.value.trim(),category:category.value.trim()})}
function turn(page:number){if(criteria.value)void loadSearch(page,criteria.value)}
</script>

<template>
  <section class="panel retrospective" aria-label="项目复盘与教训" :aria-busy="busy">
    <div class="toolbar section-toolbar">
      <div class="section-heading"><h3>项目复盘</h3>
        <el-tag v-if="state?.kind" size="small" type="info">{{state.kind==='terminated'?'终止复盘':'结项复盘'}}</el-tag>
        <el-tag v-if="state?.stage==='archived'" size="small" type="info">已归档 · 只读</el-tag>
      </div>
      <div class="controls">
        <el-button v-if="state?.canSave" type="primary" :disabled="busy" @click="begin('answers')"><Pencil :size="15"/>{{state.completedAt?'编辑复盘':'填写复盘'}}</el-button>
        <el-button aria-label="刷新复盘和教训" title="刷新复盘和教训" :disabled="busy" @click="refresh"><RefreshCw :size="15"/></el-button>
      </div>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon/>
    <p v-if="notice" role="status" class="notice">{{notice}}</p>
    <p v-if="loading" class="meta">正在读取复盘和教训…</p>
    <template v-if="state">
      <p v-if="state.completedAt" class="meta">复盘版本 {{state.revision}} · 完成于 <time :datetime="state.completedAt">{{time(state.completedAt)}}</time></p>
      <el-alert v-if="state.calibrationGap" type="warning" title="评分结果或其证据已变化，请重核校准并更新复盘。" :closable="false"/>
      <dl v-if="state.answers.length" class="answers">
        <div v-for="(question,index) in questions" :key="question"><dt>{{question}}</dt><dd>{{state.answers[index]||'—'}}</dd></div>
      </dl>
      <p v-else class="meta">{{state.kind?'复盘答复尚未登记。':'暂无复盘记录。'}}</p>
      <ReviewCalibration v-if="state.stage==='closed'||state.kind==='normal'" :project-id="projectId" :stage="state.stage"
        :api="api" :revision="projectRevision" @loaded="calibration=$event" @saved="refresh"/>
      <dl v-if="state.calibrationDeviations" class="answers">
        <div v-for="(row,key) in state.calibrationDeviations" :key="key">
          <dt>{{calibrationLabels[key]}} · 结果版本 {{row?.resultRevision}}</dt>
          <dd>{{row?.explanation}}</dd><dd>{{row?.improvement}}</dd>
        </div>
      </dl>
      <section v-if="state.costDiscussion?.length" class="group" aria-label="结项超支归因">
        <h3>结项超支归因</h3>
        <p class="meta">来自结项审批绑定的正式对账回执，供复盘对照。</p>
        <dl class="answers">
          <div v-for="item in state.costDiscussion" :key="item.type">
            <dt>{{item.type==='money'?'金额':'人天'}}</dt>
            <dd>{{attributionLabels[item.attribution]}} <code>{{item.sourceRef}}</code></dd>
          </div>
        </dl>
      </section>
    </template>

    <section class="group" aria-label="项目教训">
      <div class="section-toolbar"><div class="section-heading"><h3>项目教训</h3><span v-if="lessons" class="meta">{{lessons.total}} 条</span></div>
        <el-button v-if="state?.canSave" :disabled="busy" @click="begin('draft')"><Plus :size="15"/>新增教训草稿</el-button>
        <el-button v-if="state?.canSave&&state.completedAt" :disabled="busy" @click="begin('draft',undefined,true)">从复盘答复起草</el-button>
      </div>
      <p v-if="lessons&&!lessons.items.length" class="meta">暂无项目教训。</p>
      <ul v-if="lessons?.items.length" class="entries">
        <li v-for="item in lessons.items" :key="item.lessonId">
          <div class="section-heading"><strong>{{item.category}}</strong>
            <el-tag size="small" :type="item.status==='confirmed'?'success':'info'">{{item.status==='confirmed'?'已确认':'待独立确认'}}</el-tag>
          </div>
          <dl class="detail-lines"><template v-for="field in fields.slice(1)" :key="field.name"><dt>{{field.label}}</dt><dd>{{item[field.name]}}</dd></template></dl>
          <p class="meta">版本 {{item.revision}} · 登记于 <time :datetime="item.createdAt">{{time(item.createdAt)}}</time></p>
          <p v-if="item.confirmedAt" class="meta">确认人 {{item.confirmedBy}} · <time :datetime="item.confirmedAt">{{time(item.confirmedAt)}}</time></p>
          <el-button v-if="item.canConfirm&&item.status==='draft'" :disabled="busy" @click="begin('confirm',item)"><FileCheck2 :size="15"/>独立确认教训</el-button>
        </li>
      </ul>
    </section>

    <section class="group" aria-label="已确认教训检索" :aria-busy="searching">
      <h3>已确认教训检索</h3>
      <form class="toolbar search-form" @submit.prevent="search">
        <label>关键词<input v-model="keyword" maxlength="160" :disabled="searching"/></label>
        <label>分类<input v-model="category" maxlength="80" :disabled="searching"/></label>
        <el-button native-type="submit" :disabled="searching" :loading="searching"><Search :size="15"/>检索教训</el-button>
      </form>
      <el-alert v-if="searchError" type="error" :title="searchError" :closable="false" show-icon/>
      <template v-if="hits&&!searching">
        <p class="meta" aria-live="polite">{{hits.total?`找到 ${hits.total} 条已确认教训。`:'暂无匹配的已确认教训。'}}</p>
        <ul v-if="hits.items.length" class="entries">
          <li v-for="hit in hits.items" :key="hit.lessonId">
            <div class="section-heading"><strong>{{hit.category}}</strong><time class="meta" :datetime="hit.confirmedAt">{{time(hit.confirmedAt)}}</time></div>
            <dl class="detail-lines"><template v-for="field in fields.slice(1)" :key="field.name"><dt>{{field.label}}</dt><dd>{{hit[field.name]}}</dd></template></dl>
            <details class="meta"><summary>已确认教训来源</summary><code v-for="source in hit.sourceRefs" :key="source" class="source">{{source}}</code></details>
          </li>
        </ul>
        <el-pagination v-if="hits.total>hits.pageSize" :current-page="hits.page" :page-size="hits.pageSize" :total="hits.total"
          small layout="prev,pager,next" @current-change="turn"/>
      </template>
    </section>
  </section>

  <el-dialog v-model="dialog" :title="intent?prompts[intent.kind].title:''" width="min(580px,94vw)" append-to-body destroy-on-close
    :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
    <form v-if="intent" class="retro-form" @submit.prevent="save">
      <fieldset :disabled="busy">
        <template v-if="intent.kind==='answers'">
          <label v-for="(question,index) in questions" :key="question">{{question}}<textarea v-model="answers[index]" rows="2" required/></label>
          <template v-for="{key,revision} in requiredDeviations" :key="key">
            <fieldset v-if="deviations[key]">
              <legend>{{calibrationLabels[key]}} · 结果版本 {{revision}}</legend>
              <label>偏差解释<textarea v-model="deviations[key].explanation" rows="2" minlength="4" maxlength="6000" required/></label>
              <label>改进措施<textarea v-model="deviations[key].improvement" rows="2" minlength="4" maxlength="6000" required/></label>
            </fieldset>
          </template>
        </template>
        <template v-else-if="intent.kind==='draft'">
          <p v-if="draftSource" class="meta">已引用复盘版本 {{draftSource}}，保存前请核对和编辑。</p>
          <label v-for="field in fields" :key="field.name">{{field.label}}
            <input v-if="field.name==='category'" v-model="draft.category" maxlength="20" required/>
            <textarea v-else v-model="draft[field.name]" rows="2" required/>
          </label>
        </template>
        <dl v-else-if="selectedLesson" class="detail-lines">
          <dt>教训版本</dt><dd>{{selectedLesson.revision}}</dd>
          <template v-for="field in fields" :key="field.name"><dt>{{field.label}}</dt><dd>{{selectedLesson[field.name]}}</dd></template>
        </dl>
      </fieldset>
      <el-alert v-if="formError" type="error" :title="formError" :closable="false" show-icon/>
      <div class="actions"><el-button :disabled="saving" @click="dialog=false">取消</el-button>
        <el-button type="primary" native-type="submit" :disabled="busy||!command" :loading="saving"><component :is="prompts[intent.kind].icon" :size="15"/>{{prompts[intent.kind].submit}}</el-button>
      </div>
    </form>
  </el-dialog>
</template>

<style scoped>
.retrospective,.retro-form{overflow-wrap:anywhere}
.section-toolbar,.section-heading,.controls{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.section-toolbar{justify-content:space-between}
.controls{margin-left:auto}
.group{border-top:1px solid var(--el-border-color);margin-top:1rem;padding-top:.75rem}
.retrospective h3{margin:.15rem 0}
.retrospective p{margin:.4rem 0}
.meta{color:var(--el-text-color-secondary);font-size:13px}
.notice{color:var(--el-color-primary)}
.answers{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,14rem),1fr));gap:.7rem;margin:.7rem 0}
.answers dt{font-weight:600}.answers dd{margin:.25rem 0 0;white-space:pre-wrap}
.answers code{overflow-wrap:anywhere}
.entries{list-style:none;margin:.45rem 0;padding:0}
.entries li{border-top:1px solid var(--el-border-color-lighter);padding:.65rem 0}
.detail-lines{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.3rem .65rem;margin:.45rem 0}
.detail-lines dd{margin:0;white-space:pre-wrap}
.search-form{align-items:end;flex-wrap:wrap}
.search-form label{flex:1 1 8rem;min-width:0}
summary{cursor:pointer}.source{display:block}
.retro-form,.retro-form fieldset{display:grid;gap:.7rem}
.retro-form fieldset{min-width:0;border:0;padding:0;margin:0}
.controls :deep(.el-button){margin-left:0}
</style>
