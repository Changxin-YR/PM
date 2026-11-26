<script setup lang="ts">
import {ref} from 'vue'
import {Pencil,Save} from 'lucide-vue-next'

const directory={
  procurement:'外采',travel:'差旅',cloud:'云资源',allocation:'分摊',
  development:'研发',implementation:'实施',management:'管理',
} as const
type Category=keyof typeof directory
type Kind='money'|'effort'
type Amounts=Record<Category,string>
type Line={type:Kind;category:Category|'historical_uncategorized';initialBudget?:string;budget?:string;actual:string;
  forecastRemaining?:string;projectedTotal?:string;varianceRate?:string;unbudgetedExposure:boolean;sourceRefs:string[]}
type Categories={items:Line[];mode:'legacy'|'draft'|'classified';budgetRevision:number;forecastRevision:number;
  forecastObservedAt?:string;forecastEffectiveAt?:string;forecastNeedsReview?:boolean}
type Editor={canSaveBudget:boolean;canSaveForecast:boolean;changeRevision?:number;draftAmounts?:Amounts;forecastAmounts?:Amounts}
type CostMeasure={budget:string;actual:string;forecastRemaining:string;projectedTotal:string;varianceRate?:string}
type State=Record<Kind,CostMeasure>&{categories:Categories;categoryEditor?:Editor}
type Action='budget'|'forecast'|'change'

const props=defineProps<{projectId:string;data:State;api:(path:string,options?:RequestInit)=>Promise<any>;busy:boolean}>()
const emit=defineEmits<{changed:[]}>()
const units:Kind[]=['money','effort']
const keys=Object.keys(directory) as Category[]
const amountFields:Pick<Record<keyof Line,string>, 'initialBudget'|'budget'|'actual'|'forecastRemaining'|'projectedTotal'>={
  initialBudget:'初始预算',budget:'当前预算',actual:'已核验实际',forecastRemaining:'剩余预测',projectedTotal:'预计最终投入',
}
const amountKeys=Object.keys(amountFields) as (keyof typeof amountFields)[]
const unitLabel={money:'金额（元）',effort:'人天'}
const viewLabel={legacy:'历史总额',draft:'类别草稿',classified:'获批类别预算'}
const action=ref<Action|null>(null),amounts=ref<Partial<Amounts>>({}),reason=ref(''),reestimate=ref(''),revision=ref(1)
const saving=ref(false),notice=ref(''),error=ref('')
let key='',signature=''

function open(mode:Action){
  const {categories,categoryEditor}=props.data
  action.value=mode
  const existing=mode==='forecast'?categoryEditor?.forecastAmounts:categoryEditor?.draftAmounts
  amounts.value={...existing}
  revision.value=mode==='forecast'?categories.forecastRevision:mode==='change'?categoryEditor?.changeRevision??1:categories.budgetRevision
  reason.value='';reestimate.value='';key='';notice.value='';error.value=''
}
function cents(amount:string){
  const [whole='',fraction='']=amount.split('.')
  return BigInt(whole)*100n+(whole.startsWith('-')?-1n:1n)*BigInt(fraction.padEnd(2,'0'))
}
function total(rows:Line[],field:keyof typeof amountFields){
  const values=rows.map(row=>row[field]).filter((value):value is string=>value!==undefined)
  if(!values.length)return '未填写'
  const value=values.reduce((sum,amount)=>sum+cents(amount),0n)
  const absolute=value<0n?-value:value
  return `${value<0n?'-':''}${absolute/100n}.${String(absolute%100n).padStart(2,'0')}`
}
function variance(value?:string){
  if(!value||value==='UNAVAILABLE')return '—'
  const [whole='',fraction='']=value.replace(/^-/,'').split('.')
  const digits=whole+fraction.padEnd(4,'0'),cut=whole.length+2
  return `${value.startsWith('-')?'-':''}${digits.slice(0,cut).replace(/^0+(?=\d)/,'')}.${digits.slice(cut)}%`
}
function label(category:Line['category']){
  return category==='historical_uncategorized'?'历史未分类 / 未分配':directory[category]
}
async function save(){
  if(!action.value)return
  const mode=action.value,prefix=`projects/${props.projectId}`
  const path=mode==='change'?`${prefix}/changes/category-budget`:`${prefix}/cost-categories/${mode==='budget'?'budget-draft':'forecast'}`
  const body=JSON.stringify({amounts:amounts.value,...(mode==='budget'?{}:{reason:reason.value.trim()}),
    ...(mode==='change'&&reestimate.value.trim()?{reestimate:reestimate.value.trim()}:{})})
  const nextSignature=path+body+String(revision.value)
  if(!key||signature!==nextSignature){key=crypto.randomUUID();signature=nextSignature}
  saving.value=true;error.value=''
  try{
    await props.api(path,{method:mode==='change'?'POST':'PUT',body,headers:{'Idempotency-Key':key,'If-Match':String(revision.value)}})
    action.value=null
    notice.value=mode==='change'?'已提交预算变更，可在变更台账查看审批。':'已保存类别成本。'
    emit('changed')
  }catch(e){error.value=e instanceof Error?e.message:String(e)}finally{saving.value=false}
}
</script>

