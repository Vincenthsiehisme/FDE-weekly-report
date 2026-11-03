# FDE-weekly-report：沒做完的事

這份只寫「已知、還沒排進任何 plan」的事。做到的那個 PR 同時刪掉那一條：結案靠 git，刪掉就算解掉，留著過期的條目比沒有清單更糟。

每一條有一個不會變的名字，沒有編號。節裡固定三樣：一段說明、一行錨點、一行來源。錨點是這件事碰到的地方，只收三種：反引號包的 repo 相對路徑、反引號包的函式名、規格正本的〈節名〉；開 plan、改碼、開 PR 時拿它比對，碰到了就浮出來問這次要不要做。來源是 vault 裡記下這件事的 plan。

登記用 vault 的 `checkpoint.py --open`，格式用 `checkpoint.py --backlog-check` 驗，不要手寫新條目。

- 規格正本：`README.md`

## `成效逐週變化`

成效說明每張卡畫成效最新值的逐週變化，取各期 snapshot，只畫數字型的值；W45 起累積到 4 期以上再做，碰 `scripts/lib/render.py` 的 `_dv_full`

- 錨點：`scripts/lib/render.py`
- 來源：jira-weekly-report · 成效說明多讀者改版
