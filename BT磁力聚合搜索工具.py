# -*- coding: utf-8 -*-
"""
BT磁力聚合搜索工具 - PyQt6版
支持多站点聚合搜索，新增：网络代理智能回退、每页条目数与原站一致、
结果排序（大小/时间/热度）、工具图标、界面/性能/反爬优化。
版本：V1.2（矢量图标 / 代理预检 / whatslink 磁力预览 / 站点精简）
仅用于技术学习，请遵守版权法律法规
"""

import os
import sys
import re
import random
import time
import warnings
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                             QLineEdit, QPushButton, QListWidget, QListWidgetItem,
                             QTableWidget, QTableWidgetItem, QHeaderView, QLabel,
                             QStatusBar, QComboBox, QSpinBox, QMessageBox, QMenuBar,
                             QMenu, QDialog, QCheckBox, QDialogButtonBox, QFormLayout,
                             QPlainTextEdit, QSplitter, QStyledItemDelegate,
                             QStyle, QStyleOptionViewItem, QStyleOptionButton,
                             QScrollArea, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QRect
from PyQt6.QtGui import (QFont, QDesktopServices, QIcon, QAction, QPalette, QColor,
                         QImage, QPainter, QPixmap)
from PyQt6.QtCore import QUrl

# 屏蔽无关警告
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=ResourceWarning)

# 强制Qt使用软件渲染，解决Windows内存崩溃问题
os.environ["QT_OPENGL"] = "software"
os.environ["QT_QPA_PLATFORM"] = "windows"

if getattr(sys, "frozen", False):
    # PyInstaller 单文件打包后，资源位于解压临时目录
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "icon.ico")
SVG_ICON_PATH = os.path.join(BASE_DIR, "磁力.svg")


def load_app_icon():
    """加载应用图标：优先渲染矢量 SVG（磁力.svg）为多尺寸位图，失败回退 icon.ico。"""
    if os.path.exists(SVG_ICON_PATH):
        try:
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(SVG_ICON_PATH)
            if renderer.isValid():
                icon = QIcon()
                for size in (16, 24, 32, 48, 64, 128, 256):
                    img = QImage(size, size, QImage.Format.Format_ARGB32)
                    img.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(img)
                    renderer.render(painter)
                    painter.end()
                    icon.addPixmap(QPixmap.fromImage(img))
                if not icon.isNull():
                    return icon
        except Exception:
            pass
    if os.path.exists(ICON_PATH):
        return QIcon(ICON_PATH)
    return QIcon()

# ===================== 代理配置（全局） =====================
PROXY_CONFIG = {"enabled": False, "protocol": "http", "host": "127.0.0.1", "port": 7897}


def load_proxy_config():
    s = QSettings("BTSearch", "Proxy")
    PROXY_CONFIG["enabled"] = s.value("enabled", False, type=bool)
    PROXY_CONFIG["protocol"] = s.value("protocol", "http", type=str)
    PROXY_CONFIG["host"] = s.value("host", "127.0.0.1", type=str)
    PROXY_CONFIG["port"] = s.value("port", 7897, type=int)


def save_proxy_config():
    s = QSettings("BTSearch", "Proxy")
    s.setValue("enabled", PROXY_CONFIG["enabled"])
    s.setValue("protocol", PROXY_CONFIG["protocol"])
    s.setValue("host", PROXY_CONFIG["host"])
    s.setValue("port", PROXY_CONFIG["port"])


def get_proxies():
    if not PROXY_CONFIG["enabled"]:
        return None
    p = PROXY_CONFIG["protocol"]
    h = PROXY_CONFIG["host"]
    port = PROXY_CONFIG["port"]
    if p in ("http", "https"):
        url = f"{p}://{h}:{port}"
        return {"http": url, "https": url}
    if p == "socks5":
        url = f"socks5://{h}:{port}"
        return {"http": url, "https": url}
    return None


# ===================== 代理预检（启用代理时先验证可用性，结果缓存） =====================
import threading

_PROXY_CHECK = {"ok": None, "msg": "", "ts": 0.0, "lock": threading.Lock()}
_PROXY_CHECK_TTL = 300  # 缓存 5 分钟，避免每个搜索线程都重复探测


def reset_proxy_cache():
    """代理设置变更后清空预检缓存，下次检索时重新探测。"""
    with _PROXY_CHECK["lock"]:
        _PROXY_CHECK["ok"] = None
        _PROXY_CHECK["msg"] = ""
        _PROXY_CHECK["ts"] = 0.0


def check_proxy_available(max_seconds=12):
    """
    代理预检：TCP 连通 + 经代理的外网 HTTP 请求，双重验证。
    返回 (ok, msg)。结果缓存 _PROXY_CHECK_TTL 秒；未启用代理直接返回 (False, "")。
    """
    if not PROXY_CONFIG["enabled"]:
        return False, "代理未启用"
    now = time.time()
    with _PROXY_CHECK["lock"]:
        if _PROXY_CHECK["ok"] is not None and now - _PROXY_CHECK["ts"] < _PROXY_CHECK_TTL:
            return _PROXY_CHECK["ok"], _PROXY_CHECK["msg"]
        proxies = get_proxies()
        ok, msg = _probe_proxy(proxies, max_seconds)
        _PROXY_CHECK["ok"] = ok
        _PROXY_CHECK["msg"] = msg
        _PROXY_CHECK["ts"] = now
        return ok, msg


