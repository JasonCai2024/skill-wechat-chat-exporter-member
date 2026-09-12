#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wechat_db.py -- 微信 4.x WCDB 只读解密驱动与会话消息提取模块 (社群会员版)

基于 sqlcipher3 和 zstandard，直接对微信本地加密数据库做内存只读查询，
无须物理脱机落盘。支持通讯录解析、模糊匹配会话、zstd流式解压与纯文本提取。
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    try:
        from pysqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        sqlcipher = None

import zstandard

# 全局 zstd 解压器实例
_ZSTD_DECOMPRESSOR = zstandard.ZstdDecompressor()


def dict_row_factory(cursor, row):
    """通用的字典 Row Factory，兼容所有 DB-API 2 实现。"""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_db_salt(db_path: str | Path) -> str:
    """读取 SQLite/WCDB 文件头部 16 字节 Salt。"""
    with open(str(db_path), "rb") as f:
        return f.read(16).hex()


def open_encrypted_db(db_path: str | Path, key_hex: str) -> sqlite3.Connection:
    """以只读与 WAL 兼容模式打开 SQLCipher 4 加密数据库。"""
    if sqlcipher is None:
        raise ImportError(
            "未检测到 sqlcipher3 模块。\n"
            "Windows / macOS 请通过 pip 安装: pip install sqlcipher3 zstandard\n"
            "若在 macOS Apple Silicon 上编译遇到问题，可使用: brew install sqlcipher && pip install pysqlcipher3"
        )

    uri_path = Path(db_path).resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlcipher.connect(uri_path, uri=True)
    conn.row_factory = dict_row_factory
    cur = conn.cursor()
    cur.execute(f"PRAGMA key = \"x'{key_hex}'\";")
    cur.execute("PRAGMA cipher_compatibility = 4;")
    cur.execute("PRAGMA cipher_page_size = 4096;")
    cur.execute("PRAGMA kdf_iter = 256000;")
    cur.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512;")
    cur.execute("PRAGMA cipher_default_kdf_algorithm = PBKDF2_HMAC_SHA512;")
    return conn


def decompress_content(val: Any) -> str:
    """智能解压 WCDB 压缩文本（支持普通文本、utf-8 字符串与 zstd 压缩帧）。"""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if not isinstance(val, (bytes, bytearray)):
        return str(val)

    # 检查 zstandard magic 报头: 0x28 0xB5 0x2F 0xFD
    if val.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            return _ZSTD_DECOMPRESSOR.decompress(val).decode("utf-8", errors="replace")
        except Exception:
            pass

    return val.decode("utf-8", errors="replace")


def get_message_table_name(username: str) -> str:
    """根据会话用户名计算微信 WCDB 消息表名 Msg_<MD5>。"""
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


