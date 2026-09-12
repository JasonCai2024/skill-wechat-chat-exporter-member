#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_macos.py -- 微信 4.x 聊天记录导出助手 macOS 客户端打包脚本

需在 macOS 系统（Intel 或 Apple Silicon M 系列）下执行。
使用 PyInstaller 将 gui_app.py 编译为原生 macOS Application Bundle (.app) 或独立执行文件。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "darwin":
        print("❌ 提示: macOS 打包依赖 Apple 原生 Cocoa 框架与 Mach-O 编译器，必须在 macOS 系统环境下执行。")
        print("💡 若您手头只有 Windows 电脑，推荐使用工程内的 GitHub Actions 脚本在云端 Mac 虚拟机自动编译！")
        sys.exit(1)

    root_dir = Path(__file__).resolve().parent
    entry_script = root_dir / "scripts" / "gui_app.py"
    target_dist_dir = root_dir / "dist_macos"
    target_dist_dir.mkdir(parents=True, exist_ok=True)

    app_name = "微信聊天记录导出助手"

    print("==================================================")
    print("🍎 开始构建 macOS 原生应用包 (.app)")
    print(f"入口文件: {entry_script}")
    print(f"输出目录: {target_dist_dir}")
    print("==================================================")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",                    # 生成 .app 格式
        f"--name={app_name}",
        f"--distpath={str(target_dist_dir)}",
        f"--paths={str(root_dir / 'scripts')}",
        "--collect-all=customtkinter",
        "--collect-all=zstandard",
        "--hidden-import=darkdetect",
        "--hidden-import=urllib.request",
        "--hidden-import=urllib.error",
        "--hidden-import=hmac",
        "--hidden-import=hashlib",
    ]

    # 检测 sqlcipher3 或 pysqlcipher3
    try:
        import sqlcipher3
        cmd.append("--collect-all=sqlcipher3")
    except ImportError:
        try:
            import pysqlcipher3
            cmd.append("--collect-all=pysqlcipher3")
        except ImportError:
            pass

    cmd.append(str(entry_script))

    print("[+] 执行构建命令:")
    print(" ".join(cmd))
    print("-" * 50)

    res = subprocess.run(cmd, cwd=str(root_dir))
    if res.returncode != 0:
        print(f"\n❌ 构建失败，退出码: {res.returncode}")
        sys.exit(res.returncode)

    app_path = target_dist_dir / f"{app_name}.app"
    if app_path.exists():
        print("\n" + "=" * 50)
        print(f"🎉 打包成功！macOS 原生应用已生成:")
        print(f"应用路径: {app_path}")
        print("==================================================")
    else:
        print(f"\n❌ 未找到生成的应用文件: {app_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
