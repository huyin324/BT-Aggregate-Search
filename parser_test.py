# -*- coding: utf-8 -*-
"""用之前保存的真实结果页HTML验证各解析函数是否正确提取数据。"""
import importlib.util, os, re
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location('bt', os.path.join(BASE, 'BT磁力聚合搜索工具.py'))
m = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)

PO = os.path.join(BASE, 'probe_out')
# (文件名, 解析器名, 站点占位配置)
cases = [
    ('res_52bt_529076.html', 'zsky', {'name': '52BT'}),
    ('res_91bt_911178.html', 'zsky', {'name': '91BT'}),
    ('res_mm_9966109.html', 'zsky', {'name': '磁力妹妹'}),
    ('torrentkitty.html', 'torrentkitty', {'name': 'TorrentKitty'}),
    ('nyaa.html', 'nyaa', {'name': 'Nyaa'}),
    ('res_btmovi.html', 'btmovi', {'name': '磁力蜘蛛'}),
    ('res_btapp12.html', 'btapp', {'name': '磁力片'}),
    ('res_w1sokitty.html', 'sokitty', {'name': 'Sokitty'}),
    ('u001.html', 'u001', {'name': 'U001'}),
    ('res_starok1.html', 'starok', {'name': '磁力星球'}),
    ('res_cilisousuo_cc.html', 'generic', {'name': '磁力搜索CC'}),
]

print(f"{'解析器':14s} {'文件':28s} {'条数':>4s}  首条样本")
print('-' * 90)
for fn, parser, site in cases:
    p = os.path.join(PO, fn)
    if not os.path.exists(p):
        print(f"{parser:14s} {fn:28s} 文件缺失")
        continue
    html = open(p, encoding='utf-8', errors='ignore').read()
    soup = BeautifulSoup(html, 'lxml')
    parser_fn = m.PARSERS[parser]
    items = parser_fn(soup, site)
    sample = items[0] if items else None
    if sample:
        s = f"{sample.name[:30]} | {sample.size} | {sample.date} | 热:{sample.hot} | {sample.magnet[:40]}"
    else:
        s = "(无)"
    print(f"{parser:14s} {fn:28s} {len(items):>4d}  {s}")
