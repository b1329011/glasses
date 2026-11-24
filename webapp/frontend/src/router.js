import { createRouter, createWebHashHistory } from 'vue-router'
import AnnotateView from './views/AnnotateView.vue'
import TrainView from './views/TrainView.vue'
import DetectView from './views/DetectView.vue'
import NavView from './views/NavView.vue'
import OutdoorNavView from './views/OutdoorNavView.vue'

// 進入時依裝置決定首頁：手機 → 語音導航；電腦 → 開發介面(標註)
function isMobile() {
  try {
    return window.matchMedia('(max-width: 768px)').matches ||
      /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
  } catch (_) { return false }
}

// 主要頁面：標註 / 訓練 / 辨識測試 / 指標導航（手動輸入目標）/ 語音導航（Agent C，含台北車站指標導引）
const routes = [
  { path: '/', redirect: () => (isMobile() ? '/nav' : '/annotate') },
  { path: '/annotate', name: 'annotate', component: AnnotateView, meta: { title: '標註' } },
  { path: '/train', name: 'train', component: TrainView, meta: { title: '訓練' } },
  { path: '/detect', name: 'detect', component: DetectView, meta: { title: '辨識測試' } },
  { path: '/go', name: 'go', component: NavView, meta: { title: '指標導航', full: true } },
  { path: '/nav', name: 'nav', component: OutdoorNavView, meta: { title: '語音導航' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
