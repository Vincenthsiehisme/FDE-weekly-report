# AGENTS.md

給在這個 repo 工作的 agent（Claude Code、Codex）與人。

## 這個 repo 做什麼

每週從 Jira（`FDEPROJECT`、`CS`）抓資料，程式算出所有數字，AI 只寫兩種短文字，驗證通過後產出靜態 HTML，由 Vercel 發布 `site/`。流程與每一步的責任見 `README.md`，雲端排程的步驟見 `ROUTINE.md`。

## 門檻測試

只有一支，CI 跑的也是這支：

```bash
python3 -m unittest discover -s tests -t tests
```

測試檔在 `tests/test_*.py`，只用 Python 標準函式庫，最低支援 Python 3.9。

## 分支與 merge

- 目標分支 `origin/main`。人寫的碼一律開分支走 PR，命名 `<type>/<英文命題短句>`，merge commit，不 force push `main`。
- commit message：`<type>: <為什麼>`，前綴英文、內容繁中。

## 機器產物（例外：routine 直推 main）

下面這些路徑只由雲端 routine 產生並直接 push 到 `main`，人不要手改，要改就改產生它的程式：

| 路徑 | 內容 |
|---|---|
| `data/<YYYY-Www>/snapshot.json` | 當期 Jira 快照 |
| `data/<YYYY-Www>/metrics.json` | 程式算出的所有數字 |
| `data/<YYYY-Www>/narrative.json` | routine 的 AI 寫的文字段 |
| `data/kickoff/<epic>.json` | Kick off 鎖定狀態 |
| `site/index.html`、`site/reports/<YYYY-Www>.html` | 產出的頁面 |

人維護、走 PR 的設定：`config/units.json`（支援單位白名單）、`config/excluded.json`（不算完成的卡，每張寫理由）、`data/tokens/<YYYY-Www>.json`（Token 用量，來源待定）。

## 不能做的事

- 不寫 Jira。這個 repo 對 Jira 只讀。
- 憑證只在環境變數或本機 `~/.config/jira-ops/env`，不進任何檔案、commit、PR 說明。
- 不用 fallback 掩蓋錯誤：抓不到、驗不過就讓它失敗，不產出部分結果。
- `site/` 是公開網址的內容。卡片標題會原樣公開，不要把憑證或個資寫進卡片標題。
