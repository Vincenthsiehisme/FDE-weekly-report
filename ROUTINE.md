# 雲端 routine 執行步驟

這份是雲端 Claude Code routine 每週要照做的步驟，routine 的 prompt 只需要寫「照 ROUTINE.md 執行」。

- 排程：每週二 15:59 UTC（台北 23:59），cron `59 15 * * 2`
- 環境變數：`JIRA_SITE`、`JIRA_EMAIL`、`JIRA_API_TOKEN`，設在雲端環境 `fde-weekly-report` 的 Environment variables（claude.ai/code 輸入框上方的環境選單 → 編輯該環境），不在 routine 設定頁，也不寫進 repo。值不加引號、不加 `<>`；token 錯時第 3 步會報「Jira 不接受這組憑證」
- 寫入範圍：只有 `data/` 與 `site/`，只 push 到 `main`

任何一步失敗就停下來回報錯誤訊息，不要跳過、不要用別的方式補救、不要 commit 部分結果。

## 步驟

1. **對齊 main。** 平台可能把 session checkout 在 `claude/*` 臨時分支，先切回來：

   ```bash
   git fetch origin
   git checkout main
   git merge --ff-only origin/main
   ```

2. **跑測試門檻。** 紅就停。

   ```bash
   python3 -m unittest discover -s tests -t tests
   ```

3. **抓資料、算指標。** 排程在 23:59 啟動，`--wait-for-period-end` 會等到期間結束（週三 00:00:30）才抓，最後幾十秒完成的卡才不會漏。這一步可能要等一分多鐘，執行時 timeout 至少設 5 分鐘。最後一行 stdout 是這期的 week id。

   ```bash
   WEEK=$(python3 scripts/weekly.py prepare --wait-for-period-end)
   ```

4. **寫 `data/$WEEK/narrative.json`。** 讀 `data/$WEEK/metrics.json`，照下面〈narrative 規則〉寫。

5. **驗證並產出頁面。**

   ```bash
   python3 scripts/weekly.py finalize "$WEEK"
   ```

   驗證失敗會列出每一條問題。照訊息修 `narrative.json` 再跑，最多三次；三次都失敗就停，不 commit。

6. **commit 並 push。**

   ```bash
   git add data site
   git commit -m "data: 週報 $WEEK"
   git push origin main
   ```

7. **回報**：week id、本週完成張數、資料檢查項數、commit SHA。

## narrative 規則

格式固定兩個欄位：

```json
{
  "highlights": ["[專案名] 一句話", "..."],
  "epic_results": {"<epic id>": "一句話"}
}
```

- `highlights`：本週重點，本週有完成的專案一個一條，條數不能多也不能少（2026-10-07 使用者要求「完成多少就寫多少」）。順序：先是 `epics` 裡 `active` 為 true 的專案，照 `epics` 陣列的順序；再來是 `completed` 裡 `group` 不在 `epics` 的專案（所屬 epic 先前已結案），照它在 `completed` 第一次出現的順序。每條以「[專案名]」開頭，專案名照 `epics[].summary` 原文（已結案的取 `cumulative.epics[].summary`），驗證器逐條比對開頭與順序。寫法照範本「[專案名] 完成 XXX，已達到 XXX」。本週沒有任何完成的卡（`totals.completed` 是 0）時必須是空陣列。
- `epic_results`：`metrics.json` 的 `epics` 裡每個 `active` 為 true 的專案各一句，key 用它的 `id`（未歸屬 epic 的 id 是 `_unassigned`），不能多也不能少。
- 寫的是本週：`cumulative` 與 `people` 是給頁面用的累計與人力資料，不要拿累計數字寫進本週重點或專案結果。
- 不寫「累計完成 N 張」「進度 N%」：`epics[].progress` 是到本期結束為止的累計進度，不是本週的成果；`percent` 是同一個累計進度的百分比。
- `migrated` 是改組前在其他專案完成、之後才搬進來的卡，不算本週成果，卡號與數字都不寫進任何文字。
- 只根據 `metrics.json` 裡的卡片摘要、完成清單與數字寫。不推測影響、不補背景、不寫卡片上沒有的成果。
- 任何數值都必須由程式算，不得自己算或挑（2026-09-30 使用者裁）。數字只能用 `metrics.json` 裡程式算好的本週值：`totals` 的 `projects`、`completed`、`hours`、`tokens`、`unestimated_completed`（`stalled`、`kpis`、`health`、`allocation` 不能寫；「停滯」「補登」「照流程走」「投入比例」四個詞也不能出現，驗證器會擋）、`active` 專案的 `week`、`completed`、`people[].week`、`labels[].week`、`owners`；累計、進度、改組前的數字驗證器會擋。一律寫阿拉伯數字（「2 項」不寫「兩項」），不寫日期、不自己算百分比或差值。驗證器會擋下本週數字以外的數字，以及接量詞的中文數字；只有「第一次」「第一週」這類「第一」與「一次性」不算數量，可以寫。其他序數寫阿拉伯數字，例如「第 3 週」，那個數字也要是本週數字。
- 卡號只能引用 snapshot 裡存在、而且不在 `config/excluded.json` 整棵排除的 epic 底下的卡。
- 每條不超過 200 字。
