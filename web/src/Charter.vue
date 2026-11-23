<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {LockKeyhole,Plus,RefreshCw,Save,Trash2} from 'lucide-vue-next'

const props=defineProps<{projectId:string;stage:string;api:(path:string,options?:RequestInit)=>Promise<any>}>()
const textFields=[['valueProp','价值主张'],['targetClient','目标客户'],['attractiveness','客户或市场吸引力'],['expectedBenefit','预期收益'],['successCriteria','成功标准']] as const
type TextField=typeof textFields[number][0]
type Risk={statement:string;response:string}
type Card=Partial<Record<TextField,string>> & {roughCost?:string;roughEffort?:string;roughDurationDays?:number;topRisks?:Risk[];isFrozen?:boolean}
const path=`projects/${props.projectId}/charter`,card=ref<Card>({}),base=ref<Card>({}),revision=ref(0),exists=ref(false)
const error=ref(''),notice=ref(''),busy=ref(false),conflict=ref(false),dirty=computed(()=>JSON.stringify(base.value)!==JSON.stringify(card.value))
const locked=computed(()=>!!base.value.isFrozen||props.stage!=='drafting')
const readonly=computed(()=>busy.value||locked.value||!revision.value)
let attempt='',signature=''
async function read(){busy.value=true;error.value='';try{const {data}=await props.api(path);const source=data.charter||{};base.value=structuredClone(source);card.value=structuredClone(source);revision.value=data.expectedRevision;exists.value=!!data.charter;conflict.value=false;notice.value=''}catch(e){error.value=e instanceof Error?e.message:'读取失败'}finally{busy.value=false}}
async function save(){busy.value=true;error.value='';notice.value='';try{
 const fields:Record<string,unknown>={};for(const key of [...textFields.map(f=>f[0]),'roughCost','roughEffort','roughDurationDays','topRisks'] as const){const current=card.value[key];if(JSON.stringify(current)===JSON.stringify(base.value[key]))continue
 if(current==null||current===''||(Array.isArray(current)&&current.length===0)){if(base.value[key]!=null)throw Error('已保存的字段不能清空。');continue}
 fields[key==='roughDurationDays'?'roughDuration':key]=current}
 if(Object.keys(fields).length===0)return
 const body=JSON.stringify({fieldsCharter:fields}),sig=`${revision.value}:${body}`;if(!attempt||sig!==signature){attempt=crypto.randomUUID();signature=sig}
 await props.api(path,{method:'PUT',headers:{'If-Match':String(revision.value),'Idempotency-Key':attempt},body});await read();if(!error.value)notice.value='已保存'
 }catch(e){error.value=e instanceof Error?e.message:'保存失败';conflict.value=error.value.includes('版本已变化')}finally{busy.value=false}}
