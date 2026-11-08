<script setup>
import Chat from '@/components/chat/Chat.vue'
import StartupUpdateDialogs from '@/components/chat/StartupUpdateDialogs.vue'
</script>

<template>
    <div class="chat-container">
        <Chat />
        <StartupUpdateDialogs />
    </div>
</template>

<style scoped>
.chat-container {
    height: calc(100vh - 60px)
}
</style>
