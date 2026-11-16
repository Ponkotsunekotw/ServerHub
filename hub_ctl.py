from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from hub_core import CONTROL_TOKEN_FILE, DEFAULT_API_HOST, DEFAULT_API_PORT, ROOT, ensure_control_token


def api_base() -> str:
    return f"http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}"


def request_api(method: str, path: str, body: Optional[dict[str, Any]] = None, auth: bool = True) -> Any:
    url = api_base() + path
    headers: dict[str, str] = {}
    data: Optional[bytes] = None
    if auth:
        token = CONTROL_TOKEN_FILE.read_text(encoding="utf-8-sig").strip() if CONTROL_TOKEN_FILE.exists() else ensure_control_token()
        headers["X-Hub-Token"] = token
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {err_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"無法連線到 ServerHub Control API ({api_base()})，請確認 ServerHub 已開啟或先執行 ensure-running: {exc}") from exc


def ensure_running(timeout_sec: float = 8.0) -> dict[str, Any]:
    try:
        return request_api("GET", "/api/health", auth=False)
    except Exception:
        pass

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--headless"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            return request_api("GET", "/api/health", auth=False)
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("啟動 ServerHub headless 模式逾時")


def print_result(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if isinstance(data, dict) and "servers" in data:
        for s in data["servers"]:
            state = "RUNNING" if s["running"] else "STOPPED"
            tunnel = s["tunnel_url"] or ("starting..." if s["tunnel_running"] else "-")
            print(f"[{state}] {s['id']} ({s['name']}) :{s['port']} | tunnel={tunnel} | token={s['token'] or '-'}")
        return
    if isinstance(data, dict) and "logs" in data:
        for line in data["logs"]:
            print(line)
        return
    if isinstance(data, dict) and "server" in data:
        s = data["server"]
        state = "RUNNING" if s["running"] else "STOPPED"
        print(f"[{state}] {s['id']} ({s['name']}) :{s['port']} | tunnel={s['tunnel_url'] or '-'} | token={s['token'] or '-'}")
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="ServerHub CLI / Agent 控制介面")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式輸出")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="檢查 Control API 健康狀態")
    sub.add_parser("ensure-running", help="若 ServerHub 未啟動，則以 headless 模式背景啟動")
    sub.add_parser("status", aliases=["list"], help="列出所有伺服器狀態")

    p_get = sub.add_parser("get", help="取得單一伺服器狀態")
    p_get.add_argument("id")

    p_url = sub.add_parser("url", help="取得伺服器的公網隧道網址")
    p_url.add_argument("id")

    p_tok = sub.add_parser("token", help="取得伺服器的連線 Token")
    p_tok.add_argument("id")

    p_start = sub.add_parser("start", help="啟動伺服器")
    p_start.add_argument("id")
    p_start.add_argument("--no-tunnel", action="store_true", help="不啟動 Cloudflare 隧道")

    p_stop = sub.add_parser("stop", help="停止伺服器")
    p_stop.add_argument("id")

    p_restart = sub.add_parser("restart", help="重啟伺服器")
    p_restart.add_argument("id")
    p_restart.add_argument("--no-tunnel", action="store_true", help="不啟動 Cloudflare 隧道")

    p_logs = sub.add_parser("logs", help="查看伺服器運行日誌")
    p_logs.add_argument("id")
    p_logs.add_argument("--tail", type=int, default=80)

    p_imp_dir = sub.add_parser("import-dir", help="匯入本機伺服器資料夾")
    p_imp_dir.add_argument("path")

    p_imp_zip = sub.add_parser("import-zip", help="匯入 .zip 伺服器包")
    p_imp_zip.add_argument("zip_path")
    p_imp_zip.add_argument("--install-deps", action="store_true")

    p_rm = sub.add_parser("remove", help="移除已納管的伺服器")
    p_rm.add_argument("id")

    args = parser.parse_args()

    try:
        if args.cmd == "health":
            print_result(request_api("GET", "/api/health", auth=False), args.json)
        elif args.cmd == "ensure-running":
            print_result(ensure_running(), args.json)
        elif args.cmd in ("status", "list"):
            print_result(request_api("GET", "/api/servers"), args.json)
        elif args.cmd == "get":
            print_result(request_api("GET", f"/api/servers/{args.id}"), args.json)
        elif args.cmd == "url":
            s = request_api("GET", f"/api/servers/{args.id}")
            if args.json:
                print_result({"id": args.id, "tunnel_url": s.get("tunnel_url", "")}, True)
            else:
                print(s.get("tunnel_url", ""))
        elif args.cmd == "token":
            s = request_api("GET", f"/api/servers/{args.id}")
            if args.json:
                print_result({"id": args.id, "token": s.get("token", "")}, True)
            else:
                print(s.get("token", ""))
        elif args.cmd == "start":
            body = {"tunnel": False} if args.no_tunnel else {}
            print_result(request_api("POST", f"/api/servers/{args.id}/start", body), args.json)
        elif args.cmd == "stop":
            print_result(request_api("POST", f"/api/servers/{args.id}/stop", {}), args.json)
        elif args.cmd == "restart":
            body = {"tunnel": False} if args.no_tunnel else {}
            print_result(request_api("POST", f"/api/servers/{args.id}/restart", body), args.json)
        elif args.cmd == "logs":
            print_result(request_api("GET", f"/api/servers/{args.id}/logs?tail={args.tail}"), args.json)
        elif args.cmd == "import-dir":
            print_result(request_api("POST", "/api/servers/import-dir", {"path": args.path}), args.json)
        elif args.cmd == "import-zip":
            print_result(
                request_api(
                    "POST",
                    "/api/servers/import-zip",
                    {"zip_path": args.zip_path, "install_deps": args.install_deps},
                ),
                args.json,
            )
        elif args.cmd == "remove":
            print_result(request_api("DELETE", f"/api/servers/{args.id}"), args.json)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
