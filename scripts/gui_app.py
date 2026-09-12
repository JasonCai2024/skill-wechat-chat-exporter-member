#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gui_app.py -- 微信 4.x 聊天记录导出助手 (社群会员版 - 高性能极速桌面客户端)

优化特性：
1. 解决拖动窗口掉帧卡顿问题：采用单控件高帧率现代化 Treeview 表格，取代 500 个 Canvas 子组件，丝滑拖拽；
2. 彻底修复登录界面取消按钮显示问题：标准化次级按钮样式，深浅色自适应高对比度；
3. 默认不预填作者账号密码：登录界面默认完全空白，由会员用户自主输入认证；
4. 认证体验彻底重构：移除容易导致弹窗死锁的 Win32 MessageBox，改为对话框内实时内联状态反馈与错误指引；
5. 增加退出登录 / 清除凭据功能，方便账号切换与测试。
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import threading
from collections import defaultdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Dict, List, Optional

# 兼容 PyInstaller 冻结打包环境
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys._MEIPASS)
else:
    APP_DIR = Path(__file__).resolve().parent

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# 导入底层核心解密与数据库引擎
try:
    import export_chat
    import wechat_db
    import wechat_key
except ImportError:
    from scripts import export_chat, wechat_db, wechat_key

import customtkinter as ctk

# 全局外观模式设置
ctk.set_appearance_mode("System")  # 跟随系统深浅色
ctk.set_default_color_theme("blue")  # 科技蓝主题


