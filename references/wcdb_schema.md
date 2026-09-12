# 微信 4.x WCDB 架构与数据库字典说明 (WCDB Schema Reference)

> **适用范围**：微信 4.x (Windows & macOS)，用于底层数据解密与结构化提取。

---

## 1. 数据库目录组织结构

微信 4.x 将所有核心数据集中存放在账号专属的 `db_storage` 目录中：

```text
xwechat_files\<wxid>\db_storage\
├── contact\
│   └── contact.db          # 通讯录（联系人、群聊元信息表 contact）
├── session\
│   └── session.db          # 会话状态（最近聊天列表表 SessionTable）
└── message\
    ├── message_0.db        # 消息主分库 0（内含数十至数百张 Msg_<hash> 表与 Name2Id 表）
    ├── message_1.db        # 消息主分库 1
    ├── message_2.db        # 消息主分库 2
    ├── message_3.db        # 消息主分库 3
    └── ...
```

---

## 2. 表名 MD5 哈希计算逻辑

微信 4.x 不以明文会话名称建表，而是将 `username` 进行标准 MD5 计算后拼装前缀：

```python
import hashlib
table_name = "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()
```

- **示例 1（群聊）**：`46112508908@chatroom`  
  $\to$ MD5 为 `7bc4b862c1e3d9ed8e17a588446abaf8`  
  $\to$ 表名：`Msg_7bc4b862c1e3d9ed8e17a588446abaf8`
- **示例 2（私聊）**：`yx16391405`  
  $\to$ MD5 为 `...`  
  $\to$ 表名：`Msg_...`

---

## 3. WCDB 压缩与解密原理

### 3.1 SQLCipher 4 加密参数
- **算法**：AES-256-CBC
- **PBKDF2 迭代**：256,000 轮
- **HMAC 算法**：HMAC-SHA512
- **页面大小**：4096 字节
- **预留空间**：80 字节（前 16B 为 CBC IV，后 64B 为 HMAC 校验和）

### 3.2 zstandard 字典压缩
- **压缩标志**：字段 `WCDB_CT_message_content == 4`，或二进制数据起始为 `b"\x28\xb5\x2f\xfd"`。
- **解压要求**：必须调用 `zstandard.ZstdDecompressor().decompress(...)` 无损还原为 UTF-8 纯文本。

---

## 4. 常见消息类型字典 (`local_type`)

| `local_type` 数值 | 消息分类 | 处理策略 |
|---|---|---|
| **`1`** | **纯文本消息** | **【保留】全量提取并清洗** |
| `3` | 图片 / 缩略图 | 【排除】跳过 |
| `6` / `244813135921` | 文件 / 电子表格 (.xlsx) | 【排除】跳过 |
| `43` | 短视频 / 动态影像 | 【排除】跳过 |
| `47` | 表情包 / 动图 | 【排除】跳过 |
| `10000` | 系统消息 (拍一拍、进群退群提示) | 【排除】跳过 |
