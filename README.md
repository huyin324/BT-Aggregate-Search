# BT 磁力聚合搜索工具

一个基于 **PyQt6** 的桌面端 BT / 磁力链接聚合检索工具。可同时在 **40+ 个 BT 站点** 并发检索、汇总结果，并支持智能代理回退、结果排序、实时日志排查。已打包为单文件 exe（见 [Releases](https://github.com/huyin324/BT-Aggregate-Search/releases)），开箱即用。

---

## 一、功能特性

| 功能 | 说明 |
| --- | --- |
| **多站点并发聚合** | 一次性在全部站点并发检索，结果统一汇总到表格，左侧站点栏实时显示各站命中条数（绿色高亮）。 |
| **智能代理回退** | 菜单「设置 → 代理设置」可配置协议（http/https/socks5）、地址、端口。启用后：**先直连检索 → 失败/无数据再走代理 → 两次都失败则自动跳过该站点**。 |
| **与原站一致的每页条数** | 每个站点已按原站单页条目数配置 `page_size`（如 nyaa 75、torrentkitty 20、52BT/磁力妹妹/91BT 20、磁力片 50），不截断、不合并分页。 |
| **多维度排序** | 工具栏下拉支持按 **文件大小 / 收录时间 / 热度** 的 **升序 / 降序** 统一排序（客户端转换 `size_to_bytes` / `date_to_int` / `hot_to_int`）。 |
| **实时日志面板** | 底部白底日志框逐站打印：开始检索（直连）→ 直连失败改代理重试 → 完成 N 条 / 跳过，以及全局开始/完成、错误与状态变更，便于排查。 |
| **代理可用性自测** | 代理设置对话框内置「🔌 测试代理可用性」按钮：先做 TCP 连通性检查，再经该代理发一次外网请求，结果实时回显并写入日志。 |
| **反爬与性能优化** | 随机 User-Agent + 反爬请求头、随机微延迟；全部站点并发检索（最大 8 线程调度）；结果增量追加避免全表重建。 |
| **工具图标** | 主窗口与 exe 均使用 `icon.ico`。 |

---

## 二、已集成站点（按解析模板归类，共 40+ 个）

| 模板 | 站点 |
| --- | --- |
| nyaa | nyaa.si / nyaa.net / sukebei.nyaa.si |
| torrentkitty | cn.torrentkitty.tv |
| zsky | 52BT（529076 / 529075）、磁力妹妹（9966109 / 9966108）、91BT（911178 / 911179 新域名） |
| btmovi | 磁力蜘蛛 btmovi.cyou |
| btapp / starok 新主机 | t12.btapp.top、so2.starok.top |
| 通用尽力解析 | 磁力宝 clb04、磁力搜索88、TTBT(d2.ttbt.me)、磁力猫、北辰阁、种子吧、磁力多、天堂磁力、沙发影视、磁力狐、淘磁力、移花宫×3、磁力搜索CC、BTSearch、3D48、凌风云、w1.sokitty、so1.starok、u001.25img 等 |

> ⚠️ 说明：部分站点（磁力猫、北辰阁、种子吧、淘磁力、BTSearch、凌风云、移花宫等）为 JS 渲染或需验证码 / Cloudflare，`requests` 直连拿不到磁力，运行时会自动跳过——这是此类站点的固有限制，并非工具缺陷。可通过后续接入无头浏览器（如 Playwright）渲染后再解析来扩展。

---

## 三、使用方法

### 1. 直接运行 exe（推荐）
从 [Releases](https://github.com/huyin324/BT-Aggregate-Search/releases) 下载 `BT-Aggregate-Search-V1.0.exe`（即打包好的 `BT磁力聚合搜索工具.exe`），双击即可运行，无需安装 Python。

### 2. 源码运行
```bash
pip install PyQt6 requests beautifulsoup4 lxml
python BT磁力聚合搜索工具.py
```

### 3. 基本操作
1. 在顶部输入框输入关键词，点「🔍 搜索」检索**当前选中站点**；点「⚡ 全部搜索」并发检索**所有站点**。
2. 左侧站点栏点击可切换单站视图（「📊 全部站点」为汇总视图）；栏内「（N条）」为各站命中数（绿色）。
3. 工具栏「排序」下拉：默认 / 文件大小 / 收录时间 / 热度 × 降序 / 升序。
4. 结果表格支持翻页（每页条数与原站一致）、双击磁力链接复制、点「打开」用默认程序处理。
5. 菜单「设置 → 代理设置」配置并测试代理。

---

## 四、技术架构

- **GUI**：PyQt6（`QMainWindow` + 可拖拽 `QSplitter` 左右布局 + 底部日志面板）。
- **检索模型**：每个站点 = 一个配置字典 `{id, name, url, parser, page_size}` + 一个对应的解析函数（`parse_xxx`），解析函数与 GUI 解耦，可独立单测。
- **并发与线程安全**：每个站点一个 `SearchThread(QThread)`，所有跨线程信号显式使用 `Qt.ConnectionType.QueuedConnection` 投递到 GUI 线程（避免工作线程操作 GUI 控件导致的闪退），线程引用由 `self.threads` 持有、`finished` 时 `deleteLater` 回收。
- **代理回退**：`SearchThread.run()` 先 `_attempt(None, False)` 直连；失败/无数据且启用代理时再 `_attempt(get_proxies(), True)`；两次都失败则 `finish_signal(0)` 标记跳过。
- **反爬**：随机 UA、`Sec-CH-UA` 等请求头、随机微延迟。

### 目录结构
```
BT磁力聚合搜索工具.py     # 主程序（GUI + 解析器 + 检索线程）
icon.ico                 # 工具图标
parser_test.py           # 解析函数单测（基于保存的真实页面 HTML）
test_sites.py            # 全站实测脚本
headless_gui_test.py     # 无界面 GUI 构建校验
headless_batch_test.py   # 批量搜索不闪退 / 线程回收自测
```

---

## 五、从源码构建 exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "BT磁力聚合搜索工具" ^
            --icon icon.ico --add-data "icon.ico;." ^
            BT磁力聚合搜索工具.py
```
产物位于 `dist/BT磁力聚合搜索工具.exe`。

---

## 六、常见问题（FAQ）

- **为什么某些站点偶尔 0 结果？** 多为出口 IP 被站点限流 / 反爬，或对 JS 渲染 / 验证码类站点不支持。工具已自动跳过无数据站点；换用本机网络 / 自己的代理并以人工节奏使用通常可正常返回。
- **批量搜索闪退过，现在修复了吗？** 已修复：根因为跨线程信号被解析成直连、在工作线程操作 GUI 控件导致崩溃，已统一改为 `QueuedConnection` 并保活线程引用。
- **代理怎么配？** 菜单「设置 → 代理设置」，填协议 / 地址 / 端口后，可点「测试代理可用性」验证，再勾选「启用」。

---

## 七、免责声明

本工具仅用于聚合检索公开可访问的 BT / 磁力元数据，**不托管、不分发任何受版权保护的内容**。使用者须遵守所在国家 / 地区法律法规，因滥用造成的任何后果由使用者自行承担。