def _probe_proxy(proxies, max_seconds=12):
    """实际探测逻辑：先 TCP 连通性，再经代理外网请求（204 探测端点最快）。"""
    import socket
    host = PROXY_CONFIG["host"]
    port = int(PROXY_CONFIG["port"])
    # 1) TCP 连通性（socks5/http 通用）
    try:
        with socket.create_connection((host, port), timeout=4):
            pass
    except Exception as e:
        return False, f"TCP 无法连通 {host}:{port}（{type(e).__name__}）"
    # 2) 经代理外网请求：优先 204 探测端点，失败再用普通站点兜底
    test_urls = [
        ("http://www.gstatic.com/generate_204", 204),
        ("https://www.google.com/generate_204", 204),
        ("https://www.baidu.com", 200),
    ]
    last_err = ""
    for url, expect in test_urls:
        try:
            r = requests.get(url, proxies=proxies, timeout=max(4, max_seconds // 2),
                             allow_redirects=True)
            if r.status_code == expect:
                return True, f"外网请求正常（{url} → {r.status_code}）"
            last_err = f"{url} 返回 {r.status_code}"
        except Exception as e:
            last_err = f"{url} {type(e).__name__}: {str(e)[:60]}"
    return False, f"外网请求失败（{last_err}）"


# ===================== 反爬：随机UA与请求头 =====================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


def build_headers(extra=None):
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        # 关键：不要声明 br(brotli)。一旦服务器返回 Content-Encoding: br 而环境未装
        # brotli，requests 无法解码，resp.text 变成二进制乱码，导致解析器一条都抓不到。
        # 只声明 gzip/deflate，requests/urllib3 原生即可解码。
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        headers.update(extra)
    return headers


# ===================== 站点配置 =====================
# parser 字段决定使用哪个解析函数；page_size 为原站单页条目数（仅作展示与一致性参考）
SITE_LIST = [
    # ---------- 原有站点 ----------
    {
        "id": "ciligou", "name": "磁力狗 (ciligou.de)", "base_url": "https://ciligou.de",
        "search_url": "https://cdn.ciligou.in:39520/search?word={kw}&page={page}",
        "detail_url": "https://cdn.ciligou.in:39520/information/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "ciligou", "page_size": 20,
    },
    {
        "id": "911178", "name": "91磁力 (911178.xyz)", "base_url": "https://www.911178.xyz",
        "search_url": "https://www.911178.xyz/search-{kw}-0-0-{page}.html",
        "detail_url": "https://www.911178.xyz/hash/{hash}.html",
        "encoding": "utf-8", "status": "normal", "parser": "old911178", "page_size": 20,
    },
    {
        "id": "starok", "name": "磁力星球 (so1.starok.top)", "base_url": "https://so.starcs.top",
        "search_url": "https://so.starcs.top/search?word={kw}&page={page}&host=so1.starok.top",
        "detail_url": "https://so.starcs.top/detail/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "starok", "page_size": 20,
        "extra_headers": {"Referer": "https://so1.starok.top/"},
    },
    {
        "id": "btsow", "name": "BTSOW (btsow.com)", "base_url": "https://btsow.com",
        "search_url": "https://btsow.com/search/{kw}?page={page}",
        "detail_url": "https://btsow.com/magnet/detail/{hash}",
        "encoding": "utf-8", "status": "spa", "parser": "btsow", "page_size": 20,
    },
    {
        "id": "dobt", "name": "dobt (hn.dobt.cc)", "base_url": "https://hn.dobt.cc",
        "search_url": "https://doc2.htmcdn.com:39988/search?word={kw}&host=hn.dobt.cc&v=1",
        "detail_url": "https://doc2.htmcdn.com:39988/doc/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "dobt", "page_size": 20,
        "extra_headers": {"Referer": "https://hn.dobt.cc/"},
    },
    {
        "id": "sokitty", "name": "sokitty (w1.sokitty.me)", "base_url": "https://w1.sokitty.me",
        "search_url": "https://w1.sokitty.me/search?key={kw}&page={page}",
        "detail_url": "https://w1.sokitty.me/bt/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "sokitty", "page_size": 20,
    },
    {
        "id": "btapp", "name": "磁力片 (t10.btapp.top)", "base_url": "https://t10.btapp.top",
        "search_url": "https://so.btapp.top/so.php?word={kw}&host=t10.btapp.top",
        "detail_url": "https://so.btapp.top/view.php?id={hash}&host=t10.btapp.top",
        "encoding": "utf-8", "status": "normal", "parser": "btapp", "page_size": 50,
        "extra_headers": {"Referer": "https://t10.btapp.top/"},
    },
    {
        "id": "u9a9", "name": "U9A9 (u9a9.com)", "base_url": "https://u9a9.com",
        "search_url": "https://u9a9.com/?type=2&search={kw}&p={page}",
        "detail_url": "https://u9a9.com/view?id={hash}",
        "encoding": "utf-8", "status": "normal", "parser": "u9a9", "page_size": 50,
    },
    # V1.2 实测剔除：u001 为假搜索（任意关键词返回同一固定列表）
    # ---------- 新增站点：Nyaa 模板 ----------
    # V1.2 实测剔除：nyaa_si / nyaa_net 中文关键词（白洁/少妇）无法检索到相关内容
    {
        "id": "sukebei", "name": "Sukebei (sukebei.nyaa.si)", "base_url": "https://sukebei.nyaa.si",
        "search_url": "https://sukebei.nyaa.si/?f=0&c=0_0&q={kw}&p={page}",
        "detail_url": "https://sukebei.nyaa.si/view/{id}",
        "encoding": "utf-8", "status": "normal", "parser": "nyaa", "page_size": 75,
    },

    # ---------- 新增站点：TorrentKitty 模板 ----------
    {
        "id": "torrentkitty", "name": "TorrentKitty (cn)", "base_url": "https://cn.torrentkitty.tv",
        "search_url": "https://cn.torrentkitty.tv/search/{kw}?page={page}",
        "detail_url": "https://cn.torrentkitty.tv/information/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "torrentkitty", "page_size": 20,
    },

    # ---------- 新增站点：ZSky 模板（52BT / 磁力妹妹 / 91BT 新域名） ----------
    {
        "id": "zsky_529076", "name": "52BT (529076)", "base_url": "https://5zxz6n4z.529076.xyz",
        "search_url": "https://5zxz6n4z.529076.xyz/search-{kw}-0-0-{page}.html",
        "detail_url": "https://5zxz6n4z.529076.xyz/hash/{hash}.html",
        "encoding": "utf-8", "status": "normal", "parser": "zsky", "page_size": 20,
    },
    {
        "id": "zsky_529075", "name": "52BT (529075)", "base_url": "https://5lmg4srt.529075.xyz",
        "search_url": "https://5lmg4srt.529075.xyz/search-{kw}-0-0-{page}.html",
        "detail_url": "https://5lmg4srt.529075.xyz/hash/{hash}.html",
        "encoding": "utf-8", "status": "normal", "parser": "zsky", "page_size": 20,
    },
    {
        "id": "zsky_9966109", "name": "磁力妹妹 (9966109)", "base_url": "https://y4pzx2x8.9966109.xyz",
        "search_url": "https://y4pzx2x8.9966109.xyz/search-{kw}-0-0-{page}.html",
        "detail_url": "https://y4pzx2x8.9966109.xyz/hash/{hash}.html",
        "encoding": "utf-8", "status": "normal", "parser": "zsky", "page_size": 20,
    },
    {
        "id": "zsky_9966108", "name": "磁力妹妹 (9966108)", "base_url": "https://p5h6rl26.9966108.xyz",
        "search_url": "https://p5h6rl26.9966108.xyz/search-{kw}-0-0-{page}.html",
        "detail_url": "https://p5h6rl26.9966108.xyz/hash/{hash}.html",
        "encoding": "utf-8", "status": "normal", "parser": "zsky", "page_size": 20,
    },
    {
        "id": "zsky_911178", "name": "91BT (911178新域名)", "base_url": "https://a8knp8rt.911178.xyz",
        "search_url": "https://a8knp8rt.911178.xyz/search-{kw}-0-0-{page}.html",
        "detail_url": "https://a8knp8rt.911178.xyz/hash/{hash}.html",
        "encoding": "utf-8", "status": "normal", "parser": "zsky", "page_size": 20,
    },
    {
        "id": "zsky_911179", "name": "91BT (911179)", "base_url": "https://7wpwge2n.911179.xyz",
        "search_url": "https://7wpwge2n.911179.xyz/search-{kw}-0-0-{page}.html",
        "detail_url": "https://7wpwge2n.911179.xyz/hash/{hash}.html",
        "encoding": "utf-8", "status": "normal", "parser": "zsky", "page_size": 20,
    },

    # ---------- 新增站点：磁力蜘蛛（btmovi，类 sokitty） ----------
    {
        "id": "btmovi", "name": "磁力蜘蛛 (btmovi.cyou)", "base_url": "https://btmovi.cyou",
        "search_url": "https://btmovi.cyou/so/{kw}.html",
        "detail_url": "https://btmovi.cyou/bt/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "btmovi", "page_size": 20,
    },

    # ---------- 新增站点：磁力片新主机 ----------
    {
        "id": "btapp12", "name": "磁力片 (t12.btapp.top)", "base_url": "https://t12.btapp.top",
        "search_url": "https://so.btapp.top/so.php?word={kw}&host=t12.btapp.top",
        "detail_url": "https://so.btapp.top/view.php?id={hash}&host=t12.btapp.top",
        "encoding": "utf-8", "status": "normal", "parser": "btapp", "page_size": 50,
        "extra_headers": {"Referer": "https://t12.btapp.top/"},
    },

    # ---------- 新增站点：磁力星球新主机 ----------
    {
        "id": "starok2", "name": "磁力星球 (so2.starok.top)", "base_url": "https://so.starcs.top",
        "search_url": "https://so.starcs.top/search?word={kw}&page={page}&host=so2.starok.top",
        "detail_url": "https://so.starcs.top/detail/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "starok", "page_size": 20,
        "extra_headers": {"Referer": "https://so2.starok.top/"},
    },

    # ---------- 新增站点：通用/尽力解析（结构未完全确认，运行时若无数据自动跳过） ----------
    {
        "id": "clb04", "name": "磁力宝 (clb04.vip)", "base_url": "https://clb04.vip",
        "search_url": "https://clb04.vip/", "method": "post", "post_data": {"s": "{kw}"},
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "cilisousuo88", "name": "磁力搜索88 (cilisousuo88.shop)", "base_url": "https://www.cilisousuo88.shop",
        "search_url": "https://www.cilisousuo88.shop/?key={kw}&ie=utf-8&sux=",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "ttbt", "name": "TTBT (d2.ttbt.me)", "base_url": "https://d2.ttbt.me",
        "search_url": "https://d2.ttbt.me/search?q={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        # 前端是 iframe 代理壳，真实后端由页面内 atob() 解码得到：cdn.cilimao.fun:39520
        # 该后端与磁力狗同模板，关键词参数必须是 word（用 q 会返回空结果）
        "id": "cilimao", "name": "磁力猫 (cilimao.de)", "base_url": "https://cilimao.de",
        "search_url": "https://cdn.cilimao.fun:39520/search?word={kw}&host=cilimao.de",
        "detail_url": "https://cdn.cilimao.fun:39520/information/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "ciligou", "page_size": 15,
        "extra_headers": {"Referer": "https://cilimao.de/"},
    },
    # V1.2 实测剔除：btlms 对任意关键词仅返回默认热榜（如"元气早餐"），无搜索能力
    {
        "id": "zzb10", "name": "种子吧 (zzb10.vip)", "base_url": "https://zzb10.vip",
        "search_url": "https://zzb10.vip/", "method": "post", "post_data": {"wd": "{kw}"},
        "detail_url": "https://zzb10.vip/seed/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "zzb10", "page_size": 15,
        "resolve_detail": True, "detail_limit": 15,
    },
    {
        # iframe 代理后端 doc2.htmcdn.com:39988，与 dobt 同后端同模板
        "id": "cilido", "name": "磁力多 (zh.cilido.top)", "base_url": "https://zh.cilido.top",
        "search_url": "https://doc2.htmcdn.com:39988/search?word={kw}&host=zh.cilido.top&v=1",
        "detail_url": "https://doc2.htmcdn.com:39988/doc/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "dobt", "page_size": 15,
        "extra_headers": {"Referer": "https://zh.cilido.top/"},
    },
    {
        # iframe 代理后端 tt.ttso.top
        "id": "ttcl", "name": "天堂磁力 (tt6.ttcl.cc)", "base_url": "https://tt.ttso.top",
        "search_url": "https://tt.ttso.top/search?word={kw}",
        "detail_url": "https://tt.ttso.top/bt/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "ttcl", "page_size": 20,
        "extra_headers": {"Referer": "https://tt6.ttcl.cc/"},
    },
    {
        # iframe 代理后端 cdn.sofan.one:65533
        "id": "sofan1", "name": "搜番 (ma.sofan1.cc)", "base_url": "https://ma.sofan1.cc",
        "search_url": "https://cdn.sofan.one:65533/search?word={kw}&host=ma.sofan1.cc",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 15,
        "extra_headers": {"Referer": "https://ma.sofan1.cc/"},
    },
    {
        # iframe 代理后端 cache.foxn.top（磁力藏在 /doc/{hash} 中）
        "id": "foxr", "name": "磁力狐 (t8.foxr.top)", "base_url": "https://cache.foxn.top",
        "search_url": "https://cache.foxn.top/search?word={kw}",
        "detail_url": "https://cache.foxn.top/doc/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "foxr", "page_size": 15,
        "extra_headers": {"Referer": "https://t8.foxr.top/"},
    },
    {
        "id": "taocili", "name": "淘磁力 (taocili.com)", "base_url": "https://taocili.com",
        "search_url": "https://taocili.com/search?q={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "yhg_1111076", "name": "移花宫 (1111076)", "base_url": "https://vqlf4iof.1111076.xyz",
        "search_url": "https://vqlf4iof.1111076.xyz/search?q={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "yhg_1111075", "name": "移花宫 (1111075)", "base_url": "https://yu88psf7.1111075.xyz",
        "search_url": "https://yu88psf7.1111075.xyz/search?q={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "yhg_1111077", "name": "移花宫 (1111077)", "base_url": "https://w9cts3gs.1111077.xyz",
        "search_url": "https://w9cts3gs.1111077.xyz/search?q={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "cilisousuo_cc", "name": "磁力搜索CC (cilisousuo.cc)", "base_url": "https://cilisousuo.cc",
        "search_url": "https://cilisousuo.cc/search?q={kw}",
        "detail_url": "https://cilisousuo.cc/magnet/{hash}",
        "encoding": "utf-8", "status": "normal", "parser": "cilisousuo_cc", "page_size": 100,
        "resolve_detail": True, "detail_limit": 24,
    },
    {
        "id": "btsearch_love", "name": "BTSearch (btsearch.love)", "base_url": "https://www.btsearch.love",
        "search_url": "https://www.btsearch.love/search?q={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "3d48", "name": "3D48 (3d48.com)", "base_url": "https://www.3d48.com",
        "search_url": "https://www.3d48.com/search?name={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
    {
        "id": "lingfengyun", "name": "凌风云 (lingfengyun.com)", "base_url": "https://www.lingfengyun.com",
        "search_url": "https://www.lingfengyun.com/Search/Search?wd={kw}",
        "detail_url": "", "encoding": "utf-8", "status": "normal", "parser": "generic", "page_size": 0,
    },
]


# ===================== 已失效站点登记表 =====================
# BT 站多为一次性轮换域名，实测（2026-09）以下站点已确认不可用：
# 域名过期返回 410、站点维护返回 503、或搜索接口直接 404。
# 这里集中登记并默认停用，避免每次搜索都空跑十几个必然失败的请求。
# 若日后域名恢复可用，把对应条目从本表删除即可自动重新启用。
DEAD_SITES = {
    "911178": "域名已失效(410)",
    "zsky_529076": "域名已失效(410)",
    "zsky_529075": "域名已失效(410)",
    "zsky_9966109": "站点维护中(503)",
    "zsky_9966108": "域名已失效(410)",
    "zsky_911178": "域名已失效(410)",
    "zsky_911179": "域名已失效(410)",
    "yhg_1111076": "域名已失效(410)",
    "yhg_1111075": "域名已失效(410)",
    "yhg_1111077": "域名已失效(410)",
    "lingfengyun": "网站已关闭",
    "3d48": "搜索接口失效(404)",
    "ttbt": "后端仅提供首页,搜索路径404",
    # 以下为纯前端渲染或需签名，requests 无法取到数据
    "btsow": "结果由JS渲染,HTML无数据",
    "cilisousuo88": "已变为域名停放页,搜索无响应",
    "taocili": "SSR返回空结果,真实结果由JS拉取",
    "btsearch_love": "API需客户端签名(Missing sign)",
}

# 实际参与检索的站点（已剔除失效站点）
ACTIVE_SITES = [s for s in SITE_LIST if s["id"] not in DEAD_SITES]


# ===================== 搜索结果数据类 =====================
class TorrentItem:
    def __init__(self):
        self.name = ""
        self.magnet = ""
        self.size = "未知"
        self.date = "未知"
        self.hot = "0"
        self.files = "未知"
        self.hash = ""
        self.site = ""
        self.site_id = ""
        # 部分站点列表页只给短码（如 /magnet/xxx、/seed/xxx），需再抓详情页换真实磁力
        self.detail_url = ""


# ===================== 解析辅助 =====================
def _hash_from_magnet(magnet):
    m = re.search(r"btih:([a-fA-F0-9]+)", magnet)
    return m.group(1) if m else ""


def _clean_magnet(magnet):
    return magnet.replace("&amp;", "&")


# ===================== 各站点解析函数（模块级，便于测试） =====================
def parse_ciligou(soup, site):
    out = []
    title_items = soup.select(".SearchListTitle_list_title")
    info_items = soup.select(".Search_list_info")
    for i, title_div in enumerate(title_items):
        t = TorrentItem(); t.site = site["name"]
        title_tag = title_div.select_one("a[href*='information']")
        if title_tag:
            t.name = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            hm = re.search(r"/information/([a-fA-F0-9]+)", href)
            if hm:
                t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        if i < len(info_items):
            info_div = info_items[i]
            info_text = info_div.get_text(" ", strip=True)
            hot_tag = info_div.select_one(".Search_result_type")
            if hot_tag:
                t.hot = hot_tag.get_text(strip=True)
            sm = re.search(r"文件大小[:：]\s*(\d+\.?\d*\s*[GMK]B)", info_text, re.I)
            if sm: t.size = sm.group(1).strip()
            dm = re.search(r"创建时间[:：]\s*(\d{4}-\d{1,2}-\d{1,2})", info_text)
            if dm: t.date = dm.group(1)
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_old911178(soup, site):
    out = []
    items = soup.select(".ssbox")
    for item in items:
        t = TorrentItem(); t.site = site["name"]
        title_tag = item.select_one(".title h3 a")
        if title_tag:
            t.name = title_tag.get_text(strip=True)
            hm = re.search(r"/hash/([a-fA-F0-9]+)", title_tag.get("href", ""))
            if hm: t.hash = hm.group(1)
        sbar = item.select_one(".sbar")
        if sbar:
            magnet_tag = sbar.select_one("a[href*='magnet:']")
            if magnet_tag:
                t.magnet = magnet_tag.get("href", "")
                if not t.hash:
                    hm = re.search(r"btih:([a-fA-F0-9]+)", t.magnet)
                    if hm: t.hash = hm.group(1)
            sbar_text = sbar.get_text(" ", strip=True)
            dm = re.search(r"添加时间[:：]\s*(\d{4}-\d{1,2}-\d{1,2})", sbar_text)
            if dm: t.date = dm.group(1)
            sm = re.search(r"大小[:：]\s*(\d+\.?\d*\s*[GMK]B)", sbar_text, re.I)
            if sm: t.size = sm.group(1).strip()
            hm = re.search(r"热度[:：]\s*(\d+)", sbar_text)
            if hm: t.hot = hm.group(1)
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_starok(soup, site):
    out = []
    items = soup.select(".list-group-item")
    for item in items:
        t = TorrentItem(); t.site = site["name"]
        title_tag = item.select_one(".title a")
        if title_tag:
            t.name = title_tag.get_text(strip=True)
            hm = re.search(r"/detail/([a-fA-F0-9]+)", title_tag.get("href", ""))
            if hm:
                t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        foo = item.select_one(".foo")
        if foo:
            foo_text = foo.get_text(" ", strip=True)
            sm = re.search(r"文件大小[:：]\s*(\d+\.?\d*\s*[GMK]B)", foo_text, re.I)
            if sm: t.size = sm.group(1).strip()
            dm = re.search(r"创建时间[:：]\s*(\d{4}-\d{1,2}-\d{1,2})", foo_text)
            if dm: t.date = dm.group(1)
        sub_titles = item.select(".sub-title")
        if sub_titles:
            t.files = str(len(sub_titles))
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_btsow(soup, site):
    out = []
    rows = soup.select("table tr, .data-list tr")
    if not rows:
        rows = soup.select("div[class*='row'], div[class*='item']")
    for row in rows:
        t = TorrentItem(); t.site = site["name"]
        title_tag = row.select_one("td a, a[href*='magnet/detail']")
        if title_tag:
            t.name = title_tag.get_text(strip=True)
            hm = re.search(r"/detail/([a-fA-F0-9]+)", title_tag.get("href", ""))
            if hm:
                t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        if not t.magnet:
            magnet_text = row.select_one("textarea, input[value*='magnet']")
            if magnet_text:
                text = magnet_text.get_text() if hasattr(magnet_text, 'get_text') else magnet_text.get("value", "")
                mm = re.search(r"(magnet:\?xt=urn:btih:[a-fA-F0-9]+)", text)
                if mm: t.magnet = _clean_magnet(mm.group(1))
        size_tag = row.select_one(".size, td:nth-child(2)")
        if size_tag:
            size_text = size_tag.get_text(strip=True)
            if re.match(r"\d+\.?\d*\s*[GMK]B", size_text, re.I): t.size = size_text
        date_tag = row.select_one(".date, .convert-date, td:nth-child(3)")
        if date_tag:
            date_text = date_tag.get_text(strip=True)
            if re.match(r"\d{4}-\d{1,2}-\d{1,2}", date_text): t.date = date_text
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_dobt(soup, site):
    out = []
    items = soup.select(".ssbox")
    for item in items:
        t = TorrentItem(); t.site = site["name"]
        title_tag = item.select_one(".title h3 a")
        if title_tag:
            t.name = title_tag.get_text(strip=True)
            hm = re.search(r"/doc/([a-fA-F0-9]+)", title_tag.get("href", ""))
            if hm: t.hash = hm.group(1)
        sbar = item.select_one(".sbar")
        if sbar:
            magnet_tag = sbar.select_one("a[href*='magnet:']")
            if magnet_tag:
                t.magnet = magnet_tag.get("href", "")
                if not t.hash:
                    hm = re.search(r"btih:([a-fA-F0-9]+)", t.magnet)
                    if hm: t.hash = hm.group(1)
            sbar_text = sbar.get_text(" ", strip=True)
            dm = re.search(r"添加时间[:：]\s*(\d{4}-\d{1,2}-\d{1,2})", sbar_text)
            if dm: t.date = dm.group(1)
            sm = re.search(r"大小[:：]\s*(\d+\.?\d*\s*[GMK]B)", sbar_text, re.I)
            if sm: t.size = sm.group(1).strip()
            hm = re.search(r"热度[:：]\s*(\d+)", sbar_text)
            if hm: t.hot = hm.group(1)
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_sokitty(soup, site):
    out = []
    list_titles = soup.select(".list-title")
    for lt in list_titles:
        href = lt.get("href", "")
        if not href.startswith("/bt/"):
            continue
        t = TorrentItem(); t.site = site["name"]
        t.name = lt.get_text(strip=True)
        hm = re.search(r"/bt/([a-fA-F0-9]+)", href)
        if hm:
            t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        parent = lt.parent
        if parent:
            parent_text = parent.get_text(" ", strip=True)
            sm = re.search(r"(\d+\.?\d*\s*[GMK]B)", parent_text, re.I)
            if sm: t.size = sm.group(1).strip()
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_btapp(soup, site):
    out = []
    rows = soup.select("tbody tr")
    for row in rows:
        t = TorrentItem(); t.site = site["name"]
        tds = row.find_all("td")
        if len(tds) < 5:
            continue
        name_td = tds[1]
        title_tag = name_td.select_one("a")
        if title_tag:
            t.name = title_tag.get_text(strip=True)
            hm = re.search(r"id=([a-fA-F0-9]+)", title_tag.get("href", ""))
            if hm:
                t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        if len(tds) > 3:
            size_text = tds[3].get_text(strip=True)
            if re.match(r"\d+\.?\d*\s*[GMK]B", size_text, re.I): t.size = size_text
        if len(tds) > 4:
            date_text = tds[4].get_text(strip=True)
            if re.match(r"\d{4}-\d{1,2}-\d{1,2}", date_text): t.date = date_text
        if len(tds) > 7:
            hot_text = tds[7].get_text(strip=True)
            if hot_text.isdigit(): t.hot = hot_text
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_u9a9(soup, site):
    out = []
    rows = soup.select("table tr")
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 5:
            continue
        t = TorrentItem(); t.site = site["name"]
        name_td = tds[1]
        title_tag = name_td.select_one("a")
        if title_tag:
            t.name = title_tag.get_text(strip=True)
        link_td = tds[2]
        magnet_tag = link_td.select_one("a[href*='magnet:']")
        if magnet_tag:
            t.magnet = magnet_tag.get("href", "")
            hm = re.search(r"btih:([a-fA-F0-9]+)", t.magnet)
            if hm: t.hash = hm.group(1)
        if len(tds) > 3:
            size_text = tds[3].get_text(strip=True)
            if re.match(r"\d+\.?\d*\s*[GMK]B", size_text, re.I): t.size = size_text
        if len(tds) > 4:
            date_text = tds[4].get_text(strip=True)
            if re.match(r"\d{4}-\d{1,2}-\d{1,2}", date_text): t.date = date_text.split(" ")[0]
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_nyaa(soup, site):
    out = []
    # 兩種結構：
    #  nyaa.si / sukebei: tr.default / tr.success，名称在 td[colspan="2"] a
    #  nyaa.net: 无 class 的 tr，名称在 td.col-name > a.t-name，size/date/做种
    #            分别在 td.col-size / td.col-date / td.num-s
    rows = soup.select("tr.default, tr.success")
    if not rows:
        rows = [r for r in soup.select("tr")
                if r.select_one("td.col-name a.t-name")]
    for row in rows:
        magnet_a = row.select_one('a[href^="magnet:"]')
        if not magnet_a:
            continue
        t = TorrentItem(); t.site = site["name"]
        t.magnet = _clean_magnet(magnet_a.get("href", ""))
        hm = _hash_from_magnet(t.magnet)
        if hm: t.hash = hm
        # 名称在 colspan="2" 的单元格中；首个 td 是分类图标（无文字），需避开
        name_a = (row.select_one('td[colspan="2"] a')
                  or row.select_one("td.col-name a.t-name")
                  or row.select_one('a[title]'))
        if name_a and name_a.get_text(strip=True):
            t.name = name_a.get_text(strip=True)
        size_td = row.select_one("td.col-size")
        date_td = row.select_one("td.col-date")
        seed_td = row.select_one("td.num-s")
        if size_td is None or date_td is None:
            # nyaa.si 结构按列序解析: 分类, 名称(含colspan), 链接, 大小, 日期, 做种, 下载, 完成
            tds = row.find_all("td")
            if len(tds) > 4:
                size_text = tds[3].get_text(strip=True)
                if re.match(r"^[\d.]+ ?(GiB|MiB|KiB|GB|MB|KB|B)$", size_text, re.I): t.size = size_text
                date_text = tds[4].get_text(strip=True)
                if re.match(r"^\d{4}-\d{2}-\d{2}", date_text): t.date = date_text
            if len(tds) > 5:
                seed_text = tds[5].get_text(strip=True)
                if seed_text.isdigit(): t.hot = seed_text
        else:
            # nyaa.net 结构按语义 class 解析
            size_text = size_td.get_text(strip=True)
            if re.match(r"^[\d.]+ ?(GiB|MiB|KiB|GB|MB|KB|B)$", size_text, re.I): t.size = size_text
            date_text = date_td.get_text(strip=True)
            if re.match(r"^\d{4}-\d{2}-\d{2}", date_text): t.date = date_text
            seed_text = seed_td.get_text(strip=True) if seed_td else ""
            seed_text = seed_text.replace(" ", "")
            if seed_text.isdigit(): t.hot = seed_text
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_torrentkitty(soup, site):
    out = []
    rows = soup.select("tr")
    for row in rows:
        magnet_a = row.select_one('a[href^="magnet:"]')
        if not magnet_a:
            continue
        t = TorrentItem(); t.site = site["name"]
        t.magnet = _clean_magnet(magnet_a.get("href", ""))
        hm = _hash_from_magnet(t.magnet)
        if hm: t.hash = hm
        name_td = row.select_one("td.name")
        if name_td:
            t.name = name_td.get_text(strip=True)
        size_td = row.select_one("td.size")
        if size_td: t.size = size_td.get_text(strip=True)
        date_td = row.select_one("td.date")
        if date_td: t.date = date_td.get_text(strip=True)
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_zsky(soup, site):
    out = []
    items = soup.select("article.resource-card")
    for it in items:
        a = it.select_one("h2 a") or it.select_one("a[href*='/hash/']")
        if not a:
            continue
        t = TorrentItem(); t.site = site["name"]
        href = a.get("href", "")
        hm = re.search(r"/hash/([a-fA-F0-9]+)", href)
        if hm:
            t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        t.name = a.get_text(strip=True)
        for span in it.select(".meta span"):
            txt = span.get_text(strip=True)
            if "大小" in txt: t.size = re.sub(r"大小[:：]\s*", "", txt)
            elif "添加时间" in txt: t.date = re.sub(r"添加时间[:：]\s*", "", txt)
            elif "最近下载" in txt: t.hot = re.sub(r"最近下载[:：]\s*", "", txt)
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_btmovi(soup, site):
    out = []
    items = soup.select("div.search-item")
    for it in items:
        a = it.select_one("div.item-title h3 a") or it.select_one("a[href*='/bt/']")
        if not a:
            continue
        t = TorrentItem(); t.site = site["name"]
        href = a.get("href", "")
        hm = re.search(r"/bt/([a-fA-F0-9]+)", href)
        if hm:
            t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        t.name = a.get_text(strip=True)
        bar = it.select_one("div.item-bar")
        if bar:
            for span in bar.select("span"):
                txt = span.get_text(strip=True)
                if "创建时间" in txt: t.date = re.sub(r"创建时间[:：]\s*", "", txt)
                elif "文件大小" in txt: t.size = re.sub(r"文件大小[:：]\s*", "", txt)
                elif "下载热度" in txt: t.hot = re.sub(r"下载热度[:：]\s*", "", txt)
        if t.name and t.magnet:
            out.append(t)
    return out


def parse_cilisousuo_cc(soup, site):
    """磁力搜索CC：li.item 列表，磁力藏在 /magnet/{短码} 详情页。"""
    out = []
    for li in soup.find_all(class_="item"):
        info = li.find(class_="info")
        if not info:
            continue
        title_el = info.find(class_="result-title")
        link = li.select_one("a.link[href]")
        if not title_el or not link:
            continue
        t = TorrentItem(); t.site = site["name"]
        t.name = title_el.get_text(strip=True)
        size_el = li.find(class_="size")
        if size_el:
            t.size = size_el.get_text(strip=True)
        fn_el = info.find(class_="filename")
        if fn_el:
            t.files = fn_el.get_text(strip=True)
        t.detail_url = link.get("href", "")
        out.append(t)
    return out


def parse_zzb10(soup, site):
    """种子吧：li.media 列表，磁力藏在 /seed/{短码} 详情页。"""
    out = []
    for li in soup.find_all(class_="media"):
        a = li.select_one("h4.media-heading a[href]") or li.select_one("a[href*='/seed/']")
        if not a:
            continue
        t = TorrentItem(); t.site = site["name"]
        t.name = a.get("title", "").strip() or a.get_text(strip=True)
        t.detail_url = a.get("href", "")
        fn_el = li.find(class_="search-file")
        if fn_el:
            t.files = fn_el.get_text(strip=True)
        info_el = li.find(class_="search-info")
        if info_el:
            spans = info_el.select("span.s_b")
            if len(spans) >= 1:
                t.date = spans[0].get_text(strip=True)
            if len(spans) >= 2:
                t.size = spans[1].get_text(strip=True)
            if len(spans) >= 3:
                t.hot = spans[2].get_text(strip=True)
        out.append(t)
    return out


def parse_ttcl(soup, site):
    """天堂磁力（后端 tt.ttso.top）：div.search-panel 为一条结果。
    注意要跳过 href 指向 click2 广告域的条目，只取 /bt/{hash} 的真实结果。"""
    out = []
    for panel in soup.select("div.search-panel"):
        a = panel.select_one("a.list-title[href^='/bt/']")
        if not a:
            continue
        hm = re.search(r"/bt/([a-fA-F0-9]{32,40})", a.get("href", ""))
        if not hm:
            continue
        t = TorrentItem(); t.site = site["name"]
        t.name = a.get_text(strip=True)
        t.hash = hm.group(1)
        t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        footer = panel.select_one("div.panel-footer")
        if footer:
            spans = footer.select("span.info-item")
            if len(spans) >= 1:
                t.size = spans[0].get_text(strip=True)
            if len(spans) >= 2:
                t.files = spans[1].get_text(strip=True)
            if len(spans) >= 3:
                t.date = spans[2].get_text(strip=True)
        out.append(t)
    return out


def parse_foxr(soup, site):
    """磁力狐（后端 cache.foxn.top，layui 模板）：div.search-box 为一条结果，
    磁力藏在 /doc/{hash} 链接中。"""
    out = []
    for box in soup.select("div.search-box"):
        a = box.select_one("a[href^='/doc/']")
        if not a:
            continue
        hm = re.search(r"/doc/([a-fA-F0-9]{32,40})", a.get("href", ""))
        if not hm:
            continue
        t = TorrentItem(); t.site = site["name"]
        t.name = (a.get("title", "") or "").strip() or a.get_text(strip=True)
        t.hash = hm.group(1)
        t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        for p in box.select("div.layui-colla-content p"):
            txt = p.get_text(" ", strip=True)
            sp = p.select_one("span")
            val = sp.get_text(strip=True) if sp else ""
            if "创建时间" in txt:
                t.date = val
            elif "文件大小" in txt:
                t.size = val
            elif "文件数量" in txt:
                t.files = val
        out.append(t)
    return out


def parse_generic(soup, site):
    """通用尽力解析：优先直接磁力链接，否则尝试从 /hash/ /bt/ /information/ /view.php /detail/ 等链接构造。"""
    out = []
    seen = set()
    # 1) 直接磁力链接
    for a in soup.select("a[href^='magnet:']"):
        href = _clean_magnet(a.get("href", ""))
        hm = _hash_from_magnet(href)
        if not hm:
            continue
        t = TorrentItem(); t.site = site["name"]
        t.magnet = href; t.hash = hm
        t.name = a.get_text(strip=True) or (a.parent.get_text(strip=True)[:80] if a.parent else "")
        if t.hash not in seen:
            seen.add(t.hash); out.append(t)
    # 2) 间接链接构造磁力（过滤明显的导航链接，避免误抓取）
    patterns = [r"/hash/([a-fA-F0-9]+)", r"/bt/([a-fA-F0-9]+)",
                r"/information/([a-fA-F0-9]+)", r"id=([a-fA-F0-9]+)",
                r"/detail/([a-fA-F0-9]+)", r"/view/([a-fA-F0-9]+)"]
    nav_words = ("首页", "主页", "分类", "导航", "关于", "联系", "登录", "注册", "最新", "热门", "排行")
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        for pat in patterns:
            hm = re.search(pat, href)
            if hm and hm.group(1) not in seen:
                name = a.get_text(strip=True) or (a.parent.get_text(strip=True)[:80] if a.parent else "")
                # 名称过短或为常见导航词时跳过，降低误报
                if len(name) < 4 or name in nav_words:
                    break
                t = TorrentItem(); t.site = site["name"]
                t.hash = hm.group(1); t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
                t.name = name
                seen.add(t.hash); out.append(t)
                break
    return out


PARSERS = {
    "ciligou": parse_ciligou,
    "old911178": parse_old911178,
    "starok": parse_starok,
    "btsow": parse_btsow,
    "dobt": parse_dobt,
    "sokitty": parse_sokitty,
    "btapp": parse_btapp,
    "u9a9": parse_u9a9,
    "nyaa": parse_nyaa,
    "torrentkitty": parse_torrentkitty,
    "zsky": parse_zsky,
    "btmovi": parse_btmovi,
    "cilisousuo_cc": parse_cilisousuo_cc,
    "zzb10": parse_zzb10,
    "ttcl": parse_ttcl,
    "foxr": parse_foxr,
    "generic": parse_generic,
}


def build_search_url(site, kw, page):
    """构造搜索 URL。关键词按 RFC3986 做百分号编码，空格转 + 更贴近表单语义，
    中文/空格/特殊字符不会破坏 URL，也不会被二次编码。"""
    enc_kw = quote(kw, safe="")
    url = site["search_url"].replace("{kw}", enc_kw).replace("{page}", str(page))
    return url


def _make_session():
    """创建带重试策略的 Session。对连接类异常也重试，降低偶发网络抖动导致的整站失败。"""
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.4,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST", "HEAD"],
                  raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _resolve_encoding(resp, site):
    """智能判定响应编码，避免强制 UTF-8 把 GBK 页面解码成乱码。
    优先级：HTTP Content-Type charset -> HTML meta charset -> 站点配置 -> utf-8"""
    enc = (resp.encoding or "").strip()
    # requests 在响应头未声明 charset 时，对 text/* 会回退 ISO-8859-1，需要纠正
    if not enc or enc.lower() in ("iso-8859-1", "latin-1", "latin1"):
        head = resp.content[:4096]
        m = re.search(rb'charset=["\']?\s*([\w-]+)', head, re.I)
        if m:
            try:
                enc = m.group(1).decode("ascii", "ignore").strip().lower()
            except Exception:
                enc = ""
    # 常见 GBK 系列别名统一到 gb18030（超集，兼容性更好）
    if enc.lower() in ("gbk", "gb2312", "gb-2312", "gb_2312", "x-gbk", "gb18030"):
        enc = "gb18030"
    if not enc:
        enc = site.get("encoding", "utf-8")
    return enc


def _fetch(session, url, headers, proxies, method="get", data=None, timeout=20):
    if method == "post":
        return session.post(url, headers=headers, proxies=proxies, data=data,
                            timeout=timeout, allow_redirects=True)
    return session.get(url, headers=headers, proxies=proxies,
                       timeout=timeout, allow_redirects=True)


def _abs_url(base, href):
    """把详情页的相对路径补全为绝对 URL。"""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    base = (base or "").rstrip("/")
    if href.startswith("/"):
        return base + href
    return base + "/" + href


def _resolve_details(items, site, headers, proxies, limit=24, workers=6):
    """并发抓取详情页，把短码兑换成真实磁力链接。

    有些站点（磁力搜索CC、种子吧等）列表页只给出 /magnet/xxx、/seed/xxx 短码，
    真正的磁力在详情页里。这里用小型线程池限量并发换取，避免几十上百次串行请求被封。
    """
    pending = [t for t in items if not t.magnet and getattr(t, "detail_url", "")]
    if not pending:
        return [t for t in items if t.magnet]
    pending = pending[:limit]
    base = site.get("base_url", "")

    def work(t):
        url = _abs_url(base, t.detail_url)
        if not url:
            return
        try:
            sess = _make_session()
            r = _fetch(sess, url, headers, proxies, timeout=12)
            if r.status_code != 200:
                return
            r.encoding = _resolve_encoding(r, site)
            m = re.search(r"magnet:\?xt=urn:btih:([A-Za-z0-9]{32,40})", r.text, re.I)
            if m:
                t.hash = m.group(1)
                t.magnet = f"magnet:?xt=urn:btih:{t.hash}"
        except Exception:
            pass

    try:
        with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as ex:
            list(ex.map(work, pending))
    except Exception:
        pass
    return [t for t in items if t.magnet]


def search_site(site, kw, page, proxies=None):
    """抓取并解析单个站点一页，返回 TorrentItem 列表。请求失败或状态码非200时抛出异常。"""
    kw = kw.strip()
    method = site.get("method", "get")
    data = None
    if method == "post" and "post_data" in site:
        data = {k: v.replace("{kw}", kw) for k, v in site["post_data"].items()}
    url = build_search_url(site, kw, page)

    headers = build_headers(site.get("extra_headers"))
    # 未显式指定 Referer 时，补上站点首页，降低被判为盗链/爬虫的概率
    if "Referer" not in headers:
        headers["Referer"] = site.get("base_url", url)

    session = _make_session()
    try:
        # 反爬：随机微小延迟
        time.sleep(random.uniform(0.15, 0.6))
        # 部分站点需要先访问首页拿到 Cookie（如含 CSRF/会话校验的站点）
        if site.get("need_home_cookie"):
            try:
                session.get(site.get("base_url", url), headers=headers,
                            proxies=proxies, timeout=10, allow_redirects=True)
            except Exception:
                pass
        resp = _fetch(session, url, headers, proxies, method=method, data=data, timeout=20)
    finally:
        pass

    if resp.status_code != 200:
        raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
    resp.encoding = _resolve_encoding(resp, site)
    soup = BeautifulSoup(resp.text, "lxml")
    parser = PARSERS.get(site.get("parser", site["id"]), parse_generic)
    items = parser(soup, site)
    # 列表页只给短码的站点，再并发抓详情页换真实磁力
    if site.get("resolve_detail"):
        items = _resolve_details(items, site, headers, proxies,
                                 limit=site.get("detail_limit", 24))
    return items


# ===================== 后台搜索线程（支持代理智能回退） =====================
class SearchThread(QThread):
    result_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)
    finish_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)

    def __init__(self, site_info, keyword, page=1):
        super().__init__()
        self.site = site_info
        self.kw = keyword
        self.page = page
        self.result_count = 0

    def _attempt(self, proxies, via_proxy):
        try:
            results = search_site(self.site, self.kw, self.page, proxies)
            return results
        except Exception as e:
            detail = str(e)
            msg = f"{'代理' if via_proxy else '直连'}请求失败：{type(e).__name__}"
            if detail and detail != type(e).__name__:
                msg += f"（{detail}）"
            raise RuntimeError(msg) from e

    def run(self):
        try:
            # 代理预检：启用代理时先探测可用性（结果缓存 5 分钟），不可用则本次仅直连
            use_proxy = PROXY_CONFIG["enabled"]
            if use_proxy:
                proxy_ok, proxy_msg = check_proxy_available()
                if not proxy_ok:
                    self.log_signal.emit(f"⚠ 代理预检未通过，本次检索仅直连（{proxy_msg}）")
                    use_proxy = False
            results = []
            self.log_signal.emit(f"▶ 开始检索「{self.site['name']}」（直连）")
            try:
                results = self._attempt(None, False)
            except Exception:
                if use_proxy:
                    self.log_signal.emit(f"⚠ 「{self.site['name']}」直连失败，改用代理重试...")
                    self.status_signal.emit(f"{self.site['name']} 直连失败，改用代理重试...")
                    try:
                        results = self._attempt(get_proxies(), True)
                    except Exception as e2:
                        self.error_signal.emit(f"{self.site['name']} 失败：{str(e2)}（已尝试直连与代理）")
                        self.log_signal.emit(f"✗ 「{self.site['name']}」直连与代理均失败，跳过")
                        self.finish_signal.emit(0)
                        return
                else:
                    self.error_signal.emit(f"{self.site['name']} 失败：无法获取数据")
                    self.log_signal.emit(f"✗ 「{self.site['name']}」直连失败，跳过")
                    self.finish_signal.emit(0)
                    return

            # 直连成功但无数据，且启用代理 -> 再用代理试一次
            if not results and use_proxy:
                self.log_signal.emit(f"⚠ 「{self.site['name']}」直连无数据，改用代理重试...")
                self.status_signal.emit(f"{self.site['name']} 直连无数据，改用代理重试...")
                try:
                    results = self._attempt(get_proxies(), True)
                except Exception:
                    pass

            self.result_count = len(results)
            for t in results:
                self.result_signal.emit(t)
            self.log_signal.emit(f"✔ 「{self.site['name']}」检索完成，共 {len(results)} 条")
            self.finish_signal.emit(self.result_count)
        except Exception as e:
            self.error_signal.emit(f"{self.site['name']} 异常：{str(e)}")
            self.log_signal.emit(f"✗ 「{self.site['name']}」异常：{str(e)}")
            self.finish_signal.emit(0)


