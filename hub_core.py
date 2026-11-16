from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
REGISTRY_FILE = ROOT / "registry.json"
PACKAGES_DIR = ROOT / "packages"
DATA_DIR = ROOT / "data"
CONTROL_TOKEN_FILE = DATA_DIR / "control_token.txt"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = int(os.environ.get("SERVERHUB_API_PORT", "8799"))

DEFAULT_SERVER_DIRS = [
    Path(r"C:\Users\joseph\Documents\手機app\server"),
    Path(r"C:\Users\joseph\Documents\Diana-Bot - gemini"),
]

TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def find_cloudflared() -> Optional[str]:
    """Locate cloudflared.exe in PATH or standard Windows install paths."""
    which = shutil.which("cloudflared")
    if which:
        return which
    for candidate in (
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def kill_process_tree(proc: Optional[subprocess.Popen[str]]) -> None:
    """Terminate a process and all of its child processes on Windows."""
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def ensure_control_token() -> str:
    """Read or generate the local Control API bearer token."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONTROL_TOKEN_FILE.exists():
        tok = CONTROL_TOKEN_FILE.read_text(encoding="utf-8-sig").strip()
        if tok:
            return tok
    tok = "hub-" + secrets.token_urlsafe(24)
    CONTROL_TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


@dataclass
class ManagedServer:
    """Represents a single registered server package and its runtime processes."""

    id: str
    name: str
    version: str
    path: Path
    entry_command: list[str]
    port: int
    health_url: str = ""
    enable_tunnel: bool = True
    token_file: str = ""
    token_key: str = ""
    description: str = ""
    server_proc: Optional[subprocess.Popen[str]] = None
    tunnel_proc: Optional[subprocess.Popen[str]] = None
    tunnel_url: str = ""
    logs: list[str] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.server_proc is not None and self.server_proc.poll() is None

    @property
    def is_tunnel_running(self) -> bool:
        return self.tunnel_proc is not None and self.tunnel_proc.poll() is None

    def read_token(self) -> str:
        """Read the server's client auth token from token_file / token_key."""
        if not self.token_file:
            return ""
        tf = self.path / self.token_file
        if not tf.exists():
            return ""
        try:
            text = tf.read_text(encoding="utf-8-sig").strip()
            if not self.token_key:
                return text
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(f"{self.token_key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
        return ""

    def append_log(self, line: str) -> None:
        self.logs.append(line.rstrip("\r\n"))
        if len(self.logs) > 600:
            self.logs = self.logs[-500:]

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "path": str(self.path),
            "port": self.port,
            "running": self.is_running,
            "pid": self.server_proc.pid if self.is_running and self.server_proc else None,
            "enable_tunnel": self.enable_tunnel,
            "tunnel_running": self.is_tunnel_running,
            "tunnel_url": self.tunnel_url,
            "token": self.read_token(),
            "description": self.description,
        }


def load_manifest(server_dir: Path) -> ManagedServer:
    """Load and validate server_manifest.json from a server directory."""
    manifest_path = server_dir / "server_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"找不到 server_manifest.json：{manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    cmd = data.get("entry_command")
    if not isinstance(cmd, list) or not cmd:
        raise ValueError("server_manifest.json 缺少有效的 entry_command")
    return ManagedServer(
        id=str(data.get("id") or server_dir.name),
        name=str(data.get("name") or server_dir.name),
        version=str(data.get("version") or "1.0.0"),
        path=server_dir.resolve(),
        entry_command=[str(x) for x in cmd],
        port=int(data.get("port") or 8000),
        health_url=str(data.get("health_url") or ""),
        enable_tunnel=bool(data.get("enable_tunnel", True)),
        token_file=str(data.get("token_file") or ""),
        token_key=str(data.get("token_key") or ""),
        description=str(data.get("description") or ""),
    )


class ServerManager:
    """Thread-safe lifecycle manager for all registered server packages."""

    def __init__(self, on_change: Optional[Callable[[Optional[str]], None]] = None) -> None:
        self.lock = threading.RLock()
        self.servers: dict[str, ManagedServer] = {}
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.on_change = on_change
        self.load_registry()

    def _notify(self, sid: Optional[str] = None) -> None:
        if self.on_change is not None:
            try:
                self.on_change(sid)
            except Exception:
                pass

    def load_registry(self) -> None:
        with self.lock:
            paths: list[Path] = []
            if REGISTRY_FILE.exists():
                try:
                    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8-sig"))
                    if isinstance(raw, list):
                        paths = [Path(p) for p in raw]
                    elif isinstance(raw, dict):
                        paths = [Path(p) for p in raw.get("servers", [])]
                except Exception:
                    paths = []
            if not paths:
                paths = [p for p in DEFAULT_SERVER_DIRS if p.exists()]

            for p in paths:
                if (p / "server_manifest.json").exists():
                    try:
                        srv = load_manifest(p)
                        self.servers[srv.id] = srv
                    except Exception:
                        pass
            self.save_registry()

    def save_registry(self) -> None:
        with self.lock:
            payload = {"servers": [str(s.path) for s in self.servers.values()]}
            REGISTRY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _sync_tunnel_url_via_fcm(self, sid: str, port: int, token: str, url: str) -> None:
        if not token or not url:
            return
        endpoint = f"http://127.0.0.1:{port}/api/fcm/sync-url"
        body = json.dumps({"server_url": url}).encode("utf-8")
        for _ in range(5):
            time.sleep(1.5)
            try:
                req = urllib.request.Request(
                    endpoint,
                    data=body,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    sent = res.get("sent", 0)
                    if sent:
                        self.log_queue.put((sid, f"[ServerHub] 已透過 FCM 自動同步新網址至 {sent} 台手機"))
                    else:
                        self.log_queue.put((sid, "[ServerHub] 新網址已就緒（尚未有手機註冊 FCM Token）"))
                    return
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return
            except Exception:
                continue

    def pump_logs(self) -> set[str]:
        """Drain queued log lines into each ManagedServer and extract tunnel URLs."""
        changed_ids: set[str] = set()
        with self.lock:
            while not self.log_queue.empty():
                sid, line = self.log_queue.get_nowait()
                srv = self.servers.get(sid)
                if srv is None:
                    continue
                srv.append_log(line)
                m = TUNNEL_URL_RE.search(line)
                if m:
                    new_url = m.group(0)
                    if new_url != srv.tunnel_url:
                        srv.tunnel_url = new_url
                        tok = srv.read_token()
                        threading.Thread(
                            target=self._sync_tunnel_url_via_fcm,
                            args=(srv.id, srv.port, tok, new_url),
                            daemon=True,
                        ).start()
                changed_ids.add(sid)
        return changed_ids

    def _stream_reader(self, sid: str, pipe: Any, prefix: str) -> None:
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                self.log_queue.put((sid, f"{prefix}{line.rstrip()}"))
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def get_server(self, sid: str) -> ManagedServer:
        with self.lock:
            srv = self.servers.get(sid)
            if srv is None:
                raise KeyError(f"找不到伺服器: {sid}")
            return srv

    def list_statuses(self) -> list[dict[str, Any]]:
        self.pump_logs()
        with self.lock:
            return [s.to_status_dict() for s in self.servers.values()]

    def get_logs(self, sid: str, tail: int = 100) -> list[str]:
        self.pump_logs()
        with self.lock:
            srv = self.get_server(sid)
            return srv.logs[-max(1, min(tail, 600)) :]

    def start_server(self, sid: str, enable_tunnel: Optional[bool] = None) -> ManagedServer:
        with self.lock:
            srv = self.get_server(sid)
            if srv.is_running:
                return srv
            srv.tunnel_url = ""
            if enable_tunnel is not None:
                srv.enable_tunnel = bool(enable_tunnel)

            cmd = list(srv.entry_command)
            local_exe = srv.path / cmd[0]
            if local_exe.exists():
                cmd[0] = str(local_exe.resolve())
            elif cmd[0] in ("python", "python3"):
                cmd[0] = sys.executable

            srv.append_log(f"[ServerHub] 啟動指令: {' '.join(cmd)}")
            no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(
                cmd,
                cwd=str(srv.path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=no_win,
            )
            srv.server_proc = proc
            threading.Thread(target=self._stream_reader, args=(srv.id, proc.stdout, ""), daemon=True).start()

            if srv.enable_tunnel:
                cf = find_cloudflared()
                if cf:
                    try:
                        tproc = subprocess.Popen(
                            [cf, "tunnel", "--url", f"http://127.0.0.1:{srv.port}"],
                            cwd=str(srv.path),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            creationflags=no_win,
                        )
                        srv.tunnel_proc = tproc
                        threading.Thread(
                            target=self._stream_reader,
                            args=(srv.id, tproc.stdout, "[Tunnel] "),
                            daemon=True,
                        ).start()
                    except Exception as exc:
                        srv.append_log(f"[Tunnel] 啟動失敗: {exc}")
                else:
                    srv.append_log("[Tunnel] 找不到 cloudflared.exe，僅啟動本機伺服器")

        self._notify(sid)
        return srv

    def stop_server(self, sid: str) -> ManagedServer:
        with self.lock:
            srv = self.get_server(sid)
            kill_process_tree(srv.tunnel_proc)
            srv.tunnel_proc = None
            srv.tunnel_url = ""
            kill_process_tree(srv.server_proc)
            srv.server_proc = None
            srv.append_log("[ServerHub] 伺服器已停止")
        self._notify(sid)
        return srv

    def restart_server(self, sid: str, enable_tunnel: Optional[bool] = None) -> ManagedServer:
        self.stop_server(sid)
        time.sleep(0.25)
        return self.start_server(sid, enable_tunnel=enable_tunnel)

    def stop_all(self) -> None:
        with self.lock:
            for sid, srv in list(self.servers.items()):
                if srv.is_running or srv.is_tunnel_running:
                    self.stop_server(sid)

    def import_folder(self, folder: Path) -> ManagedServer:
        srv = load_manifest(folder)
        with self.lock:
            self.servers[srv.id] = srv
            self.save_registry()
        self._notify(srv.id)
        return srv

    def import_zip(self, zip_path: Path, install_deps: bool = False) -> tuple[ManagedServer, bool]:
        """Extract and register a .zip server package with zip-slip protection."""
        if not zip_path.exists():
            raise FileNotFoundError(f"找不到壓縮檔：{zip_path}")
        PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            manifest_member = next((n for n in names if Path(n).name == "server_manifest.json"), None)
            if not manifest_member:
                raise ValueError("壓縮檔內找不到 server_manifest.json")
            manifest_data = json.loads(zf.read(manifest_member).decode("utf-8-sig"))
            pkg_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(manifest_data.get("id") or zip_path.stem))
            dest_dir = (PACKAGES_DIR / pkg_id).resolve()
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)

            prefix = str(Path(manifest_member).parent).replace("\\", "/")
            if prefix == ".":
                prefix = ""

            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel = member.filename.replace("\\", "/")
                if prefix and rel.startswith(prefix + "/"):
                    rel = rel[len(prefix) + 1 :]
                target = (dest_dir / rel).resolve()
                if not str(target).startswith(str(dest_dir)):
                    raise ValueError("偵測到不安全的壓縮檔路徑")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        srv = load_manifest(dest_dir)
        req_file = dest_dir / "requirements.txt"
        has_req = req_file.exists()
        if has_req and install_deps:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)], check=False)

        with self.lock:
            self.servers[srv.id] = srv
            self.save_registry()
        self._notify(srv.id)
        return srv, has_req

    def remove_server(self, sid: str) -> None:
        with self.lock:
            srv = self.servers.get(sid)
            if srv is None:
                raise KeyError(f"找不到伺服器: {sid}")
            if srv.is_running or srv.is_tunnel_running:
                self.stop_server(sid)
            self.servers.pop(sid, None)
            self.save_registry()
        self._notify(None)


