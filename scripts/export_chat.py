#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_chat.py -- 微信 4.x 聊天记录导出助手 CLI 主入口 (社群会员认证版)

通过 ServiceHub 远程密码机安全鉴权，安全提取指定微信私聊或群聊会话中的纯文本聊天记录，
按自然月分块清洗为标准化 Markdown 文档。
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import os
import sys
from collections import defaultdict
from pathlib import Path

# 将当前 scripts 目录加入 sys.path，保证完全独立运行
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import wechat_db
import wechat_key


def find_default_db_dir() -> Path | None:
    """自动扫描定位本机微信 4.x 当前活跃账号的 db_storage 目录（按最后更新时间判定）。"""
    candidates = []
    if sys.platform == "darwin":
        candidates.extend([
            Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
            Path.home() / "Documents/xwechat_files",
            Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat",
            Path.home() / "Library/Application Support/com.tencent.xinWeChat",
        ])
    elif sys.platform == "win32":
        candidates.extend([
            Path.home() / "Documents" / "xwechat_files",
            Path.home() / "Documents" / "WeChat Files",
            Path("D:/xwechat_files"),
            Path("E:/xwechat_files"),
            Path("C:/xwechat_files"),
        ])
    else:
        candidates.extend([
            Path.home() / "Documents" / "xwechat_files",
            Path.home() / ".xwechat_files",
        ])

    found = []
    for base in candidates:
        if not base.exists():
            continue
        if base.name == "db_storage" and (base / "session" / "session.db").exists():
            return base

        for pattern in ("*/db_storage", "*/*/db_storage"):
            for db_s in base.glob(pattern):
                if db_s.is_dir():
                    sess_p = db_s / "session" / "session.db"
                    mtime = sess_p.stat().st_mtime if sess_p.exists() else db_s.stat().st_mtime
                    found.append((mtime, db_s))

    if found:
        found.sort(key=lambda x: x[0], reverse=True)
        return found[0][1]
    return None


def format_msg_time(dt: datetime.datetime) -> str:
    """格式化单条消息时间，匹配 '2025年01月15日  9:23' 或 '2025年04月30日 20:34'。"""
    return f"{dt.year}年{dt.month:02d}月{dt.day:02d}日 {dt.hour:2d}:{dt.minute:02d}"


def get_file_ts(dt: datetime.datetime) -> str:
    """生成文件名起止时间戳 YYYYMMDDHHMM。"""
    return dt.strftime("%Y%m%d%H%M")


