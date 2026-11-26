import type {ReplyContext} from './assistantFollowups'
import type {AssistantTopic, QuestionTarget} from './assistantQuestions'

export type Mode='charter'|'process'|'closing'|'ledger'
export type Topic=AssistantTopic
export type Cited={text:string;sourceRefs:string[];observedAt?:string|null}
export type Preview={status:'ready'|'manual'|'empty';items:(Cited & {prompt?:string})[];errorCode?:string}
type Gap={code:string;message:string;sourceRefs:string[]}
type LessonRef=Cited & {lessonId:string;projectId:string;category:string;matchedTerm?:string|null}
type Score={total:string|null;means:Record<string,string>;sourceRefs:string[]}
type Charter={completeness:{eligible:boolean;missing:Gap[]};scoreSummary:null|Score & {decidedAt:string};
  pendingScore:null|Score & {observedAt:string};pendingWeaknesses:Cited[];charterFacts:Cited[];
  weaknesses:Cited[];lessonRefs:LessonRef[];reviewDraft:Cited[];previewEnabled:boolean}
type Process={reportId:string|null;modelEnabled:boolean;analysis:null|{status:string;errorCode:string|null};
  factCandidates:{subject:string;quote:string;sourceRef:string;asOf:string}[];conflicts:Cited[];
  health:{health:string;dataTrust:string;gaps:string[];timeLine:string|null;costLine:string|null;qualityLine:string|null};alerts:(Cited & {ruleId:string})[];
  weeklyDraft:{period:string;verifiedPersonDays:string|null;structuredFields:{milestone_progress:{summary:string}[];
    requirement_progress:{summary:string}[];risks_and_blockers:{summary:string}[]}}}
type Closing={gateEligible:boolean|null;gateGaps:Gap[];retrospectiveKind:'normal'|'terminated'|null;
  retrospectiveQuestions:string[];lessonDrafts:Cited[];canStartLessonDraft:boolean;previewEnabled:boolean}
