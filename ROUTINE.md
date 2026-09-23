# 雲端 routine 執行步驟

這份是雲端 Claude Code routine 每週要照做的步驟，routine 的 prompt 只需要寫「照 ROUTINE.md 執行」。

- 排程：每週二 15:59 UTC（台北 23:59），cron `59 15 * * 2`
- 環境變數：`JIRA_SITE`、`JIRA_EMAIL`、`JIRA_API_TOKEN`，設在 routine 的環境設定，不寫進 repo
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

3. **抓資料、算指標。** 最後一行 stdout 是這期的 week id。

   ```bash
   WEEK=$(python3 scripts/weekly.py prepare)
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

- `highlights`：本週重點 1 到 3 條，寫法照範本「[專案名] 完成 XXX，已達到 XXX」。本週沒有任何完成的卡（`totals.completed` 是 0）時必須是空陣列。
- `epic_results`：`metrics.json` 的 `epics` 裡每個 `active` 為 true 的專案各一句，key 用它的 `id`（未歸屬 epic 的 id 是 `_unassigned`），不能多也不能少。
- 只根據 `metrics.json` 裡的卡片摘要、完成清單與數字寫。不推測影響、不補背景、不寫卡片上沒有的成果。
- 數字只能用 `metrics.json` 裡出現的值，一律寫阿拉伯數字（「2 項」不寫「兩項」），不寫日期、不自己算百分比或差值。驗證器會擋下 metrics 裡沒有的數字，以及接量詞的中文數字。
- 卡號只能引用 snapshot 裡存在的卡。
- 每條不超過 200 字。