# ===================== 排序辅助 =====================
SIZE_MULT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4,
             "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}


def size_to_bytes(s):
    m = re.match(r"([\d.]+)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)", s, re.I)
    if not m:
        return 0
    return int(float(m.group(1)) * SIZE_MULT.get(m.group(2).upper(), 1))


def date_to_int(s):
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if not m:
        return 0
    return int(f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}")


def hot_to_int(s):
    digits = re.sub(r"\D", "", str(s))
    return int(digits) if digits else 0


# ===================== 站点列表“条数”绿色高亮代理 =====================
class CountHighlightDelegate(QStyledItemDelegate):
    """仅把站点列表中“（N条）”的条数数字渲染为绿色，站点名保持默认颜色。"""

    def paint(self, painter, option, index):
        painter.save()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        # 只绘制背景与选中态，不绘制文字；文字由下方手动绘制，
        # 避免 drawControl 已画过一次文字又叠加绘制导致“重影”。
        opt.text = ""
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDisplay
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        m = re.search(r"(.*?)(\s*\(\d+条\))\s*$", text)
        name = text
        count = ""
        if m:
            name = m.group(1)
            count = m.group(2)

        rect = opt.rect
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, opt, widget) + 4
        y = rect.top()
        h = rect.height()
        fm = painter.fontMetrics()

        if opt.state & QStyle.StateFlag.State_Selected:
            painter.setPen(opt.palette.color(QPalette.ColorRole.HighlightedText))
        else:
            painter.setPen(opt.palette.color(QPalette.ColorRole.Text))
        avail = rect.width() - margin
        drawn_name = fm.elidedText(name, Qt.TextElideMode.ElideRight, avail)
        painter.drawText(QRect(rect.left() + margin, y, avail, h),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         drawn_name)

        if count:
            # 基于实际绘制出的文本宽度定位条数，避免名称被省略时错位
            name_w = fm.horizontalAdvance(drawn_name)
            painter.setPen(QColor("#27ae60"))
            painter.drawText(QRect(rect.left() + margin + name_w, y, max(0, avail - name_w), h),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             count)
        painter.restore()


