# 关键词放量监控 (keyword-monitor)

每小时检索 X(Twitter) 上一组关键词的最新内容，按推文 ID **跨小时去重**统计「本轮新增」条数，
**突然放量或出现异常信号时高亮**，结果发布到 GitHub Pages 看板。是失效的 TweetDeck「多列监控」的近似复刻。

**站点**：https://wenhanweime.github.io/keyword-monitor/

## 当前监控对象（改 `keywords.py` 增删）
- **存储/内存**：美光 $MU、闪迪 $SNDK、海力士 Hynix、存储/HBM/DRAM/NAND
- **光模块/CPO**：光模块/光通信、CPO/硅光、$SIVE

## 工作原理
`launchd`（每小时 HH:17）→ `monitor.py`：
1. 每个**簇**跑一次 grok Latest 检索（2 簇 = 每轮 2 次调用，省成本）。
2. 解析原始推文，按**推文 ID 跨小时去重** → 每个桶「本轮新增」条数。
   > 用 ID 去重而非时间戳——绕开 grok 时间戳不准的问题。
3. **放量检测**：本轮新增 ≥4 且 ≥2.5× 该桶近 24 轮基线 → 标 🔺放量。
4. **信号层**：grok 在每簇查询里附「信号：」一句，识别突发/新叙事/bot 刷屏。
5. 渲染 `docs/index.html`（每桶放量曲线 + 告警 + 本轮新帖）→ `git push`。

## 准确性说明（诚实）
新增数是**采样代理**，不是精确计数：grok 每轮每簇只采样 ~40 条最新帖。
- **低频词**（如 $SIVE）：基本拉全，计数准。
- **高频词**（如存储/MU）：会被采样上限截顶，页面标「采样上限,可能偏低」。
- 价值在**相对变化/放量信号**（跟自己基线比），不在绝对条数。要精确计数需 Apify/X API（付费）。

## 手动运行
```bash
cd ~/Documents/keyword-monitor
python3 monitor.py
```

## 改频率
编辑 `~/Library/LaunchAgents/com.pot.keyword-monitor.plist` 的 `StartCalendarInterval`，然后
`launchctl bootout gui/$(id -u)/com.pot.keyword-monitor; launchctl bootstrap gui/$(id -u) <plist>`。

## 文件
- `monitor.py` 主逻辑 · `keywords.py` 监控对象（改这里）
- `data/state.json` 去重记忆 + 小时时间序列（本地，不入库）
- `docs/` GitHub Pages（`index.html` + `assets/style.css`）

仅供研究，非投资建议。
