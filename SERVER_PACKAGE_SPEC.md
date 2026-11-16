# ServerHub 伺服器包規範 (`server_manifest.json`)

任何要匯入或上傳至 **ServerHub 桌面伺服器管理員** 的伺服器包（資料夾或 `.zip` 壓縮檔），只需在根目錄包含一個 `server_manifest.json`：

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

### 欄位說明
- `id`：唯一識別碼（英數字與 `-`/`_`）。
- `name`：顯示在左側清單的名稱。
- `entry_command`：啟動指令陣列（相對於伺服器根目錄執行）。
- `port`：伺服器監聽的本機通訊埠。
- `health_url`（選填）：健康檢查網址。
- `enable_tunnel`：是否預設啟用 Cloudflare Quick Tunnel（免 VPN 公網 HTTPS）。
- `token_file` / `token_key`（選填）：自動讀取並顯示連線 Token（若 `token_key` 為空，則讀取整個檔案內容）。
- 若 `.zip` 內含 `requirements.txt`，上傳後可在介面點擊安裝依賴，或由管理員自動偵測執行。
