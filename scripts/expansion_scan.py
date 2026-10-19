#!/usr/bin/env python3
"""
每日跨链价差扩围扫描（纸面观察，不下单）。
数据源（免 key）：GeckoTerminal 七链 + Binance 公共数据 data-api.binance.vision
有新候选时推送企微。用法: python3 expansion_scan.py [--push]
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE, "docs", "state")
STATE_PATH = os.path.join(STATE_DIR, "expansion-scan-latest.json")
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")

GT_BASE = "https://api.geckoterminal.com/api/v2"
BN_BASE = "https://data-api.binance.vision"
UA = {"User-Agent": "paper-arb-expansion/1.0"}

STABLES = {"USDT","USDC","DAI","FDUSD","TUSD","USDD","PYUSD","USDP",
           "FRAX","LUSD","USTC","USD1","BUSD","USDe","sUSDe","USDG","RLUSD"}
WRAPPED = {"WBNB","WSOL","WETH","WBTC","WMATIC","WAVAX","WSTETH","CBETH","CBBTC"}
CHAINS = ["bsc", "solana", "base", "polygon_pos", "arbitrum",
          "optimism", "avax"]  # eth 已剔除：主网 gas 太高，不适合套利观察

# 已人工确认为"同名不同币"的组合：扫描时直接跳过，不再报警
CONFIRMED_FAKE = {
    ("DNA", frozenset(("bsc", "solana"))),  # 2026-10-19 ave.ai 查证：BSC=DE NEO AGENT，Solana=NeoSoul 仿盘
}


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def scan_pools(network, endpoint, pages):
    out = {}
    for page in range(1, pages + 1):
        try:
            d = get_json("%s/networks/%s/%s?page=%d&include=base_token,quote_token"
                         % (GT_BASE, network, endpoint, page))
        except Exception as e:
            print("[%s] %s p%d 失败: %s" % (network, endpoint, page, e),
                  file=sys.stderr)
            break
        pools = d.get("data", [])
        if not pools:
            break
        included = {}
        for t in d.get("included", []):
            if t.get("type") == "token":
                included[t["id"]] = t.get("attributes", {})
        for p in pools:
            a = p.get("attributes", {})
            rel = p.get("relationships", {})
            try:
                price = float(a.get("base_token_price_usd") or 0)
                tvl = float(a.get("reserve_in_usd") or 0)
            except Exception:
                continue
            if price <= 0 or tvl < 30000:
                continue
            bt = rel.get("base_token", {}).get("data", {}).get("id", "")
            tok = included.get(bt, {})
            sym = (tok.get("symbol") or "").upper().strip()
            if not sym or sym in STABLES or sym in WRAPPED:
                continue
            if sym not in out or tvl > out[sym][1]:
                out[sym] = (price, tvl, a.get("address", ""),
                            tok.get("address", ""), tok.get("name", ""))
        time.sleep(1.0)
    return out


def binance_spot_symbols():
    try:
        d = get_json(BN_BASE + "/api/v3/exchangeInfo", timeout=30)
        return {s["baseAsset"].upper() for s in d.get("symbols", [])
                if s.get("status") == "TRADING"
                and s.get("quoteAsset") == "USDT"}
    except Exception as e:
        print("[warn] 币安现货列表拉取失败: %s" % e, file=sys.stderr)
        return set()


def wecom_push(webhook, text):
    if not webhook:
        return
    try:
        payload = json.dumps({"msgtype": "text",
                              "text": {"content": text}}).encode()
        req = urllib.request.Request(webhook, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print("[warn] 企微推送失败: %s" % e, file=sys.stderr)


def main():
    push = "--push" in sys.argv
    cst = timezone(timedelta(hours=8))
    today = datetime.now(cst).strftime("%Y-%m-%d")

    chains = {}
    for net in CHAINS:
        merged = scan_pools(net, "trending_pools", 2)
        top = scan_pools(net, "pools", 3)
        for sym, v in top.items():
            if sym not in merged or v[1] > merged[sym][1]:
                merged[sym] = v
        chains[net] = merged
        print("[%s] %d tokens" % (net, len(merged)), file=sys.stderr)
        time.sleep(1.2)

    syms = {}
    for net, toks in chains.items():
        for sym, v in toks.items():
            syms.setdefault(sym, {})[net] = v

    cands = []
    for sym, per in syms.items():
        nets = sorted(per.keys())
        if len(nets) < 2:
            continue
        for i in range(len(nets)):
            for j in range(i + 1, len(nets)):
                n1, n2 = nets[i], nets[j]
                if (sym, frozenset((n1, n2))) in CONFIRMED_FAKE:
                    continue  # 已确认为同名不同币，跳过
                p1, t1, pa1, ta1, nm1 = per[n1]
                p2, t2, pa2, ta2, nm2 = per[n2]
                spread = abs(p2 - p1) / min(p1, p2) * 100
                if 1.0 <= spread <= 30.0:
                    cands.append({
                        "symbol": sym, "chain_a": n1, "price_a": p1,
                        "tvl_a": round(t1), "pool_a": pa1, "token_a": ta1,
                        "name_a": nm1, "chain_b": n2, "price_b": p2,
                        "tvl_b": round(t2), "pool_b": pa2, "token_b": ta2,
                        "name_b": nm2, "spread_pct": round(spread, 3),
                        "cheap": n1 if p1 < p2 else n2,
                    })
    cands.sort(key=lambda x: -x["spread_pct"])

    bn = binance_spot_symbols()
    for c in cands:
        c["binance_spot"] = c["symbol"] in bn

    prev_syms = set()
    if os.path.exists(STATE_PATH):
        try:
            prev = json.load(open(STATE_PATH))
            prev_syms = {c["symbol"] for c in prev.get("candidates", [])}
        except Exception:
            pass
    new_cands = [c for c in cands if c["symbol"] not in prev_syms]

    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump({"date": today, "candidates": cands},
              open(STATE_PATH, "w"), indent=1, ensure_ascii=False)

    lines = ["【跨链价差每日扫描 %s】纸面观察" % today]
    if cands:
        lines.append("候选 %d 对（TVL>$30k，价差1-30%%）：" % len(cands))
        for c in cands[:10]:
            tag = "有币安现货" if c["binance_spot"] else "DEX-only"
            lines.append("- %s: %s便宜 %s$%g vs %s$%g 价差%s%%（%s）"
                         % (c["symbol"], c["cheap"], c["chain_a"],
                            c["price_a"], c["chain_b"], c["price_b"],
                            c["spread_pct"], tag))
        if len(cands) > 10:
            lines.append("…另有 %d 对" % (len(cands) - 10))
        lines.append("注意：同名可能是不同项目，入选需人工复核双链合约。")
    else:
        lines.append("今日无符合条件的跨链价差候选。")
    summary = "\n".join(lines)
    print(summary)

    if push and new_cands:
        try:
            wecom_push(WECOM_WEBHOOK,
                       "【新价差候选】\n" + "\n".join(
                           "%s %s↔%s 价差%s%%" % (c["symbol"], c["chain_a"],
                                                  c["chain_b"], c["spread_pct"])
                           for c in new_cands[:10]))
        except Exception as e:
            print("[warn] 推送配置读取失败: %s" % e, file=sys.stderr)


if __name__ == "__main__":
    main()
