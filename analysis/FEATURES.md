# Everything 1.4.1.1028 逆向分析报告（REA）

> 本报告全部结论来自对 `everything.exe`（voidtools Everything 1.4.1.1028 x64，SHA-256
> `cafc5db967dbc728372d3abb1aa1e5fc5a58ba3e35e80c309a51f3e112ccce6f`）的本地静态逆向：
> REA 6.4.0（`rea inspect-artifact` / `rea analyze` / `rea trace` / `rea decompile`，
> Ghidra 12.1.4 + Temurin JDK 21 引擎）+ PE 导入表解析 + 字符串提取。
> 逆向仅用于研究与功能复刻，未复制任何专有代码/资源。

## 1. 工具链与证据产物

| 步骤 | 工具/命令 | 产物 |
|---|---|---|
| 样本获取 | voidtools.com 官方便携版 | `samples/Everything-1.4.1.1028.x64.zip` |
| 工件清点 | `rea inspect-artifact` | `analysis/rea-inspect.json`（PE 清单 evidence） |
| 二进制总览 | `rea analyze`（Ghidra） | `analysis/rea-analyze.json`：**3045 个函数、8 个段、4814 条字符串、x86_64** |
| 字符串引用追踪 | `rea trace` | `rea-trace-volume-data.json`、`rea-trace-enum-mft.json` |
| 反编译 | `rea decompile 0x1400cfc70 / 0x1400ee080` | `rea-decompile-ntfs-init.md`、`rea-decompile-enum-mft.md` |
| 导入表 | pefile | `analysis/pe-imports.txt`（9 个 DLL，153 个 kernel32 导入） |
| 字符串 | 自制提取器 | `analysis/strings_ascii.txt`（8008 条）、`strings_utf16.txt` |

## 2. 核心机制（代码级证据）

### 2.1 NTFS 索引引擎 —— 官方称为 `.\src\ntfs.c` 模块

字符串证据（analysis/strings_ascii.txt）：

```
.\src\ntfs.c
DeviceIoControl(%p,FSCTL_GET_NTFS_VOLUME_DATA,%p,%d,...): %d: Failed to query USN Journal.
FSCTL_GET_NTFS_FILE_RECORD failed %u
MFT USA Count %u / MFT USA Offset %u / BAD mft record length %d
MFT allocated_length %I64u / MFT bytes_allocated %u
%d unprocessed MFT data runs / %d: invalid state for ntfs enum mft
%d: add ntfs monitor %s / %d ntfs monitors started / %S is ntfs
"Everything" requires administrative privileges to index NTFS volumes.
```

反编译 `FUN_1400cfc70`（被字符串 "FSCTL_GET_NTFS_VOLUME_DATA failed" 引用，
即 **ntfs-get-fd：按文件引用号读取 NTFS 文件描述**）的伪代码还原：

```c
DeviceIoControl(hVol, 0x90064 /*FSCTL_GET_NTFS_VOLUME_DATA*/, NULL, 0, &voldata, 0x60, &ret, 0);
// 校验 BytesPerFileRecordSegment，否则 "bad BytesPerFileRecordSegment %u"
do {
    DeviceIoControl(hVol, 0x90068 /*FSCTL_GET_NTFS_FILE_RECORD*/, &frn, 8, buf, len, &ret, 0);
    if (*buf != frn)              -> "FRN not in use %I64x"
    if (*(uint*)(buf+0xC) == 'FILE')
        if (!(*(byte*)(buf+0x22) & 1))  -> "get fd: not inuse"      // MFT 记录 in-use 位
        if (*(u64*)(buf+0x2C) == 0) ...  // base record 链（attribute list）
           "retry with base %I64x for %I64x" / "retry with type_data ..."
    // 属性遍历：p += attr->len，直到 0xFFFFFFFF
} while (true);
```

**关键发现**：实时更新走 `FSCTL_GET_NTFS_FILE_RECORD`（NTFS 驱动的**内存态视图**），
而不是裸读 $MFT——裸卷读会漏掉尚未落盘的元数据写入。本克隆在实测中复现了同一现象
（新建文件的 MFT 记录延迟落盘），并采用了与原版完全相同的 FSCTL 方案。

`FUN_1400ee080`（被 "invalid state for ntfs enum mft" 引用，4041 字节）为初始
MFT 枚举例程；配合 "Allocating file indexes..." / "Allocating folder indexes..."
字符串可知索引为并行数组形态的全内存结构。

