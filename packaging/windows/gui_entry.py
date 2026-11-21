"""Windowed launcher. Does not import tankextract so the bundle stays small.

It starts the sibling console program ``tankextract.exe`` and shows its log.
The windowed file is ``TankExtractGUI.exe``: on Windows that is a different
file from ``tankextract.exe``, while ``TankExtract.exe`` would not be.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path


def cli_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve().with_name("tankextract.exe"))]
    return [sys.executable, "-m", "tankextract"]


def bundled_blender() -> str:
    if not getattr(sys, "frozen", False):
        return ""
    candidate = Path(sys.executable).resolve().parent / "blender" / "blender.exe"
    return str(candidate) if candidate.is_file() else ""


def main() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("罐体提取")
    root.minsize(720, 520)

    input_var = tk.StringVar()
    output_var = tk.StringVar()
    local_var = tk.BooleanVar(value=True)
    tree_var = tk.BooleanVar(value=False)
    exclude_var = tk.StringVar()
    faces_var = tk.StringVar(value="2000")
    precision_var = tk.StringVar(value="0.50")
    prefix_var = tk.StringVar(value="LA")
    material_var = tk.StringVar(value="Standard")
    origin_var = tk.StringVar()
    blender = bundled_blender()
    status = tk.StringVar(value="Blender: " + (blender or "安装目录或本机已安装的 Blender"))

    pad = {"padx": 8, "pady": 4}
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="OSGB 块（文件夹或 zip）").grid(row=0, column=0, sticky="w", **pad)
    ttk.Entry(frame, textvariable=input_var, width=70).grid(row=1, column=0, columnspan=2, sticky="ew", **pad)

    def pick_dir() -> None:
        path = filedialog.askdirectory(title="选择 OSGB 块文件夹")
        if path:
            input_var.set(path)

    def pick_zip() -> None:
        path = filedialog.askopenfilename(title="选择 OSGB zip", filetypes=[("zip", "*.zip"), ("全部", "*.*")])
        if path:
            input_var.set(path)

    buttons = ttk.Frame(frame)
    buttons.grid(row=1, column=2, sticky="e", **pad)
    ttk.Button(buttons, text="文件夹…", command=pick_dir).pack(side="left", padx=2)
    ttk.Button(buttons, text="zip…", command=pick_zip).pack(side="left", padx=2)

    ttk.Label(frame, text="输出文件夹").grid(row=2, column=0, sticky="w", **pad)
    ttk.Entry(frame, textvariable=output_var, width=70).grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

    def pick_out() -> None:
        path = filedialog.askdirectory(title="选择输出文件夹")
        if path:
            output_var.set(path)

    ttk.Button(frame, text="浏览…", command=pick_out).grid(row=3, column=2, sticky="e", **pad)

    opts = ttk.Frame(frame)
    opts.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
    ttk.Checkbutton(opts, text="局部坐标输出（推荐，避免大坐标精度损失）", variable=local_var).pack(anchor="w")
    ttk.Checkbutton(opts, text="关闭树冠过滤", variable=tree_var).pack(anchor="w")
    row = ttk.Frame(opts)
    row.pack(anchor="w", pady=4)
    ttk.Label(row, text="排除罐编号（如 Tank_001,Tank_059）").pack(side="left")
    ttk.Entry(row, textvariable=exclude_var, width=40).pack(side="left", padx=6)
    grid = ttk.Frame(opts)
    grid.pack(anchor="w", pady=4)
    ttk.Label(grid, text="面数/单体（超出只警告，不删细节）").grid(row=0, column=0, sticky="w")
    ttk.Entry(grid, textvariable=faces_var, width=8).grid(row=0, column=1, padx=4)
    ttk.Label(grid, text="精度 m（记录，不减面）").grid(row=0, column=2, sticky="w", padx=(12, 0))
    ttk.Entry(grid, textvariable=precision_var, width=8).grid(row=0, column=3, padx=4)
    ttk.Label(grid, text="命名前缀").grid(row=1, column=0, sticky="w")
    ttk.Entry(grid, textvariable=prefix_var, width=8).grid(row=1, column=1, padx=4)
    ttk.Label(grid, text="材质名").grid(row=1, column=2, sticky="w", padx=(12, 0))
    ttk.Entry(grid, textvariable=material_var, width=12).grid(row=1, column=3, padx=4)
    ttk.Label(grid, text="约定原点 X,Y,Z（空=metadata）").grid(row=2, column=0, sticky="w")
    ttk.Entry(grid, textvariable=origin_var, width=28).grid(row=2, column=1, columnspan=3, sticky="w", padx=4)

    ttk.Label(frame, textvariable=status).grid(row=5, column=0, columnspan=3, sticky="w", **pad)
    log = scrolledtext.ScrolledText(frame, height=16, wrap="word")
    log.grid(row=6, column=0, columnspan=3, sticky="nsew", **pad)
    frame.rowconfigure(6, weight=1)
    frame.columnconfigure(0, weight=1)
    run_button = ttk.Button(frame, text="开始提取")
    run_button.grid(row=7, column=0, sticky="w", **pad)

    def start() -> None:
        src = input_var.get().strip()
        dst = output_var.get().strip()
        if not src or not dst:
            messagebox.showwarning("罐体提取", "请选择输入和输出路径。")
            return
        if not Path(src).exists():
            messagebox.showerror("罐体提取", f"输入不存在：\n{src}")
            return
        argv = ["--input", src, "--output", dst]
        if local_var.get():
            argv.append("--local-offset")
        if tree_var.get():
            argv.append("--no-tree-filter")
        argv.extend(["--max-faces", faces_var.get().strip() or "2000"])
        argv.extend(["--precision", precision_var.get().strip() or "0.50"])
        argv.extend(["--name-prefix", prefix_var.get().strip() or "LA"])
        argv.extend(["--material", material_var.get().strip() or "Standard"])
        origin = origin_var.get().replace("，", ",").strip()
        if origin:
            parts = [p.strip() for p in origin.split(",")]
            if len(parts) == 3:
                argv.extend(["--model-origin", *parts])
        text = exclude_var.get().replace("，", ",").strip()
        if text:
            argv.extend(["--exclude", text])
        cmd = cli_command() + argv
        log.delete("1.0", "end")
        log.insert("end", "命令: " + " ".join(cmd) + "\n\n")
        run_button.state(["disabled"])

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as exc:
                root.after(0, lambda: log.insert("end", f"无法启动：{exc}\n"))
                root.after(0, lambda: run_button.state(["!disabled"]))
                return

            def read() -> None:
                assert proc.stdout is not None
                for line in proc.stdout:
                    root.after(0, lambda text=line: (log.insert("end", text), log.see("end")))
                code = proc.wait()
                root.after(0, lambda: log.insert("end", f"\n结束，退出码 {code}\n"))
                root.after(0, lambda: run_button.state(["!disabled"]))

            threading.Thread(target=read, daemon=True).start()

        worker()

    run_button.configure(command=start)
    root.mainloop()


if __name__ == "__main__":
    main()
