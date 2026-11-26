<script setup lang="ts">
import {ref} from 'vue'
import {CircleCheck,Folder,List} from 'lucide-vue-next'
import PmoBrand from './PmoBrand.vue'

const formPanel=ref<HTMLElement>()
function focusForm(){formPanel.value?.querySelector('input')?.focus()}
defineExpose({focusForm})
</script>

<template>
  <main class="auth-layout">
    <div class="auth-brand-panel">
      <PmoBrand/>
      <div class="auth-brand-content">
        <div class="auth-intro">
          <p>让项目协作，更清晰</p>
          <span>人与 Agent 共同使用的项目工作台</span>
        </div>
        <div class="auth-illustration" aria-hidden="true">
          <div class="auth-node"><Folder :size="22"/></div>
          <div class="auth-node"><List :size="22"/></div>
          <div class="auth-node"><CircleCheck :size="22"/></div>
        </div>
      </div>
    </div>
    <div ref="formPanel" class="auth-form-panel">
      <div class="login"><slot/></div>
    </div>
  </main>
</template>