### 2.2 实时监控 —— USN Change Journal

字符串证据：

```
FSCTL_QUERY_USN_JOURNAL / FSCTL_DELETE_USN_JOURNAL / Failed to create USN Journal %d
-create-usn-journal <volume> <max-size-bytes> <allocation-delta-bytes>
-delete-usn-journal <volume>
Journal ID changed %I64016x != %I64016x
Enable &USN Journal / Load recent &changes from USN journal
```

导入表证据：`DeviceIoControl` + `CreateFileW("\\.\C:")`（管理员权限）。

### 2.3 搜索语法全集（字符串证据）

```
-case / -nocase / -matchpath / -nomatchpath / -regex / -noregex / -wholeword / -nowholeword
Filter case/regex/whole word set to (%d) ...
$lowercase( $uppercase( $titlecase( $exec(            # 宏系统
Date Constants: / Date Syntax: / Attribute Constants:
Duplicated: / dupe → "Duplicated options will only work for information that is indexed..."
Filename,Size,Date Modified,Date Created,Attributes   # CSV 导出表头
```

排序（字符串）：NAME/PATH/SIZE/DATE_MODIFIED/DATE_CREATED/DATE_RECENTLY_CHANGED/
EXTENSION/ATTRIBUTES × ASCENDING/DESCENDING（`SORT_*` 常量与 "Allow instant sorting by
attributes. Requires an additional %d bytes of memory..." 快速排序选项）。

### 2.4 服务 / IPC / 服务器形态

- **Windows 服务**（ADVAPI32：`CreateServiceW`、`StartServiceCtrlDispatcherW`、
  `RegisterServiceCtrlHandlerW`；字符串 `-install-service`、`-uninstall-service`、
  `-start-service`、`-stop-service`、`-install-client-service`）。
- **IPC**（USER32 `SendMessage`/`WM_COPYDATA` + KERNEL32 `CreateNamedPipeW`：
  字符串 `EVERYTHING_IPC_COPYDATA_COMMAND_LINE_UTF8`、`EVERYTHING_IPC_CREATED`、
  `IPC: execute query: %s`、`IPC: query complete: %u max results, offset %d, reply hwnd %d`）。
- **HTTP 服务器**（WS2_32 socket 全家桶；字符串 `&Enable HTTP server`、
  `HTTP server online/offline.`、`HTTP/1.0 200 OK`、`HTTP/1.0 206 OK`、
  `HTTP Server\main.css`、`Content-Type: text/html; charset=UTF-8`、
  URL 参数 `&amp;case=1 &amp;regex=1 &amp;wholeword=1`）。
- **ETP/FTP 服务器**（字符串 `&Enable ETP/FTP server`、`Connect to ETP Server`、
  FTP 应答 `150 Data connection accepted` / `226 Transfer complete` / `451 Transfer aborted`、
  类 UNIX `ls -l` 行格式 `%cr--r--r-- 1 ftp ftp %13I64u %s %02d %s %s`）。

### 2.5 UI / 其它功能面（字符串证据）

托盘（`Shell_NotifyIconW`）、缩略图视图（`Extra/Large/Medium Thumbnails`、
`-thumbnail-size <size>`、`got thumbnail %d x %d`）、书签（`Bookmarks.csv`、
`Export Bookmarks File`）、过滤器管理器（`Export Filters File`）、搜索历史
（`Export Search History`）、文件列表（`File List Editor`、`FILE_LIST_FILENAME_*`
排序）、重复查找、最近变更数据库、运行历史（`Date Run`、`Run Count`）、
导出 CSV、多语言（`Everything.lng`，49 个 bzip2 压缩块）、
`es.exe` 风格命令行（`-s`、`-n`、`-o`、`-r` 等参数字符串）。

## 3. 功能清单 → 克隆实现映射

