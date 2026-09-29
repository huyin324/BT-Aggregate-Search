# -*- coding: utf-8 -*-
"""无界面批量搜索自测：验证修复后的并发线程不会闪退，且结果正确汇总。"""
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import importlib.util
BASE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("bt", os.path.join(BASE, "BT磁力聚合搜索工具.py"))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

from PyQt6.QtWidgets import QApplication

# 关键：用假搜索函数替换真实网络请求，快速返回固定数据，重点验证线程生命周期/信号/汇总
def fake_search_site(site, kw, page, proxies=None):
    items = []
    for i in range(3):
        t = m.TorrentItem()
        t.site = site["name"]
        t.name = f"{kw} 测试条目{i+1}"
        t.magnet = f"magnet:?xt=urn:btih:{'a'*40}"
        t.size = f"{i+1}GB"
        t.date = "2026-08-07"
        t.hot = str(i + 1)
        items.append(t)
    return items

m.search_site = fake_search_site

PROXY_ORIG = dict(m.PROXY_CONFIG)
m.PROXY_CONFIG["enabled"] = False  # 直连分支，避免真实代理

app = QApplication(sys.argv)
win = m.MainWindow()

# 设置搜索关键词，否则 start_all_search 会因空关键词提前返回
win.search_edit.setText("test")

print(f"开始批量搜索自检（模拟 {len(m.ACTIVE_SITES)} 个启用站点，每站 3 条）...")
win.start_all_search()

deadline = time.time() + 30
while (win.active_threads > 0 or len(win.threads) > 0) and time.time() < deadline:
    app.processEvents()
    time.sleep(0.05)

print(f"完成后 active_threads={win.active_threads} 活动线程数={len(win.threads)}")
total = win.result_table.rowCount()
print(f"表格结果行数={total}（预期={len(m.ACTIVE_SITES)*3}）")
print(f"日志行数={win.log_text.document().blockCount()}")

# 验证线程已全部清理（核心修复点）
assert len(win.threads) == 0, f"线程未清理干净：{len(win.threads)}"
assert total == len(m.ACTIVE_SITES) * 3, f"结果数量不符：{total}"
print("✅ 批量搜索自检通过：无闪退、线程已回收、结果正确汇总。")

# 测试代理对话框构造 + 测试线程类可实例化
dlg = m.ProxyDialog(win)
tt = m.ProxyTestThread("http", "127.0.0.1", 7897)
print("✅ 代理对话框与测试线程类构造正常。")
tt.deleteLater()
app.processEvents()
print("ALL OK")
sys.exit(0)