function addRisk(){(card.value.topRisks??=[]).push({statement:'',response:''})}
onMounted(read)
</script>
<template>
  <section class="charter" aria-label="立项卡" :aria-busy="busy">
    <div class="bar">
      <el-tag size="small" effect="light" :type="base.isFrozen?'primary':'info'">{{base.isFrozen?'已冻结':exists?'草稿':'未填写'}}</el-tag>
      <span class="meta">版本 {{revision||'—'}}</span>
      <el-button title="重新读取立项卡" aria-label="重新读取立项卡" :disabled="busy||dirty" @click="read"><RefreshCw :size="16"/></el-button>
    </div>
    <p v-if="locked&&revision" class="readonly-note">
      <LockKeyhole :size="16" aria-hidden="true"/>{{base.isFrozen?'立项卡已冻结 · 当前内容只读':'当前阶段的立项卡只读'}}
    </p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-button v-if="conflict" @click="read">放弃本次修改并读取新版</el-button>
    <form @submit.prevent="save">
      <fieldset :disabled="readonly">
        <section class="panel charter-section" aria-labelledby="charter-value">
          <h3 id="charter-value">项目价值</h3>
          <dl v-if="locked" class="narratives">
            <div v-for="[key,label] in textFields" :key="key"><dt>{{label}}</dt><dd>{{card[key]??'未填写'}}</dd></div>
          </dl>
          <div v-else class="narratives">
            <label v-for="[key,label] in textFields" :key="key">{{label}}
              <textarea v-model="card[key]" :maxlength="key==='targetClient'?2000:3000" rows="2"></textarea>
            </label>
          </div>
        </section>
        <section class="panel charter-section" aria-labelledby="charter-estimate">
          <h3 id="charter-estimate">初步估算</h3>
          <dl v-if="locked" class="numbers">
            <div><dt>粗估金额（元）</dt><dd>{{card.roughCost??'未填写'}}</dd></div>
            <div><dt>粗估人天</dt><dd>{{card.roughEffort??'未填写'}}</dd></div>
            <div><dt>预计工期（天）</dt><dd>{{card.roughDurationDays??'未填写'}}</dd></div>
          </dl>
          <div v-else class="numbers">
            <label>粗估金额（元）<input v-model="card.roughCost" inputmode="decimal" pattern="(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?"></label>
            <label>粗估人天<input v-model="card.roughEffort" inputmode="decimal" pattern="(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?"></label>
            <label>预计工期（天）<input v-model.number="card.roughDurationDays" type="number" min="1" max="3650" step="1"></label>
          </div>
        </section>
        <section class="panel charter-section" aria-labelledby="charter-risks">
          <div class="bar risks-heading">
            <h3 id="charter-risks">主要风险</h3>
            <el-button text :disabled="readonly" @click="addRisk"><Plus :size="15"/>添加风险</el-button>
          </div>
          <p v-if="!card.topRisks?.length&&revision" class="meta">暂无风险记录</p>
          <div v-for="(risk,index) in card.topRisks" :key="index" class="risk">
            <dl v-if="locked" class="risk-fields">
              <div><dt>风险描述</dt><dd>{{risk.statement}}</dd></div>
              <div><dt>应对措施</dt><dd>{{risk.response}}</dd></div>
            </dl>
            <div v-else class="risk-fields">
              <label>风险描述<input v-model="risk.statement" required maxlength="3000"></label>
              <label>应对措施<input v-model="risk.response" required maxlength="3000"></label>
            </div>
            <el-button text :disabled="readonly" title="移除风险项" aria-label="移除风险项" @click="card.topRisks?.splice(index,1)"><Trash2 :size="15"/></el-button>
          </div>
        </section>
      </fieldset>
      <div class="panel actions charter-actions">
        <span role="status">{{notice}}</span>
        <span v-if="locked" class="meta readonly-label"><LockKeyhole :size="15" aria-hidden="true"/>只读</span>
        <el-button type="primary" native-type="submit" :disabled="readonly||!dirty||conflict"><Save :size="15"/>保存立项卡</el-button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.charter,.charter fieldset {display:grid;gap:16px;min-width:0}
.charter fieldset {border:0;margin:0;padding:0}
.bar,.readonly-note,.readonly-label {display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.meta,.charter-actions span {font-size:13px;color:var(--pmo-muted)}
.readonly-note {background:var(--pmo-selected);color:var(--pmo-primary-hover);border-radius:8px;padding:10px 12px;margin:0}
.charter-section + .charter-section {margin-top:0}
.charter-section h3 {font-size:16px;font-weight:600;margin:0 0 12px}
.risks-heading {justify-content:space-between;margin-bottom:8px}
.risks-heading h3 {margin:0}
.narratives,.numbers,.risk-fields {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 20px;margin:0}
.narratives > :first-child {grid-column:1/-1}
.numbers {grid-template-columns:repeat(3,minmax(0,1fr));font-variant-numeric:tabular-nums}
.risk {display:grid;align-items:end;grid-template-columns:minmax(0,1fr) auto;gap:8px;margin:8px 0}
dt {font-size:13px;color:var(--pmo-secondary)}
dd {margin:4px 0 0;background:var(--pmo-bg);border:1px solid var(--pmo-line);border-radius:8px;padding:8px 12px;min-height:38px;white-space:pre-wrap;overflow-wrap:anywhere}
.charter-actions {margin-top:16px;justify-content:flex-end}
.readonly-label {margin-right:auto}
@media (max-width:700px) {
  .narratives,.numbers,.risk-fields {grid-template-columns:1fr}
}
</style>
