<script setup lang="ts">
import {nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {ArrowLeft,LogOut,Menu,UserRound,X} from 'lucide-vue-next'
import PmoBrand from './PmoBrand.vue'
import type {NavigationGroup} from './navigation'

const props=defineProps<{
  active:string
  groups:NavigationGroup[]
  title:string
  account:string
  project?:{name:string;code:string}
}>()
const emit=defineEmits<{navigate:[key:string]; back:[]; logout:[]}>()
const open=ref(false)
const mobileToggle=ref<HTMLButtonElement>()
const mobileClose=ref<HTMLButtonElement>()
const content=ref<HTMLElement>()
const sidebar=ref<HTMLElement>()
const wide=window.matchMedia('(min-width: 1000px)')

function desktopChanged(){if(wide.matches)open.value=false}
onMounted(()=>wide.addEventListener('change',desktopChanged))
onUnmounted(()=>wide.removeEventListener('change',desktopChanged))
watch(open,async value=>{
  if(value){
    await nextTick()
    mobileClose.value?.focus()
  }
})
function closeNavigation(){
  open.value=false
  void nextTick(()=>mobileToggle.value?.focus())
}
function navigate(key:string){
  emit('navigate',key)
  if(open.value){
    open.value=false
    void nextTick(()=>content.value?.focus({preventScroll:true}))
  }
}
function moveTab(event:KeyboardEvent){
  const tabs=Array.from(sidebar.value?.querySelectorAll<HTMLButtonElement>('[role="tab"]')??[])
  const from=tabs.indexOf(event.target as HTMLButtonElement)
  if(from<0)return
  let to:number
  switch(event.key){
    case 'ArrowDown': to=(from+1)%tabs.length;break
    case 'ArrowUp': to=(from+tabs.length-1)%tabs.length;break
    case 'Home': to=0;break
    case 'End': to=tabs.length-1;break
    default: return
  }
  event.preventDefault()
  tabs[to]?.focus()
}
function trapNavigation(event:KeyboardEvent){
  if(!open.value)return
  if(event.key==='Escape'){
    event.preventDefault()
    closeNavigation()
  }
  if(event.key!=='Tab')return
  const controls=sidebar.value?.querySelectorAll<HTMLButtonElement>('button:not([tabindex="-1"])')
  if(!controls?.length)return
  const first=controls[0],last=controls[controls.length-1]
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
  if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
}
</script>

<template>
  <div class="app-shell" :role="project?'region':undefined"
    :aria-label="project?`${project.name} · 项目资料`:undefined" :data-testid="project?'project-workspace':undefined">
    <div v-if="open" class="nav-scrim" aria-hidden="true" @click="closeNavigation"/>
    <aside id="context-sidebar" ref="sidebar" class="context-sidebar" :class="{'is-open':open}"
      :role="open?'dialog':undefined" :aria-modal="open?true:undefined" :aria-label="open?'工作台导航':undefined"
      @keydown="trapNavigation">
      <div class="sidebar-brand">
        <PmoBrand/>
        <button ref="mobileClose" class="icon-button nav-close" type="button" title="关闭导航" aria-label="关闭导航" @click="closeNavigation"><X :size="20"/></button>
      </div>
      <nav class="context-menu" :aria-label="project?'项目导航':'工作台导航'">
        <div :role="project?'tablist':undefined" :aria-orientation="project?'vertical':undefined"
          :aria-label="project?'项目模块':undefined" @keydown="project && moveTab($event)">
          <div v-for="group in groups" :key="group.label" class="nav-group"
            :role="project?'group':undefined" :aria-label="project?group.label:undefined">
            <div class="nav-caption" :aria-hidden="project?true:undefined">{{group.label}}</div>
            <button v-for="item in group.items" :id="project?'project-tab-'+item.key:undefined" :key="item.key"
              type="button" class="nav-entry" :class="{'is-current':active===item.key}"
              :role="project?'tab':undefined" :aria-selected="project?active===item.key:undefined"
              :aria-controls="project?'project-pane-'+item.key:undefined" :tabindex="project&&active!==item.key?-1:undefined"
              :aria-current="!project&&active===item.key?'page':undefined" @click="navigate(item.key)">
              <component :is="item.icon" :size="17" aria-hidden="true"/><span>{{item.label}}</span>
            </button>
          </div>
        </div>
      </nav>
      <div class="sidebar-account">
        <div class="account-name"><span class="account-avatar"><UserRound :size="16" aria-hidden="true"/></span><span>{{account}}</span></div>
        <button class="nav-entry" type="button" @click="emit('logout')"><LogOut :size="17" aria-hidden="true"/>退出登录</button>
      </div>
    </aside>
    <div class="shell-body" :inert="open?true:undefined">
      <header class="shell-header">
        <button ref="mobileToggle" class="icon-button nav-toggle" type="button" aria-label="打开导航" aria-controls="context-sidebar" :aria-expanded="open" @click="open=true"><Menu :size="20"/></button>
        <template v-if="project || active!=='ledger'">
          <button type="button" class="return-link" @click="emit('back')"><ArrowLeft :size="16" aria-hidden="true"/>返回项目台账</button>
          <span class="header-divider" aria-hidden="true"/>
        </template>
        <span v-if="project" class="project-code">{{project.code}}</span>
        <span v-else>工作台 <span aria-hidden="true">/</span> {{title}}</span>
      </header>
      <main ref="content" class="shell-content" tabindex="-1"><slot/></main>
    </div>
  </div>
</template>
