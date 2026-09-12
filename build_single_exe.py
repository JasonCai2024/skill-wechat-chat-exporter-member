#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_single_exe.py -- 微信 4.x 聊天记录导出助手自动化单文件打包脚本

使用 PyInstaller 将 gui_app.py 编译为单个绿色免安装可执行文件 (.exe)，
并直接输出至 E:\BaiduSyncdisk\LocalHub\微信聊天记录导出助手.exe
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    root_dir = Path(__file__).resolve().parent
    entry_script = root_dir / "scripts" / "gui_app.py"
    target_dist_dir = root_dir
    target_dist_dir.mkdir(parents=True, exist_ok=True)

    app_name = "微信聊天记录导出助手"
    target_exe = target_dist_dir / f"{app_name}.exe"

    print(f"==================================================")
    print(f"🚀 开始构建单文件可执行程序 (.exe)")
    print(f"入口文件: {entry_script}")
    print(f"目标目录: {target_dist_dir}")
    print(f"目标程序: {target_exe}")
    print(f"==================================================")

    # 构造 PyInstaller 构建参数
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",                     # 单文件模式
        "--windowed",                    # 无黑窗口
        f"--name={app_name}",
        f"--distpath={str(target_dist_dir)}",
        f"--paths={str(root_dir / 'scripts')}",
        "--collect-all=customtkinter",   # 完整收集 customtkinter 样式和资源
        "--collect-all=sqlcipher3",      # 完整收集 sqlcipher3 动态库
        "--collect-all=zstandard",       # 完整收集 zstd 解密解压驱动
        "--hidden-import=darkdetect",
        "--hidden-import=urllib.request",
        "--hidden-import=urllib.error",
        "--hidden-import=hmac",
        "--hidden-import=hashlib",
        str(entry_script),
    ]

    print("[+] 执行构建命令:")
    print(" ".join(cmd))
    print("-" * 50)

    res = subprocess.run(cmd, cwd=str(root_dir))
    if res.returncode != 0:
        print(f"\n❌ 打包构建失败，退出码: {res.returncode}")
        sys.exit(res.returncode)

    if target_exe.exists():
        size_mb = target_exe.stat().st_size / (1024 * 1024)
        print("\n" + "=" * 50)
        print(f"🎉 打包成功！独立单文件可执行程序已就绪:")
        print(f"文件路径: {target_exe}")
        print(f"文件大小: {size_mb:.2f} MB")
        print("=" * 50)
    else:
        print(f"\n❌ 未在目标路径找到生成的文件: {target_exe}")
        sys.exit(1)


if __name__ == "__main__":
    main()