<template>
  <section class="category-costs" aria-label="类别预算与预测">
    <div class="toolbar"><h3>类别预算与预测</h3><span>{{viewLabel[data.categories.mode]}}</span></div>
    <p v-if="data.categories.forecastNeedsReview" role="status">预算或实际基础已更新。预测仍使用 {{new Date(data.categories.forecastObservedAt!).toLocaleString('zh-CN')}} 的基础，待复核。</p>
    <p v-if="notice" role="status">{{notice}}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <div class="actions">
      <el-button v-if="data.categoryEditor?.canSaveBudget" :disabled="busy||!!action" @click="open('budget')"><Pencil :size="14"/>编辑类别预算草稿</el-button>
      <el-button v-if="data.categoryEditor?.canSaveForecast" :disabled="busy||!!action" @click="open('forecast')"><Pencil :size="14"/>编辑类别预测</el-button>
      <el-button v-if="data.categoryEditor?.changeRevision" :disabled="busy||!!action" @click="open('change')">{{data.categories.mode==='legacy'?'申请首次预算分配':'申请类别预算变更'}}</el-button>
    </div>
    <form v-if="action" @submit.prevent="save">
      <div class="category-fields">
        <label v-for="item in keys" :key="item">{{directory[item]}}
          <input v-model="amounts[item]" inputmode="decimal" pattern="(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?" required>
        </label>
      </div>
      <label v-if="action!=='budget'">{{action==='forecast'?'类别预测原因':'预算变更原因'}}<textarea v-model="reason" minlength="3" maxlength="2000" required/></label>
      <label v-if="action==='change'&&data.categories.mode==='classified'">总额调整的范围、预测和立项估算说明<textarea v-model="reestimate" minlength="3" maxlength="2000"/></label>
      <div class="actions"><el-button :disabled="saving" @click="action=null">取消</el-button><el-button type="primary" native-type="submit" :loading="saving"><Save :size="15"/>{{action==='change'?'提交审批':'保存类别计划'}}</el-button></div>
    </form>
    <div v-for="unit in units" :key="unit" class="category-table" tabindex="0">
      <table>
        <caption>{{unitLabel[unit]}}</caption>
        <thead><tr><th scope="col">类别</th><th v-for="field in amountKeys" :key="field" scope="col">{{amountFields[field]}}</th><th scope="col">偏差</th><th scope="col">来源</th></tr></thead>
        <tbody><tr v-for="row in data.categories.items.filter(row=>row.type===unit)" :key="row.category">
          <th scope="row">{{label(row.category)}}</th>
          <td v-for="field in amountKeys" :key="field">{{row[field]??(field==='forecastRemaining'?'未填写':'—')}}</td>
          <td>{{row.unbudgetedExposure?'无类别预算但存在投入':variance(row.varianceRate)}}</td>
          <td><details v-if="row.sourceRefs.length"><summary>{{row.sourceRefs.length}} 条来源</summary><ul><li v-for="ref in row.sourceRefs" :key="ref">{{ref}}</li></ul></details><span v-else>—</span></td>
        </tr></tbody>
        <tfoot><tr><th scope="row">合计</th><td v-for="field in amountKeys" :key="field">{{total(data.categories.items.filter(row=>row.type===unit),field)}}</td><td>{{variance(data[unit].varianceRate)}}</td><td>—</td></tr></tfoot>
      </table>
    </div>
  </section>
</template>

<style scoped>
.category-costs {margin-top:20px;display:grid;gap:12px}
.category-table {max-width:100%;overflow:auto}
.category-table table {border-collapse:collapse;min-width:44rem;width:100%;font-variant-numeric:tabular-nums}
caption {text-align:left;font-weight:600;padding:8px}
th,td {padding:8px;border-bottom:1px solid var(--pmo-line);text-align:left;vertical-align:top}
tfoot {font-weight:600;background:var(--pmo-bg)}
details ul {max-width:15rem;overflow-wrap:anywhere;padding-left:18px}
.category-fields {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,9rem),1fr));gap:12px}
form {display:grid;gap:12px;padding:12px;background:var(--pmo-bg);border:1px solid var(--pmo-line);border-radius:8px}
</style>
