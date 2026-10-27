import { createRouter, createWebHashHistory } from 'vue-router'
import AnnotateView from './views/AnnotateView.vue'
import TrainView from './views/TrainView.vue'
import DetectView from './views/DetectView.vue'
import NavView from './views/NavView.vue'

// 進入時依裝置決定首頁：手機 → 導航畫面；電腦 → 開發介面(標註)
function isMobile() {
  try {
    return window.matchMedia('(max-width: 768px)').matches ||
      /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
  } catch (_) { return false }
}

// 主要頁面：標註 / 訓練 / 辨識測試 / 手機導航
const routes = [
  { path: '/', redirect: () => (isMobile() ? '/go' : '/annotate') },
  { path: '/annotate', name: 'annotate', component: AnnotateView, meta: { title: '標註' } },
  { path: '/train', name: 'train', component: TrainView, meta: { title: '訓練' } },
  { path: '/detect', name: 'detect', component: DetectView, meta: { title: '辨識測試' } },
  { path: '/go', name: 'go', component: NavView, meta: { title: '手機導航', full: true } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
