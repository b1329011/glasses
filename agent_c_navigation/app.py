"""
Agent C 語音導航 —— 單獨啟動用（不含指標辨識）
====================================================
一般使用請啟動合併後端（webapp/backend，一個後端包含指標辨識 + Agent C 導航）：
    cd webapp/backend && uvicorn main:app --port 8000

這支只在「只想測導航、電腦沒裝 YOLO」時使用：
    python3 app.py
    → https://你的電腦區網IP:8080  開舊版單頁介面（static_index.html）
    指標辨識會改用 HTTP 呼叫 SIGN_DETECTOR_URL，或設 SIGN_DETECTOR_MOCK=1 用模擬結果。

手機要用麥克風/GPS/相機必須是 https，所以啟動時會自動產生一張自簽憑證
（第一次連線會跳「憑證不受信任」，點「進階」→「繼續前往」即可）。
"""

import os
import tempfile

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from nav_api import router, AGENT_C_DIR

app = FastAPI(title="Agent C 語音導航（單獨啟動）")
app.include_router(router, prefix="/api")


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(AGENT_C_DIR, "static_index.html"), "r", encoding="utf-8") as f:
        return f.read()


def _self_signed_cert():
    """產生一次性的自簽憑證（取代 Flask 的 ssl_context='adhoc'）；沒裝 cryptography 就回傳 None。"""
    try:
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return None
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent-c-local")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .sign(key, hashes.SHA256()))
    d = tempfile.mkdtemp(prefix="agent_c_ssl_")
    key_path, cert_path = os.path.join(d, "key.pem"), os.path.join(d, "cert.pem")
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


if __name__ == "__main__":
    PORT = 8080  # 避開 macOS AirPlay 佔用的 5000 埠
    ssl = _self_signed_cert()
    scheme = "https" if ssl else "http"
    print("=" * 50)
    print("Agent C 語音導航（單獨啟動；一般請改用 webapp/backend 合併後端）")
    print(f"  {scheme}://你的電腦區網IP:{PORT}")
    if not ssl:
        print("  [提醒] 沒裝 cryptography，改用 http；手機上麥克風/GPS 可能無法使用")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT,
                ssl_keyfile=ssl[0] if ssl else None, ssl_certfile=ssl[1] if ssl else None)
