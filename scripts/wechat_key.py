#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wechat_key.py -- 微信 4.x 远程密码机鉴权与密钥提取模块 (社群会员版)

本模块采用“远程密码机（Remote Cryptographic Oracle）”安全架构：
1. 客户端在微信本地内存中扫描，仅提取未解密的加密特征结构体（Blobs，约100~300字节）；
2. 本地不存储任何通用 XOR 解码掩码，通过 HTTPS 将特征结构体发送至 ServiceHub 鉴权中心；
3. ServiceHub 验证会员有效性后，在云端安全解算当前机器专用的 Raw Key 并返回；
4. 本地对收到的 Raw Key 执行数据库第一页 HMAC-SHA512 双重校验，确保 100% 真实有效。
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac as hmac_mod
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# 常量定义
PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
HMAC_SZ = 64
WINDOWS_CONFIG_CIPHER_NAME = b"com.Tencent.WCDB.Config.Cipher"
WINDOWS_MAX_USER_ADDRESS = 0x0000_8000_0000_0000
WINDOWS_CONFIG_BLOB_MAX = 1024

# 默认 ServiceHub 服务器地址（可通过配置文件、环境变量或命令行参数覆盖）
DEFAULT_SERVICEHUB_URL = "https://www.ccailab.top"

if sys.platform == "win32":
    import ctypes.wintypes as wt

    class MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_uint64),
            ("AllocationBase", ctypes.c_uint64),
            ("AllocationProtect", wt.DWORD),
            ("_pad1", wt.DWORD),
            ("RegionSize", ctypes.c_uint64),
            ("State", wt.DWORD),
            ("Protect", wt.DWORD),
            ("Type", wt.DWORD),
            ("_pad2", wt.DWORD),
        ]


def verify_enc_key(enc_key: bytes, db_page1: bytes) -> bool:
    """与 SQLCipher 4 规范一致的 HMAC-SHA512 双重真伪校验。"""
    if len(db_page1) < PAGE_SZ or len(enc_key) != KEY_SZ:
        return False
    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def get_wechat_pids() -> List[Tuple[int, int]]:
    """获取正在运行的微信进程列表 [(pid, mem_kb)] (Windows: Weixin.exe, macOS: WeChat)。"""
    if sys.platform == "win32":
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        pids: List[Tuple[int, int]] = []
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            p = line.strip('"').split('","')
            if len(p) >= 5:
                try:
                    pid = int(p[1])
                    mem = int(p[4].replace(",", "").replace(" K", "").strip() or "0")
                    pids.append((pid, mem))
                except ValueError:
                    continue
        pids.sort(key=lambda x: x[1], reverse=True)
        return pids
    elif sys.platform == "darwin":
        pids: List[Tuple[int, int]] = []
        try:
            r = subprocess.run(["pgrep", "-x", "WeChat"], capture_output=True, text=True)
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines():
                    if line.strip().isdigit():
                        pids.append((int(line.strip()), 0))
                if pids:
                    return pids
        except Exception:
            pass
        return pids
    else:
        return []


def collect_db_files(db_dir: str | Path) -> Tuple[List[Tuple[str, str, int, str, bytes]], Dict[str, List[str]]]:
    """收集指定目录下所有 sqlite/wcdb 数据库文件及其第一页 Salt。"""
    db_files = []
    salt_to_dbs: Dict[str, List[str]] = {}
    for root, _dirs, files in os.walk(str(db_dir)):
        for name in files:
            if not name.endswith(".db") or name.endswith("-wal") or name.endswith("-shm"):
                continue
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
                if size < PAGE_SZ:
                    continue
                with open(path, "rb") as f:
                    page1 = f.read(PAGE_SZ)
                if len(page1) < PAGE_SZ:
                    continue
                rel = os.path.relpath(path, str(db_dir))
                salt = page1[:SALT_SZ].hex().lower()
                db_files.append((rel, path, size, salt, page1))
                salt_to_dbs.setdefault(salt, []).append(rel)
            except Exception:
                continue
    return db_files, salt_to_dbs


