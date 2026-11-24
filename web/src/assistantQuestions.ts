export type AssistantTopic = 'health' | 'cost' | 'requirements' | 'fact' | 'lesson' | 'requirement_history' |
  'forecast' | 'spending' | 'weekly_health'

type QuestionTarget = { mode: 'ledger'; topic: AssistantTopic; keyword?: string } |
  { mode: 'charter' | 'closing'; keyword?: string }

export function interpretQuestion(question: string): QuestionTarget | undefined {
  const text = question.normalize('NFKC').trim()
  const quoted = text.match(/“([^”]+)”|「([^」]+)」|"([^"]+)"/u)
  const keyword = (quoted?.[1] ?? quoted?.[2] ?? quoted?.[3])?.trim().slice(0, 160)
  const plain = quoted ? text.replace(quoted[0], '') : text
  if (/立项前|没.{0,6}立项|未.{0,6}立项|立项.*(?:未通过|没通过)/u.test(plain) && /花钱|支出|成本|流水/u.test(plain)) {
    return {mode:'ledger', topic:'spending'}
  }
  if (/本周|这周|周内|这个星期/u.test(plain) && /黄灯|红灯|黄红|红黄|预警/u.test(plain)) {
    return {mode:'ledger', topic:'weekly_health'}
  }
  if (/需求/u.test(plain) && /变更|改过|改动|变动/u.test(plain)) {
    return {mode:'ledger', topic:'requirement_history', keyword}
  }
  if (/里程碑/u.test(plain) && /延期|推迟|延后|晚于/u.test(plain)) return {mode:'ledger', topic:'forecast'}
  if (/结项|收尾/u.test(plain) && /缺|门槛|条件|能否|能不能/u.test(plain)) return {mode:'closing'}
  if (/立项/u.test(plain) && /分数|评分|弱项|多少分/u.test(plain)) return {mode:'charter'}
  if (/教训|踩(?:过|了)?(?:什么)?坑/u.test(plain)) {
    return /类似|相似|同类/u.test(plain) && !keyword ? {mode:'charter'} : {mode:'ledger',topic:'lesson',keyword}
  }
  if (/三线|健康度|健康灯/u.test(plain) || (/时间|进度/u.test(plain) && /钱|金额|成本/u.test(plain) && /人天/u.test(plain))) {
    return {mode:'ledger', topic:'health'}
  }
  if (/满足率|需求确认率/u.test(plain)) return {mode:'ledger', topic:'requirements'}
  if (/成本|人天|预算|工时|偏差率/u.test(plain)) return {mode:'ledger', topic:'cost'}
  if (/事实|核验记录|\bfact\b/iu.test(plain) && keyword) return {mode:'ledger', topic:'fact', keyword}
  return undefined
}