# ===================== 主窗口 =====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowIcon(load_app_icon())
        self.setWindowTitle("BT磁力聚合搜索工具 V1.2")
        self.resize(1500, 880)
        self.search_thread = None
        self.current_site = ACTIVE_SITES[0]
        self.current_page = 1
        self.clipboard = QApplication.clipboard()

        self.all_results = {}
        self.site_result_counts = {}
        self.current_view = "all"

        # 排序设置
        self.sort_field = "default"   # default / size / date / hot
        self.sort_order = "desc"       # desc / asc

        # 并发检索状态
        self.pending_sites = []
        self.active_threads = 0
        self.max_concurrent = 8
        self.search_all_keyword = ""
        # 持有所有 SearchThread 引用，避免线程仍在运行时被 GC 导致闪退
        self.threads = []
        # 磁力预览状态（同一时刻仅允许一个 whatslink 请求线程；弹窗引用防 GC）
        self._preview_thread = None
        self._preview_windows = []

        self._init_ui()
        self._init_menu()

        self.site_list.setCurrentRow(0)

    # ---------- 菜单（代理设置等） ----------
    def _init_menu(self):
        menubar = self.menuBar()
        setting_menu = menubar.addMenu("设置")
        proxy_action = QAction("代理设置...", self)
        proxy_action.triggered.connect(self.open_proxy_dialog)
        setting_menu.addAction(proxy_action)

        help_menu = menubar.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        self._update_proxy_status_label()

    def _update_proxy_status_label(self):
        if hasattr(self, "proxy_status_label"):
            if PROXY_CONFIG["enabled"]:
                self.proxy_status_label.setText(
                    f"代理：已启用 {PROXY_CONFIG['protocol']}://{PROXY_CONFIG['host']}:{PROXY_CONFIG['port']}")
            else:
                self.proxy_status_label.setText("代理：未启用")

    def open_proxy_dialog(self):
        dlg = ProxyDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            save_proxy_config()
            reset_proxy_cache()  # 设置变更后重新预检
            self._update_proxy_status_label()
            self.log("代理设置已保存，代理预检缓存已重置")

    def show_about(self):
        QMessageBox.information(self, "关于",
            "BT磁力聚合搜索工具 V1.2\n\n"
            "多站点磁力聚合检索。\n"
            "V1.2：矢量图标、代理预检（不可用自动仅直连）、"
            "whatslink 磁力预览、站点精简与检索优化。\n\n"
            "仅用于技术学习，请遵守版权法律法规。")

    # ---------- UI ----------
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 顶部搜索栏
        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入搜索关键词，按回车或点击搜索按钮")
        self.search_edit.setMinimumHeight(35)
        self.search_edit.returnPressed.connect(self.start_search)
        self.search_btn = QPushButton("🔍 搜索")
        self.search_btn.setMinimumHeight(35)
        self.search_btn.setMinimumWidth(100)
        self.search_btn.clicked.connect(self.start_search)
        self.search_all_btn = QPushButton("⚡ 全部站点搜索")
        self.search_all_btn.setMinimumHeight(35)
        self.search_all_btn.setMinimumWidth(130)
        self.search_all_btn.clicked.connect(self.start_all_search)
        top_bar.addWidget(self.search_edit, stretch=8)
        top_bar.addWidget(self.search_btn, stretch=1)
        top_bar.addWidget(self.search_all_btn, stretch=1)
        main_layout.addLayout(top_bar)

        # 排序栏
        sort_bar = QHBoxLayout()
        sort_bar.addWidget(QLabel("排序："))
        self.sort_field_combo = QComboBox()
        self.sort_field_combo.addItems(["默认", "文件大小", "收录时间", "热度"])
        self.sort_field_combo.setMinimumHeight(30)
        self.sort_field_combo.currentIndexChanged.connect(self.on_sort_changed)
        self.sort_order_combo = QComboBox()
        self.sort_order_combo.addItems(["降序", "升序"])
        self.sort_order_combo.setMinimumHeight(30)
        self.sort_order_combo.currentIndexChanged.connect(self.on_sort_changed)
        sort_bar.addWidget(self.sort_field_combo)
        sort_bar.addWidget(self.sort_order_combo)
        sort_bar.addStretch(1)
        self.proxy_status_label = QLabel("代理：未启用")
        sort_bar.addWidget(self.proxy_status_label)
        main_layout.addLayout(sort_bar)

        # 中间内容区
        content_layout = QHBoxLayout()
        content_layout.setSpacing(10)

        left_layout = QVBoxLayout()
        site_label = QLabel("📌 搜索站点")
        site_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(site_label)
        self.site_list = QListWidget()
        self._count_delegate = CountHighlightDelegate()
        self.site_list.setItemDelegate(self._count_delegate)
        all_item = QListWidgetItem("📊 全部站点")
        all_item.setData(Qt.ItemDataRole.UserRole, "all")
        self.site_list.addItem(all_item)
        # 只列出可用站点；失效站点集中登记在 DEAD_SITES，不再占位
        for s in ACTIVE_SITES:
            item = QListWidgetItem(s["name"])
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            self.site_list.addItem(item)
        self.site_list.currentRowChanged.connect(self.switch_site)
        left_layout.addWidget(self.site_list)

        page_label = QLabel("📄 翻页控制")
        page_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(page_label)
        page_layout = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 上一页")
        self.prev_btn.clicked.connect(self.prev_page)
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 200)
        self.page_spin.setValue(1)
        self.next_btn = QPushButton("下一页 ▶")
        self.next_btn.clicked.connect(self.next_page)
        page_layout.addWidget(self.prev_btn)
        page_layout.addWidget(self.page_spin)
        page_layout.addWidget(self.next_btn)
        left_layout.addLayout(page_layout)
        left_widget = QWidget()
        left_widget.setLayout(left_layout)

        right_layout = QVBoxLayout()
        result_label = QLabel("📋 搜索结果（单击复制 / 点「预览」看磁力截图，双击磁力链接打开）")
        result_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(result_label)
        self.result_table = QTableWidget()
        self.table_headers = ["文件名", "磁力链接", "文件大小", "收录时间", "热度", "来源站点", "磁力预览"]
        self.result_table.setColumnCount(len(self.table_headers))
        self.result_table.setHorizontalHeaderLabels(self.table_headers)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.result_table.setColumnWidth(2, 100)
        self.result_table.setColumnWidth(3, 110)
        self.result_table.setColumnWidth(4, 80)
        self.result_table.setColumnWidth(5, 150)
        self.result_table.setColumnWidth(6, 92)
        self._preview_delegate = MagnetPreviewDelegate()
        self.result_table.setItemDelegateForColumn(6, self._preview_delegate)
        self.result_table.cellClicked.connect(self.copy_cell_text)
        self.result_table.cellDoubleClicked.connect(self.open_magnet)
        self.result_table.verticalHeader().setDefaultSectionSize(35)
        right_layout.addWidget(self.result_table)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        # 使用分隔条：左侧站点栏宽度可拖拽调整；默认加宽 50%（230 -> 345）
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.addWidget(left_widget)
        content_splitter.addWidget(right_widget)
        content_splitter.setStretchFactor(0, 0)
        content_splitter.setStretchFactor(1, 1)
        content_splitter.setSizes([345, 1155])
        content_splitter.setCollapsible(0, False)
        content_layout.addWidget(content_splitter)
        main_layout.addLayout(content_layout, stretch=1)

        # 实时日志面板
        log_label = QLabel("📜 实时日志（排查问题用）")
        log_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        main_layout.addWidget(log_label)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setMinimumHeight(90)
        self.log_text.setStyleSheet(
            "background-color: #ffffff; color: #000000; "
            "font-family: Consolas, Menlo, monospace; font-size: 11px; "
            "border-radius: 4px; border: 1px solid #ddd;")
        main_layout.addWidget(self.log_text)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("就绪，请选择站点并输入关键词")
        self.status_bar.addWidget(self.status_label, 1)
        self._proxy_label_widget = QLabel("")
        self.status_bar.addPermanentWidget(self._proxy_label_widget)
        self._update_proxy_status_label()

    # ---------- 日志 ----------
    def log(self, message):
        if not hasattr(self, "log_text") or self.log_text is None:
            return
        ts = time.strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{ts}] {message}")
        # 滚动到底部
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _cleanup_thread(self, thread):
        """线程结束后清理引用并释放，避免运行中被 GC 闪退。"""
        try:
            self.threads.remove(thread)
        except ValueError:
            pass
        thread.deleteLater()

    # ---------- 站点切换 ----------
    def switch_site(self, row):
        item = self.site_list.item(row)
        if not item:
            return
        site_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_view = site_id
        if site_id == "all":
            self.current_site = None
            self.status_label.setText("当前视图：全部站点汇总")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.page_spin.setEnabled(False)
        else:
            for s in SITE_LIST:
                if s["id"] == site_id:
                    self.current_site = s
                    break
            self.status_label.setText(f"当前视图：{self.current_site['name']}")
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            self.page_spin.setEnabled(True)
        self.current_page = 1
        self.page_spin.setValue(1)
        self.refresh_table()

    def clear_table(self):
        self.result_table.setRowCount(0)
        self.all_results = {}
        self.site_result_counts = {}
        for i in range(self.site_list.count()):
            item = self.site_list.item(i)
            site_id = item.data(Qt.ItemDataRole.UserRole)
            if site_id == "all":
                item.setText("📊 全部站点")
            else:
                for s in SITE_LIST:
                    if s["id"] == site_id:
                        item.setText(s["name"])
                        break

    def _collect_view_items(self):
        items = []
        if self.current_view == "all":
            for sid, results in self.all_results.items():
                items.extend(results)
        else:
            items = self.all_results.get(self.current_view, [])
        return items

    def _sort_items(self, items):
        if self.sort_field == "default":
            return items
        if self.sort_field == "size":
            key = lambda t: size_to_bytes(t.size)
        elif self.sort_field == "date":
            key = lambda t: date_to_int(t.date)
        else:
            key = lambda t: hot_to_int(t.hot)
        return sorted(items, key=key, reverse=(self.sort_order == "desc"))

    def refresh_table(self):
        self.result_table.setRowCount(0)
        items = self._sort_items(self._collect_view_items())
        for torrent in items:
            self._add_row_to_table(torrent)
        total = self.result_table.rowCount()
        self.status_label.setText(f"当前显示 {total} 条结果")

    def _add_row_to_table(self, torrent):
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        cells = [torrent.name, torrent.magnet, torrent.size, torrent.date, torrent.hot, torrent.site]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            if col == 1:
                item.setForeground(Qt.GlobalColor.blue)
            self.result_table.setItem(row, col, item)
        # 第 7 列「磁力预览」：仅放置占位项，按钮由 MagnetPreviewDelegate 绘制
        preview_item = QTableWidgetItem("")
        preview_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        preview_item.setData(Qt.ItemDataRole.UserRole, torrent.magnet)
        self.result_table.setItem(row, 6, preview_item)

    def copy_cell_text(self, row, col):
        if col == 6:
            self._start_magnet_preview(row)
            return
        item = self.result_table.item(row, col)
        if not item:
            return
        text = item.text()
        if not text:
            return
        self.clipboard.setText(text)
        show_text = text[:50] + "..." if len(text) > 50 else text
        self.status_label.setText(f"✅ 已复制：{show_text}")

    def open_magnet(self, row, col):
        if col == 1:
            item = self.result_table.item(row, col)
            if item and item.text().startswith("magnet:"):
                QDesktopServices.openUrl(QUrl(item.text()))
                self.status_label.setText("🚀 已调用下载工具打开磁力链接")

    # ---------- 磁力预览（whatslink.info） ----------
    def _start_magnet_preview(self, row):
        item = self.result_table.item(row, 1)
        magnet = item.text() if item else ""
        if not magnet.startswith("magnet:"):
            self.status_label.setText("⚠️ 该行没有磁力链接，无法预览")
            return
        if self._preview_thread is not None:
            self.status_label.setText("⏳ 正在获取上一个磁力预览，请稍候...")
            return
        # 代理决策：启用代理且预检可用 → 走代理；否则直连
        use_proxy = False
        if PROXY_CONFIG["enabled"]:
            proxy_ok, _ = check_proxy_available()
            use_proxy = proxy_ok
        self.status_label.setText("🔍 正在从 whatslink.info 获取磁力预览...")
        self.log(f"🔍 磁力预览：{magnet[:70]}...（代理：{'开' if use_proxy else '关'}）")
        t = WhatslinkPreviewThread(magnet, use_proxy)
        self._preview_thread = t
        t.log_signal.connect(self.log, Qt.ConnectionType.QueuedConnection)
        t.done_signal.connect(self._on_preview_done, Qt.ConnectionType.QueuedConnection)
        t.error_signal.connect(self._on_preview_error, Qt.ConnectionType.QueuedConnection)
        t.finished.connect(self._cleanup_preview_thread, Qt.ConnectionType.QueuedConnection)
        t.start()

    def _cleanup_preview_thread(self):
        if self._preview_thread is not None:
            self._preview_thread.deleteLater()
            self._preview_thread = None
        self.status_label.setText("就绪")

    def _on_preview_done(self, name, image_bytes_list, info_text):
        magnet = ""
        if self._preview_thread is not None:
            magnet = self._preview_thread.magnet
        dlg = MagnetPreviewDialog(self, magnet, name, image_bytes_list, info_text)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._preview_windows.append(dlg)
        # 弹窗关闭后由 WA_DeleteOnClose 释放 C++ 对象；这里仅限制引用列表长度防累积
        if len(self._preview_windows) > 20:
            self._preview_windows = self._preview_windows[-20:]
        dlg.show()
        self.log(f"✅ 磁力预览就绪：{name[:50]}（{len(image_bytes_list)} 张截图）")

    def _on_preview_error(self, msg):
        self.status_label.setText(f"❌ {msg}")
        self.log(f"❌ {msg}")

    # ---------- 排序切换 ----------
    def on_sort_changed(self):
        field_map = {0: "default", 1: "size", 2: "date", 3: "hot"}
        self.sort_field = field_map[self.sort_field_combo.currentIndex()]
        self.sort_order = "desc" if self.sort_order_combo.currentIndex() == 0 else "asc"
        self.refresh_table()

    # ---------- 单站点搜索 ----------
    def start_search(self):
        kw = self.search_edit.text().strip()
        if not kw:
            self.status_label.setText("⚠️ 请输入搜索关键词！")
            QMessageBox.warning(self, "提示", "请输入搜索关键词！")
            return
        if self.active_threads > 0:
            self.status_label.setText("⏳ 正在搜索中，请等待完成...")
            return
        if self.current_view == "all":
            self.start_all_search()
            return
        if not self.current_site:
            return
        if self.current_site["id"] in self.all_results:
            del self.all_results[self.current_site["id"]]
        if self.current_site["id"] in self.site_result_counts:
            del self.site_result_counts[self.current_site["id"]]
        self.update_site_list_counts()
        self.refresh_table()
        self.search_btn.setEnabled(False)
        self.search_all_btn.setEnabled(False)
        self.status_label.setText(f"🔍 正在搜索 {self.current_site['name']}，关键词：{kw}（第{self.current_page}页）")
        self.log(f"▶ 单站点搜索：{self.current_site['name']} | 关键词：{kw} | 第{self.current_page}页")
        self._run_one_site(self.current_site, kw, self.current_page, self.search_complete)

    def _run_one_site(self, site, kw, page, on_finish):
        t = SearchThread(site, kw, page)
        self.threads.append(t)
        # 强制队列连接：SearchThread 对象亲和性在主线程，若不显式指定，
        # 跨线程发射的信号会被解析为直连，导致槽函数在工作线程中操作 GUI 而崩溃（闪退）。
        t.result_signal.connect(self.add_result_row, Qt.ConnectionType.QueuedConnection)
        t.error_signal.connect(self.show_error, Qt.ConnectionType.QueuedConnection)
        t.status_signal.connect(self.update_status, Qt.ConnectionType.QueuedConnection)
        t.log_signal.connect(self.log, Qt.ConnectionType.QueuedConnection)
        t.finish_signal.connect(lambda c, cb=on_finish: cb(c), Qt.ConnectionType.QueuedConnection)
        t.finished.connect(lambda tw=t: self._cleanup_thread(tw), Qt.ConnectionType.QueuedConnection)
        t.start()

    def search_complete(self, count):
        self.search_btn.setEnabled(True)
        self.search_all_btn.setEnabled(True)
        # 排序激活时统一刷新以应用排序
        if self.sort_field != "default":
            self.refresh_table()
        total = self.result_table.rowCount()
        if self.current_site:
            self.status_label.setText(
                f"✅ 搜索完成，{self.current_site['name']} 共 {count} 条结果（第{self.current_page}页）")

    # ---------- 全部站点并发搜索 ----------
    def start_all_search(self):
        kw = self.search_edit.text().strip()
        if not kw:
            self.status_label.setText("⚠️ 请输入搜索关键词！")
            QMessageBox.warning(self, "提示", "请输入搜索关键词！")
            return
        if self.active_threads > 0:
            self.status_label.setText("⏳ 正在搜索中，请等待完成...")
            return
        self.clear_table()
        self.search_btn.setEnabled(False)
        self.search_all_btn.setEnabled(False)
        self.status_label.setText(f"⚡ 正在从全部站点并发搜索：{kw}")
        self.log(f"⚡ 开始全部站点并发搜索：{kw}（共 {len(ACTIVE_SITES)} 个可用站点，"
                 f"已停用 {len(DEAD_SITES)} 个失效站点，并发上限 {self.max_concurrent}）")
        self.site_list.setCurrentRow(0)
        self.current_view = "all"
        self.search_all_keyword = kw
        self.pending_sites = list(ACTIVE_SITES)
        self.active_threads = 0
        self._pump_all()

    def _pump_all(self):
        while self.active_threads < self.max_concurrent and self.pending_sites:
            site = self.pending_sites.pop(0)
            self.active_threads += 1
            t = SearchThread(site, self.search_all_keyword, 1)
            self.threads.append(t)
            t.result_signal.connect(self.add_result_row, Qt.ConnectionType.QueuedConnection)
            t.error_signal.connect(self.show_error, Qt.ConnectionType.QueuedConnection)
            t.status_signal.connect(self.update_status, Qt.ConnectionType.QueuedConnection)
            t.log_signal.connect(self.log, Qt.ConnectionType.QueuedConnection)
            t.finish_signal.connect(lambda c, s=site: self._on_site_done(c, s), Qt.ConnectionType.QueuedConnection)
            t.finished.connect(lambda tw=t: self._cleanup_thread(tw), Qt.ConnectionType.QueuedConnection)
            t.start()
        if not self.pending_sites and self.active_threads == 0:
            self.search_all_complete()

    def _on_site_done(self, count, site):
        self.active_threads -= 1
        if count == 0:
            # 该站点无数据（已尝试直连/代理）：在状态栏简要提示
            self.status_label.setText(f"⚠️ {site['name']} 无可用数据，已跳过")
        self._pump_all()

    def search_all_complete(self):
        self.search_btn.setEnabled(True)
        self.search_all_btn.setEnabled(True)
        total = self.result_table.rowCount()
        self.status_label.setText(f"✅ 全部站点搜索完成，共 {total} 条结果")
        self.log(f"✅ 全部站点搜索完成，共 {total} 条结果")
        self.site_list.setCurrentRow(0)
        self.refresh_table()

    # ---------- 结果处理 ----------
    def add_result_row(self, torrent):
        site_id = ""
        for s in SITE_LIST:
            if s["name"] == torrent.site:
                site_id = s["id"]
                break
        torrent.site_id = site_id
        if site_id not in self.all_results:
            self.all_results[site_id] = []
        self.all_results[site_id].append(torrent)
        self.site_result_counts[site_id] = len(self.all_results[site_id])
        self._update_one_site_count(site_id)
        # 性能优化：默认排序时增量追加单行；启用排序时暂缓，待完成/切视图时统一刷新
        if self.current_view == "all" or self.current_view == site_id:
            if self.sort_field == "default":
                self._add_row_to_table(torrent)
            # 排序状态下暂不逐行刷新，避免 O(N^2) 重建

    def _update_one_site_count(self, site_id):
        """只更新指定站点与“全部站点”的条数显示，避免每次结果都重绘整个列表。"""
        for i in range(self.site_list.count()):
            item = self.site_list.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            if sid == site_id:
                site_name = ""
                for s in SITE_LIST:
                    if s["id"] == sid:
                        site_name = s["name"]
                        break
                ps = next((s.get("page_size", 0) for s in SITE_LIST if s["id"] == sid), 0)
                count = self.site_result_counts.get(sid, 0)
                suffix = f" ({count}条)" if count > 0 else ""
                item.setText(f"{site_name}{suffix}")
            elif sid == "all":
                total = sum(self.site_result_counts.values())
                item.setText(f"📊 全部站点 ({total}条)" if total > 0 else "📊 全部站点")

    def update_site_list_counts(self):
        for i in range(self.site_list.count()):
            item = self.site_list.item(i)
            site_id = item.data(Qt.ItemDataRole.UserRole)
            if site_id == "all":
                total = sum(self.site_result_counts.values())
                item.setText(f"📊 全部站点 ({total}条)" if total > 0 else "📊 全部站点")
            else:
                site_name = ""
                for s in SITE_LIST:
                    if s["id"] == site_id:
                        site_name = s["name"]
                        break
                ps = next((s.get("page_size", 0) for s in SITE_LIST if s["id"] == site_id), 0)
                count = self.site_result_counts.get(site_id, 0)
                suffix = f" ({count}条)" if count > 0 else ""
                item.setText(f"{site_name}{suffix}")

    def show_error(self, msg):
        self.status_label.setText(f"❌ {msg}")
        self.log(f"❌ {msg}")

    def update_status(self, msg):
        self.status_label.setText(msg)

    # ---------- 翻页 ----------
    def prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self.page_spin.setValue(self.current_page)
            self.start_search()

    def next_page(self):
        self.current_page += 1
        self.page_spin.setValue(self.current_page)
        self.start_search()


