"""Chinese Tkinter launcher for the Windows installer.

The window is not imported by the CLI. The Windows installer launches
``TankExtractGUI.exe``; ``tankextract.exe`` stays the command-line tool.
Those names differ by more than case, because Windows treats
``TankExtract.exe`` and ``tankextract.exe`` as one file.
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from pathlib import Path


def build_cli_args(
    input_path: str,
    output_path: str,
    *,
    local_offset: bool = False,
    no_tree_filter: bool = False,
    exclude: str = "",
    max_faces: int | None = None,
    precision_m: float | None = None,
    name_prefix: str | None = None,
    material: str | None = None,
    model_origin: str = "",
    quality: str | None = None,
) -> list[str]:
    """Arguments passed to ``tankextract.cli.main`` (no program name)."""
    args = ["--input", input_path, "--output", output_path]
    if local_offset:
        args.append("--local-offset")
    if no_tree_filter:
        args.append("--no-tree-filter")
    if max_faces is not None:
        args.extend(["--max-faces", str(int(max_faces))])
    if precision_m is not None:
        args.extend(["--precision", f"{float(precision_m):g}"])
    if name_prefix:
        args.extend(["--name-prefix", name_prefix.strip()])
    if material:
        args.extend(["--material", material.strip()])
    if quality:
        args.extend(["--quality", quality])
    origin = model_origin.replace("，", ",").strip()
    if origin:
        parts = [p.strip() for p in origin.split(",")]
        if len(parts) == 3:
            args.extend(["--model-origin", *parts])
    text = exclude.replace("，", ",").strip()
    if text:
        args.extend(["--exclude", text])
    return args


def launch() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    from tankextract import __version__
    from tankextract.tools import resolve_blender

    root = tk.Tk()
    root.title(f"罐体提取 {__version__}")
    root.minsize(720, 520)
    try:
        root.tk.call("tk", "scaling", 1.4)
    except tk.TclError:
        pass

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
    quality_var = tk.StringVar(value="budget")
    blender = resolve_blender(None)
    status = tk.StringVar(value="Blender: " + (blender or "未找到"))

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
        path = filedialog.askopenfilename(
            title="选择 OSGB zip",
            filetypes=[("zip", "*.zip"), ("全部", "*.*")],
        )
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
    ttk.Label(grid, text="面数预算（budget 时减曲面，不删件）").grid(row=0, column=0, sticky="w")
    ttk.Entry(grid, textvariable=faces_var, width=8).grid(row=0, column=1, padx=4)
    ttk.Label(grid, text="质量").grid(row=0, column=2, sticky="w", padx=(12, 0))
    ttk.Combobox(grid, textvariable=quality_var, values=("budget", "full"), width=8, state="readonly").grid(row=0, column=3, padx=4)
    ttk.Label(grid, text="精度 m（罐体弦高，不改栏杆）").grid(row=3, column=0, sticky="w")
    ttk.Entry(grid, textvariable=precision_var, width=8).grid(row=3, column=1, padx=4)
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

    messages: queue.Queue[str] = queue.Queue()
    running = {"on": False}

    def append(text: str) -> None:
        log.insert("end", text)
        log.see("end")

    def poll() -> None:
        try:
            while True:
                append(messages.get_nowait())
        except queue.Empty:
            pass
        root.after(120, poll)

    def worker(argv: list[str]) -> None:
        from tankextract.cli import main as cli_main

        class _Writer:
            def write(self, data: str) -> None:
                if data:
                    messages.put(data)

            def flush(self) -> None:
                return None

        out = _Writer()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = out
        sys.stderr = out
        code = 1
        try:
            code = cli_main(argv)
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
        except Exception:
            messages.put(traceback.format_exc())
            code = 1
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            messages.put(f"\n结束，退出码 {code}\n")
            running["on"] = False

    def start() -> None:
        if running["on"]:
            return
        src = input_var.get().strip()
        dst = output_var.get().strip()
        if not src or not dst:
            messagebox.showwarning("罐体提取", "请选择输入和输出路径。")
            return
        if not Path(src).exists():
            messagebox.showerror("罐体提取", f"输入不存在：\n{src}")
            return
        argv = build_cli_args(
            src,
            dst,
            local_offset=bool(local_var.get()),
            no_tree_filter=bool(tree_var.get()),
            exclude=exclude_var.get(),
            max_faces=int(faces_var.get() or "2000"),
            precision_m=float(precision_var.get() or "0.5"),
            name_prefix=prefix_var.get(),
            material=material_var.get(),
            model_origin=origin_var.get(),
            quality=quality_var.get(),
        )
        log.delete("1.0", "end")
        append("命令: tankextract " + " ".join(argv) + "\n\n")
        running["on"] = True
        threading.Thread(target=worker, args=(argv,), daemon=True).start()

    ttk.Button(frame, text="开始提取", command=start).grid(row=7, column=0, sticky="w", **pad)
    poll()
    root.mainloop()