def _u64_from(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(data):
        return 0
    return struct.unpack_from("<Q", data, offset)[0]


def _iter_windows_region_chunks(
    regions: List[Tuple[int, int]],
    read_region: Any,
    *,
    chunk_size: int = 2 * 1024 * 1024,
    overlap: int = 0,
):
    """Yield readable chunks from Windows process regions with optional overlap."""
    for base, size in regions:
        offset = 0
        tail = b""
        tail_base = base
        while offset < size:
            current_size = min(chunk_size, size - offset)
            chunk = read_region(base + offset, current_size) or b""
            data_base = tail_base if tail else base + offset
            data = tail + chunk
            if data:
                yield data_base, data
                if overlap:
                    tail = data[-overlap:]
                    tail_base = data_base + max(0, len(data) - len(tail))
                else:
                    tail = b""
                    tail_base = base + offset + current_size
            else:
                tail = b""
                tail_base = base + offset + current_size
            offset += current_size


def _find_bytes_in_regions(
    regions: List[Tuple[int, int]],
    read_region: Any,
    needle: bytes,
) -> set[int]:
    addresses: set[int] = set()
    overlap = max(0, len(needle) - 1)
    for data_base, haystack in _iter_windows_region_chunks(regions, read_region, overlap=overlap):
        pos = haystack.find(needle)
        while pos >= 0:
            addresses.add(data_base + pos)
            pos = haystack.find(needle, pos + 1)
    return addresses


def collect_memory_blobs() -> List[str]:
    """从微信内存中抓取未解密的原始二进制特征块 (十六进制字符串列表)。客户端不执行解密。"""
    if sys.platform != "win32":
        return []

    pids = get_wechat_pids()
    if not pids:
        return []

    kernel32 = ctypes.windll.kernel32
    MEM_COMMIT = 0x1000
    READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}

    def read_mem(h, addr, sz):
        buf = ctypes.create_string_buffer(sz)
        n = ctypes.c_size_t(0)
        if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n)):
            return buf.raw[: n.value]
        return None

    def enum_regions(h):
        regs = []
        addr = 0
        mbi = MBI()
        while addr < 0x7FFFFFFFFFFF:
            if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < mbi.RegionSize < 500 * 1024 * 1024:
                regs.append((mbi.BaseAddress, mbi.RegionSize))
            nxt = mbi.BaseAddress + mbi.RegionSize
            if nxt <= addr:
                break
            addr = nxt
        return regs

    extracted_blobs: List[str] = []
    seen_blobs = set()

    for pid, _mem in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)  # PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
        if not h:
            continue
        try:
            regions = enum_regions(h)
            read_region = lambda addr, sz, _h=h: read_mem(_h, addr, sz)
            needle_addresses = _find_bytes_in_regions(regions, read_region, WINDOWS_CONFIG_CIPHER_NAME)
            if not needle_addresses:
                continue

            pair_patterns = [
                struct.pack("<Q", addr) + struct.pack("<Q", len(WINDOWS_CONFIG_CIPHER_NAME))
                for addr in needle_addresses
            ]

            for base, data in _iter_windows_region_chunks(regions, read_region, overlap=0x80):
                for pattern in pair_patterns:
                    pos = data.find(pattern)
                    while pos >= 0:
                        qaddr = base + pos
                        node_base = qaddr - 0x10
                        node = read_mem(h, node_base, 0x50)
                        if not node or len(node) < 0x40:
                            pos = data.find(pattern, pos + 1)
                            continue
                        if _u64_from(node, 0x10) not in needle_addresses or _u64_from(node, 0x18) != len(WINDOWS_CONFIG_CIPHER_NAME):
                            pos = data.find(pattern, pos + 1)
                            continue
                        config_ptr = _u64_from(node, 0x28)
                        if not (0x10000 <= config_ptr < WINDOWS_MAX_USER_ADDRESS):
                            pos = data.find(pattern, pos + 1)
                            continue
                        obj = read_mem(h, config_ptr + 0x88, 0x28)
                        if not obj or len(obj) < 0x18:
                            pos = data.find(pattern, pos + 1)
                            continue
                        data_ptr = _u64_from(obj, 0x08)
                        data_len = _u64_from(obj, 0x10)
                        if not (0 < data_len <= WINDOWS_CONFIG_BLOB_MAX and 0x10000 <= data_ptr < WINDOWS_MAX_USER_ADDRESS):
                            pos = data.find(pattern, pos + 1)
                            continue
                        blob = read_mem(h, data_ptr, int(data_len))
                        if not blob or len(blob) != data_len:
                            pos = data.find(pattern, pos + 1)
                            continue
                        blob_hex = blob.hex().lower()
                        if blob_hex not in seen_blobs:
                            seen_blobs.add(blob_hex)
                            extracted_blobs.append(blob_hex)
                        pos = data.find(pattern, pos + 1)
        finally:
            kernel32.CloseHandle(h)

        if extracted_blobs:
            break

    return extracted_blobs


# ==========================================
# ServiceHub 会员凭据管理与远端密码机请求
# ==========================================

def get_credentials_path() -> Path:
    """获取用户本地 ServiceHub 凭证配置文件路径。"""
    config_dir = Path.home() / ".servicehub"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.json"