type Ledger={answer:Cited[];asOf:string;sourceRefs:string[];unknowns:string[];previewEnabled:boolean}
type Data={charter:Charter;process:Process;closing:Closing;ledger:Ledger}
export type Packet={ [K in Mode]:{mode:K;data:Data[K];meta:{asOf:string;sourceRefs:string[];ruleVersion:string}} }[Mode]
export type Answer = {lines:string[]; sources:string[]; asOf?:string}
export type Turn = {id:number; question:string; answer:Answer; target?:QuestionTarget} & ReplyContext
export type ConversationStore = Map<string, Record<Mode,Turn[]>>
export const starters:Record<Mode,string[]> = {
  charter:['立项材料还缺什么？','立项评分有哪些弱项？','有没有类似项目的教训？'],
  process:['帮我起草本周周报','有哪些事实需要澄清？','当前有哪些风险预警？'],
  closing:['这个项目结项还差什么？','带我做一次复盘四问','根据复盘起草教训'],
  ledger:['当前三线如何？','预算和实际投入相差多少？','哪些里程碑预测延期了？'],
}
const lights:Record<string,string>={green:'绿灯',yellow:'黄灯',red:'红灯',untrusted:'数据待核实',unavailable:'数据暂缺'}
export const reviewQuestion=(text:string)=>text.replace('写入 Lesson，带分类标签','记录为教训并标注分类')
export function closingStatus(stage:string, kind:Closing['retrospectiveKind']) {
  if(stage==='archived')return kind==='terminated'?'项目已按终止流程归档，可查看终止复盘和归档记录。':'项目已归档，可查看结项裁决、复盘答复和归档记录。'
  if(stage==='retrospective')return kind==='terminated'?'项目已终止，当前正在复盘。':'项目已通过结项，现在处于复盘阶段。'
  if(stage==='closed')return '项目已通过结项，可进入复盘。'
  if(stage==='terminated')return '项目已终止，可查看终止裁决。'
  return '当前阶段还没有进入结项检查。'
}
export function makeAnswer(packet:Packet, question:string, stage:string):Answer {
  const answer:Answer={lines:[],sources:[],asOf:packet.meta.asOf}
  const add=(text:string,refs:string[]=[])=>{answer.lines.push(text);answer.sources.push(...refs)}
  const cite=(rows:Cited[],empty:string)=>{
    if(!rows.length)add(empty)
    for(const row of rows.slice(0,10))add(row.text,row.sourceRefs)
    if(rows.length>10)add(`另有 ${rows.length-10} 项，可在下方材料中查看。`)
  }
  switch(packet.mode){
    case 'charter': {
      const data=packet.data,score=data.pendingScore??data.scoreSummary
      if(/教训|经验/.test(question))cite(data.lessonRefs,'没有查到可引用的已确认教训。')
      else if(/评[分审]|弱项|多少分/.test(question)&&!/材料/.test(question)){
        add(score?`${data.pendingScore?'本轮待裁决':'正式评审'}总分为 ${score.total??'暂无记录'}。`:'项目还没有可见的评分记录。',score?.sourceRefs)
        cite(data.pendingScore?data.pendingWeaknesses:data.weaknesses,'当前评分记录未列出弱项。')
      }else{
        const gaps=data.completeness.missing.filter(gap=>gap.code!=='GATE_STAGE_MISMATCH')
        if(score===data.scoreSummary&&score)add('项目已有正式立项评审记录。',score.sourceRefs)
        else add(gaps.length?'以下材料需要补齐。':data.completeness.eligible?'立项材料已通过完整性预检。':'当前阶段可以查阅已有立项材料。')
        for(const gap of gaps)add(gap.message,gap.sourceRefs)
        cite(data.reviewDraft,'缺少形成评审建议的材料。')
      }
      break
    }
    case 'process': {
      const data=packet.data
      if(/周报|小结|简报/.test(question)){
        const draft=data.weeklyDraft, fields=draft.structuredFields
        add(`${draft.period} 周报草稿`)
        for(const [label,rows] of [['里程碑',fields.milestone_progress],['需求进展',fields.requirement_progress],['风险阻塞',fields.risks_and_blockers]] as const){
          add(`${label}｜${rows.map(row=>row.summary).join('；')||'本期尚无记录'}`)
        }
        add(draft.verifiedPersonDays==null?'本期尚无已核验人天流水。':`本期已核验 ${draft.verifiedPersonDays} 人天。`)
      }else if(/事实|冲突|澄清|候选/.test(question)){
        cite(data.conflicts,'当前没有待澄清的冲突记录。')
        for(const item of data.factCandidates)add(`待核验候选：${item.subject}，原文为“${item.quote}”。`,[item.sourceRef])
      }else{
        add(`当前三线总体为${lights[data.health.health]??'数据待核实'}。`)
        cite(data.alerts,'当前没有可见的规则预警。')
      }
      break
    }
    case 'closing': {
      const data=packet.data
      if(/教训|沉淀/.test(question))cite(data.lessonDrafts,'目前没有可引用的复盘回答，请先在「复盘与教训」记录四问。')
      else if(/四问|复盘/.test(question))data.retrospectiveQuestions.forEach((item,i)=>add(`${i+1}. ${reviewQuestion(item)}`))
      else{
        add(data.gateEligible===null?closingStatus(stage,data.retrospectiveKind):data.gateEligible?'结项预检通过，可由评审人进入关口裁决。':'结项预检还有以下缺口。')
        data.gateGaps.forEach(gap=>add(gap.message,gap.sourceRefs))
      }
      break
    }
    case 'ledger': {
      for(const item of packet.data.answer){
        const text=item.text.replace(/^金额（CNY）/u,'金额（元）').replace(/^时间线：(green|yellow|red)$/u,(_,lamp:string)=>`时间线：${lights[lamp]}`)
        add(text,item.sourceRefs)
      }
      packet.data.unknowns.forEach(item=>add(item))
      if(!answer.lines.length)add('没有找到可核验的台账记录。')
      break
    }
  }
  if(!answer.lines.length)add('当前没有可引用的记录。')
  // Empty searches and built-in prompts have no supporting business record.
  const usePacketSources=(packet.mode==='process'&&/周报|小结|简报|风险|预警/.test(question)) ||
    (packet.mode==='closing'&&!/教训|四问|复盘/.test(question))
  answer.sources=[...new Set(answer.sources.length||!usePacketSources?answer.sources:packet.meta.sourceRefs)]
  return answer
}
