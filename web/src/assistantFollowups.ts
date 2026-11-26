import type {Answer, Turn} from './assistantConversation'

export type GuidedReview = {questions:string[]; answers:string[]}
export type ReplyContext = {basisQuestion?:string; guided?:GuidedReview}
type Followup = {answer:Answer; context:ReplyContext}
const noSource='当前对话还没有可引用的项目记录。'

export function followup(text:string, previous?:Turn):Followup|undefined {
  const short=text.trim().replace(/[？?。！!]+$/u,'')
  const context={basisQuestion:previous?.basisQuestion??previous?.question,guided:previous?.guided}
  const sources=previous?.answer.sources??[], asOf=previous?.answer.asOf
  const reply=(lines:string[], refs=sources):Followup=>({answer:{lines,sources:[...refs],asOf},context})
  if(/^(?:请|那|再)?(?:上一条|上条|这个|刚才的|它的)?(?:回答的)?(?:依据|来源|出处)(?:是什么|有哪些|呢|在哪(?:里)?)?$/.test(short) || /^(?:为什么这么说|你是根据什么回答的|能给我看看依据吗)$/.test(short)){
    return reply([sources.length?`“${context.basisQuestion}”的回答可以对照下面 ${sources.length} 条来源。`:noSource])
  }
  if(/^(?:这些|这条|上条|刚才的)?(?:数据|记录|回答)?(?:是什么时候的|截至什么时候|数据时点是什么|是什么时间的)$/.test(short)){
    return reply([asOf?'下方的数据时点对应上次读取，发送“再查一次”可读取当前记录。':'当前对话还没有记录读取时点。'])
  }
  const section=short.match(/^(?:展开|解释|说说)第\s*([1-9]\d?)\s*[条项点]$/)
  if(section&&previous){
    const line=previous.answer.lines[Number(section[1])-1]
    return reply([line??`上一条回答只有 ${previous.answer.lines.length} 项。`])
  }
  const guided=previous?.guided
  if(!guided||guided.answers.length>=guided.questions.length)return
  if(/^(?:退出|结束|取消)复盘/.test(short))return {answer:{lines:['本轮复盘已结束。'],sources:[]},context:{}}
  if(!/^答[：:]/u.test(text)&&(/(?:[？?]$)|^(?:请问|帮我查|查看|查询|重新|再查|切换|起草)/u.test(text)))return
  const content=text.replace(/^答[：:]\s*/u,'').trim()
  if(!content)return reply(['请写下这一问的记录。'])
  const answers=[...guided.answers,content], questions=guided.questions
  const lines=answers.length===questions.length
    ?['复盘草稿已整理。四项答复均来自本次对话，尚未保存到项目记录。',...questions.map((q,i)=>`${q}\n${answers[i]}`)]
    :[`已记下第 ${answers.length} 问。`,questions[answers.length],'可以直接回答这一问。']
  return {answer:{lines,sources:[]},context:{basisQuestion:context.basisQuestion,guided:{questions,answers}}}
}