def handle_login_interactive():
    """交互式配置会员账号与授权密码。"""
    print("=== ServiceHub 社群会员登录绑定 ===")
    user = input("请输入您的社群会员用户名 (username): ").strip()
    if not user:
        print("[ERROR] 用户名不能为空。")
        sys.exit(1)

    pwd = getpass.getpass("请输入您的会员密码或授权码 (passtoken): ").strip()
    if not pwd:
        print("[ERROR] 授权密码不能为空。")
        sys.exit(1)

    default_srv = wechat_key.DEFAULT_SERVICEHUB_URL
    srv = input(f"请输入授权服务器地址 [默认: {default_srv}]: ").strip()
    if not srv:
        srv = default_srv

    wechat_key.save_servicehub_credentials(user, pwd, srv)
    print(f"\n[+] 会员凭据已成功保存至: {wechat_key.get_credentials_path()}")
    print("[+] 以后直接运行导出命令即可，无需重复输入密码。")
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="微信 4.x 聊天记录导出助手 (社群会员版 - 基于 ServiceHub 远程密码机鉴权)"
    )
    parser.add_argument("--login", action="store_true", help="交互式绑定并保存社群会员账号凭证")
    parser.add_argument("-t", "--target", help="目标群聊名称、好友昵称/备注或微信ID (如: CC付费答疑群)")
    parser.add_argument("-o", "--output-dir", help="导出 Markdown 文件的目标保存目录")
    parser.add_argument("--db-dir", help="微信数据根目录 db_storage 路径 (留空则自动探测)")
    parser.add_argument("--user", help="ServiceHub 会员用户名 (覆盖本地配置)")
    parser.add_argument("--token", help="ServiceHub 会员密码或授权码 (覆盖本地配置)")
    parser.add_argument("--server", help="ServiceHub 授权服务器地址 (默认: https://www.ccailab.top)")
    parser.add_argument("--key", help="单个 64 位十六进制解密密钥 (直接单机调试模式)")
    parser.add_argument("--self-name", default="CC", help="账号本人在导出记录中的统一显示名称 (默认: CC)")
    parser.add_argument("--since", help="起始日期过滤 (格式: YYYY-MM-DD)")
    parser.add_argument("--until", help="截止日期过滤 (格式: YYYY-MM-DD)")
    parser.add_argument("-l", "--list", action="store_true", help="列出当前微信最近活跃的会话列表")
    parser.add_argument("--limit", type=int, default=20, help="列出会话的最大条数 (默认: 20)")

    args = parser.parse_args()

    # 0. 登录绑定流程
    if args.login:
        handle_login_interactive()

    # 1. 定位微信本地数据库根目录
    db_dir = Path(args.db_dir) if args.db_dir else find_default_db_dir()
    if not db_dir or not db_dir.exists():
        os_hint = "macOS 默认在 ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files" if sys.platform == "darwin" else "Windows 默认在 Documents/xwechat_files"
        print(f"[ERROR] 未找到微信 4.x 的 db_storage 数据目录 ({os_hint})，请使用 --db-dir 手动指定。")
        sys.exit(1)

    print(f"[+] 微信本地数据目录: {db_dir}")

    # 2. 通过 ServiceHub 远程密码机解算密钥
    try:
        keys_map = wechat_key.load_keys_mapping_member(
            db_dir,
            username=args.user,
            passtoken=args.token,
            server_url=args.server,
            single_key_hex=args.key,
        )
        print(f"[+] 会员认证通过，远程密码机已成功解密并校验 {len(keys_map)} 个本地分库密钥")
    except (PermissionError, ConnectionError, RuntimeError) as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    # 3. 初始化数据库管理器
    manager = wechat_db.WeChatDatabaseManager(db_dir, keys_map)

    # 4. 若传入 --list 参数，列出活跃会话并退出
    if args.list:
        sessions = manager.list_active_sessions(limit=args.limit)
        print(f"\n=== 最近活跃的 {len(sessions)} 个会话列表 ===")
        for idx, s in enumerate(sessions, 1):
            type_tag = "[群聊]" if s["is_group"] else "[私聊]"
            last_dt = datetime.datetime.fromtimestamp(s["last_time"]).strftime("%Y-%m-%d %H:%M") if s["last_time"] else "-"
            print(f"{idx:2d}. {type_tag} {s['display_name']} ({s['username']}) | 最新时间: {last_dt}")
            if s["summary"]:
                print(f"    摘要: {s['summary'][:40]}")
        sys.exit(0)

    # 5. 校验导出目标
    if not args.target:
        print("[ERROR] 必须指定导出目标 (--target / -t) 或使用 --list 查看会话列表。")
        sys.exit(1)

    target_username, target_display_name, is_group = manager.resolve_chat_target(args.target)
    type_str = "群聊" if is_group else "私聊"
    print(f"[+] 识别目标会话: {target_display_name} ({target_username}) [{type_str}]")

    # 确定输出目录
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = Path.cwd() / "output" / target_display_name

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[+] 导出保存目录: {output_dir}")

    # 6. 提取纯文本消息
    print(f"[+] 正在检索并解析纯文本消息...")
    all_msgs = manager.extract_chat_messages(
        target_username,
        self_display_name=args.self_name,
        since=args.since,
        until=args.until,
    )

    if not all_msgs:
        print("[WARN] 未找到符合条件的纯文本消息（可能该会话无文本记录或时间范围内无发言）。")
        sys.exit(0)

    print(f"[+] 共提取到有效纯文本消息: {len(all_msgs)} 条")

    # 7. 按自然月分组
    monthly_groups = defaultdict(list)
    for msg in all_msgs:
        ym = msg["dt"].strftime("%Y-%m")
        monthly_groups[ym].append(msg)

    # 8. 逐月生成规范化 Markdown 文档
    exported_records = []
    for ym in sorted(monthly_groups.keys()):
        msgs = monthly_groups[ym]
        if not msgs:
            continue

        start_ts = get_file_ts(msgs[0]["dt"])
        end_ts = get_file_ts(msgs[-1]["dt"])
        filename = f"{target_display_name}_{start_ts}-{end_ts}.md"
        filepath = output_dir / filename

        # 组装标准三行式正文
        blocks = []
        for m in msgs:
            sender_line = m["sender"]
            time_line = format_msg_time(m["dt"])
            body = m["text"]
            blocks.append(f"{sender_line}\n{time_line}\n{body}\n")

        doc_content = "\n" + "\n".join(blocks).strip() + "\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(doc_content)

        exported_records.append({
            "month": ym,
            "filename": filename,
            "count": len(msgs),
            "size": filepath.stat().st_size,
        })

    # 9. 自动配套输出目录的 README.md
    readme_path = output_dir / "README.md"
    if not readme_path.exists():
        readme_content = f"""# {target_display_name} 聊天记录归档

> **目标名称**：{target_display_name}  
> **会话类型**：{type_str}  
> **会话标识**：`{target_username}`  
> **归档时间**：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}  
> **归档规范**：
> 1. 按月归档为 Markdown 文档，彻底排除图片、表格等非文本噪音；
> 2. 文件命名严格遵循方案 A：`{target_display_name}_YYYYMMDDHHMM-YYYYMMDDHHMM.md`；
> 3. 单条消息排版遵循三行式标准（发言人、日期时间、消息正文）。
"""
        with open(readme_path, "w", encoding="utf-8") as rf:
            rf.write(readme_content)

    # 10. 输出结果清单
    print(f"\n=== 导出完成！共生成 {len(exported_records)} 个自然月归档文档 ===")
    print(f"{'月份':<9} | {'纯文本条数':<8} | {'文件大小':<10} | 文件名")
    print("-" * 75)
    for rec in exported_records:
        print(f"{rec['month']:<9} | {rec['count']:<8} | {rec['size']:<10} | {rec['filename']}")
    print("-" * 75)
    print(f"所有文件已成功写入: {output_dir}")


if __name__ == "__main__":
    main()
