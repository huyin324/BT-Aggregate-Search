# -*- coding: utf-8 -*-
"""
实测每个站点的检索可用性：直接抓取并解析，输出每站结果数。
用法：python test_sites.py
"""
import importlib.util
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("btapp", os.path.join(BASE, "BT磁力聚合搜索工具.py"))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

PROXY = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}
KW = "test"

def test_site(site, use_proxy):
    try:
        results = mod.search_site(site, KW, 1, PROXY if use_proxy else None)
        return len(results), results
    except Exception as e:
        return -1, [str(e)[:60]]

def main():
    print(f"{'ID':16s} {'解析器':14s} {'直连':>6s} {'代理':>6s}  名称")
    print("-" * 90)
    summary = {"ok_direct": [], "ok_proxy": [], "fail": []}
    for s in mod.SITE_LIST:
        d_cnt, _ = test_site(s, False)
        p_cnt, pres = test_site(s, True)
        d_s = str(d_cnt) if d_cnt >= 0 else "ERR"
        p_s = str(p_cnt) if p_cnt >= 0 else "ERR"
        flag = ""
        if p_cnt > 0:
            flag = "✅代理可"
            summary["ok_proxy"].append(s["id"])
        elif d_cnt > 0:
            flag = "✅直连可"
            summary["ok_direct"].append(s["id"])
        else:
            flag = "❌不可用"
            summary["fail"].append(s["id"])
        print(f"{s['id']:16s} {s['parser']:14s} {d_s:>6s} {p_s:>6s}  {s['name']}  {flag}")
        if p_cnt > 0 and pres:
            sample = pres[0]
            print(f"       样本: {sample.name[:40]} | {sample.size} | {sample.date} | 热:{sample.hot}")
    print("-" * 90)
    print(f"直连可用: {len(summary['ok_direct'])}  代理可用: {len(summary['ok_proxy'])}  不可用: {len(summary['fail'])}")
    print("不可用站点:", ", ".join(summary["fail"]))

if __name__ == "__main__":
    main()
