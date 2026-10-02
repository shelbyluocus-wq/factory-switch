"""Small local proof UI. All credentials stay in switcher.py's encrypted storage."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import switcher as core


class App:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.events = queue.Queue()
        root.title("Factory 快速切号器 · 验证版")
        root.geometry("720x440")
        root.minsize(660, 380)
        root.protocol("WM_DELETE_WINDOW", self.close)
        container = ttk.Frame(root, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Factory 快速切号器", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(container, text="账号在本机加密保存。切换会重启 Factory，请先结束正在执行的任务。",
                  wraplength=650).pack(anchor="w", pady=(8, 12))
        self.current = tk.StringVar(value="正在读取当前账号…")
        ttk.Label(container, textvariable=self.current).pack(anchor="w", pady=(0, 10))
        self.table = ttk.Treeview(container, columns=("label", "email"), show="headings", selectmode="browse", height=7)
        self.table.heading("label", text="账号备注")
        self.table.heading("email", text="登录邮箱")
        self.table.column("label", width=200)
        self.table.column("email", width=410)
        self.table.pack(fill="both", expand=True)
        row = ttk.Frame(container)
        row.pack(fill="x", pady=14)
        self.buttons = []
        for title, command in [("保存当前账号", self.save), ("登录新账号", self.new_login),
                               ("切换所选账号", self.switch), ("在线验证", self.verify), ("刷新", self.refresh)]:
            button = ttk.Button(row, text=title, command=command)
            button.pack(side="left", padx=(0, 7))
            self.buttons.append(button)
        self.note = tk.StringVar(value="就绪")
        ttk.Label(container, textvariable=self.note, wraplength=650).pack(anchor="w")
        self.root.after(100, self.drain)
        self.refresh()

    def close(self):
        if self.busy:
            messagebox.showinfo("操作进行中", "请等当前操作结束再关闭。", parent=self.root)
        else:
            self.root.destroy()

    def run(self, action, done):
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.state(["disabled"])
        self.note.set("正在处理，请稍候…")

        def worker():
            try:
                with core.exclusive():
                    result = action()
                self.events.put((done, result, None))
            except core.SwitchError as exc:
                self.events.put((done, None, str(exc)))
            except Exception as exc:
                self.events.put((done, None, "操作失败：" + type(exc).__name__ + "。未输出敏感内容。"))

        threading.Thread(target=worker, daemon=False).start()

    def drain(self):
        try:
            done, result, error = self.events.get_nowait()
            self.busy = False
            for button in self.buttons:
                button.state(["!disabled"])
            if error:
                self.note.set(error)
                messagebox.showerror("操作未完成", error, parent=self.root)
            else:
                done(result)
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def render(self, status, note="列表已更新；当前账号依据本地登录文件识别。"):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        for account in status["saved_accounts"]:
            self.table.insert("", "end", iid=account["account_id"], values=(account["label"], account.get("email", "")))
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        account = status["local_identity"]
        self.current.set("当前账号：" + (account.get("email") or account["account_id"]) if account else "当前未登录：请在 Factory 完成登录，再点击“保存当前账号”。")
        self.note.set(note)

    def refresh(self):
        self.run(core.status, self.render)

    def save(self):
        label = simpledialog.askstring("保存当前账号", "账号备注（可留空，默认使用邮箱）：", parent=self.root)
        if label is None:
            return

        def action():
            core.capture(label.strip())
            return core.status()

        self.run(action, lambda state: self.render(state, "当前账号已加密保存，并完成备份回读校验。"))

    def new_login(self):
        if not messagebox.askokcancel("登录新账号", "将保存当前账号并重启 Factory 到登录页。\n请先结束 Factory 中正在执行的任务。", parent=self.root):
            return

        def action():
            core.activate(None)
            return core.status()

        self.run(action, lambda state: self.render(state, "请在 Factory 登录另一个账号，再点击“保存当前账号”。"))

    def switch(self):
        selection = self.table.selection()
        if not selection:
            messagebox.showinfo("选择账号", "请先选择一个已保存的账号。", parent=self.root)
            return
        label = self.table.item(selection[0], "values")[0]
        if not messagebox.askokcancel("切换账号", "将重启 Factory 并切换到：" + label + "\n请先结束正在执行的任务。", parent=self.root):
            return

        def action():
            core.activate(selection[0])
            return core.status()

        self.run(action, lambda state: self.render(state, "本地登录状态已切换，Factory 已启动；可点击“在线验证”检查账号有效性。"))

    def verify(self):
        def done(result):
            if result.get("server_verified"):
                self.note.set("在线验证成功：Factory 服务端确认当前登录有效，账号一致。")
            else:
                self.note.set("在线验证未通过：" + str(result.get("error") or "HTTP " + str(result.get("http_status", "未知"))) + "。请在 Factory 检查登录状态。")
        self.run(core.verify, done)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
