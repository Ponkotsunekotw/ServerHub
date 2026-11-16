from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Optional
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from hub_core import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    ManagedServer,
    ServerManager,
    find_cloudflared,
    kill_process_tree,
    load_manifest,
    start_control_api,
)

__all__ = [
    "ManagedServer",
    "ServerHubApp",
    "find_cloudflared",
    "kill_process_tree",
    "load_manifest",
]


class ServerHubApp:
    """Tkinter desktop UI backed by ServerManager and local Control API."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ServerHub — 桌面伺服器管理員")
        self.root.geometry("1040x680")
        self.root.minsize(880, 560)
        self.root.configure(bg="#11161F")

        self.selected_id: Optional[str] = None
        self._ordered_ids: list[str] = []

        self.manager = ServerManager(on_change=self._schedule_ui_sync)
        self.api_server = start_control_api(self.manager, mode="gui")

        self._setup_styles()
        self._build_ui()
        self._refresh_list()
        self.root.after(150, self._pump_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _schedule_ui_sync(self, sid: Optional[str] = None) -> None:
        def _sync() -> None:
            if sid and sid in self.manager.servers and self.selected_id is None:
                self.selected_id = sid
            self._refresh_list()
            self._render_logs()

        try:
            self.root.after(0, _sync)
        except Exception:
            pass

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#11161F")
        style.configure("Card.TFrame", background="#1A212E", relief="flat")
        style.configure("TLabel", background="#11161F", foreground="#E5E9F0", font=("Microsoft JhengHei UI", 10))
        style.configure("Card.TLabel", background="#1A212E", foreground="#E5E9F0", font=("Microsoft JhengHei UI", 10))
        style.configure("Title.TLabel", background="#1A212E", foreground="#FFFFFF", font=("Microsoft JhengHei UI", 14, "bold"))
        style.configure("Muted.TLabel", background="#1A212E", foreground="#9AA5B4", font=("Microsoft JhengHei UI", 9))
        style.configure("TButton", font=("Microsoft JhengHei UI", 9, "bold"), padding=6)
        style.configure("Primary.TButton", background="#2563EB", foreground="#FFFFFF")
        style.map("Primary.TButton", background=[("active", "#1D4ED8")])
        style.configure("Danger.TButton", background="#DC2626", foreground="#FFFFFF")
        style.map("Danger.TButton", background=[("active", "#B91C1C")])

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill=tk.BOTH, expand=True)

        # Left sidebar
        left = ttk.Frame(main, width=280)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 14))
        left.pack_propagate(False)

        ttk.Label(left, text="已納管伺服器包", font=("Microsoft JhengHei UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 8))

        self.listbox = tk.Listbox(
            left,
            bg="#1A212E",
            fg="#E5E9F0",
            selectbackground="#2563EB",
            selectforeground="#FFFFFF",
            font=("Microsoft JhengHei UI", 10),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#2A3447",
            activestyle="none",
        )
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        ttk.Button(left, text="上傳伺服器包 (.zip)", style="Primary.TButton", command=self._upload_zip_package).pack(fill=tk.X, pady=(0, 6))
        ttk.Button(left, text="匯入本機資料夾", command=self._import_folder).pack(fill=tk.X, pady=(0, 6))
        ttk.Button(left, text="移除選取伺服器", command=self._remove_selected).pack(fill=tk.X, pady=(0, 8))

        api_text = (
            f"Agent 控制介面：http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}"
            if self.api_server
            else f"Agent 控制介面：埠 {DEFAULT_API_PORT} 已被佔用"
        )
        ttk.Label(left, text=api_text, font=("Microsoft JhengHei UI", 8), foreground="#7E8BA0").pack(anchor=tk.W)

        # Right panel
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        card = ttk.Frame(right, style="Card.TFrame", padding=16)
        card.pack(fill=tk.X, pady=(0, 12))

        self.lbl_title = ttk.Label(card, text="請選擇左側伺服器", style="Title.TLabel")
        self.lbl_title.pack(anchor=tk.W)

        self.lbl_desc = ttk.Label(card, text="", style="Muted.TLabel")
        self.lbl_desc.pack(anchor=tk.W, pady=(2, 8))

        self.lbl_status = ttk.Label(card, text="狀態：-", style="Card.TLabel")
        self.lbl_status.pack(anchor=tk.W, pady=(0, 4))

        self.lbl_path = ttk.Label(card, text="路徑：-", style="Muted.TLabel")
        self.lbl_path.pack(anchor=tk.W, pady=(0, 6))

        url_row = ttk.Frame(card, style="Card.TFrame")
        url_row.pack(fill=tk.X, pady=(2, 4))
        self.lbl_tunnel = ttk.Label(url_row, text="公網網址：未啟動", style="Card.TLabel")
        self.lbl_tunnel.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(url_row, text="複製網址", command=self._copy_tunnel_url).pack(side=tk.RIGHT)

        tok_row = ttk.Frame(card, style="Card.TFrame")
        tok_row.pack(fill=tk.X, pady=(2, 10))
        self.lbl_token = ttk.Label(tok_row, text="連線 Token：-", style="Card.TLabel")
        self.lbl_token.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(tok_row, text="配對 QR Code", style="Primary.TButton", command=self._show_pairing_qr).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(tok_row, text="複製 Token", command=self._copy_token).pack(side=tk.RIGHT)

        btn_row = ttk.Frame(card, style="Card.TFrame")
        btn_row.pack(fill=tk.X)
        self.btn_start = ttk.Button(btn_row, text="啟動伺服器", style="Primary.TButton", command=self._start_selected)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = ttk.Button(btn_row, text="停止伺服器", style="Danger.TButton", command=self._stop_selected)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_restart = ttk.Button(btn_row, text="重啟", command=self._restart_selected)
        self.btn_restart.pack(side=tk.LEFT, padx=(0, 8))
        self.tunnel_var = tk.BooleanVar(value=True)
        self.chk_tunnel = ttk.Checkbutton(btn_row, text="同時啟動免 VPN 公網通道 (Cloudflare)", variable=self.tunnel_var)
        self.chk_tunnel.pack(side=tk.LEFT, padx=(8, 0))

        log_hdr = ttk.Frame(right)
        log_hdr.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(log_hdr, text="即時運行日誌", font=("Microsoft JhengHei UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(log_hdr, text="清空日誌", command=self._clear_logs).pack(side=tk.RIGHT)

        self.log_text = scrolledtext.ScrolledText(
            right,
            bg="#0B0F17",
            fg="#D8DEE9",
            font=("Consolas", 9),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#2A3447",
            state=tk.DISABLED,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        with self.manager.lock:
            self._ordered_ids = list(self.manager.servers.keys())
            for sid in self._ordered_ids:
                srv = self.manager.servers[sid]
                dot = "●" if srv.is_running else "○"
                self.listbox.insert(tk.END, f" {dot}  {srv.name} (:{srv.port})")
        if self.selected_id in self._ordered_ids:
            idx = self._ordered_ids.index(self.selected_id)
            self.listbox.selection_set(idx)
        elif self._ordered_ids:
            self.selected_id = self._ordered_ids[0]
            self.listbox.selection_set(0)
        self._update_detail_panel()

    def _on_select(self, _event: Any = None) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self._ordered_ids):
            self.selected_id = self._ordered_ids[idx]
            srv = self.manager.servers.get(self.selected_id)
            if srv:
                self.tunnel_var.set(srv.enable_tunnel)
            self._update_detail_panel()
            self._render_logs()

    def _current_server(self) -> Optional[ManagedServer]:
        if self.selected_id:
            return self.manager.servers.get(self.selected_id)
        return None

    def _update_detail_panel(self) -> None:
        srv = self._current_server()
        if srv is None:
            self.lbl_title.config(text="尚未選擇伺服器")
            self.lbl_desc.config(text="請由左側選擇、匯入資料夾或上傳 .zip 伺服器包")
            self.lbl_status.config(text="狀態：-")
            self.lbl_path.config(text="路徑：-")
            self.lbl_tunnel.config(text="公網網址：-")
            self.lbl_token.config(text="連線 Token：-")
            return

        status_str = "運行中" if srv.is_running else "已停止"
        tunnel_str = "（通道連線中）" if srv.is_tunnel_running else ""
        self.lbl_title.config(text=f"{srv.name}  v{srv.version}")
        self.lbl_desc.config(text=srv.description)
        self.lbl_status.config(
            text=f"狀態：{status_str}  |  Port: {srv.port} {tunnel_str}",
            foreground="#34D399" if srv.is_running else "#F87171",
        )
        self.lbl_path.config(text=f"路徑：{srv.path}")
        self.lbl_tunnel.config(text=f"公網網址：{srv.tunnel_url or ('等待產生...' if srv.is_tunnel_running else '未啟動')}")
        tok = srv.read_token()
        self.lbl_token.config(text=f"連線 Token：{tok or '（無）'}")

    def _render_logs(self) -> None:
        srv = self._current_server()
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        if srv and srv.logs:
            self.log_text.insert(tk.END, "\n".join(srv.logs) + "\n")
            self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _pump_events(self) -> None:
        changed = self.manager.pump_logs()
        if self.selected_id and self.selected_id in changed:
            self._render_logs()
        self._update_detail_panel()
        self.root.after(200, self._pump_events)

    def _start_selected(self) -> None:
        srv = self._current_server()
        if srv is None:
            return
        try:
            self.manager.start_server(srv.id, enable_tunnel=bool(self.tunnel_var.get()))
        except Exception as exc:
            messagebox.showerror("啟動失敗", str(exc))

    def _stop_selected(self) -> None:
        srv = self._current_server()
        if srv is None:
            return
        self.manager.stop_server(srv.id)

    def _restart_selected(self) -> None:
        srv = self._current_server()
        if srv is None:
            return
        try:
            self.manager.restart_server(srv.id, enable_tunnel=bool(self.tunnel_var.get()))
        except Exception as exc:
            messagebox.showerror("重啟失敗", str(exc))

    def _copy_tunnel_url(self) -> None:
        srv = self._current_server()
        if srv and srv.tunnel_url:
            self.root.clipboard_clear()
            self.root.clipboard_append(srv.tunnel_url)
            messagebox.showinfo("已複製", f"已複製公網網址：\n{srv.tunnel_url}")

    def _copy_token(self) -> None:
        srv = self._current_server()
        tok = srv.read_token() if srv else ""
        if tok:
            self.root.clipboard_clear()
            self.root.clipboard_append(tok)
            messagebox.showinfo("已複製", "已複製連線 Token")

    def _show_pairing_qr(self) -> None:
        srv = self._current_server()
        if srv is None:
            return
        url = srv.tunnel_url.strip()
        if not url:
            messagebox.showwarning("尚未取得公網網址", "請先啟動伺服器並等待 Cloudflare 公網網址產生後，再產生配對 QR Code。")
            return
        tok = srv.read_token()
        import json
        try:
            import qrcode
            from PIL import ImageTk
        except ImportError as exc:
            messagebox.showerror("缺少套件", f"請先安裝 qrcode 與 pillow：{exc}")
            return

        payload = json.dumps({"app": srv.id, "url": url, "token": tok}, ensure_ascii=False)
        qr_img = qrcode.make(payload).resize((260, 260))

        win = tk.Toplevel(self.root)
        win.title(f"手機快速配對 — {srv.name}")
        win.configure(bg="#11161F")
        win.resizable(False, False)

        photo = ImageTk.PhotoImage(qr_img)
        lbl_img = tk.Label(win, image=photo, bg="#FFFFFF", padx=10, pady=10)
        lbl_img.image = photo  # Keep reference
        lbl_img.pack(padx=20, pady=(18, 10))

        ttk.Label(win, text=f"請在手機 App「設定」點擊「掃描 QR Code 快速配對」", font=("Microsoft JhengHei UI", 10, "bold")).pack(padx=16, pady=(0, 6))
        ttk.Label(win, text=f"網址：{url}", font=("Consolas", 9), foreground="#9AA5B4").pack(padx=16)
        ttk.Label(win, text=f"Token：{tok or '（無）'}", font=("Consolas", 9), foreground="#9AA5B4").pack(padx=16, pady=(2, 16))

    def _clear_logs(self) -> None:
        srv = self._current_server()
        if srv:
            srv.logs.clear()
            self._render_logs()

    def _import_folder(self) -> None:
        chosen = filedialog.askdirectory(title="選擇包含 server_manifest.json 的伺服器資料夾")
        if not chosen:
            return
        try:
            srv = self.manager.import_folder(Path(chosen))
            self.selected_id = srv.id
        except Exception as exc:
            messagebox.showerror("匯入失敗", str(exc))

    def _upload_zip_package(self) -> None:
        zip_path = filedialog.askopenfilename(
            title="選擇打包好的伺服器包 (.zip)",
            filetypes=[("Server Package Zip", "*.zip")],
        )
        if not zip_path:
            return
        try:
            srv, has_req = self.manager.import_zip(Path(zip_path), install_deps=False)
            if has_req:
                if messagebox.askyesno("安裝依賴", f"偵測到 {srv.name} 內含 requirements.txt，是否立即自動安裝套件？"):
                    import subprocess
                    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(srv.path / "requirements.txt")], check=False)
            self.selected_id = srv.id
            messagebox.showinfo("上傳成功", f"已匯入伺服器包：{srv.name} (v{srv.version})")
        except Exception as exc:
            messagebox.showerror("上傳失敗", str(exc))

    def _remove_selected(self) -> None:
        srv = self._current_server()
        if srv is None:
            return
        self.manager.remove_server(srv.id)
        self.selected_id = None

    def _on_close(self) -> None:
        running = [s for s in self.manager.servers.values() if s.is_running or s.is_tunnel_running]
        if running:
            if not messagebox.askyesno("確認關閉", f"目前仍有 {len(running)} 個伺服器運行中，關閉視窗將會一併停止它們，確定嗎？"):
                return
            self.manager.stop_all()
        if self.api_server:
            try:
                self.api_server.shutdown()
            except Exception:
                pass
        self.root.destroy()


def run_headless() -> None:
    """Run ServerHub without a GUI window, exposing the local Control API."""
    manager = ServerManager()
    api = start_control_api(manager, mode="headless")
    if api is None:
        print(f"Control API port {DEFAULT_API_PORT} is already in use.", file=sys.stderr)
        sys.exit(1)
    print(f"ServerHub headless Control API listening on http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}")
    try:
        while True:
            manager.pump_logs()
            time.sleep(0.25)
    except KeyboardInterrupt:
        manager.stop_all()
        api.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="ServerHub 桌面伺服器管理員")
    parser.add_argument("--headless", action="store_true", help="以無介面背景模式啟動，僅提供本機 Control API")
    args = parser.parse_args()

    if args.headless:
        run_headless()
        return

    root = tk.Tk()
    ServerHubApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