class WeChatDatabaseManager:
    """微信 4.x 本地数据库管理器。"""

    def __init__(self, db_dir: str | Path, keys_map: dict[str, str]):
        self.db_dir = Path(db_dir).resolve()
        self.keys_map = keys_map  # {salt_hex: key_hex}

        self.contact_db_path = self.db_dir / "contact" / "contact.db"
        self.session_db_path = self.db_dir / "session" / "session.db"

        # 扫描所有的 message_*.db
        msg_dir = self.db_dir / "message"
        self.message_db_paths = sorted(
            [p for p in msg_dir.glob("message_*.db") if not p.name.endswith("-wal") and not p.name.endswith("-shm")],
            key=lambda x: x.name,
        )

        self._contacts: dict[str, dict[str, Any]] | None = None

    def _get_key_for_db(self, db_path: Path) -> str:
        salt = get_db_salt(db_path)
        key = self.keys_map.get(salt)
        if not key:
            raise KeyError(f"未找到数据库 {db_path.name} (Salt={salt}) 的有效解密密钥")
        return key

    def load_contacts(self) -> dict[str, dict[str, Any]]:
        """从 contact.db 加载联系人与群聊索引。"""
        if self._contacts is not None:
            return self._contacts

        contacts = {}
        if not self.contact_db_path.exists():
            self._contacts = contacts
            return contacts

        try:
            key = self._get_key_for_db(self.contact_db_path)
            conn = open_encrypted_db(self.contact_db_path, key)
            cur = conn.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contact'")
            if cur.fetchone():
                for row in cur.execute("SELECT username, nick_name, remark, alias, verify_flag FROM contact").fetchall():
                    u = str(row["username"] or "").strip()
                    if not u:
                        continue
                    remark = str(row["remark"] or "").strip()
                    nick_name = str(row["nick_name"] or "").strip()
                    alias = str(row["alias"] or "").strip()
                    verify_flag = int(row["verify_flag"] or 0)
                    display_name = remark or nick_name or alias or u
                    contacts[u] = {
                        "username": u,
                        "remark": remark,
                        "nick_name": nick_name,
                        "alias": alias,
                        "verify_flag": verify_flag,
                        "display_name": display_name,
                        "is_group": u.endswith("@chatroom"),
                    }
            conn.close()
        except Exception as e:
            print(f"[WARN] 加载通讯录失败: {e}")

        self._contacts = contacts
        return contacts

    @staticmethod
    def is_official_or_system_account(username: str, contact_info: dict[str, Any] | None = None) -> bool:
        """精准识别是否为微信公众号（订阅号/服务号）、系统聚合会话或内置功能号。"""
        u = (username or "").strip().lower()
        if not u:
            return True
        # 1. 微信公众号/服务号专属前缀 gh_
        if u.startswith("gh_"):
            return True
        # 2. 常见系统会话、折叠聚合与通知功能号
        system_accounts = {
            "brandsessionholder",
            "brandservicesessionholder",
            "notifymessage",
            "newsapp",
            "fmessage",
            "floatbottle",
            "medianote",
            "qmessage",
            "qqmail",
            "weixin",
            "weibo",
            "masssendgetmsg",
            "readerapp",
            "blogapp",
            "facebookapp",
            "voiceinputapp",
            "voicevoipapp",
            "qqfriend",
            "appbrandcustomerservicemsg",
        }
        if u in system_accounts or "sessionholder" in u:
            return True
        # 3. 通讯录 verify_flag 识别 (公众号/企业认证号 verify_flag 通常非零，普通用户为 0)
        if contact_info:
            vf = contact_info.get("verify_flag", 0)
            if vf not in (0, None):
                return True
        return False

    def list_active_sessions(self, limit: int = 100, exclude_official: bool = True) -> list[dict[str, Any]]:
        """从 session.db 读取活跃会话列表，默认自动过滤所有公众号与系统通知。"""
        sessions = []
        if not self.session_db_path.exists():
            return sessions

        contacts = self.load_contacts()
        try:
            key = self._get_key_for_db(self.session_db_path)
            conn = open_encrypted_db(self.session_db_path, key)
            cur = conn.cursor()

            # 优先查询 SessionTable 表
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='SessionTable'")
            if cur.fetchone():
                # 若启用过滤，拉取稍大基数后做二次纯净过滤
                fetch_limit = limit * 3 if exclude_official else limit
                query = "SELECT username, unread_count, summary, last_timestamp FROM SessionTable ORDER BY last_timestamp DESC LIMIT ?"
                for r in cur.execute(query, (fetch_limit,)).fetchall():
                    u = str(r["username"] or "")
                    c_info = contacts.get(u, {})

                    # 过滤公众号和系统通知
                    if exclude_official and self.is_official_or_system_account(u, c_info):
                        continue

                    sessions.append({
                        "username": u,
                        "display_name": c_info.get("display_name") or u,
                        "unread": int(r["unread_count"] or 0),
                        "summary": str(r["summary"] or ""),
                        "last_time": int(r["last_timestamp"] or 0),
                        "is_group": u.endswith("@chatroom"),
                    })
                    if len(sessions) >= limit:
                        break
            conn.close()
        except Exception as e:
            print(f"[WARN] 加载会话列表失败: {e}")

        return sessions

    def resolve_chat_target(self, query: str, exclude_official: bool = True) -> tuple[str, str, bool]:
        """根据用户输入的名称/ID智能解析会话目标（优先排除公众号）。

        返回: (username, display_name, is_group)
        """
        query = query.strip()
        contacts = self.load_contacts()

        # 1. 精准用户名匹配
        if query in contacts:
            info = contacts[query]
            return info["username"], info["display_name"], info["is_group"]

        # 2. 匹配备注名（排除公众号）
        for u, info in contacts.items():
            if exclude_official and self.is_official_or_system_account(u, info):
                continue
            if info["remark"] and query.lower() == info["remark"].lower():
                return u, info["display_name"], info["is_group"]

        # 3. 匹配昵称（排除公众号）
        for u, info in contacts.items():
            if exclude_official and self.is_official_or_system_account(u, info):
                continue
            if info["nick_name"] and query.lower() == info["nick_name"].lower():
                return u, info["display_name"], info["is_group"]

        # 4. 模糊匹配备注/昵称（排除公众号）
        for u, info in contacts.items():
            if exclude_official and self.is_official_or_system_account(u, info):
                continue
            if query.lower() in info["display_name"].lower():
                return u, info["display_name"], info["is_group"]

        # 5. 兜底
        is_group = query.endswith("@chatroom")
        return query, query, is_group

    def extract_chat_messages(
        self,
        target_username: str,
        self_display_name: str = "CC",
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """跨所有 message_*.db 分库检索并提取纯文本消息。"""
        tbl_name = get_message_table_name(target_username)
        contacts = self.load_contacts()

        since_ts = int(datetime.datetime.strptime(since, "%Y-%m-%d").timestamp()) if since else None
        until_ts = int(datetime.datetime.strptime(until, "%Y-%m-%d").replace(hour=23, minute=59, second=59).timestamp()) if until else None

        all_msgs: list[dict[str, Any]] = []

        for db_path in self.message_db_paths:
            if not db_path.exists():
                continue

            try:
                key = self._get_key_for_db(db_path)
                conn = open_encrypted_db(db_path, key)
                cur = conn.cursor()

                # 检查该库中是否存在目标消息表
                cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl_name,))
                if not cur.fetchone():
                    conn.close()
                    continue

                # 读取 Name2Id 表
                sender_names = {}
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Name2Id'")
                if cur.fetchone():
                    for r in cur.execute("SELECT rowid, user_name FROM Name2Id").fetchall():
                        sender_names[int(r["rowid"])] = str(r["user_name"] or "")

                # 检查所需字段
                cols = {r["name"] for r in cur.execute(f'PRAGMA table_info("{tbl_name}")').fetchall()}
                content_col = "message_content" if "message_content" in cols else "compress_content"

                # 强约束：仅提取纯文本 (local_type = 1)
                sql = f'SELECT local_id, real_sender_id, create_time, {content_col} as content FROM "{tbl_name}" WHERE local_type = 1'
                params: list[Any] = []
                if since_ts:
                    sql += " AND create_time >= ?"
                    params.append(since_ts)
                if until_ts:
                    sql += " AND create_time <= ?"
                    params.append(until_ts)
                sql += " ORDER BY create_time ASC, local_id ASC"

                for row in cur.execute(sql, params).fetchall():
                    raw_content = decompress_content(row["content"])
                    clean_text = raw_content.strip()

                    # 若群聊正文中包含 wxid_xxxx:\n 前缀，剔除报头
                    sender_wxid = sender_names.get(int(row["real_sender_id"] or 0), "")
                    if ":\n" in clean_text:
                        parts = clean_text.split(":\n", 1)
                        if len(parts) == 2 and ("wxid_" in parts[0] or parts[0] == sender_wxid):
                            clean_text = parts[1].strip()

                    if not clean_text:
                        continue

                    # 发言人昵称解析
                    if sender_wxid in ("wxid_hjl4i7u6vllc22", "jasoncai1101") or sender_wxid == "":
                        sender_name = self_display_name
                    else:
                        c_info = contacts.get(sender_wxid, {})
                        sender_name = c_info.get("display_name") or sender_wxid or "未知用户"

                    create_time = int(row["create_time"] or 0)
                    dt = datetime.datetime.fromtimestamp(create_time)

                    all_msgs.append({
                        "local_id": int(row["local_id"]),
                        "create_time": create_time,
                        "dt": dt,
                        "sender": sender_name,
                        "text": clean_text,
                    })

                conn.close()
            except Exception as e:
                print(f"[WARN] 读取 {db_path.name} 出错: {e}")

        # 全局升序排序
        all_msgs.sort(key=lambda x: (x["create_time"], x["local_id"]))
        return all_msgs