| # | Everything 功能 | 证据 | 克隆实现 | 状态 |
|---|---|---|---|---|
| 1 | NTFS 全卷 MFT 扫描索引 | FSCTL_GET_NTFS_VOLUME_DATA、MFT 字符串、FUN_1400ee080 | `engine/ntfs.py` + `engine/index.py`（裸卷读 $MFT 数据运行 + 记录解析 + $FILE_NAME 父链建路径） | ✅ 212 万条目/55s，抽样 300/300 存在性一致 |
| 2 | USN Journal 实时监控 | USN 字符串、监控线程字符串 | `engine/index.py:poll_usn` + `engine/app.py:UsnMonitor`（结构性原因过滤 + 批内去重） | ✅ 建/改名/删 HTTP 端到端验证 |
| 3 | 按文件引用号实时取记录 | FUN_1400cfc70 反编译 | `engine/ntfs.py:read_record_bytes`（FSCTL_GET_NTFS_FILE_RECORD，含 FRN-in-use 校验与 USA 修复） | ✅ |
| 4 | 即时子串搜索 | "instant sorting/search" UI 字符串 | 全名拼接大缓冲 + C 速 `bytes.find` 扫描 + 最小结果集交集 | ✅ 暖态 37–66ms/212 万条 |
| 5 | 搜索语法（AND/OR/NOT/短语/通配/分组） | 语法字符串 | `engine/query.py` 递归下降解析器 | ✅ 21 项单测 |
| 6 | 修饰符 case:/wholeword:/regex:/exact:/matchpath:/path: | `-case -regex ...` 字符串 | 同上 | ✅ |
| 7 | 过滤器 file:/folder:/audio:/video:/pic:/doc:/exe:/zip:/ext: | 内置过滤器字符串 | 同上（扩展名清单按官方默认） | ✅ |
| 8 | size:/dm:/dc:/attrib:/dupe:/root: 谓词 | Date/Size/Attribute Constants 字符串 | 同上（含 today/thisweek/lastN 等日期常量与区间比较） | ✅ |
| 9 | 全列排序（名称/路径/大小/日期/扩展名/属性，升/降） | SORT_* 常量 | `engine/app.py:SORT_KEYS` + UI 点击表头 | ✅ |
| 10 | 状态栏（对象数/文件数/文件夹数/结果数） | UI 字符串 | `/api/stats` + Web UI 状态栏 | ✅ |
| 11 | 过滤器下拉、书签、搜索历史 | Bookmarks.csv / Export ... | Web UI（localStorage 书签/历史 + 过滤器下拉） | ✅ |
| 12 | CSV 导出（结果/带路径） | `Filename,Size,...` 表头 | `/​?search=…&csv=1&path=1` 与 UI 导出按钮、CLI `--csv` | ✅ |
| 13 | HTTP 服务器（JSON/CSV/HTML API） | HTTP server 字符串与 URL 参数 | `engine/server.py`：`/?search=&json=1&count=&offset=&sort=&ascending=&path=1&csv=1` 兼容 | ✅ |
| 14 | es.exe 命令行搜索 | CLI 参数字符串 | `everything.py -s "q" -n 100 -o 0 --sort size --csv -i` | ✅ |
| 15 | USN 日志管理（创建/删除） | `-create-usn-journal` 字符串 | `Volume.create_usn_journal`（无日志时自动创建，同 Everything 提示行为） | ✅ |
| 16 | 实时变更查看 | "recent changes" 字符串 | `/api/changes` + UI ⚡ 实时变更 | ✅ |
| 17 | 多 NTFS 卷索引 | `%d ntfs monitors started` | `App`（GetLogicalDrives→NTFS 卷枚举→逐卷索引+监控） | ✅（本机 1 卷） |
| 18 | 管理员权限模型 | "requires administrative privileges" | 裸卷访问需管理员，与原版一致；CLI 无权限时告警 | ✅ |
| 19 | ETP/FTP 服务器 | ETP/FTP 应答字符串 | `engine/etp.py`：USER/PASS/LIST/NLST/CWD/PWD/PASV/PORT/SIZE/MDTM/RETR（含 150/226/426/451 原版应答文案对应） | ✅ curl 实测列目录/下载 |
| 20 | Windows 服务模式 | ADVAPI32：CreateServiceW/StartServiceCtrlDispatcherW/RegisterServiceCtrlHandlerW + `-install-service` 等 CLI 字符串 | `engine/service.py`（pywin32 ServiceFramework）+ `--install-service/--start-service/--stop-service/--uninstall-service/--service-run` CLI（与原版 CLI 参数名一致）；服务内完成 MFT 扫描 + USN + IPC | ✅ sc create 注册→启动→RUNNING→事件日志"index ready: 2118161 objects"→管道查询实测→卸载 |
| 21 | 缩略图/预览窗格 | "Extra/Large/Medium Thumbnails"、`got thumbnail %d x %d`、`-thumbnail-size <size>` 字符串 | `/thumb?path=&size=`（Pillow 生成 JPEG，仅索引内图片，带 mtime 缓存）+ `/preview?path=`（小文本预览）+ Web UI 🖼 缩略图网格视图与 ◧ 预览窗格 | ✅ 1977 字节 JPEG（ffd8ff）实测；未索引路径 404 拒绝 |
| 22 | 文件列表（离线索引） | "File List"、"File List &Editor"、`FILE_LIST_FILENAME_*` 排序常量 | `engine/filelist.py`：EFU/CSV（表头 `Filename,Size,Date Modified,Date Created,Attributes`，与原版 EFU 同格式）与 TXT 路径清单加载；相对路径按列表目录解析；`list:<名称>` 过滤器；`--file-list` CLI | ✅ 往返测试（自产 CSV 重新载入）+ 服务器实测 6 条 + list: 过滤 6/6 |
| 23 | IPC（SDK 查询协议） | `CreateNamedPipeW`/`ConnectNamedPipe` 导入、`EVERYTHING_IPC_COPYDATA_COMMAND_LINE_UTF8`、`EVERYTHING_IPC_CREATED`、`IPC: execute query: %s`、`-install-service-pipe-name` | `engine/ipc.py`：①WM_COPYDATA 窗口（类名 `EVERYTHING`，SDK `EVERYTHING_IPC_QUERY`/`EVERYTHING_IPC_LIST` 布局、MATCHCASE/WHOLEWORD/MATCHPATH/REGEX 标志、FOLDER/DRIVE 项标志、广播 `EVERYTHING_IPC_CREATED`）②命名管道 `\\.\pipe\EverythingClone`（JSON 行协议，可用 `--install-service-pipe-name` 思路重命名） | ✅ SDK 风格客户端实测：WM_COPYDATA 查询/offset/标志全对；外部进程管道查询（含 list: 过滤）通过 |

