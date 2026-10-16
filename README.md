# 跨链套利扫描 · GitHub 实时版

BSC × Solana 跨链价差扫描 + CoinGecko 双链合约自动验证，纯纸面观察，不交易。

## 架构

```
GitHub Actions（每 30 分钟）
  ├─ scripts/scan.py    拉 GeckoTerminal 热门池，按 symbol 配对算价差 → docs/state/candidates.json
  └─ scripts/verify.py  调 CoinGecko 比对双链合约 coin id → 写回 verification 字段
GitHub Pages（docs/ 目录）
  └─ index.html         纯静态页，fetch JSON 渲染，每 5 分钟自动刷新
```

全程只用公开免费 API，**不需要任何密钥 / Token / 私钥**。

## 三步上手

1. **建仓库**：在 GitHub 新建一个公开仓库（比如 `paper-arb-monitor`），把本目录所有文件推上去：
   ```bash
   cd paper-arb-gh
   git init && git add . && git commit -m "init"
   git remote add origin https://github.com/你的用户名/paper-arb-monitor.git
   git push -u origin main
   ```

2. **开 Pages**：仓库 Settings → Pages → Build and deployment → Deploy from a branch → 分支选 `main`，目录选 `/docs` → Save。等一两分钟，得到 `https://你的用户名.github.io/paper-arb-monitor/`。

3. **开 Actions**：Actions 页面点 "I understand my workflows" 启用（公开仓库默认启用）。之后每 30 分钟自动跑一次，也可以在 Actions 里点 Run workflow 手动触发。首次运行后 `docs/state/candidates.json` 生成，页面就有数据了。

## 可选：企微推送

仓库 Settings → Secrets and variables → Actions → New repository secret：
- Name: `WECOM_WEBHOOK`，Value: 企微群机器人 webhook 地址

然后在 `.github/workflows/scan.yml` 的两个 `run:` 步骤下加一行：
```yaml
env:
  WECOM_WEBHOOK: ${{ secrets.WECOM_WEBHOOK }}
```

## 验证结论说明

| 状态 | 含义 |
|---|---|
| ✅ 同一币种 | 双链合约在 CoinGecko 对应同一个币种 ID |
| ❌ 不是同一币种 | 双链合约对应不同币种，疑似仿盘/同名币 |
| ⚠️ 无法自动验证 | 某条链合约 CoinGecko 未收录，需人工去 CoinGecko 核对 |

## 调参

改 `scripts/scan.py` 顶部的 `SCANNER` 常量：`min_score`（最低分）、`max_spread_pct`（价差上限，超了视为仿盘）、`min_liquidity_usd`（最小池深）等。

## 免责

本项目只做数据展示与纸面观察，不执行任何交易，不构成投资建议。meme/土狗币风险极高。
