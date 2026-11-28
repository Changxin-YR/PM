export type AssistantTopic = 'health' | 'cost' | 'requirements' | 'fact' | 'lesson' | 'requirement_history' |
  'forecast' | 'spending' | 'weekly_health'

export type QuestionTarget = ({ mode: 'ledger'; topic: AssistantTopic; keyword?: string } |
  { mode: 'charter' | 'process' | 'closing'; keyword?: string }) & {projects?: string[]; riskRegister?: boolean}

export type AssistantQuestion = {text:string; target:QuestionTarget}

const projectLabel = /^项目(?:(?:编号|代码|名称)\s*[：:]?\s*|[\s：:]+)/u
const projectCode = /\b[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+\b/giu
const projectSuffix = /项目(?:编号|代码|名称)?\s*(?:是|为|[：:])\s*([^。？?！!，,；;]+)$/u
const questionLead = /^(?:我想知道|请问|请检查|请查询|请查看|帮我查一下|帮我看看|帮我查|查一下|看一下|看看|查询|查看|还有哪些|有哪些|哪些|某条|这条|该条|现在|目前|当前)(?:\s+|[：:，,、])*/u
const currentProject = /^(?:本|该|此|这|当前|现有|这个|这一个|新)?项目$/u

function scopedPrefix(plain:string, marker:RegExp):string|undefined {
  const index=plain.search(marker)
  if(index<0)return undefined
  let prefix=plain.slice(0,index).trim()
  while(questionLead.test(prefix))prefix=prefix.replace(questionLead,'')
  prefix=prefix.replace(/(?:[\s：:，,、的]+|现在|目前|当前|最近|最新|有哪些|哪些)+$/u,'')
  if(!prefix||currentProject.test(prefix))return undefined
  return prefix.replace(projectLabel,'').trim()||undefined
}

export function interpretQuestion(question: string): QuestionTarget | undefined {
  const text = question.normalize('NFKC').trim().replace(/^(?:那(?:么)?|再帮我看看|帮我看看|请帮我看看|顺便问一下)[，,、\s]*/u,'')
  const quotePattern=/“([^”]+)”|「([^」]+)」|"([^"]+)"/gu
  const quotes=[...text.matchAll(quotePattern)].map(match=>({
    value:(match[1]??match[2]??match[3]).trim(),
    project:/项目(?:编号|代码|名称)?\s*[：:]?\s*$/u.test(text.slice(0,match.index)),
  })).filter(item=>item.value)
  const named=quotes.filter(item=>item.project).map(item=>item.value)
  const other=quotes.filter(item=>!item.project).map(item=>item.value)
  const keyword=other[0]?.slice(0,160)
  const plain=text.replace(quotePattern,'')
  function scoped(target:QuestionTarget,marker?:RegExp,keywordQuote=false):QuestionTarget {
    const prefix=marker&&(!quotes.length||keywordQuote&&!named.length)?scopedPrefix(plain,marker):undefined
    const suffix=marker?plain.replace(/[。？！?!]+$/u,'').match(projectSuffix)?.[1].trim():undefined
    const codes=[...plain.matchAll(projectCode)].map(match=>match[0])
    const candidates=[...named,...(marker&&!keywordQuote?other:[]),...(prefix?[prefix]:[]),...(suffix?[suffix]:[]),...codes]
    const projects=[...new Map(candidates.map(value=>[value.toLowerCase(),value])).values()]
    return projects.length?{...target,projects}:target
  }
  // A possessive such as “西湖工单的周报” is an explicit project, even without quotes or a project code.
  function workProduct(mode:'charter'|'process'|'closing', marker:RegExp):QuestionTarget {
    const target=scoped({mode}, /项目(?:编号|代码|名称)/u)
    if(quotes.length || target.projects?.length)return target
    const before=plain.slice(0,plain.search(marker)).trim()
    const possessive=before.match(/^(.+?)的(?:本周|这周|上周|本期|当前|本次|最新)?$/u)
    const namedProject=before.match(/^(.+?项目)(?:本周|这周|上周|本期|当前|本次|最新)?$/u)
    const subject=(possessive??namedProject)?.[1]?.replace(/^(?:(?:请|帮我|给我|麻烦|我想)?(?:检查|起草|整理|生成|看一下|看看|查询|查看|汇总|核对|准备|总结|复核)(?:一下)?|请问|我想知道|帮我)+/u,'').trim()
    if(!subject||currentProject.test(subject)||/^(?:刚才|之前|已有|本次|这次|本周|上周|本期|最新|目前|当前)$/u.test(subject))return target
    return {...target,projects:[subject]}
  }
  if (/复盘|结项教训/.test(plain)) return workProduct('closing',/复盘|结项教训/u)
  if (/阻塞风险/u.test(plain) && !/周报|预警/u.test(plain)) {
    return {...workProduct('process',/阻塞风险/u),riskRegister:true}
  }
  const processWork=/本周项目进展|周报|简报|事实冲突|澄清|待核验候选|风险预警/u
  if (processWork.test(plain) && !/本周.*(?:红灯|黄灯)/.test(plain)) return workProduct('process',processWork)
  const charterWork=/立项材料|立项卡|评审材料|评审建议/u
  if (charterWork.test(plain)) return workProduct('charter',charterWork)
  if (/立项(?:批准)?前|没.{0,6}立项|未.{0,6}立项|立项.*(?:未通过|没通过)/u.test(plain) && /花钱|支出|成本|流水/u.test(plain)) {
    return {mode:'ledger', topic:'spending'}
  }
  if (/本周|这周|周内|这个星期/u.test(plain) && /黄灯|红灯|黄红|红黄|预警/u.test(plain)) {
    return {mode:'ledger', topic:'weekly_health'}
  }
  if (/需求/u.test(plain) && /变更|改过|改动|变动/u.test(plain)) {
    return scoped({mode:'ledger', topic:'requirement_history', keyword}, /需求/u, true)
  }
  if (/里程碑/u.test(plain) && /延期|推迟|延后|晚于/u.test(plain)) return scoped({mode:'ledger', topic:'forecast'}, /里程碑/u)
  if (/结项|收尾/u.test(plain) && /缺|还差|门槛|条件|能否|能不能|不能/u.test(plain)) return scoped({mode:'closing'}, /(?:还缺哪些|还差哪些|为什么(?:当前)?不能)?(?:结项|收尾)/u)
  if (/最低分|薄弱维度|需要补强/u.test(plain)) return scoped({mode:'charter'}, /(?:为什么)?(?:最低分|薄弱维度|需要补强)/u)
  if (/立项/u.test(plain) && /分数|评分|弱项|多少分/u.test(plain)) return scoped({mode:'charter'}, /立项/u)
  if (/教训|踩(?:过|了)?(?:什么)?坑/u.test(plain)) {
    return scoped(/类似|相似|同类/u.test(plain) && !keyword ? {mode:'charter'} : {mode:'ledger',topic:'lesson',keyword})
  }
  if (/三线|健康度|健康灯/u.test(plain) || (/时间|进度/u.test(plain) && /钱|金额|成本/u.test(plain) && /人天/u.test(plain))) {
    return scoped({mode:'ledger', topic:'health'}, /三线|健康度|健康灯|时间|进度/u)
  }
  if (/满足率|需求确认率/u.test(plain)) return scoped({mode:'ledger', topic:'requirements'}, /(?:需求)?满足率|需求确认率/u)
  if (/成本|人天|预算|工时|偏差率/u.test(plain)) return scoped({mode:'ledger', topic:'cost'}, /金额和人天|成本|人天|预算|工时|偏差率/u)
  if (/事实|核验记录|\bfact\b/iu.test(plain) && keyword) return scoped({mode:'ledger', topic:'fact', keyword}, /(?:核验|候选)?事实|核验记录|\bfact\b/iu, true)
  return undefined
}