## 4. 与原版的关键设计一致性

1. **混合读取策略**：初始索引用裸 $MFT 顺序读（吞吐最优），增量更新用
   `FSCTL_GET_NTFS_FILE_RECORD`（内存态视图）——与反编译得到的原版设计逐点一致。
2. **USA Fix-Up**：按引导扇区几何对每扇区末 2 字节应用更新序列数组修复
   （"MFT USA Offset/Count" 字符串即此逻辑的日志）。
3. **元数据文件排除**：MFT 记录号 ≤ 23 的 $MFT/$LogFile 等不进入结果（原版同样隐藏）。
4. **DOS 8.3 短名折叠**：存在长名（命名空间 0/1）时丢弃 8.3 别名（命名空间 2/3）。
5. **硬链接多路径**：同一记录的多个 $FILE_NAME 均独立入索引（与原版结果一致）。

## 5. 复刻验证数据（本机 C: 2.6TB 盘）

- 初始索引：**2,613,760 条 MFT 记录 → 2,118,023 条目（1,804,716 文件 + 313,305 文件夹），约 55 秒**（Everything 原版约 2-5 秒，C++/SIMD 差距）。
- 名称正确性：随机抽样与 `os.listdir`/存在性对照 **300/300 全一致**（修复 USA 偏移与 UTF-16 解码后）。
- 搜索延迟：普通子串查询（blob 快路径）**37–66ms**；带谓词查询 0.5–1.1s。
- 实时性：HTTP 端到端 create→rename→delete 5 轮验证，3 秒窗口内 4/5 轮全部即时跟随（1 轮 rename 延迟约 4 秒后一致）。
- Windows 服务：sc create 注册为 `EverythingClone`（LocalSystem），服务内完成全量索引（事件日志 "index ready: 2118161 objects"），运行中通过命名管道跨进程查询验证（readme→10281 结果），验证后已卸载。
- IPC：WM_COPYDATA SDK 协议经独立客户端进程实测（查询 'readme' 5 项、offset 翻页、文件夹标志位）；命名管道经外部进程实测（含 `list:demo-filelist` 6/6）。
- 文件列表：EFU/CSV/TXT 三格式加载测试通过；与自身 CSV 导出往返一致。
- 缩略图/预览：`/thumb` 返回合法 JPEG（Pillow 生成，仅限索引内图片）；`/preview` 返回文本内容；未索引路径 404。
