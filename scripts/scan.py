#!/usr/bin/env python3
"""
机会扫描器：发现"曾经或现在有价差"的跨链代币，丢进监控名单。

流程：
1. 拉 BSC / Solana 的热门池（trending），取 base 代币
2. 双边按 symbol 配对；只在一边热门的，再用 search 接口去另一边定向找
3. 算当前价差 + 24h 涨跌幅分歧（"曾经有价差"的代理指标），打分排名
4. 写入 state/candidates.json；按配置自动把高分候选加入 watchlist（verified=false）

注意：跨链不同合约地址是常态，symbol 相同不等于同一代币（仿盘很多）。
自动加入的均为"未验证"，paper_arb.py 会跳过它们，直到人工确认后在
config.json 里把 verified 设为 true。确认方法见 README。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE, "docs", "state")
CAND_PATH = os.path.join(STATE_DIR, "candidates.json")
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")

SCANNER = {
    "enabled": True,
    "networks": ["bsc", "solana"],
    "trending_pages": 1,
    "min_liquidity_usd": 1000.0,
    "min_score": 3.0,
    "max_candidates": 20,
    "max_spread_pct": 30.0,
    "hist_weight": 0.5,
    "targeted_search_n": 10,
}
TRENDING_API = "https://api.geckoterminal.com/api/v2/networks/{}/trending_pools?page={}"
SEARCH_API = "https://api.geckoterminal.com/api/v2/search/pools?query={}&network={}"
UA = {"User-Agent": "paper-arb-monitor/1.0"}


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def parse_pool(p):
    """从 pool 对象提取关键字段，失败返回 None。"""
    attr = p.get("attributes") or {}
    rel = p.get("relationships") or {}
    base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
    if "_" not in base_id:
        return None
    _, addr = base_id.split("_", 1)
    name = attr.get("name", "") or ""
    symbol = name.split("/")[0].strip().upper() if "/" in name else ""
    if not symbol:
        return None
    try:
        price = float(attr.get("base_token_price_usd"))
    except (TypeError, ValueError):
        return None
    try:
        reserve = float(attr.get("reserve_in_usd") or 0)
    except (TypeError, ValueError):
        reserve = 0.0
    chg = attr.get("price_change_percentage") or {}
    try:
        h24 = float(chg.get("h24") or 0)
    except (TypeError, ValueError):
        h24 = 0.0
    return {"symbol": symbol, "addr": addr, "price": price,
            "reserve": reserve, "h24": h24, "name": name}


def trending(network, pages=1):
    out = []
    for pg in range(1, pages + 1):
        data = get_json(TRENDING_API.format(network, pg)).get("data") or []
        for p in data:
            info = parse_pool(p)
            if info:
                info["network"] = network
                out.append(info)
        time.sleep(1)
    # 同 symbol 去重，保留池子最深的
    best = {}
    for i in out:
        s = i["symbol"]
        if s not in best or i["reserve"] > best[s]["reserve"]:
            best[s] = i
    return best


def search_other_side(symbol, network):
    """去另一条链按 symbol 找池子，取同名且池子最深的一个。"""
    try:
        data = get_json(SEARCH_API.format(urllib.parse.quote(symbol),
                                          network)).get("data") or []
    except Exception:  # noqa: BLE001
        return None
    cands = []
    for p in data:
        info = parse_pool(p)
        if info and info["symbol"] == symbol.upper():
            cands.append(info)
    if not cands:
        return None
    return max(cands, key=lambda c: c["reserve"])


# 主流代币双链合约白名单：命中则自动视为同一代币（verified=True）
CANONICAL = {
    "USDT": {"bsc": "0x55d398326f99059ff775485246999027b3197955",
             "solana": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"},
    "USDC": {"bsc": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
             "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
}


def is_canonical(symbol, bsc_addr, sol_addr):
    c = CANONICAL.get(symbol.upper())
    return bool(c and c["bsc"].lower() == bsc_addr.lower()
                and c["solana"].lower() == sol_addr.lower())


def score_pair(a, b):
    """a/b 为双链 info。返回 (score, current_spread, hist_div)。
    当前价差超过 max_spread_pct 的直接排除：不可能是同一经济代币（仿盘）。"""
    lo = min(a["price"], b["price"])
    if lo <= 0:
        return None
    cur = abs(a["price"] - b["price"]) / lo * 100
    if cur > SCANNER["max_spread_pct"]:
        return None
    hist = abs(a["h24"] - b["h24"])
    w = SCANNER["hist_weight"]
    return (cur + w * hist, cur, hist)


def main():
    sc = SCANNER
    if not sc.get("enabled", True):
        print("scanner disabled")
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    networks = sc.get("networks", ["bsc", "solana"])
    pages = sc.get("trending_pages", 1)
    min_liq = sc.get("min_liquidity_usd", 1000.0)
    min_score = sc.get("min_score", 3.0)
    max_cand = sc.get("max_candidates", 20)

    print("拉取热门池...", flush=True)
    books = {n: trending(n, pages) for n in networks}
    for n, b in books.items():
        print(f"  {n}: {len(b)} 个代币")

    pairs = {}  # symbol -> {bsc: info, solana: info}
    bsc, sol = books.get("bsc", {}), books.get("solana", {})
    for s in set(bsc) & set(sol):
        pairs[s] = {"bsc": bsc[s], "solana": sol[s]}

    # 只在一边热门的，定向去另一边找（限流：每边最多查 top N）
    targeted = sc.get("targeted_search_n", 10)
    bsc_only = sorted(set(bsc) - set(sol), key=lambda s: bsc[s]["reserve"],
                      reverse=True)[:targeted]
    for s in bsc_only:
        hit = search_other_side(s, "solana")
        time.sleep(2)  # 免费接口限流，温柔一点
        if hit:
            pairs[s] = {"bsc": bsc[s], "solana": hit}
    sol_only = sorted(set(sol) - set(bsc), key=lambda s: sol[s]["reserve"],
                      reverse=True)[:targeted]
    for s in sol_only:
        hit = search_other_side(s, "bsc")
        time.sleep(2)
        if hit:
            pairs[s] = {"bsc": hit, "solana": sol[s]}

    print(f"配对成功 {len(pairs)} 个 symbol，计算价差...", flush=True)
    ranked = []
    for s, legs in pairs.items():
        a, b = legs["bsc"], legs["solana"]
        if min(a["reserve"], b["reserve"]) < min_liq:
            continue
        sc3 = score_pair(a, b)
        if not sc3:
            continue
        s_total, cur, hist = sc3
        if s_total < min_score:
            continue
        verified = is_canonical(s, a["addr"], b["addr"])
        ranked.append({"symbol": s, "score": round(s_total, 2),
                       "current_spread_pct": round(cur, 2),
                       "h24_divergence": round(hist, 2),
                       "verified": verified,
                       "bsc": {"addr": a["addr"], "price": a["price"],
                               "reserve": round(a["reserve"], 0), "h24": a["h24"]},
                       "solana": {"addr": b["addr"], "price": b["price"],
                                  "reserve": round(b["reserve"], 0),
                                  "h24": b["h24"]}})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    ranked = ranked[:sc["max_candidates"]]

    with open(CAND_PATH, "w", encoding="utf-8") as f:
        json.dump({"scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                time.gmtime()),
                   "count": len(ranked), "candidates": ranked},
                  f, ensure_ascii=False, indent=2)

    nv = sum(1 for c in ranked if c["verified"])
    print(f"=== 扫描完成：{len(ranked)} 个候选（其中已验证 {nv} 个）===")
    # 企微推送候选摘要（环境变量未设置则跳过）
    wh = WECOM_WEBHOOK
    if wh and ranked:
        lines = ["【跨链套利机会扫描】{} 个候选：".format(len(ranked))]
        for c in ranked[:5]:
            tag = "已验证" if c["verified"] else "待验证"
            bp, sp = c["bsc"]["price"], c["solana"]["price"]
            buy = "BSC" if bp <= sp else "Solana"
            lines.append("{} {}｜价差 {}%｜24h分歧 {}".format(
                c["symbol"], tag, c["current_spread_pct"], c["h24_divergence"]))
            lines.append("  BSC ${:.6f} vs SOL ${:.6f}，买{}".format(bp, sp, buy))
            lines.append("  BSC: {}".format(c["bsc"]["addr"]))
            lines.append("  SOL: {}".format(c["solana"]["addr"]))
        if len(ranked) > 5:
            lines.append("…等共 {} 个，详见 state/candidates.json".format(len(ranked)))
        lines.append("待验证候选需人工确认双链为同一代币后再参与模拟。")
        try:
            import urllib.request
            payload = json.dumps(
                {"msgtype": "text", "text": {"content": "\n".join(lines)}}
            ).encode()
            req = urllib.request.Request(
                wh, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
            print("企微推送已发送")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 企微推送失败: {e}")
    for c in ranked:
        tag = "已验证" if c["verified"] else "待验证"
        print(f"[{c['symbol']}] {tag} | 综合分 {c['score']} | 当前价差 "
              f"{c['current_spread_pct']}% | 24h分歧 {c['h24_divergence']} | "
              f"池深 bsc ${c['bsc']['reserve']:.0f} / sol ${c['solana']['reserve']:.0f}")


if __name__ == "__main__":
    main()
