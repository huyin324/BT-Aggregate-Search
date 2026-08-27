# -*- coding: utf-8 -*-
"""无界面验证主窗口与对话框能否正常构建（offscreen 平台）。"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import importlib.util
BASE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location('bt', os.path.join(BASE, 'BT磁力聚合搜索工具.py'))
m = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)

from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
win = m.MainWindow()
win.show()
app.processEvents()
# 打开代理对话框测试
dlg = m.ProxyDialog(win)
dlg.show()
app.processEvents()
print("GUI 构建成功：MainWindow + ProxyDialog 均无异常")
print("站点总数:", len(m.SITE_LIST))
print("解析器种类:", sorted(set(s["parser"] for s in m.SITE_LIST)))
