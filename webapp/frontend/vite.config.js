import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import basicSsl from '@vitejs/plugin-basic-ssl'

// Vite 設定
//  * proxy：把 /api 代理到後端 FastAPI（port 8000），前端只打相對路徑 /api/...
//  * host: true：對外開放（區網內的手機才連得到，不再只綁 localhost）
//  * basicSsl：啟用 HTTPS（手機用攝影機必須在 HTTPS 下，瀏覽器才允許）
//    → 手機開 https://<電腦IP>:5173，第一次會有憑證警告，按「繼續前往」即可。
export default defineConfig({
  plugins: [vue(), basicSsl()],
  server: {
    host: true,       // = 0.0.0.0，允許同網段裝置連入
    port: 5173,
    // 允許 Cloudflare Tunnel 的網域連入（否則 Vite 會擋掉非 localhost 的 Host）
    allowedHosts: ['.trycloudflare.com', '.cfargotunnel.com', 'localhost'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
