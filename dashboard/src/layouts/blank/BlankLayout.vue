<template>
  <v-app>
    <RouterView />
    <ProjectTodoNotices v-if="route.meta.requiresAuth" />
  </v-app>
</template>
<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router';
import ProjectTodoNotices from '@/components/chat/ProjectTodoNotices.vue';
const route = useRoute();
</script>