class LoginDialog(ctk.CTkToplevel):
    """ServiceHub 会员登录配置弹窗 (内联即时反馈，无死锁弹窗)。"""

    def __init__(self, parent: "WeChatExporterApp", on_success_callback):
        super().__init__(parent)
        self.parent_app = parent
        self.on_success = on_success_callback

        self.title("ServiceHub 社群会员登录认证")
        self.geometry("500x520")
        self.minsize(500, 500)
        self.resizable(False, False)

        # 模态置顶
        self.transient(parent)
        self.grab_set()

        # 居中显示
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 500) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 520) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self.init_ui()

    def init_ui(self):
        pad_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="transparent")
        pad_frame.pack(fill="both", expand=True, padx=30, pady=24)

        # 1. 优先使用 side='bottom' 将底部按钮区锁定在最底端，确保拥有完整高度，绝不被上层控件挤压
        btn_row = ctk.CTkFrame(pad_frame, fg_color="transparent", height=46)
        btn_row.pack(side="bottom", fill="x", pady=(12, 0))

        self.cancel_btn = ctk.CTkButton(
            btn_row,
            text="取消",
            fg_color=("gray85", "gray30"),
            text_color=("gray15", "gray95"),
            hover_color=("gray75", "gray40"),
            font=ctk.CTkFont(size=14),
            width=110,
            height=40,
            command=self.destroy,
        )
        self.cancel_btn.pack(side="left")

        self.save_btn = ctk.CTkButton(
            btn_row,
            text="保存并验证",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=160,
            height=40,
            command=self.save_and_verify,
        )
        self.save_btn.pack(side="right")

        # 2. 内联状态提示标签 (紧贴按钮上方)
        self.status_lbl = ctk.CTkLabel(
            pad_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="gray",
            anchor="w",
            wraplength=430,
            height=28,
        )
        self.status_lbl.pack(side="bottom", fill="x", pady=(4, 6))

        # 3. 顶部标题与副标题
        title_lbl = ctk.CTkLabel(
            pad_frame,
            text="🔐 社群会员凭证配置",
            font=ctk.CTkFont(size=19, weight="bold"),
        )
        title_lbl.pack(anchor="w", pady=(0, 4))

        sub_lbl = ctk.CTkLabel(
            pad_frame,
            text="本工具由 ServiceHub 远程密码机提供专属解密，请填入您的会员账号",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        sub_lbl.pack(anchor="w", pady=(0, 14))

        # 4. 用户名输入框 (默认完全留空)
        ctk.CTkLabel(pad_frame, text="会员用户名 / 邮箱:", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.user_entry = ctk.CTkEntry(
            pad_frame,
            placeholder_text="请输入您的 ServiceHub 用户名或邮箱",
            height=36,
        )
        self.user_entry.pack(fill="x", pady=(0, 10))

        # 5. 密码输入框 (默认完全留空)
        ctk.CTkLabel(pad_frame, text="会员密码 / 授权码:", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.token_entry = ctk.CTkEntry(
            pad_frame,
            placeholder_text="请输入您的会员授权密码",
            show="*",
            height=36,
        )
        self.token_entry.pack(fill="x", pady=(0, 10))

        # 6. 授权服务器地址
        ctk.CTkLabel(pad_frame, text="授权服务器地址:", font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 2))
        self.server_entry = ctk.CTkEntry(
            pad_frame,
            placeholder_text=wechat_key.DEFAULT_SERVICEHUB_URL,
            height=32,
        )
        self.server_entry.insert(0, wechat_key.DEFAULT_SERVICEHUB_URL)
        self.server_entry.pack(fill="x", pady=(0, 6))

    def save_and_verify(self):
        u = self.user_entry.get().strip()
        p = self.token_entry.get().strip()
        s = self.server_entry.get().strip() or wechat_key.DEFAULT_SERVICEHUB_URL

        if not u or not p:
            self.status_lbl.configure(text="⚠️ 请输入会员用户名和授权密码！", text_color="#E06C75")
            return

        self.save_btn.configure(state="disabled", text="正在验证...")
        self.cancel_btn.configure(state="disabled")
        self.status_lbl.configure(text="⏳ 正在向 ServiceHub 远程密码机核验会员有效性...", text_color=("#1F6AA5", "#3B8ED0"))

        def _do_save():
            try:
                auth = wechat_key.verify_servicehub_license(s, u, p)
                if auth.get("authorized"):
                    wechat_key.save_servicehub_credentials(u, p, s)
                    self.after(0, lambda: self._on_success_finish(u))
                else:
                    self.after(0, lambda: self._on_fail("该账号未获得此技能的会员授权。"))
            except PermissionError as pe:
                pe_str = str(pe)
                if "过期" in pe_str:
                    msg = "会员资格已过期，请联系社群管理员续费"
                elif "密码错误" in pe_str:
                    msg = "密码错误，请核对后重试"
                else:
                    msg = "请核对会员账号、密码或会员资格有效期"
                self.after(0, lambda: self._on_fail(f"鉴权未通过: {msg}"))
            except Exception as e:
                self.after(0, lambda: self._on_fail(f"连接失败: {str(e)}"))

        threading.Thread(target=_do_save, daemon=True).start()

    def _on_success_finish(self, user: str):
        self.status_lbl.configure(text="✅ 验证成功！已激活社群会员权限，正在同步...", text_color="#2ECC71")
        self.save_btn.configure(text="验证通过")
        self.after(800, self._close_and_callback)

    def _close_and_callback(self):
        self.destroy()
        self.on_success()

    def _on_fail(self, err_msg: str):
        self.save_btn.configure(state="normal", text="保存并验证")
        self.cancel_btn.configure(state="normal")
        self.status_lbl.configure(text=f"❌ {err_msg}", text_color="#E74C3C")


class WeChatExporterApp(ctk.CTk):
    """主程序窗口 (高性能极速流畅版)。"""

    def __init__(self):
        super().__init__()

        self.title("微信 4.x 聊天记录按月导出助手 (社群会员版)")
        self.geometry("860x700")
        self.minsize(800, 640)

        # 核心数据状态
        self.db_dir: Optional[Path] = None
        self.keys_map: Dict[str, str] = {}
        self.manager: Optional[wechat_db.WeChatDatabaseManager] = None
        self.all_sessions: List[Dict[str, Any]] = []
        self.filtered_sessions: List[Dict[str, Any]] = []
        self.selected_session: Optional[Dict[str, Any]] = None
        self.is_exporting: bool = False
        self.last_output_dir: Optional[Path] = None

        # 界面初始化
        self.init_ui()

        # 启动后台检测
        self.after(150, self.start_bootstrap_thread)

    def init_ui(self):
        # 根部纵向网格布局
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # 中间会话列表自动伸展

        # ================= 1. 顶部会员与运行状态栏 =================
        header_frame = ctk.CTkFrame(self, corner_radius=10)
        header_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header_frame.grid_columnconfigure(0, weight=1)

        # 左侧软件标题与标语
        top_left = ctk.CTkFrame(header_frame, fg_color="transparent")
        top_left.grid(row=0, column=0, sticky="w", padx=14, pady=10)

        app_title = ctk.CTkLabel(
            top_left, text="💬 微信聊天记录按月导出助手", font=ctk.CTkFont(size=18, weight="bold")
        )
        app_title.pack(anchor="w")

        app_sub = ctk.CTkLabel(
            top_left,
            text="社群付费会员专属 · ServiceHub 远程密码机鉴权",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        app_sub.pack(anchor="w")

        # 右侧会员账号与操作
        top_right = ctk.CTkFrame(header_frame, fg_color="transparent")
        top_right.grid(row=0, column=1, sticky="e", padx=14, pady=10)

        self.user_badge = ctk.CTkLabel(
            top_right, text="👤 未登录", font=ctk.CTkFont(size=12, weight="bold")
        )
        self.user_badge.pack(side="left", padx=(0, 10))

        self.login_btn = ctk.CTkButton(
            top_right, text="会员登录", width=80, height=28, command=self.open_login_dialog
        )
        self.login_btn.pack(side="left", padx=(0, 6))

        self.logout_btn = ctk.CTkButton(
            top_right,
            text="清除凭据",
            width=70,
            height=28,
            fg_color=("gray85", "gray30"),
            text_color=("gray20", "gray90"),
            hover_color=("gray75", "gray40"),
            command=self.handle_logout,
        )
        self.logout_btn.pack(side="right")

        # ================= 2. 系统状态指示灯条 =================
        indicator_frame = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "gray17"))
        indicator_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        self.wechat_status_lbl = ctk.CTkLabel(
            indicator_frame, text="⏳ 微信进程: 检测中...", font=ctk.CTkFont(size=12)
        )
        self.wechat_status_lbl.pack(side="left", padx=14, pady=6)

        self.db_status_lbl = ctk.CTkLabel(
            indicator_frame, text="⏳ 存储目录: 检索中...", font=ctk.CTkFont(size=12)
        )
        self.db_status_lbl.pack(side="left", padx=14, pady=6)

        self.key_status_lbl = ctk.CTkLabel(
            indicator_frame, text="⏳ 数据库密钥: 等待鉴权...", font=ctk.CTkFont(size=12)
        )
        self.key_status_lbl.pack(side="left", padx=14, pady=6)

        self.refresh_btn = ctk.CTkButton(
            indicator_frame, text="🔄 刷新", width=65, height=24, command=self.start_bootstrap_thread
        )
        self.refresh_btn.pack(side="right", padx=10, pady=4)

        # ================= 3. 核心会话选择卡片 (极速 Treeview 架构，零拖动卡顿) =================
        sessions_card = ctk.CTkFrame(self, corner_radius=10)
        sessions_card.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        sessions_card.grid_columnconfigure(0, weight=1)
        sessions_card.grid_rowconfigure(1, weight=1)

        # 搜索过滤条
        search_bar = ctk.CTkFrame(sessions_card, fg_color="transparent")
        search_bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        search_bar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(
            search_bar,
            placeholder_text="🔍 输入好友备注、群聊名称或微信 ID 实时过滤...",
            height=32,
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.search_entry.bind("<KeyRelease>", self.on_search_text_changed)

        self.session_count_lbl = ctk.CTkLabel(
            search_bar, text="共 0 个会话", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self.session_count_lbl.grid(row=0, column=1, sticky="e")

        # 高性能现代化 Treeview 容器
        tree_container = ctk.CTkFrame(sessions_card, corner_radius=6, fg_color=("gray96", "gray14"))
        tree_container.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        tree_container.grid_columnconfigure(0, weight=1)
        tree_container.grid_rowconfigure(0, weight=1)

        # 配置原生平滑样式
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "WeChat.Treeview",
            font=("Microsoft YaHei UI", 10),
            rowheight=32,
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#202020",
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "WeChat.Treeview.Heading",
            font=("Microsoft YaHei UI", 10, "bold"),
            background="#EFEFEF",
            foreground="#333333",
            relief="flat",
            padding=(6, 4),
        )
        style.map(
            "WeChat.Treeview",
            background=[("selected", "#1F6AA5")],
            foreground=[("selected", "#FFFFFF")],
        )

        # 创建 Treeview 单控件
        self.tree = ttk.Treeview(
            tree_container,
            columns=("type", "name", "time", "summary"),
            show="headings",
            selectmode="browse",
            style="WeChat.Treeview",
        )
        self.tree.heading("type", text="类型")
        self.tree.heading("name", text="会话名称")
        self.tree.heading("time", text="最新活跃时间")
        self.tree.heading("summary", text="最新消息摘要 / 微信号")

        self.tree.column("type", width=75, minwidth=60, anchor="center")
        self.tree.column("name", width=220, minwidth=150, anchor="w")
        self.tree.column("time", width=140, minwidth=120, anchor="center")
        self.tree.column("summary", width=340, minwidth=200, anchor="w")

        # 纵向滚动条
        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")

        # 绑定选择事件
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # ================= 4. 导出配置与操作卡片 =================
        config_card = ctk.CTkFrame(self, corner_radius=10)
        config_card.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        config_card.grid_columnconfigure(1, weight=1)

        # 当前选中高亮提示
        sel_row = ctk.CTkFrame(config_card, fg_color="transparent")
        sel_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(10, 6))

        ctk.CTkLabel(
            sel_row, text="当前选定目标:", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left", padx=(0, 8))

        self.selected_target_lbl = ctk.CTkLabel(
            sel_row,
            text="【尚未选择】请在上方列表中点击要导出的群聊或联系人",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("blue", "#3B8ED0"),
        )
        self.selected_target_lbl.pack(side="left")

        # 选项第 1 行：时间过滤与本人昵称
        opt_row = ctk.CTkFrame(config_card, fg_color="transparent")
        opt_row.grid(row=1, column=0, columnspan=3, sticky="ew", padx=14, pady=4)

        ctk.CTkLabel(opt_row, text="时间过滤:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 6))

        self.seg_time = ctk.CTkSegmentedButton(
            opt_row,
            values=["全部历史", "近30天", "近90天"],
        )
        self.seg_time.set("全部历史")
        self.seg_time.pack(side="left", padx=(0, 24))

        ctk.CTkLabel(opt_row, text="本人在记录中显示为:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 6))
        self.self_name_entry = ctk.CTkEntry(opt_row, width=90)
        self.self_name_entry.insert(0, "CC")
        self.self_name_entry.pack(side="left")

        # 选项第 2 行：输出目录选择
        dir_row = ctk.CTkFrame(config_card, fg_color="transparent")
        dir_row.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=6)
        dir_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(dir_row, text="导出保存目录:", font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", padx=(0, 8))

        default_base_dir = Path(r"E:\BaiduSyncdisk\WorkSpace\社交媒体\社群运营\聊天记录\私聊")
        if not default_base_dir.exists():
            default_base_dir = Path.home() / "Documents" / "微信聊天归档"

        self.output_dir_entry = ctk.CTkEntry(dir_row)
        self.output_dir_entry.insert(0, str(default_base_dir))
        self.output_dir_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        browse_btn = ctk.CTkButton(dir_row, text="浏览...", width=70, command=self.on_browse_output_dir)
        browse_btn.grid(row=0, column=2, sticky="e")

        # 选项第 3 行：进度条与状态展示
        status_row = ctk.CTkFrame(config_card, fg_color="transparent")
        status_row.grid(row=3, column=0, columnspan=3, sticky="ew", padx=14, pady=4)
        status_row.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(status_row)
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(4, 4))
        self.progress_bar.set(0)

        self.action_status_lbl = ctk.CTkLabel(
            status_row, text="就绪。请选择会话后点击开始导出。", font=ctk.CTkFont(size=12), text_color="gray"
        )
        self.action_status_lbl.grid(row=1, column=0, sticky="w")

        # 选项第 4 行：大操作按钮
        btn_action_row = ctk.CTkFrame(config_card, fg_color="transparent")
        btn_action_row.grid(row=4, column=0, columnspan=3, sticky="ew", padx=14, pady=(6, 12))

        self.export_btn = ctk.CTkButton(
            btn_action_row,
            text="🚀 开始全量按月导出 Markdown",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=38,
            command=self.start_export_thread,
        )
        self.export_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.open_folder_btn = ctk.CTkButton(
            btn_action_row,
            text="📂 打开导出文件夹",
            font=ctk.CTkFont(size=13),
            height=38,
            width=140,
            state="disabled",
            fg_color=("gray85", "gray30"),
            text_color=("gray40", "gray70"),
            command=self.open_exported_folder,
        )
        self.open_folder_btn.pack(side="right")

    # ================= 核心工作流与异步多线程 =================

    def start_bootstrap_thread(self):
        """后台异步初始化。"""
        self.refresh_btn.configure(state="disabled", text="加载中...")
        self.action_status_lbl.configure(text="正在检测微信 4.x 运行状态与会员凭据...")
        self.progress_bar.start()

        threading.Thread(target=self._do_bootstrap, daemon=True).start()

    def _do_bootstrap(self):
        try:
            # 1. 检查微信 4.x 进程
            pids = wechat_key.get_wechat_pids()
            if pids:
                main_pid = pids[0][0]
                self.after(0, lambda: self.wechat_status_lbl.configure(text=f"🟢 微信 4.x 运行中 (PID:{main_pid})"))
            else:
                self.after(0, lambda: self.wechat_status_lbl.configure(text="🔴 微信未运行 (请打开微信4.x)"))

            # 2. 定位 db_storage
            db_dir = export_chat.find_default_db_dir()
            if not db_dir or not db_dir.exists():
                self.after(0, lambda: self.db_status_lbl.configure(text="🔴 未找到 db_storage"))
                raise RuntimeError("未检测到微信 4.x 的 db_storage 目录，请确保微信正在运行并已登录。")

            self.db_dir = db_dir
            self.after(0, lambda: self.db_status_lbl.configure(text=f"🟢 存储就绪: {db_dir.parent.name}"))

            # 3. 检查会员凭证
            user, token, srv = wechat_key.load_servicehub_credentials()
            if not user or not token:
                self.after(0, self._on_bootstrap_need_login)
                return

            self.after(0, lambda: self.user_badge.configure(text=f"👤 {user} (已认证)"))
            self.after(0, lambda: self.login_btn.configure(text="切换账号"))

            # 4. 远程密码机解算与真伪校验
            self.after(0, lambda: self.action_status_lbl.configure(text="正在向 ServiceHub 请求远程密码机解密..."))
            self.keys_map = wechat_key.load_keys_mapping_member(db_dir)
            self.after(0, lambda: self.key_status_lbl.configure(text=f"🟢 密钥已验真 ({len(self.keys_map)} 个分库)"))

            # 5. 读取活跃会话列表
            self.manager = wechat_db.WeChatDatabaseManager(self.db_dir, self.keys_map)
            sessions = self.manager.list_active_sessions(limit=200)
            self.all_sessions = sessions

            self.after(0, lambda: self._on_bootstrap_success(sessions))

        except PermissionError as pe:
            self.after(0, lambda: self._on_bootstrap_need_login(str(pe)))
        except Exception as e:
            self.after(0, lambda: self._on_bootstrap_failed(str(e)))

    def _on_bootstrap_need_login(self, reason: str = ""):
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.refresh_btn.configure(state="normal", text="🔄 刷新")
        self.user_badge.configure(text="👤 未登录")
        self.login_btn.configure(text="会员登录")
        self.key_status_lbl.configure(text="🟡 等待会员认证")
        tip = "请点击右上角【会员登录】完成认证。" if not reason else f"凭据失效: {reason}"
        self.action_status_lbl.configure(text=tip)

    def _on_bootstrap_success(self, sessions: List[Dict[str, Any]]):
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.refresh_btn.configure(state="normal", text="🔄 刷新")
        self.action_status_lbl.configure(text=f"已成功加载 {len(sessions)} 个最近活跃会话，请在表格中点选导出。")
        self.render_tree_sessions(sessions)

    def _on_bootstrap_failed(self, err: str):
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.refresh_btn.configure(state="normal", text="🔄 刷新")
        self.action_status_lbl.configure(text=f"初始化提示: {err}")

    # ================= 极速 Treeview 会话渲染与搜索 =================

    def render_tree_sessions(self, sessions: List[Dict[str, Any]]):
        """清空并一次性填充 Treeview，仅需 2ms，完全杜绝移动窗口掉帧！"""
        # 清空现有行
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.filtered_sessions = sessions
        self.session_count_lbl.configure(text=f"共 {len(sessions)} 个会话")

        for idx, s in enumerate(sessions):
            tag = "[群聊]" if s["is_group"] else "[私聊]"
            dt_str = (
                datetime.datetime.fromtimestamp(s["last_time"]).strftime("%Y-%m-%d %H:%M")
                if s["last_time"]
                else "-"
            )
            summary = (s["summary"] or s["username"]).replace("\n", " ").strip()
            # 存入 iid 为索引
            self.tree.insert("", "end", iid=str(idx), values=(tag, s["display_name"], dt_str, summary))

    def on_tree_select(self, event=None):
        """用户点击某一行。"""
        selected = self.tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        if 0 <= idx < len(self.filtered_sessions):
            session = self.filtered_sessions[idx]
            self.selected_session = session
            type_str = "群聊" if session["is_group"] else "私聊"
            self.selected_target_lbl.configure(
                text=f"【{type_str}】{session['display_name']} ({session['username']})",
                text_color=("green", "#2CC985"),
            )

    def on_search_text_changed(self, event=None):
        """实时关键词搜索过滤。"""
        query = self.search_entry.get().strip().lower()
        if not query:
            self.render_tree_sessions(self.all_sessions)
            return

        filtered = [
            s
            for s in self.all_sessions
            if query in s["display_name"].lower()
            or query in s["username"].lower()
            or query in (s["summary"] or "").lower()
        ]
        self.render_tree_sessions(filtered)

    # ================= 导出配置与操作 =================

    def on_browse_output_dir(self):
        d = filedialog.askdirectory(initialdir=self.output_dir_entry.get().strip(), parent=self)
        if d:
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, d)

    def start_export_thread(self):
        if not self.selected_session:
            self.action_status_lbl.configure(text="⚠️ 请先在上方表格中点击选择要导出的会话！", text_color="#E74C3C")
            return

        if not self.manager:
            self.action_status_lbl.configure(text="⚠️ 数据库未就绪，请先登录会员或点击刷新。", text_color="#E74C3C")
            return

        out_base = self.output_dir_entry.get().strip()
        if not out_base:
            self.action_status_lbl.configure(text="⚠️ 导出保存目录不能为空！", text_color="#E74C3C")
            return

        self.is_exporting = True
        self.export_btn.configure(state="disabled", text="⏳ 正在提取并清洗纯文本...")
        self.open_folder_btn.configure(state="disabled")
        self.progress_bar.start()

        threading.Thread(target=self._do_export, daemon=True).start()

    def _do_export(self):
        session = self.selected_session
        target_name = session["display_name"]
        target_username = session["username"]
        is_group = session["is_group"]
        self_name = self.self_name_entry.get().strip() or "CC"

        seg_val = self.seg_time.get()
        since_str = None
        now = datetime.datetime.now()
        if seg_val == "近30天":
            since_str = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        elif seg_val == "近90天":
            since_str = (now - datetime.timedelta(days=90)).strftime("%Y-%m-%d")

        try:
            base_dir = Path(self.output_dir_entry.get().strip())
            if base_dir.name == target_name:
                out_dir = base_dir
            else:
                out_dir = base_dir / target_name
            out_dir.mkdir(parents=True, exist_ok=True)
            self.last_output_dir = out_dir

            self.after(0, lambda: self.action_status_lbl.configure(text=f"正在扫描消息分库并解密纯文本..."))

            msgs = self.manager.extract_chat_messages(
                target_username=target_username,
                self_display_name=self_name,
                since=since_str,
            )

            if not msgs:
                self.after(0, lambda: self._on_export_finished(0, out_dir, "该会话在指定时间范围内无纯文本记录。"))
                return

            # 按自然月分组写入
            monthly_groups = defaultdict(list)
            for m in msgs:
                ym = m["dt"].strftime("%Y-%m")
                monthly_groups[ym].append(m)

            month_files = []
            for ym in sorted(monthly_groups.keys()):
                group = monthly_groups[ym]
                if not group:
                    continue

                start_ts = export_chat.get_file_ts(group[0]["dt"])
                end_ts = export_chat.get_file_ts(group[-1]["dt"])
                filename = f"{target_name}_{start_ts}-{end_ts}.md"
                filepath = out_dir / filename

                blocks = []
                for m in group:
                    sender = m["sender"]
                    t_str = export_chat.format_msg_time(m["dt"])
                    body = m["text"]
                    blocks.append(f"{sender}\n{t_str}\n{body}\n")

                doc_text = "\n" + "\n".join(blocks).strip() + "\n"
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(doc_text)
                month_files.append(filename)

            # 配套写入 README.md
            readme_path = out_dir / "README.md"
            if not readme_path.exists():
                type_str = "群聊" if is_group else "私聊"
                readme_content = f"""# {target_name} 聊天记录归档

> **目标名称**：{target_name}  
> **会话类型**：{type_str}  
> **会话标识**：`{target_username}`  
> **归档时间**：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}  
> **归档规范**：
> 1. 按月归档为 Markdown 文档，彻底排除图片、表格等非文本噪音；
> 2. 文件命名严格遵循方案 A：`{target_name}_YYYYMMDDHHMM-YYYYMMDDHHMM.md`；
> 3. 单条消息排版遵循三行式标准（发言人、日期时间、消息正文）。
"""
                with open(readme_path, "w", encoding="utf-8") as rf:
                    rf.write(readme_content)

            self.after(0, lambda: self._on_export_finished(len(msgs), out_dir, f"导出完成！共提取 {len(msgs)} 条纯文本，切片为 {len(month_files)} 个月份文档"))

        except Exception as e:
            self.after(0, lambda: self._on_export_failed(str(e)))

    def _on_export_finished(self, count: int, out_dir: Path, msg: str):
        self.progress_bar.stop()
        self.progress_bar.set(1.0)
        self.export_btn.configure(state="normal", text="🚀 开始全量按月导出 Markdown")
        self.open_folder_btn.configure(
            state="normal",
            fg_color=("#1F6AA5", "#3B8ED0"),
            text_color="#FFFFFF",
        )
        self.action_status_lbl.configure(text=f"✅ {msg}", text_color=("#2ECC71", "#2ECC71"))

    def _on_export_failed(self, err: str):
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.export_btn.configure(state="normal", text="🚀 开始全量按月导出 Markdown")
        self.action_status_lbl.configure(text=f"❌ 导出失败: {err}", text_color="#E74C3C")

    def open_exported_folder(self):
        target_dir = self.last_output_dir or Path(self.output_dir_entry.get().strip())
        if target_dir.exists():
            if sys.platform == "win32":
                os.startfile(str(target_dir))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(target_dir)])
            else:
                subprocess.run(["xdg-open", str(target_dir)])

    def handle_logout(self):
        """退出登录并清除本地保存的凭据。"""
        cfg_p = wechat_key.get_credentials_path()
        if cfg_p.exists():
            try:
                cfg_p.unlink()
            except Exception:
                pass
        self.user_badge.configure(text="👤 未登录")
        self.login_btn.configure(text="会员登录")
        self.key_status_lbl.configure(text="🟡 等待会员认证")
        self.action_status_lbl.configure(text="已清除本地会员凭据。")
        # 清空表格
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.session_count_lbl.configure(text="共 0 个会话")

    def open_login_dialog(self):
        LoginDialog(self, on_success_callback=self.start_bootstrap_thread)


def main():
    app = WeChatExporterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