# ===================== whatslink.info 磁力预览 =====================
WHATSLINK_API = "https://whatslink.info/api/v1/link"
WHATSLINK_PAGE = "https://whatslink.info/?url={url}"


def bytes_to_human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fetch_whatslink(magnet, proxies=None, timeout=15):
    """调用 whatslink.info 解析接口，返回 JSON dict。"""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://whatslink.info/",
    }
    r = requests.get(WHATSLINK_API, params={"url": magnet}, headers=headers,
                     proxies=proxies, timeout=timeout)
    r.raise_for_status()
    return r.json()


class WhatslinkPreviewThread(QThread):
    """后台线程：调用 whatslink 接口 →（必要时轮询等待解析）→ 下载截图字节。"""
    log_signal = pyqtSignal(str)
    done_signal = pyqtSignal(str, list, str)   # (名称, [图片字节...], 信息文本)
    error_signal = pyqtSignal(str)

    MAX_WAIT_ROUNDS = 5      # 新磁力解析轮询次数
    WAIT_SECONDS = 8         # 每轮间隔
    MAX_IMAGES = 12          # 最多下载的截图张数

    def __init__(self, magnet, use_proxy=False, parent=None):
        super().__init__(parent)
        self.magnet = magnet
        self.use_proxy = use_proxy
        self._stop = False

    def stop(self):
        self._stop = True

    def _sleep_interruptible(self, seconds):
        for _ in range(int(seconds * 10)):
            if self._stop:
                return False
            time.sleep(0.1)
        return not self._stop

    def run(self):
        try:
            proxies = get_proxies() if self.use_proxy else None
            data = None
            shots = []
            # 1) 调接口；新磁力可能需要等待服务端解析，轮询若干轮
            for attempt in range(1, self.MAX_WAIT_ROUNDS + 1):
                if self._stop:
                    return
                try:
                    data = fetch_whatslink(self.magnet, proxies)
                except Exception as e:
                    self.error_signal.emit(
                        f"whatslink 接口请求失败：{type(e).__name__} {str(e)[:80]}")
                    return
                shots = [s.get("screenshot")
                         for s in (data.get("screenshots") or []) if s.get("screenshot")]
                if shots:
                    break
                if attempt < self.MAX_WAIT_ROUNDS:
                    self.log_signal.emit(
                        f"⏳ whatslink 正在解析该磁力（等待 {self.WAIT_SECONDS}s 后重试，"
                        f"{attempt}/{self.MAX_WAIT_ROUNDS - 1}）...")
                    if not self._sleep_interruptible(self.WAIT_SECONDS):
                        return
            name = data.get("name") or "（未命名）"
            info_parts = [name, bytes_to_human(data.get("size"))]
            ftype = data.get("file_type") or data.get("type") or ""
            if ftype:
                info_parts.append(str(ftype))
            info_text = "  |  ".join(p for p in info_parts if p)
            if not shots:
                self.done_signal.emit(name, [], "whatslink 暂无该磁力的截图（可能解析超时或资源未被索引）")
                return
            # 2) 下载截图（jpg 字节，QPixmap 必须在 GUI 线程构造）
            imgs = []
            total = min(len(shots), self.MAX_IMAGES)
            for i, u in enumerate(shots[:total]):
                if self._stop:
                    return
                try:
                    rr = requests.get(u, headers={
                        "User-Agent": random.choice(USER_AGENTS),
                        "Referer": "https://whatslink.info/",
                    }, proxies=proxies, timeout=20)
                    if rr.status_code == 200 and rr.content:
                        imgs.append(rr.content)
                except Exception:
                    pass
                self.log_signal.emit(f"⬇ 截图下载 {i + 1}/{total}")
            if not imgs:
                self.error_signal.emit("截图下载全部失败（网络异常）")
                return
            self.done_signal.emit(name, imgs, info_text)
        except Exception as e:
            self.error_signal.emit(f"预览线程异常：{type(e).__name__} {str(e)[:100]}")


