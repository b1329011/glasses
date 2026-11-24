<script setup>
// 主框架：頂部導覽 + 路由出口
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">🔎 指標牌辨識操作介面</div>
      <nav class="nav">
        <RouterLink to="/annotate">① 標註</RouterLink>
        <RouterLink to="/train">② 訓練</RouterLink>
        <RouterLink to="/detect">③ 辨識測試</RouterLink>
        <RouterLink to="/go">🧭 導航</RouterLink>
      </nav>
    </header>
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand { font-weight: 700; font-size: 18px; }
.nav { display: flex; gap: 8px; }
.nav a {
  color: var(--muted);
  text-decoration: none;
  padding: 8px 14px;
  border-radius: 6px;
  font-weight: 600;
}
.nav a.router-link-active {
  color: #04283a;
  background: var(--accent);
}
.content { padding: 24px; max-width: 1200px; margin: 0 auto; }

/* 手機：導覽列改為換行、內距縮小 */
@media (max-width: 640px) {
  .topbar { flex-direction: column; align-items: stretch; gap: 10px; padding: 12px 16px; }
  .nav { justify-content: space-between; }
  .nav a { flex: 1; text-align: center; padding: 8px 6px; font-size: 14px; }
  .content { padding: 16px; }
}
</style>
