// Service Worker — 讓網頁可安裝、離線開得起來(快取 App 殼)
// 策略：/api 一律走網路(要連後端辨識)；其餘 GET 走「線上優先」：
//   有網路 → 永遠抓最新並更新快取；沒網路 → 才用快取/App 殼。
//   (不要用 cache-first，否則改過的程式碼在手機上重新整理也看不到)
const CACHE = 'sign-nav-v3'

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (e) => {
  const req = e.request
  const url = new URL(req.url)
  // 後端 API 與非 GET：直接走網路，不快取
  if (req.method !== 'GET' || url.pathname.startsWith('/api')) return
  // 線上優先：先抓最新；抓到就順便更新快取，抓不到(離線)才用快取/App 殼
  e.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp && resp.ok && url.origin === location.origin) {
          const copy = resp.clone()
          caches.open(CACHE).then((c) => c.put(req, copy))
        }
        return resp
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('./index.html')))
  )
})