class MagnetPreviewDialog(QDialog):
    """磁力预览弹窗：展示 whatslink 返回的截图（可滚动）。"""
    def __init__(self, parent, magnet, name, image_bytes_list, info_text):
        super().__init__(parent)
        import hashlib as _hashlib
        self.setWindowTitle(f"磁力预览 - {name[:40]}")
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px;")
        layout.addWidget(info_label)
        if image_bytes_list:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            inner = QWidget()
            v = QVBoxLayout(inner)
            v.setSpacing(8)
            for i, data in enumerate(image_bytes_list, 1):
                pix = QPixmap()
                if pix.loadFromData(data):
                    lbl = QLabel()
                    lbl.setPixmap(pix.scaledToWidth(700, Qt.TransformationMode.SmoothTransformation))
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lbl.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
                    v.addWidget(lbl)
                else:
                    v.addWidget(QLabel(f"第 {i} 张截图解码失败"))
            v.addStretch(1)
            scroll.setWidget(inner)
            layout.addWidget(scroll, 1)
            layout.addWidget(QLabel(f"共 {len(image_bytes_list)} 张截图 · 数据来源：whatslink.info"))
        else:
            layout.addWidget(QLabel(info_text))
        btn_row = QHBoxLayout()
        page_url = WHATSLINK_PAGE.format(url=QUrl.toPercentEncoding(magnet).data().decode())
        open_btn = QPushButton("🌐 在浏览器打开 whatslink")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(page_url)))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(open_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class MagnetPreviewDelegate(QStyledItemDelegate):
    """在「磁力预览」列绘制按钮样式；点击行为由 MainWindow.cellClicked 触发。"""
    def paint(self, painter, option, index):
        widget = option.widget
        btn = QStyleOptionButton()
        btn.rect = option.rect.adjusted(8, 4, -8, -4)
        btn.text = "🔍 预览"
        state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Raised
        if option.state & QStyle.StateFlag.State_MouseOver:
            state |= QStyle.StateFlag.State_MouseOver
        btn.state = state
        if widget is not None:
            widget.style().drawControl(QStyle.ControlElement.CE_PushButton, btn, painter, widget)
        else:
            QApplication.style().drawControl(QStyle.ControlElement.CE_PushButton, btn, painter)


# ===================== 代理可用性测试线程 =====================
class ProxyTestThread(QThread):
    result_signal = pyqtSignal(bool, str)

    def __init__(self, protocol, host, port):
        super().__init__()
        self.protocol = protocol
        self.host = host
        self.port = port

    def run(self):
        import socket
        try:
            p = self.protocol
            if p not in ("http", "https", "socks5"):
                self.result_signal.emit(False, f"不支持的协议：{p}")
                return
            url = f"{p}://{self.host}:{self.port}"
            proxies = {"http": url, "https": url}
            # 先做 TCP 连通性检查（socks5 也可走 socket 验证端口可达）
            try:
                with socket.create_connection((self.host, int(self.port)), timeout=5):
                    pass
            except Exception as e:
                self.result_signal.emit(False, f"无法连接代理 {url}：{type(e).__name__} {str(e)[:80]}")
                return
            # 再通过代理发一次外网请求验证可用性
            test_url = "https://www.google.com"
            r = requests.get(test_url, proxies=proxies, timeout=12)
            if r.status_code == 200:
                self.result_signal.emit(True, f"代理可用（HTTP {r.status_code}），可正常访问外网")
            else:
                self.result_signal.emit(False, f"代理连接成功但外网请求返回状态码 {r.status_code}")
        except Exception as e:
            self.result_signal.emit(False, f"代理测试失败：{type(e).__name__} {str(e)[:120]}")


# ===================== 代理设置对话框 =====================
class ProxyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("代理设置")
        self.setMinimumWidth(360)
        layout = QFormLayout(self)
        self.enable_cb = QCheckBox("启用网络代理")
        self.enable_cb.setChecked(PROXY_CONFIG["enabled"])
        layout.addRow(self.enable_cb)
        self.proto_combo = QComboBox()
        self.proto_combo.addItems(["http", "https", "socks5"])
        self.proto_combo.setCurrentText(PROXY_CONFIG["protocol"])
        layout.addRow("协议：", self.proto_combo)
        self.host_edit = QLineEdit(PROXY_CONFIG["host"])
        layout.addRow("地址：", self.host_edit)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(PROXY_CONFIG["port"])
        layout.addRow("端口：", self.port_spin)
        hint = QLabel("启用后：检索前先预检代理可用性（结果缓存5分钟）；"
                      "预检通过时先直连、失败再用代理重试；预检不通过则本次仅直连。")
        hint.setWordWrap(True)
        layout.addRow(hint)

        # 代理测试
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton("🔌 测试代理可用性")
        self.test_btn.setMinimumHeight(32)
        self.test_btn.clicked.connect(self.test_proxy)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setStyleSheet("color: #555;")
        test_layout.addWidget(self.test_btn)
        test_layout.addWidget(self.test_result, stretch=1)
        layout.addRow(test_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._test_thread = None

    def accept(self):
        PROXY_CONFIG["enabled"] = self.enable_cb.isChecked()
        PROXY_CONFIG["protocol"] = self.proto_combo.currentText()
        PROXY_CONFIG["host"] = self.host_edit.text().strip()
        PROXY_CONFIG["port"] = self.port_spin.value()
        super().accept()

    def test_proxy(self):
        protocol = self.proto_combo.currentText()
        host = self.host_edit.text().strip()
        port = self.port_spin.value()
        if not host:
            self.test_result.setText("⚠ 请先填写代理地址")
            self.test_result.setStyleSheet("color: #c0392b;")
            return
        self.test_result.setText("⏳ 正在测试代理连通性...")
        self.test_result.setStyleSheet("color: #555;")
        self.test_btn.setEnabled(False)
        self._test_thread = ProxyTestThread(protocol, host, port)
        self._test_thread.result_signal.connect(self.on_test_result)
        self._test_thread.finished.connect(lambda: self._test_thread.deleteLater())
        # 同时把结果写入主窗口日志（若可访问）
        self._test_thread.start()

    def on_test_result(self, ok, msg):
        self.test_btn.setEnabled(True)
        if ok:
            self.test_result.setText("✅ " + msg)
            self.test_result.setStyleSheet("color: #27ae60;")
        else:
            self.test_result.setText("❌ " + msg)
            self.test_result.setStyleSheet("color: #c0392b;")
        parent = self.parent()
        if hasattr(parent, "log"):
            parent.log(f"代理测试（{PROXY_CONFIG['protocol']}://{PROXY_CONFIG['host']}:{PROXY_CONFIG['port']}）：{'成功' if ok else '失败'} - {msg}")


# ===================== 程序入口 =====================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    load_proxy_config()
    app.setStyleSheet("""
        QMainWindow { background-color: #f5f5f5; }
        QPushButton {
            background-color: #4a90e2; color: white; border: none;
            border-radius: 4px; padding: 5px 15px; font-weight: bold;
        }
        QPushButton:hover { background-color: #357abd; }
        QPushButton:disabled { background-color: #cccccc; }
        QLineEdit { border: 1px solid #ddd; border-radius: 4px; padding: 5px; }
        QLineEdit:focus { border-color: #4a90e2; }
        QListWidget { border: 1px solid #ddd; border-radius: 4px; background-color: white; }
        QListWidget::item:selected { background-color: #4a90e2; color: white; }
        QTableWidget { border: 1px solid #ddd; border-radius: 4px; background-color: white; gridline-color: #eee; }
        QHeaderView::section { background-color: #f0f0f0; padding: 5px; border: none;
            border-bottom: 2px solid #4a90e2; font-weight: bold; }
        QStatusBar { background-color: #f0f0f0; border-top: 1px solid #ddd; }
        QComboBox { border: 1px solid #ddd; border-radius: 4px; padding: 3px; }
        QMenuBar { background-color: #f0f0f0; }
        QMenu { background-color: white; border: 1px solid #ddd; }
    """)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
