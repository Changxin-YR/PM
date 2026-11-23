import {
  Activity,ArrowLeftRight,Bell,BookOpen,CalendarDays,ClipboardCheck,Database,FileCheck2,
  FileClock,FileText,Flag,Folder,House,Inbox,KeyRound,Layers3,Link,List,Network,Paperclip,
  ShieldCheck,SquareCheck,TriangleAlert,UserRound,Users,Wallet,Bot,
} from 'lucide-vue-next'
import type {Component} from 'vue'

export type NavigationItem = {key:string; label:string; icon:Component}
export type NavigationGroup = {label:string; items:NavigationItem[]}

export const globalNavigation = [
  {label:'导航', items:[{key:'ledger',label:'项目台账',icon:Folder}]},
  {label:'我的工作', items:[
    {key:'approvals',label:'我的审批收件箱',icon:Inbox},
    {key:'personal-tasks',label:'我的补强待办',icon:ClipboardCheck},
    {key:'termination',label:'我的终止确认',icon:SquareCheck},
  ]},
  {label:'项目协作', items:[
    {key:'appointments',label:'人员任命',icon:UserRound},
    {key:'incoming-risk',label:'风险接收',icon:ShieldCheck},
    {key:'alerts',label:'预警中心',icon:Bell},
    {key:'spending',label:'立项前支出',icon:Wallet},
  ]},
  {label:'个人授权', items:[{key:'agent-tokens',label:'我的 Agent 令牌',icon:KeyRound}]},
]

export const adminNavigation = {label:'组织管理', items:[
  {key:'audit',label:'审计记录',icon:FileClock},
  {key:'directory',label:'组织人员',icon:Users},
  {key:'org-tokens',label:'组织令牌',icon:Link},
]}

export const projectNavigation = [
  {label:'项目总览', items:[{key:'overview',label:'概览',icon:House}]},
  {label:'计划与资料', items:[
    {key:'charter',label:'立项卡',icon:FileText},
    {key:'milestones',label:'里程碑',icon:Flag},
    {key:'requirements',label:'需求',icon:List},
    {key:'baseline',label:'需求基线',icon:Layers3},
    {key:'exceptions',label:'需求例外',icon:FileCheck2},
    {key:'changes',label:'变更台账',icon:ArrowLeftRight},
    {key:'evidence',label:'证据附件',icon:Paperclip},
  ]},
  {label:'执行与治理', items:[
    {key:'risks',label:'风险',icon:TriangleAlert},
    {key:'costs',label:'成本',icon:Database},
    {key:'health',label:'三线健康',icon:Activity},
    {key:'reviews',label:'关口送审',icon:ShieldCheck},
    {key:'lifecycle',label:'阶段流转',icon:Network},
  ]},
  {label:'协作与沉淀', items:[
    {key:'tasks',label:'补强任务',icon:SquareCheck},
    {key:'reports',label:'日报周报',icon:CalendarDays},
    {key:'stakeholders',label:'联系台账',icon:UserRound},
    {key:'members',label:'项目成员',icon:Users},
    {key:'retrospective',label:'复盘与教训',icon:BookOpen},
    {key:'assistant',label:'项目助理',icon:Bot},
  ]},
]
