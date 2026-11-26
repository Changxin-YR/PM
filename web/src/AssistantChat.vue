<script setup lang="ts">
import {computed,nextTick,ref,watch} from 'vue'
import {ArrowUp,BookOpen,MessageCircle,SquarePen} from 'lucide-vue-next'
import {starters,type Mode,type Turn} from './assistantConversation'
const props=defineProps<{mode:Mode;stage:string;turns:Turn[];busy:boolean;pending:string}>()
const emit=defineEmits<{send:[text:string];reset:[];reviewDraft:[answers:string[]];navigate:[target:'reviews'|'reports'|'retrospective'|'health'|'costs'|'requirements'|'milestones']}>()
const draft=ref(''),log=ref<HTMLElement>(),editor=ref<HTMLTextAreaElement>()
const names:Record<Mode,string>={charter:'立项评审助理',process:'过程管家',closing:'结项回顾助理',ledger:'台账问答'}
const last=computed(()=>props.turns.at(-1))
const completedReview=computed(()=>{
  const guided=last.value?.guided
  return guided&&guided.answers.length===guided.questions.length?guided.answers:undefined
})
const sourceNames:Record<string,string>={project:'项目档案',charter:'立项卡',decision:'评审裁决',review:'评审记录',report:'项目报告',milestone:'里程碑',
  requirement:'需求',fact:'核验事实',lesson:'教训记录',retrospective:'复盘记录','cost-plan':'成本计划','cost-entry':'预算台账','cost-posting':'核验流水',
  'health-snapshot':'三线快照','project-transition':'阶段流转','review-snapshot':'送审快照','review-decision-snapshot':'裁决快照',approval:'审批记录'}
const sourceName=(reference:string)=>sourceNames[reference.split(':')[0]]??'来源记录'
const destination=computed(()=>{
  const target=last.value?.target
  if(!target)return
  if(target.mode==='charter')return ['reviews','查看评审材料'] as const
  if(target.mode==='process')return ['reports','查看项目报告'] as const
  if(target.mode==='closing')return ['retrospective','打开复盘记录'] as const
  if(target.mode!=='ledger')return
  if(target.topic==='cost')return ['costs','查看成本台账'] as const
  if(target.topic==='health')return ['health','查看三线快照'] as const
  if(target.topic==='forecast')return ['milestones','查看里程碑'] as const
  if(target.topic==='requirements'||target.topic==='requirement_history')return ['requirements','查看需求台账'] as const
})
function send(text=draft.value){
  if(!text.trim()||props.busy)return
  draft.value=''; emit('send',text.trim()); editor.value?.focus()
}
function keydown(e:KeyboardEvent){
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();send()}
}
watch(()=>props.mode,()=>{draft.value=''})
watch(()=>[props.turns.at(-1)?.id,props.mode,props.pending],async()=>{
  await nextTick(); if(log.value)log.value.scrollTop=log.value.scrollHeight
})
const stamp=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
</script>
<template>
  <section class="dialogue" aria-label="项目对话">
    <header><strong><MessageCircle :size="18" aria-hidden="true"/>{{names[mode]}}对话</strong>
      <button v-if="turns.length" type="button" class="new" :disabled="busy" @click="emit('reset')"><SquarePen :size="15"/>新对话</button></header>
    <div ref="log" class="transcript" role="log" aria-label="对话记录" aria-live="polite" aria-relevant="additions">
      <div v-if="!turns.length&&!pending" class="welcome"><h3>想先了解什么？</h3>
        <div class="starters"><button v-for="prompt in starters[mode]" :key="prompt" type="button" @click="send(prompt)">{{prompt}}</button></div>
      </div>
      <article v-for="turn in turns" :key="turn.id" class="turn"><p class="mine"><span class="sr">我的问题</span>{{turn.question}}</p>
        <div class="reply"><b>{{names[mode]}}回复</b><p v-for="(line,index) in turn.answer.lines" :key="index">{{line}}</p>
          <small v-if="turn.answer.asOf">数据时点 {{stamp(turn.answer.asOf)}} 北京时间</small>
          <details v-if="turn.answer.sources.length"><summary><BookOpen :size="13"/>查看本条依据（{{turn.answer.sources.length}}）</summary>
            <div v-for="source in turn.answer.sources" :key="source" class="citation"><span>{{sourceName(source)}}</span><code>{{source}}</code></div></details>
        </div>
      </article>
      <div v-if="pending" role="status"><p class="mine">{{pending}}</p>正在核对项目资料…</div>
    </div>
    <div v-if="last&&!busy" class="follow-actions" aria-label="对话操作">
      <button v-if="last.answer.sources.length" type="button" @click="send('依据呢？')">追问依据</button>
      <button v-if="last.target&&!last.guided" type="button" @click="send('再查一次')">更新这条回答</button>
      <button v-if="completedReview&&stage==='retrospective'" type="button" @click="emit('reviewDraft',[...completedReview])">带入复盘表单</button>
      <button v-if="destination" type="button" @click="emit('navigate',destination[0])">{{destination[1]}}</button>
    </div>
    <form class="composer" @submit.prevent="send()"><textarea ref="editor" v-model="draft" :aria-label="mode==='ledger'?'用一句话查询台账':'向项目助理提问'" rows="2" maxlength="600" placeholder="输入问题，或追问上一条回答的依据…" @keydown="keydown"/>
      <div><small>回车发送 · 换行请按上档键加回车</small><button type="submit" aria-label="提问" :disabled="!draft.trim()||busy"><ArrowUp :size="16"/>发送</button></div>
    </form>
  </section>
