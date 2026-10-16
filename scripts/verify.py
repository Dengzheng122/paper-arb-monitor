#!/usr/bin/env python3
"""
候选跨链代币的服务器端自动验证（GitHub Actions 版）。

原理：用 CoinGecko 公开 API /api/v3/coins/{platform}/contract/{address}
分别查候选在每条链上的合约地址，比较返回的 coin id：
- 所有链都查到且 id 相同  -> 同一币种
- 都查到但 id 不同      -> 不是同一币种（疑似仿盘）
- 任一条链 404/失败      -> 无法自动验证，需人工看

结果写回 docs/state/candidates.json 的 verification 字段，
GitHub Pages 前端直接读取展示。

只读公开 API，不下单不交易。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAND_PATH = os.path.join(REPO_ROOT, "docs", "state", "candidates.json")

PLATFORM_MAP = {
    "bsc": "bsc",
    "solana": "solana",
    "base": "base",
    "ethereum": "ethereum",
    "eth": "ethereum",
}

UA = {"User-Agent": "paper-arb-gh/1.0"}
SLEEP_BETWEEN_CALLS = 7  # CoinGecko 免费档限流，保守间隔


def cg_lookup(platform, address):
    url = f"https://api.coingecko.com/api/v3/coins/{platform}/contract/{address}"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.load(r)
        return {"ok": True, "coin_id": d.get("id"), "coin_name": d.get("name"),
                "symbol": d.get("symbol")}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "reason": "CoinGecko未收录该链合约(404)"}
        if e.code == 429:
            return {"ok": False, "reason": "CoinGecko限流(429)，稍后重试"}
        return {"ok": False, "reason": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"请求失败: {e}"}


def verify_candidate(cand):
    chains = [k for k in PLATFORM_MAP
              if isinstance(cand.get(k), dict) and cand[k].get("addr")]
    if not chains:
        chains = [k for k in PLATFORM_MAP
                  if isinstance(cand.get(k), str) and cand[k]]
    results = {}
    for ch in chains:
        platform = PLATFORM_MAP.get(ch, ch)
        addr = cand[ch]["addr"] if isinstance(cand[ch], dict) else cand[ch]
        results[ch] = cg_lookup(platform, addr)
        results[ch]["address"] = addr
        time.sleep(SLEEP_BETWEEN_CALLS)
    ok_results = {ch: r for ch, r in results.items() if r["ok"] and r.get("coin_id")}
    now = datetime.now(timezone.utc).isoformat()
    if len(ok_results) == len(results) and len(results) >= 2:
        ids = {r["coin_id"] for r in ok_results.values()}
        if len(ids) == 1:
            coin_id = ids.pop()
            return {"status": "same",
                    "coin_id": coin_id,
                    "coin_name": next(iter(ok_results.values()))["coin_name"],
                    "chains": results, "checked_at": now,
                    "note": "双链合约对应同一CoinGecko币种"}
        return {"status": "different",
                "chains": results, "checked_at": now,
                "note": "双链合约对应不同CoinGecko币种，疑似仿盘/同名币"}
    failed = {ch: r["reason"] for ch, r in results.items() if not r["ok"]}
    return {"status": "unknown",
            "chains": results, "checked_at": now,
            "note": "无法自动验证，需人工核对: " + "; ".join(
                f"{ch}{reason}" for ch, reason in failed.items())}


def main():
    if not os.path.exists(CAND_PATH):
        print("candidates.json 不存在，跳过验证")
        return 0
    with open(CAND_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cands = data.get("candidates", [])
    if not cands:
        print("无候选，跳过验证")
        return 0

    changed = False
    for cand in cands:
        v = cand.get("verification") or {}
        if v.get("status") == "same":
            continue  # 已确认为同一币种，不重复验证
        print(f"验证 {cand.get('symbol')} ...", flush=True)
        res = verify_candidate(cand)
        cand["verification"] = res
        if res["status"] == "same":
            cand["verified"] = True
        changed = True
        print(f"  -> {res['status']}: {res['note']}")

    if changed:
        with open(CAND_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    print("验证完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