def start_control_api(
    manager: ServerManager,
    mode: str = "gui",
    host: str = DEFAULT_API_HOST,
    port: int = DEFAULT_API_PORT,
) -> Optional[ThreadingHTTPServer]:
    """Start the local-only HTTP Control API for CLI/Agent automation."""
    token = ensure_control_token()

    class HubAPIHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check_auth(self) -> bool:
            hdr_tok = self.headers.get("X-Hub-Token", "").strip()
            auth = self.headers.get("Authorization", "").strip()
            bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if secrets.compare_digest(hdr_tok, token) or secrets.compare_digest(bearer, token):
                return True
            self._send_json(401, {"error": "Unauthorized"})
            return False

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/health":
                self._send_json(200, {"ok": True, "mode": mode, "port": port})
                return
            if not self._check_auth():
                return

            if path == "/api/servers":
                self._send_json(200, {"servers": manager.list_statuses()})
                return

            parts = path.split("/")
            if len(parts) == 4 and parts[:3] == ["", "api", "servers"]:
                sid = parts[3]
                try:
                    manager.pump_logs()
                    self._send_json(200, manager.get_server(sid).to_status_dict())
                except KeyError as exc:
                    self._send_json(404, {"error": str(exc)})
                return

            if len(parts) == 5 and parts[:3] == ["", "api", "servers"] and parts[4] == "logs":
                sid = parts[3]
                qs = parse_qs(parsed.query)
                tail = int(qs.get("tail", ["100"])[0])
                try:
                    self._send_json(200, {"id": sid, "logs": manager.get_logs(sid, tail=tail)})
                except KeyError as exc:
                    self._send_json(404, {"error": str(exc)})
                return

            self._send_json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            if not self._check_auth():
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            parts = path.split("/")
            try:
                body = self._read_json()
            except Exception as exc:
                self._send_json(400, {"error": f"Invalid JSON: {exc}"})
                return

            try:
                if path == "/api/servers/import-dir":
                    srv = manager.import_folder(Path(str(body.get("path", ""))))
                    self._send_json(200, {"server": srv.to_status_dict()})
                    return

                if path == "/api/servers/import-zip":
                    srv, has_req = manager.import_zip(
                        Path(str(body.get("zip_path", ""))),
                        install_deps=bool(body.get("install_deps", False)),
                    )
                    self._send_json(200, {"server": srv.to_status_dict(), "has_requirements": has_req})
                    return

                if len(parts) == 5 and parts[:3] == ["", "api", "servers"]:
                    sid, action = parts[3], parts[4]
                    if action == "start":
                        tunnel_opt = body.get("tunnel") if "tunnel" in body else None
                        srv = manager.start_server(sid, enable_tunnel=tunnel_opt)
                        self._send_json(200, {"server": srv.to_status_dict()})
                        return
                    if action == "stop":
                        srv = manager.stop_server(sid)
                        self._send_json(200, {"server": srv.to_status_dict()})
                        return
                    if action == "restart":
                        tunnel_opt = body.get("tunnel") if "tunnel" in body else None
                        srv = manager.restart_server(sid, enable_tunnel=tunnel_opt)
                        self._send_json(200, {"server": srv.to_status_dict()})
                        return
            except KeyError as exc:
                self._send_json(404, {"error": str(exc)})
                return
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})
                return

            self._send_json(404, {"error": "Not found"})

        def do_DELETE(self) -> None:
            if not self._check_auth():
                return
            path = urlparse(self.path).path.rstrip("/") or "/"
            parts = path.split("/")
            if len(parts) == 4 and parts[:3] == ["", "api", "servers"]:
                try:
                    manager.remove_server(parts[3])
                    self._send_json(200, {"ok": True})
                except KeyError as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            self._send_json(404, {"error": "Not found"})

    try:
        server = ThreadingHTTPServer((host, port), HubAPIHandler)
    except OSError:
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