</template>
<style scoped>
.follow-actions{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px}.follow-actions button{font-size:12px}.dialogue{min-width:0;color:var(--pmo-text)}header,.composer>div{display:flex;align-items:center;justify-content:space-between;gap:12px}header strong,button,summary{display:inline-flex;align-items:center;gap:7px}header strong{font-size:15px}button{font:inherit;cursor:pointer;border:1px solid var(--pmo-line);background:var(--pmo-surface);color:var(--pmo-primary-hover);border-radius:8px;padding:6px 12px}button:hover{background:var(--el-color-primary-light-9)}button:disabled{opacity:.55;cursor:default}.new{font-size:13px}.transcript{height:clamp(185px,32vh,370px);overflow:auto;overscroll-behavior:contain;padding:10px 4px;scrollbar-gutter:stable}.welcome{display:grid;align-content:center;justify-items:center;height:100%;gap:16px;text-align:center}.welcome h3{font-size:20px;margin:0}.starters{display:flex;flex-wrap:wrap;justify-content:center;gap:8px}.turn{max-width:760px;margin:8px auto 20px;display:grid;gap:10px;min-width:0}.mine{justify-self:end;max-width:85%;width:fit-content;margin:0 0 0 auto;background:var(--el-color-primary-light-9);border-radius:14px 14px 3px 14px;padding:9px 14px;white-space:pre-wrap}.reply{line-height:1.6}.reply b,.reply small,.composer small{color:var(--pmo-muted);font-size:12px}.reply p{margin:4px 0;white-space:pre-wrap}.reply details{margin-top:4px}.reply summary{cursor:pointer;color:var(--pmo-primary-hover);font-size:13px}.citation{margin:5px 0}.citation span{color:var(--pmo-muted);font-size:12px}.reply code{display:block;overflow-wrap:anywhere;font-size:12px}.composer{border:1px solid var(--pmo-line);border-radius:12px;padding:9px 12px;background:var(--pmo-surface)}.composer:focus-within{outline:2px solid var(--pmo-primary);outline-offset:1px}.composer textarea{width:100%;box-sizing:border-box;border:none;background:transparent;color:inherit;resize:vertical;font:inherit;outline:none;min-height:44px;padding:0}.composer button{background:var(--pmo-primary);color:white;border:0}.sr{position:absolute;clip-path:inset(50%);height:1px;width:1px;overflow:hidden}@media(max-width:540px){.starters{gap:5px}.starters button{font-size:13px}.composer small{max-width:60%}}
</style>
