# FDE weekly report

每週二 23:59（UTC+8）由雲端 Claude Code routine 從 Jira（`FDEPROJECT`、`CS`）產出 FDE 週報，發布成靜態網頁（Vercel，發布 `site/`）。

## 流程

```
prepare   抓 Jira → data/<week>/snapshot.json → 算數字 → data/<week>/metrics.json   程式
narrate   AI 讀 metrics.json，寫 data/<week>/narrative.json                       AI（routine）
finalize  驗證 narrative 的卡號與數字 → site/index.html、site/reports/<week>.html   程式
publish   commit data/ 與 site/，push main，Vercel 自動部署                          routine
```

數字全部由程式算，AI 只寫「本週重點」1 到 3 條和每個專案一句「結果」。驗證器擋下兩種編造：不存在的卡號、metrics 裡沒有的數字。

## 規則

- **報告期間**：上週三 00:00 到本週二 23:59:59（UTC+8）。由執行當下的日期往回找最近的週二決定，排程晚幾分鐘跨到週三仍是同一期。
- **當週完成**：狀態類別 done，且 `resolutiondate` 落在期間內。
- **Hours**：Story point estimate（`customfield_10016`，預估值），只加葉節點。有子卡的卡自己的點數不算，epic 與 Kick off 卡不算。
- **專案**：以 epic 分組；不在 epic 底下的卡歸「未歸屬 epic」。先前就完成的 epic 不再列。
- **Status**：epic 已完成為 Done，其餘 On Track。
- **Owner**：卡片自己的 assignee，沒有就用父卡的，再沒有就是「未指派」。
- **支援單位**：卡片 label 在 `config/units.json` 白名單裡才算；不在白名單的歸「未分類」並列進資料檢查。
- **Token**：讀 `data/tokens/<week>.json`，格式 `{"CS-17": 12000}`（卡號對 token 數）。檔案不存在或卡號沒有值都顯示「—」，不顯示 0。

## Kick off 描述格式

每個 epic 底下放一張 Kick off 卡（父系設為該 epic），描述照這個格式寫。標籤要一字不差、放在行首，冒號全半形都可以；標籤也可以單獨當標題，內容寫在下面：

```
Objective：這個專案要達成什麼
Why It Matters：為什麼重要
Before/After
Before：現在的狀況
After：完成後的狀況
Evidence：用什麼證明完成
Decision Needed：需要誰決定什麼
Next Milestone：下一個里程碑
```

第一次讀到時鎖定：之後頁面一律顯示鎖定版本。空的段落之後補上算正常填寫；已經填過的段落再被改，頁面會標示「鎖定後被修改」。

## 本機執行

憑證讀環境變數 `JIRA_SITE`、`JIRA_EMAIL`、`JIRA_API_TOKEN`，沒有的話讀 `~/.config/jira-ops/env`。

```bash
python3 scripts/weekly.py prepare --week-ending 2026-09-22   # 印出 2026-W39
python3 scripts/weekly.py finalize 2026-W39                   # 需要先有 narrative.json
python3 -m unittest discover -s tests -t tests                # 門檻測試
```

## Vercel 設定

Import 這個 repo，Framework Preset 選 Other，Output Directory 由 `vercel.json` 指定為 `site`，不需要 build command。只有 `site/` 會公開，`data/` 不會。
