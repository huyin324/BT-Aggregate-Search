# -*- coding: utf-8 -*-
"""V1.2 无头自测：矢量图标 / 代理预检 / 预览按钮绘制 / whatslink 线程与预览弹窗。"""
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import importlib.util
BASE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("bt", os.path.join(BASE, "BT磁力聚合搜索工具.py"))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter, QPixmap, QColor
from PyQt6.QtCore import Qt, QEventLoop, QTimer

app = QApplication(sys.argv)

# ---------- 1) 矢量图标 ----------
icon = m.load_app_icon()
assert not icon.isNull(), "load_app_icon 失败"
assert len(icon.availableSizes()) >= 3, f"SVG 多尺寸渲染异常: {icon.availableSizes()}"
print(f"✅ 矢量图标：{len(icon.availableSizes())} 个尺寸 {[(s.width()) for s in icon.availableSizes()]}")

# ---------- 2) 代理预检（未启用 → 直接返回不可用） ----------
m.PROXY_CONFIG["enabled"] = False
ok, msg = m.check_proxy_available()
assert ok is False and "未启用" in msg, f"未启用时应返回 False: {ok}, {msg}"
print("✅ 代理预检（未启用）：", ok, msg)

# 启用 + 探测真实代理 127.0.0.1:7897
m.PROXY_CONFIG["enabled"] = True
ok2, msg2 = m.check_proxy_available()
print(f"{'✅' if ok2 else '⚠️'} 代理预检（启用 7897）：{ok2} {msg2}")
# 缓存验证：第二次调用应立即返回
t0 = time.time()
ok3, _ = m.check_proxy_available()
assert time.time() - t0 < 0.05, "缓存未生效"
print("✅ 代理预检缓存生效（二次调用 <50ms）")
m.reset_proxy_cache()
m.PROXY_CONFIG["enabled"] = False

# ---------- 3) 预览按钮绘制（MagnetPreviewDelegate） ----------
win = m.MainWindow()
t = m.TorrentItem()
t.name = "测试条目"; t.magnet = "magnet:?xt=urn:btih:" + "a" * 40
t.size = "1GB"; t.date = "2026-09-29"; t.hot = "5"; t.site = "测试站"
win._add_row_to_table(t)
assert win.result_table.columnCount() == 7, f"列数应为 7: {win.result_table.columnCount()}"
assert win.result_table.item(0, 6) is not None, "第 7 列占位项缺失"
# 实际绘制一帧验证 delegate 无异常
img = QImage(92, 35, QImage.Format.Format_ARGB32)
painter = QPainter(img)
opt = m.QStyleOptionViewItem()
opt.rect = win.result_table.visualItemRect(win.result_table.item(0, 6))
idx = win.result_table.model().index(0, 6)
win._preview_delegate.paint(painter, opt, idx)
painter.end()
print("✅ 预览按钮列：7 列 + 占位项 + delegate 绘制无异常")

# ---------- 4) whatslink 线程 + 预览弹窗（真实网络，直连） ----------
MAGNET = "magnet:?xt=urn:btih:a73007419541f0ab5ad124a0b0ba959ee7da4f02"
result = {"done": False, "imgs": None, "name": "", "err": ""}

def on_done(name, imgs, info):
    result.update(done=True, imgs=imgs, name=name)
    loop.quit()

def on_err(msg):
    result.update(done=True, err=msg)
    loop.quit()

loop = QEventLoop()
th = m.WhatslinkPreviewThread(MAGNET, use_proxy=False)
th.done_signal.connect(on_done)
th.error_signal.connect(on_err)
th.start()
QTimer.singleShot(90000, loop.quit)  # 兜底超时
loop.exec()
if result["err"]:
    print("❌ whatslink 线程失败：", result["err"]); sys.exit(1)
assert result["imgs"] and len(result["imgs"]) >= 1, "未获取到截图"
print(f"✅ whatslink 线程：{result['name'][:30]} | 截图 {len(result['imgs'])} 张")

# 预览弹窗构造（用真实截图字节）
dlg = m.MagnetPreviewDialog(win, MAGNET, result["name"], result["imgs"], "测试信息 2.43 GB")
assert dlg is not None
print("✅ 预览弹窗构造正常（含真实截图解码）")

# ---------- 5) 点击触发链路 ----------
win._start_magnet_preview(0)
assert win._preview_thread is not None, "点击后应启动预览线程"
print("✅ cellClicked → _start_magnet_preview 触发链路正常")
if win._preview_thread is not None:
    win._preview_thread.stop()
win.close()
print("ALL OK")
sys.exit(0)
