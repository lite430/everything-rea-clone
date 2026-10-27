# Everything 克隆 · REA 逆向分析复刻版

> 使用 **REA (Reverse Engineer Anything)** 对 voidtools **Everything 1.4.1.1028 x64**
> 进行逆向分析，依据分析得到的机制与功能清单，从零实现的文件名即时搜索工具。
>
> ⚖️ 合规声明：逆向仅用于研究/互操作目的；本项目**未复制** Everything 的任何代码、
> 资源、图标或文案，全部实现独立编写，仅复刻功能行为。Everything 是免费软件，
> 日常使用请支持[官方原版](https://www.voidtools.com/)。

## 快速开始（需管理员权限）

**方式一：桌面客户端（推荐）** — 双击桌面快捷方式 **「Everything Clone」**
（或项目根目录 `启动Everything桌面客户端.cmd`）。Electron 桌面窗口 + 托盘常驻 +
记住窗口尺寸；引擎未运行时会自动拉起（冷启动建索引约 1 分钟）。

**方式二：命令行**

```bat
:: 1. 启动 Web UI + HTTP API（索引约 1 分钟，之后 USN 实时更新）
启动Everything克隆.cmd          :: 或: python everything.py --server 8888
:: 浏览器打开 http://127.0.0.1:8888/

:: 2. 命令行搜索（es.exe 风格）
python everything.py -s "readme ext:md" -n 20
python everything.py -s "size:>1gb" --sort size --desc -i
python everything.py -s "zcode" --csv > results.csv

:: 3. 索引统计
python everything.py --stats

:: 4. IPC（SDK WM_COPYDATA + 命名管道）
python everything.py --server 8888 --ipc
python tools\pipe_client.py readme 5

:: 5. Windows 服务模式（管理员）
python everything.py --install-service    :: sc create EverythingClone
python everything.py --start-service       :: 服务内完成索引 + USN + IPC
python everything.py --stop-service
python everything.py --uninstall-service

:: 6. 文件列表（离线 EFU/CSV/TXT）
python everything.py -s "list:demo-filelist" --file-list analysis\demo-filelist.txt -n 10
```

单元测试（无需管理员）：

```bat
python tests\test_query.py
```

## 与原版对齐的核心能力

| 能力 | 说明 |
|---|---|
| NTFS MFT 全量索引 | 直接解析 $MFT（引导扇区→数据运行→记录→$FILE_NAME 父链），**212 万条目 / 55 秒** |
| USN Journal 实时更新 | 文件创建/改名/删除秒级跟随；无日志时自动创建（同原版行为） |
| 即时搜索 | 子串查询暖态 **37–66ms**（全名拼接缓冲 C 速扫描） |
| Everything 搜索语法 | `a b`、`a|b`、`!a`、`( )`、`"短语"`、`*?`、`case:` `regex:` `wholeword:` `exact:` `matchpath:` `path:`、`file:` `folder:` `audio:` `video:` `pic:` `doc:` `exe:` `zip:` `ext:`、`size:` `dm:` `dc:` `attrib:` `dupe:` `root:`、`C:\路径\` 前缀 |
| 全列排序 | 名称/路径/大小/修改时间/创建时间/扩展名/属性，升/降序 |
| Web UI | 即输即搜、过滤器下拉、书签、搜索历史、CSV 导出、复制路径、实时变更查看、状态栏 |
| HTTP API（兼容原版） | `http://127.0.0.1:8888/?search=…&json=1&count=100&offset=0&sort=name&ascending=1&path=1&csv=1` |
| ETP/FTP 服务器 | `python everything.py --server 8888 --etp 8899`，FTP 客户端可列目录/下载（`ftp://127.0.0.1:8899/`，任意用户名密码） |
| IPC（EVERYTHING_IPC 兼容） | `--ipc`：WM_COPYDATA 窗口（类 `EVERYTHING`，SDK `EVERYTHING_IPC_QUERY/LIST` 协议，官方 SDK 客户端可直接查询）+ 命名管道 `\\.\pipe\EverythingClone`（JSON 行协议：`tools/pipe_client.py`） |
| Windows 服务模式 | `--install-service` / `--start-service` / `--stop-service` / `--uninstall-service`（服务名 `EverythingClone`，服务内完成索引+USN+IPC，管理员权限） |
| 文件列表（离线索引） | `--file-list 路径.efu|.csv|.txt`：加载 Everything EFU/CSV（表头同原版）或纯路径清单；`list:<名称>` 过滤器选择某个列表 |
| 缩略图 / 预览窗格 | Web UI 🖰 缩略图视图（图片结果网格，`/thumb` 本地生成）与 ◧ 预览窗格（图片大图 / 文本内容预览）；仅限索引内文件 |
| CLI | `-s/-n/-o/--sort/--desc/--csv/-i/--stats/--server/--etp/--ipc/--file-list/--install-service…` |

## 目录结构

```
everything-rea-clone/
├── samples/                  # 官方样本（everything.exe 1.4.1.1028 x64 便携版）
├── analysis/                 # ★ REA 逆向分析产物
│   ├── FEATURES.md           #   ★ 功能全景 + 代码级证据报告（本项目的分析成果）
│   ├── rea-analyze.json      #   REA analyze：3045 函数 / 8 段 / 4814 字符串
│   ├── rea-trace-*.json      #   字符串→函数引用追踪（FSCTL/MFT 枚举）
│   ├── rea-decompile-*.md    #   Ghidra 反编译伪代码（ntfs-get-fd / enum-mft）
│   ├── pe-imports.txt        #   PE 导入表（DeviceIoControl/命名管道/服务/socket…）
│   └── strings_*.txt         #   字符串证据（ntfs.c、USN、IPC、HTTP、ETP、语法）
├── tools/                    # REA 依赖引擎（Ghidra 12.1.4、JDK 21）与调试脚本
├── engine/                   # ★ 克隆引擎（纯 Python，无第三方依赖）
│   ├── ntfs.py               #   裸卷访问/MFT 解析/USA fixup/FSCTL/USN 读取
│   ├── index.py              #   卷索引/路径树/实时刷新/名 blob 快扫
│   ├── query.py              #   Everything 搜索语法解析与求值
│   ├── app.py                #   多卷 App、USN 监控线程、搜索/排序/统计、缩略图/预览
│   ├── ipc.py                #   IPC：WM_COPYDATA SDK 协议窗口 + 命名管道
│   ├── service.py            #   Windows 服务（pywin32，sc create 可注册）
│   ├── filelist.py           #   文件列表（EFU/CSV/TXT）加载与索引
│   ├── etp.py                #   ETP/FTP 服务器（列目录/下载）
│   └── server.py             #   HTTP 服务器（Web UI + 原版兼容 API + CSV/JSON + /thumb /preview）
├── app/index.html            # ★ Web UI（深色主题，即输即搜，与桌面客户端共用）
├── client/                   # ★ 桌面客户端（Electron 壳）
│   ├── main.js               #   自动拉起引擎/托盘常驻/记住窗口尺寸/截图验收
│   └── assets/icon.png|.ico  #   自绘应用图标
├── tests/test_query.py       # 21 项语法单元测试（全部通过）
├── everything.py             # CLI / 服务器入口
├── 启动Everything克隆.cmd    # 一键启动（Web UI + ETP + IPC + 文件列表）
└── 启动Everything桌面客户端.cmd # 一键启动桌面客户端
```

## 逆向 → 实现的三个关键发现

1. **混合读取策略**（来自 `FUN_1400cfc70` 反编译）：初始索引用裸 $MFT 顺序读，
   增量更新用 `FSCTL_GET_NTFS_FILE_RECORD`——后者走 NTFS 驱动的**内存态视图**，
   能立即看到尚未落盘的元数据（实测裸读会漏掉刚创建的文件，与反编译结论一致）。
2. **USA Fix-Up 几何**（来自 "MFT USA Offset/Count" 字符串）：每扇区末 2 字节
   需用更新序列数组第 j 项修复（`off = j*sector-2`），修复错位会造成文件名中部
   出现 U+0000——实测已修复并对全量 212 万条目验证。
3. **监控节流**（来自 "%d ntfs monitors started" 每卷一线程）：索引只关心结构性
   变更（创建/删除/重命名/属性/硬链接），过滤高频数据写入事件并用有界轮询防止
   追赶循环卡死。

## 与原版的差距（如实说明）

- 索引速度：原版 C++ 约 2–5 秒，本克隆（纯 Python）约 55 秒（212 万条目）。
- 复杂查询（正则/OR 组合）延迟 0.5–2.5 秒（原版 <100ms）。
- 服务模式下（Session 0）的 WM_COPYDATA 窗口对本机交互会话不可见（FindWindow 跨会话隔离，Windows 机制）；服务场景请用命名管道。桌面/服务器模式（`--ipc`）下 SDK 客户端可正常 FindWindow。
- 文件列表提供加载与索引（`--file-list` + `list:` 过滤），未含原版的 File List Editor 编辑器 UI。
- 单卷重命名风暴等极端负载下，实时性可能退化到秒级。

## 需要重新运行 REA 分析时

```bat
set JAVA_HOME=%cd%\tools\jdk21\jdk-21.0.12.1+1
set GHIDRA_INSTALL_DIR=%cd%\tools\ghidra_12.1.4_PUBLIC
rea analyze samples\everything-x64\everything.exe
rea trace  samples\everything-x64\everything.exe "FSCTL_GET_NTFS_VOLUME_DATA"
rea decompile samples\everything-x64\everything.exe 0x1400cfc70
```
