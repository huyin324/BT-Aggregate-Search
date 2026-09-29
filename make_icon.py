# -*- coding: utf-8 -*-
"""将 磁力.svg 渲染为多尺寸 icon.ico（exe 图标用），并验证 QIcon 可直接加载 SVG。"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QImage, QPainter, QIcon, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

BASE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(BASE, "磁力.svg")
PNG = os.path.join(BASE, "icon_v2.png")
ICO = os.path.join(BASE, "icon.ico")

app = QApplication(sys.argv)

# 1) SVG -> 256px PNG（透明底）
renderer = QSvgRenderer(SVG)
if not renderer.isValid():
    print("FAIL: SVG 无效")
    sys.exit(1)
img = QImage(256, 256, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
renderer.render(p)
p.end()
img.save(PNG)
print("PNG saved:", PNG, os.path.getsize(PNG), "bytes")

# 2) PNG -> 多尺寸 ICO（覆盖原 icon.ico）
from PIL import Image
src = Image.open(PNG)
src.save(ICO, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ICO saved:", ICO, os.path.getsize(ICO), "bytes")

# 3) 验证 QIcon 能直接加载 SVG（窗口图标运行时路径）
icon = QIcon(SVG)
ok = not icon.isNull() and icon.availableSizes()
print("QIcon(svg) valid:", not icon.isNull(), "| sizes:", [s.width() for s in ok][:3])
icon2 = QIcon(ICO)
print("QIcon(ico) valid:", not icon2.isNull(), "| sizes:", [s.width() for s in icon2.availableSizes()][:3])
sys.exit(0 if (not icon.isNull() and os.path.getsize(ICO) > 10000) else 1)