def load_servicehub_credentials(
    custom_user: Optional[str] = None,
    custom_token: Optional[str] = None,
    custom_server: Optional[str] = None,
) -> Tuple[str, str, str]:
    """智能解析会员账号、授权凭据与服务器 URL。"""
    user = custom_user or os.getenv("SERVICEHUB_USERNAME") or os.getenv("SERVICEHUB_USER")
    token = custom_token or os.getenv("SERVICEHUB_PASSTOKEN") or os.getenv("SERVICEHUB_TOKEN")
    server = custom_server or os.getenv("SERVICEHUB_URL")

    # 尝试从本地配置文件读取
    cfg_p = get_credentials_path()
    if cfg_p.exists():
        try:
            with open(cfg_p, "r", encoding="utf-8") as f:
                saved = json.load(f)
            user = user or saved.get("username")
            token = token or saved.get("passtoken")
            server = server or saved.get("server_url")
        except Exception:
            pass

    server = (server or DEFAULT_SERVICEHUB_URL).rstrip("/")
    return user or "", token or "", server


def save_servicehub_credentials(username: str, passtoken: str, server_url: Optional[str] = None) -> None:
    """将社群会员凭据持久化保存到本地 ~/.servicehub/config.json。"""
    cfg_p = get_credentials_path()
    data = {
        "username": username.strip(),
        "passtoken": passtoken.strip(),
        "server_url": (server_url or DEFAULT_SERVICEHUB_URL).rstrip("/"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(cfg_p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def request_keys_from_servicehub(
    server_url: str,
    username: str,
    passtoken: str,
    blobs: List[str],
    salts: List[str],
) -> Dict[str, str]:
    """向 ServiceHub 远程密码机接口发送解密请求，获取当前机器专属的数据库 Key。"""
    endpoint = f"{server_url}/api/auth/resolve-wechat-key"
    payload = {
        "username": username,
        "passtoken": passtoken,
        "blobs": blobs,
        "salts": salts,
        "skill_name": "skill-wechat-chat-exporter-member",
    }
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=req_data,
        headers={"Content-Type": "application/json", "User-Agent": "WeChatChatExporterMember/1.1"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if data.get("code") == 200:
                res_data = data.get("data", {})
                return res_data.get("keys", {})
            else:
                raise RuntimeError(data.get("message", "未知服务端错误"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_msg)
            detail = err_json.get("detail", err_msg)
        except Exception:
            detail = err_msg
        if e.code == 401:
            raise PermissionError(f"[ServiceHub 会员鉴权失败] {detail}\n请确认会员账号是否输入正确，或您的社群会员资格是否已到期。")
        raise RuntimeError(f"ServiceHub 接口返回错误 (HTTP {e.code}): {detail}")
    except urllib.error.URLError as e:
        raise ConnectionError(f"无法连接到 ServiceHub 授权服务器 ({server_url}): {e.reason}\n请检查网络连接或服务器地址是否正常。")


def verify_servicehub_license(
    server_url: str,
    username: str,
    passtoken: str,
) -> dict:
    """向 ServiceHub 验证社群会员授权状态。"""
    endpoint = f"{server_url}/api/auth/verify-skill-license"
    payload = {
        "username": username,
        "passtoken": passtoken,
        "skill_name": "skill-wechat-chat-exporter-member",
    }
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=req_data,
        headers={"Content-Type": "application/json", "User-Agent": "WeChatChatExporterMember/1.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if data.get("code") == 200:
                return data.get("data", {})
            else:
                raise RuntimeError(data.get("message", "未知会员验证错误"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_msg)
            detail = err_json.get("detail", err_msg)
        except Exception:
            detail = err_msg
        if e.code == 401:
            raise PermissionError(f"[ServiceHub 会员鉴权失败] {detail}\n请确认会员账号密码输入正确，或您的社群会员资格是否已到期。")
        raise RuntimeError(f"ServiceHub 验证接口返回错误 (HTTP {e.code}): {detail}")
    except urllib.error.URLError as e:
        raise ConnectionError(f"无法连接到 ServiceHub 授权服务器 ({server_url}): {e.reason}\n请检查网络连接或服务器地址是否正常。")


def get_key_cache_path() -> Path:
    """获取会员本地已解密密钥的缓存文件路径。"""
    cache_dir = Path.home() / ".servicehub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "wechat_keys_cache.json"


def save_keys_to_cache(keys_map: Dict[str, str]) -> None:
    """缓存经过真伪校验的解密密钥到本地。"""
    cache_p = get_key_cache_path()
    data = {
        "keys": keys_map,
        "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(cache_p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_keys_mapping_member(
    db_dir: str | Path,
    username: Optional[str] = None,
    passtoken: Optional[str] = None,
    server_url: Optional[str] = None,
    single_key_hex: Optional[str] = None,
) -> Dict[str, str]:
    """会员版密钥加载器：内存只读扫描 -> ServiceHub 远程密码机解算 -> 本地 HMAC-SHA512 验真。"""
    db_dir = Path(db_dir).resolve()
    db_files, salt_to_dbs = collect_db_files(db_dir)
    if not db_files:
        raise RuntimeError(f"在 {db_dir} 未找到任何微信 .db 数据库文件")

    salt_sample_map = {salt: page1 for _rel, _path, _sz, salt, page1 in db_files}
    salts_list = list(salt_sample_map.keys())

    # 1. 若用户直接传入了单密钥 (例如已知 Raw Key 调试)
    if single_key_hex:
        clean_key = single_key_hex.strip().lower()
        if len(clean_key) == 64:
            try:
                kb = bytes.fromhex(clean_key)
                m = {}
                for s, p1 in salt_sample_map.items():
                    if verify_enc_key(kb, p1):
                        m[s] = clean_key
                if m:
                    return m
            except Exception:
                pass

    # 2. 解析 ServiceHub 凭证
    user, token, srv = load_servicehub_credentials(username, passtoken, server_url)
    if not user or not token:
        raise PermissionError(
            "未检测到 ServiceHub 社群会员登录信息！\n"
            "本技能为社群会员专属，请通过以下方式配置会员凭证：\n"
            "  1. 运行: python scripts/export_chat.py --login (交互式配置账号密码)\n"
            "  2. 或运行时追加参数: --user <用户名> --token <授权码>\n"
            "  3. 或设置环境变量: SERVICEHUB_USERNAME 与 SERVICEHUB_PASSTOKEN"
        )

    print(f"[+] 正在向 ServiceHub 验证会员身份: 用户={user}, 服务器={srv}")

    # 3. 优先尝试从微信进程内存抓取混淆特征块 (纯客户端抓取，零算法暴露)
    blobs = collect_memory_blobs()
    verified_keys: Dict[str, str] = {}

    if blobs:
        print(f"[+] 已安全捕获 {len(blobs)} 组内存特征结构体，正在请求云端密码机解密...")
        try:
            resolved_keys = request_keys_from_servicehub(srv, user, token, blobs, salts_list)
            for salt, key_hex in resolved_keys.items():
                s_clean = salt.lower()
                if s_clean in salt_sample_map:
                    try:
                        kb = bytes.fromhex(key_hex)
                        if verify_enc_key(kb, salt_sample_map[s_clean]):
                            verified_keys[s_clean] = key_hex.lower()
                    except Exception:
                        pass
            if verified_keys:
                save_keys_to_cache(verified_keys)
                return verified_keys
        except PermissionError:
            raise
        except Exception as e:
            print(f"[WARN] 远程密码机解算异常: {e}，尝试本地已授权缓存...")

    # 4. 若内存扫描未抓取到特征（例如微信窗口最小化、锁屏后台或多平台运行），
    # 必须先通过 ServiceHub 验证会员有效性，确保非会员无法使用
    auth_info = verify_servicehub_license(srv, user, token)
    if not auth_info.get("authorized"):
        raise PermissionError(f"[ServiceHub] 用户 {user} 未获得此技能的会员使用授权。")

    print(f"[+] 会员授权状态有效 ({auth_info.get('username')})，正在检索已授权的有效密钥缓存...")

    candidate_cache_files = [
        get_key_cache_path(),
        Path.home() / ".config" / "rion-wechat-reader" / "keys.json",
        Path.home() / "Library" / "Application Support" / "rion-wechat-reader" / "keys.json",
        Path.home() / ".wcdb-key-tool" / "all_keys.json",
        Path.home() / ".wechat-keys.json",
        Path("all_keys.json"),
        Path("keys.json"),
    ]

    for cache_p in candidate_cache_files:
        if not cache_p.exists():
            continue
        try:
            with open(cache_p, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_keys = data.get("keys", data)
            if isinstance(raw_keys, dict):
                for k, v in raw_keys.items():
                    kh = v.get("enc_key") if isinstance(v, dict) else v
                    if isinstance(kh, str) and len(kh.strip()) == 64:
                        kh_clean = kh.strip().lower()
                        try:
                            kb = bytes.fromhex(kh_clean)
                            for s, p1 in salt_sample_map.items():
                                if s not in verified_keys and verify_enc_key(kb, p1):
                                    verified_keys[s] = kh_clean
                        except Exception:
                            pass
        except Exception:
            pass

    if verified_keys:
        save_keys_to_cache(verified_keys)
        return verified_keys

    raise RuntimeError("未能提取到有效解密密钥：内存特征提取未命中且无本地可用缓存，请确保微信 4.x 已打开且处于登录状态。")
