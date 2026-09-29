# FDE weekly report

每週二 23:59（UTC+8）由雲端 Claude Code routine 從 Jira（`FDEPROJECT`、`CS`）產出 FDE 週報，發布成靜態網頁（Vercel，發布 `site/`）。

## 流程

```
prepare   抓 Jira → data/<week>/snapshot.json → 算數字 → data/<week>/metrics.json   程式
narrate   AI 讀 metrics.json，寫 data/<week>/narrative.json                       AI（routine）
finalize  驗證 narrative 的卡號與數字 → site/index.html、site/reports/<week>.html   程式
publish   commit data/ 與 site/，push main，Vercel 自動部署                          routine
```

數字全部由程式算，AI 只寫「本週重點」1 到 3 條和每個專案一句「結果」。驗證器擋下兩種編造：不存在或已排除的卡號、metrics 裡沒有的數字。

## 規則

- **報告期間**：上週三 00:00 到本週二 23:59:59（UTC+8）。由執行當下的日期往回找最近的週二決定，排程晚幾分鐘跨到週三仍是同一期。
- **當週完成**：狀態類別 done，且 `resolutiondate` 落在期間內。
- **實際工時**：Story point estimate（`customfield_10016`），由執行人完工後回填實際花費時數（2026-09-30 使用者確認），只加葉節點。有子卡的卡自己的點數不算，epic 與 Kick off 卡不算。
- **專案**：以 epic 分組；不在 epic 底下的卡歸「未歸屬 epic」。先前就完成的 epic 不再列。
- **Status**：epic 已完成為 Done，其餘 On Track。
- **Owner**：卡片自己的 assignee。子任務沒有 assignee 時用父卡的；一般卡沒有 assignee 就是「未指派」，不往上算給 epic 負責人。
- **支援單位**：卡片 label 在 `config/units.json` 白名單裡才算；不在白名單的歸「未分類」並列進資料檢查。
- **排除**：`config/excluded.json` 列的卡不算完成，本週與累計的張數、工時、交付、人、支援單位都不算；epic 進度照 Jira 狀態算完成。每張要寫理由，影響到當週的會在資料檢查列一行。`prepare` 剛抓完 Jira 時找不到列的卡直接失敗（卡號打錯或卡不見了）；`metrics` 重算與 `finalize` 放行並在 stderr 印一行警告。metrics.json 還把排除的卡算成完成（之後才加進排除）時 finalize 直接失敗，要先重跑 `metrics <週>`。目前列的是 2026-09-23、24 從 Squad 8 移轉時補建的 8 張。
- **整棵排除**：`config/excluded.json` 的 `epics` 列的 epic 連同底下所有卡（一般卡、子任務、Kick off）在算之前就移出：不列專案、不算數字、不做 Kick off 鎖定與資料檢查；`data/<週>/snapshot.json` 照存原始的全部卡片。只能列 epic，列到別的卡直接失敗；`prepare` 剛抓完 Jira 時找不到列的 epic 也直接失敗（卡號打錯或 epic 不見了）；`metrics` 重算與 `finalize` 放行並在 stderr 印一行警告，因為 epic 可能比那一期的 snapshot 晚建。有設定整棵排除時，子任務的父卡不在 snapshot 就直接失敗（父卡一定同專案，找不到是抓取不一致，判斷不出它屬不屬於被排除的 epic）；一般卡掛在 snapshot 外的 epic 底下照常列 parent_missing 資料檢查，掛在排除清單裡的 epic 底下則一起移出。finalize 驗 narrative 卡號時同樣扣掉這些卡；metrics.json 還列著排除的 epic（之後才加進排除）時 finalize 直接失敗，要先重跑 `metrics <週>`；週報頁與首頁都畫完才寫檔。本週有完成卡被拿掉時在資料檢查列一行帶理由。目前列的是 FDEPROJECT-119 行政作業、CS-7 開工確認。已發布的舊期不回溯重產：加進排除清單之前發布的那一期（例：2026-W39 含 CS-7）維持原樣，它的 narrative 可能引用後來才排除的卡，重產要先重寫 narrative。
- **累計**：snapshot 抓的是兩個專案的全部卡片，累計直接從同一份 snapshot 重算到本期結束（`resolutiondate` 早於本期結束的完成卡），不讀舊期的 metrics。每張完成卡標上它完成的那一週。
- **人**：執行人規則同 Owner。工時與「進行中」只算葉節點；一個人的 Token 是他名下所有完成卡（含父卡）的用量。
- **Token**：讀 `data/tokens/<week>.json`，格式 `{"CS-17": 12000}`（卡號對 token 數）。檔案裡沒有的卡號代表這期沒有記錄到用量。單張卡沒有記錄顯示「—」；專案、交付、Owner 的彙總只加有記錄的卡，一張都沒有才顯示「—」；Total Token 是檔案裡全部值的總和。整個檔案不存在時全部顯示「—」，不顯示 0。累計 Token 是 `data/tokens/` 裡週次不晚於本期的檔案逐卡相加；檔名不是 `YYYY-Www.json` 就直接失敗，不安靜少算一週。

## 頁面

讀者是主管。三個頁籤，預設打開「本週」，網址加 `#cumulative`、`#week`、`#team` 可以直連：

- **持續累積**：累計的實際工時、Token、交付、完成卡片；專案列表（含已結案的 epic）；每人累計；依週分組的完成卡片。
- **本週**：本週重點；資源；專案列表（進度、結果、Kick off 有填的欄位、進行中的卡）；每人本週完成與進行中；依支援單位；依專案與交付單位分組的完成卡片。
- **團隊**：一人一張卡，本週與累計的工時、Token、各專案時數，本週完成與進行中的卡。「未指派」也有一張，列出沒有執行人的卡。

頁面底部是名詞定義（本週、累計、完成卡片、交付、實際工時等的口徑，正本在 `render.py` 的 `_glossary`）、資料檢查（未填工時、缺 Kick off、白名單外的 label、排除說明等）與歷史各期。Decision Needed 不顯示。沒有 JS 時三個頁籤全部攤開。

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

第一次讀到時鎖定目標五段（Objective、Why It Matters、Before、After、Evidence）：之後頁面一律顯示鎖定版本。空的段落之後補上算正常填寫；已經填過的段落再被改，頁面會標示「鎖定後被修改」。

Decision Needed 與 Next Milestone 不鎖：決定做完、里程碑到了就直接改，頁面每週顯示當次的內容，不算修改。決定做完就把 Decision Needed 清空。Kick off 卡不見時這兩段是空的；重算舊的一期時，這兩段取那一期的描述。

## 本機執行

憑證讀環境變數 `JIRA_SITE`、`JIRA_EMAIL`、`JIRA_API_TOKEN`，沒有的話讀 `~/.config/jira-ops/env`。抓資料前先打 `/rest/api/3/myself` 確認 Jira 認得這組憑證：email 或 token 錯時查詢不會回 401，而是當成匿名回 0 張卡，所以要先驗。

```bash
python3 scripts/weekly.py prepare --week-ending 2026-09-22   # 印出 2026-W39
python3 scripts/weekly.py finalize 2026-W39                   # 需要先有 narrative.json
python3 -m unittest discover -s tests -t tests                # 門檻測試
```

## Vercel 設定

Import 這個 repo，Framework Preset 選 Other，Output Directory 由 `vercel.json` 指定為 `site`，不需要 build command。只有 `site/` 會公開，`data/` 不會。
