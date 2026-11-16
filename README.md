# ServerHub 🖥️

**ServerHub** 是一個輕量級的本機多伺服器管理桌面工具（Python + CustomTkinter），專為管理多組本機微服務（如 FastAPI 後端、AI Agent 橋接伺服器、Bot API 等）而設計，內建 **Cloudflare Quick Tunnel** 公網穿透、**配對 QR Code** 產生，以及提供給 **AI Agent 自動化聯動** 的本機 Control API。

---

## ✨ 核心功能

- **多伺服器集中管理**：透過桌面 GUI 或本機 API 一鍵啟動、停止、重啟多個伺服器專案。
- **自動公網穿透（Cloudflare Quick Tunnel）**：支援 `cloudflared` 臨時公網 HTTPS 通道，免 VPN、免設定路由器即可讓手機 App 連線。
- **一鍵配對 QR Code**：自動讀取各伺服器的 Tunnel 網址與連線 Token，產生配對 QR Code 供手機端掃描連線。
- **背景無黑窗啟動**：提供 `launch_serverhub.vbs` 靜默啟動器，不殘留黑色終端機視窗。
- **AI Agent 本機控制介面**：內建本機 HTTP Control API 與 `hub_ctl.py` CLI，讓 AI Agent 可程式化重啟服務與讀取狀態。

---

## 📦 標準 Server 包規範

任何要匯入或上傳（資料夾或 `.zip`）到 ServerHub 的標準伺服器包，建議包含以下檔案結構：

```text
my-server/
├── server_manifest.json    # 【必要】ServerHub 識別與啟動設定檔
├── main.py                 # 【必要】伺服器主程式（對應 entry_command）
├── requirements.txt        # 【選填】Python 依賴清單（匯入時可自動/手動安裝）
├── .env.example            # 【建議】環境變數範本（實際金鑰放 .env，勿入版控）
└── .env                    # 【選填】本機執行的 Token / 環境變數
```

### `server_manifest.json` 範例與欄位說明

```json
{
  "id": "my-server-id",
  "name": "顯示名稱",
  "version": "1.0.0",
  "entry_command": ["python", "main.py"],
  "port": 8000,
  "health_url": "http://127.0.0.1:8000/api/health",
  "enable_tunnel": true,
  "token_file": ".env",
  "token_key": "API_TOKEN",
  "description": "伺服器簡短說明"
}
```

- `id`：唯一識別碼（英數字與 `-` / `_`），供 GUI 與 `hub_ctl.py` 指定目標伺服器。
- `name`：顯示在左側清單的名稱。
- `entry_command`：啟動指令陣列（相對於該伺服器包根目錄執行，若專案內有 `.venv` 可指向 `.venv/Scripts/python.exe`）。
- `port`：伺服器監聽的本機通訊埠。
- `health_url`（選填）：健康檢查 URL，用於判斷服務是否已就緒。
- `enable_tunnel`：是否於啟動時自動建立 Cloudflare Quick Tunnel。
- `token_file` / `token_key`（選填）：自動讀取並顯示連線 Token（若 `token_key` 留空，則直接讀取整個檔案內容）。

---

## 🤖 如何與 AI Agent 聯動（API 實戰範例）

ServerHub 在本機提供 **Control API**（只監聽 `127.0.0.1`，並使用 `data/control_token.txt` 的 `X-Hub-Token` 驗證）以及封裝好的 `hub_ctl.py`，非常適合與 AI Coding Agent（例如 Antigravity）整合形成自動化閉環：

### 1. 透過 `hub_ctl.py` 指令聯動（最簡單）

AI Agent 在修改完後端程式碼或發布新版 APK 後，可直接呼叫 `hub_ctl.py` 控制服務：

```bash
# 確保 ServerHub 本體正在背景運行
python hub_ctl.py ensure-running

# 查看所有受管伺服器的運行狀態、本機 Port、Tunnel 網址與 Token
python hub_ctl.py status

# 啟動 / 停止 / 重啟指定 ID 的伺服器
python hub_ctl.py start antigravity-bridge
python hub_ctl.py stop antigravity-bridge
python hub_ctl.py restart antigravity-bridge
```

### 2. 直接呼叫本機 HTTP Control API

若要由其他程式或 Agent 工具直接呼叫本機 API，只需帶上 `X-Hub-Token`（讀取自 `data/control_token.txt`）：

- `GET /api/health`：檢查 ServerHub 控制端是否存活（免驗證）。
- `GET /api/servers`：取得所有已註冊伺服器清單與即時狀態（`running`、`tunnel_url`、`port` 等）。
- `POST /api/servers/{server_id}/start`：啟動指定伺服器。
- `POST /api/servers/{server_id}/stop`：停止指定伺服器。
- `POST /api/servers/{server_id}/restart`：重啟指定伺服器（程式更新後常用）。

### 3. 常見的 AI Agent 聯動工作流範例

1. **遠端收指令**：Agent 在背景執行長輪詢監聽腳本（例如連接橋接伺服器的 `/api/wait` 或 WebSocket），接收來自手機端的開發任務。
2. **本機開發與建置**：Agent 依指令修改程式碼、跑測試或編譯 Android APK。
3. **透過 ServerHub 熱重啟與發布**：
   - 若有修改後端程式，Agent 執行 `python hub_ctl.py restart <server_id>` 套用新程式碼。
   - 透過 `hub_ctl.py status` 取得最新 Tunnel 網址，或呼叫橋接伺服器的 `/api/app/notify` 推播通知給手機端。
4. **回報並繼續待命**：Agent 將結果發回手機聊天室，並重新啟動監聽腳本等待下一道指令。

---

## 🚀 啟動方式

- **日常使用（無終端黑窗）**：雙擊 `launch_serverhub.vbs`。
- **開發偵錯（顯示終端輸出）**：執行 `python app.py` 或 `launch_serverhub.bat`。
