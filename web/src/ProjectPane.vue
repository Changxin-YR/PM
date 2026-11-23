<script setup lang="ts">
import {ref,watch} from 'vue'

const props=defineProps<{name:string; active:boolean}>()
const visited=ref(false)
watch(()=>props.active,value=>{if(value)visited.value=true},{immediate:true})
</script>

<template>
  <div v-show="active" :id="'project-pane-'+name" role="tabpanel" :aria-labelledby="'project-tab-'+name" class="project-pane">
    <slot v-if="visited"/>
  </div>
</template>
