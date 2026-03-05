<template>
  <Dashboard />
</template>

<script setup>
// 引入你写好的 Dashboard 视图
import Dashboard from './views/Dashboard.vue';
</script>

<style>
/* 去除默认的外边距，让后台占满全屏 */
body {
  margin: 0;
  padding: 0;
}
</style>