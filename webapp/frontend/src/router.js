import { createRouter, createWebHashHistory } from 'vue-router'
import NavView from './views/NavView.vue'
import OutdoorNavView from './views/OutdoorNavView.vue'

// 頁面：語音導航（Agent C，含台北車站指標導引）/ 指標導航（手動輸入目標）
// （標註、訓練、辨識測試頁已移除；後端的訓練 API 仍保留）
const routes = [
  { path: '/', redirect: '/nav' },
  { path: '/nav', name: 'nav', component: OutdoorNavView, meta: { title: '語音導航' } },
  { path: '/go', name: 'go', component: NavView, meta: { title: '指標導航', full: true } },
  { path: '/:pathMatch(.*)*', redirect: '/nav' },   // 舊書籤（#/annotate 等）導回首頁
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
