<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {Check,Plus,RefreshCw,Undo2} from 'lucide-vue-next'
type Row={postingId:string;type:'money'|'effort';amountSigned:string;sourceRef:string;status:string;revision:number;period:string;occurredAt?:string;postingOfId?:string;canVerify:boolean;canReverse:boolean;reversalReason?:string;verificationNote?:string}
const props=defineProps<{projectId:string;api:(p:string,o?:RequestInit)=>Promise<any>}>()
const emit=defineEmits<{changed:[]}>()
const path=`projects/${props.projectId}/cost-postings`,items=ref<Row[]>([]),page=ref(1),total=ref(0),can=ref(false),busy=ref(false),error=ref('')
const action=ref<''|'propose'|'verify'|'reverse'>(''),row=ref<Row|null>(null),kind=ref('money'),amount=ref(''),period=ref(''),source=ref(''),reason=ref(''),when=ref('')
let key='',signature=''
async function load(n=page.value){const {data}=await props.api(`${path}?page=${n}`);items.value=data.items;total.value=data.total;page.value=n;can.value=data.canPropose}
async function refresh(n=page.value){busy.value=true;error.value='';try{await load(n)}catch(e){error.value=String(e);can.value=false;items.value=[]}finally{busy.value=false}}
function open(mode:'propose'|'verify'|'reverse',r:Row|null=null){action.value=mode;row.value=r;kind.value='money';amount.value='';period.value='';source.value='';reason.value='';when.value='';error.value='';key=''}
async function save(){busy.value=true;error.value='';try{const r=row.value,a=action.value,body=JSON.stringify(a==='propose'?{type:kind.value,amount:amount.value,period:period.value,sourceRef:source.value.trim()}:{reason:reason.value.trim(),...(a==='verify'&&!r?.postingOfId?{occurredAt:when.value+':00Z'}:{})}),target=r?`cost-postings/${r.postingId}/${a}`:path,sig=target+body+(r?.revision??0)
 if(!key||sig!==signature){key=crypto.randomUUID();signature=sig}
 await props.api(target,{method:'POST',body,headers:{'Idempotency-Key':key,...(r?{'If-Match':String(r.revision)}:{})}});action.value='';await load(1);emit('changed')
 }catch(e){error.value=String(e)}finally{busy.value=false}}
onMounted(()=>refresh(1))
</script>
<template><section aria-label="成本凭证" class="ledger"><div class="toolbar"><h3>成本凭证 · {{total}}</h3><el-button v-if="can" :disabled="busy||!!action" @click="open('propose')"><Plus :size="15"/>登记候选凭证</el-button><el-button title="刷新凭证" aria-label="刷新凭证" :disabled="busy||!!action" @click="refresh()"><RefreshCw :size="15"/></el-button></div>
<el-alert v-if="error" type="error" :title="error" :closable="false"/>
<form v-if="action" @submit.prevent="save"><template v-if="action==='propose'"><label>凭证口径<select v-model="kind"><option value="money">金额（元）</option><option value="effort">人天</option></select></label><label>凭证数额<input v-model="amount" inputmode="decimal" required></label><label>统计月份（UTC）<input type="month" v-model="period" required></label><label>凭证来源<input v-model="source" minlength="3" maxlength="256" required></label></template>
<template v-else><p>{{row?.sourceRef}} · {{row?.amountSigned}} {{row?.type==='money'?'元':'人天'}}</p><label v-if="action==='verify'&&!row?.postingOfId">实际发生时间（UTC）<input v-model="when" type="datetime-local" required></label><label>{{action==='verify'?'核验说明':'冲销原因'}}<textarea v-model="reason" minlength="4" maxlength="2000" required/></label></template>
<div class="actions"><el-button :disabled="busy" @click="action=''">取消</el-button><el-button type="primary" native-type="submit" :loading="busy"><Check :size="15"/>{{action==='propose'?'提交候选凭证':action==='verify'?'确认核验':'申请全额冲销'}}</el-button></div></form>
<el-table :data="items" row-key="postingId" empty-text="暂无成本凭证" v-loading="busy"><el-table-column type="expand"><template #default="{row:r}"><dl><dt>凭证编号</dt><dd>{{r.postingId}}</dd><template v-if="r.postingOfId"><dt>原凭证</dt><dd>{{r.postingOfId}}</dd><dt>冲销原因</dt><dd>{{r.reversalReason}}</dd></template><template v-if="r.occurredAt"><dt>发生时间</dt><dd>{{r.occurredAt}}</dd></template><template v-if="r.verificationNote"><dt>核验说明</dt><dd>{{r.verificationNote}}</dd></template></dl></template></el-table-column>
<el-table-column prop="sourceRef" label="来源" min-width="130"/><el-table-column prop="period" label="月份" width="95"/><el-table-column label="数额" min-width="100"><template #default="{row:r}">{{r.amountSigned}} {{r.type==='money'?'元':'人天'}}</template></el-table-column><el-table-column label="状态" width="95"><template #default="{row:r}">{{r.status==='verified'?'已核验':'待核验'}}</template></el-table-column>
<el-table-column label="操作" width="90"><template #default="{row:r}"><el-button text aria-label="核验凭证" title="核验凭证" v-if="r.canVerify" :disabled="busy||!!action" @click="open('verify',r)"><Check :size="16"/></el-button><el-button text aria-label="全额冲销" title="全额冲销" v-if="r.canReverse" :disabled="busy||!!action" @click="open('reverse',r)"><Undo2 :size="16"/></el-button></template></el-table-column></el-table>
<el-pagination v-if="total>20" :disabled="busy||!!action" :current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="refresh"/></section></template>
<style scoped>.ledger{margin-top:1.25rem}.ledger form{display:grid;gap:.65rem;padding:.6rem 0}.toolbar{flex-wrap:wrap}dl{margin:.5rem 1rem;overflow-wrap:anywhere}dd{margin:0 0 .5rem}</style>
