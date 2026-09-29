# -*- coding: utf-8 -*-
"""关键词实测：对全部启用站点用给定关键词检索，统计条数与"标题含关键词"命中数。
用法：python kw_check.py --kw=白洁 [--kw2=少妇] [--proxy]
"""
import os, sys, time, random, json
import importlib.util

BASE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("bt", os.path.join(BASE, "BT磁力聚合搜索工具.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
PROXIES = PROXY if "--proxy" in sys.argv else None
KWS = []
for a in sys.argv:
    if a.startswith("--kw="):
        KWS.append(a.split("=", 1)[1])
if not KWS:
    KWS = ["白洁"]


def test_one(site, kw):
    try:
        items = m.search_site(site, kw, 1, proxies=PROXIES)
    except Exception as e:
        return {"id": site["id"], "name": site["name"], "err": f"{type(e).__name__}: {str(e)[:70]}",
                "items": 0, "hits": 0, "sample": ""}
    hits = sum(1 for it in items if kw in (it.name or ""))
    sample = items[0].name[:40] if items else ""
    return {"id": site["id"], "name": site["name"], "err": "", "items": len(items),
            "hits": hits, "sample": sample}


for kw in KWS:
    print(f"\n===== 关键词「{kw}」 | mode: {'PROXY' if PROXIES else 'DIRECT'} | sites: {len(m.ACTIVE_SITES)} =====")
    rows = []
    for site in m.ACTIVE_SITES:
        r = test_one(site, kw)
        rows.append(r)
        flag = "OK " if r["items"] > 0 and r["hits"] > 0 else ("HOT?" if r["items"] > 0 else "FAIL")
        print(f"[{flag}] {site['id']:<16} items={r['items']:<3} kw命中={r['hits']:<3} {r['err'][:40]:<40} 样本: {r['sample']}")
        time.sleep(random.uniform(0.2, 0.6))
    dead = [r["id"] for r in rows if r["items"] == 0 or r["hits"] == 0]
    print(f"\n-- 汇总：{len(rows)} 站 | 检索到含关键词内容: {len(rows)-len(dead)} | 建议删除: {dead}")

# 逐站保存结果供分析
out = os.path.join(BASE, "kw_check_out")
os.makedirs(out, exist_ok=True)
print("\n(kw_check_out 仅保存脚本汇总，不保存 HTML)")
