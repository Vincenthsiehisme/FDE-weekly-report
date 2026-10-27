# FDE weekly report

每週二 23:59（UTC+8）由雲端 Claude Code routine 從 Jira（`FDEPROJECT`、`CS`）產出 FDE 週報，發布成靜態網頁（Vercel，發布 `site/`）。

## 流程

```
prepare   抓 Jira → data/<week>/snapshot.json → 算數字 → data/<week>/metrics.json   程式
narrate   AI 讀 metrics.json，寫 data/<week>/narrative.json                       AI（routine）
finalize  驗證 narrative 的卡號與數字 → site/index.html、site/reports/<week>.html   程式
publish   commit data/ 與 site/，push main，Vercel 自動部署                          routine
```

數字全部由程式算，AI 只寫「本週重點」（本週有完成的專案一個一條）和每個專案一句「結果」。驗證器擋下兩種編造：不存在或已排除的卡號、metrics 裡沒有的數字。

## 規則

- **報告期間**：上週三 00:00 到本週二 23:59:59（UTC+8）。由執行當下的日期往回找最近的週二決定，排程晚幾分鐘跨到週三仍是同一期。
- **當週完成**：狀態類別 done，且 `resolutiondate` 落在期間內。
- **實際工時**：Story point estimate（`customfield_10016`），由執行人完工後回填實際花費時數（2026-09-30 使用者確認），只加葉節點。有子卡的卡自己的點數不算，epic 與 Kick off 卡不算。總覽叫「累計工時」，口徑相同。
- **完成卡片**：只數完成的葉節點，跟實際工時同一批卡；有子卡的父卡不算一張，它的張數與工時都計在子卡（2026-10-07 使用者裁）。交付不再算。
- **進度**：每個專案到本期結束為止算進完成卡片的葉節點數／epic 底下全部葉節點數。排除與改組前的卡不算完成、仍在分母，所以 Jira 上全部完成的專案可能不到 100%（2026-10-07 使用者裁）。本週頁與總覽同一個數。
- **專案**：以 epic 分組；不在 epic 底下的卡歸「未歸屬 epic」。先前就完成的 epic 不再列。
- **Status**：epic 已完成為 Done，其餘 On Track。
- **Owner**：卡片自己的 assignee。子任務沒有 assignee 時用父卡的；一般卡沒有 assignee 就是「未指派」，不往上算給 epic 負責人。
- **label**：專案的 label 取 epic 在 Jira 上的 label，不設白名單；不在 epic 底下的卡用自己的 label；不是恰好一個 label 的歸「未分類」。epic 不是恰好一個 label、或底下卡片的 label 跟 epic 不同，列進資料檢查（2026-10-07 使用者裁，同日 Jira 上已補齊）。label 表每列：本週實際工時、累計工時、epic 數；各列加總等於工時總數，epic 數加總等於總覽專案列表的 epic 數。
- **排除**：`config/excluded.json` 列的卡不算完成，本週與累計的張數、工時、進度、人、label 都不算。每張要寫理由，影響到當週的會在資料檢查列一行。`prepare` 剛抓完 Jira 時找不到列的卡直接失敗（卡號打錯或卡不見了）；`metrics` 重算與 `finalize` 放行並在 stderr 印一行警告。metrics.json 還把排除的卡算成完成（之後才加進排除）時 finalize 直接失敗，要先重跑 `metrics <週>`。目前列 FDEPROJECT-75（2026-09-24 在本專案補建即結案，changelog 看不出搬入）與 FDEPROJECT-114～118（2026-09-30 路線取消，轉完成只為結案，沒有交付）；09-23 從 Squad 8 搬進來時已結案的 7 張改由「改組前」規則處理。
- **改組前**：卡片在 Jira 的結案時間早於或等於它從其他專案搬進 FDEPROJECT／CS 的時間，是改組前在別的專案完成的歷史，本週與累計的完成卡片、實際工時、進度、人、label 都不算，也不列資料檢查，只在總覽「完成」清單最後的「改組前」分隔線之後灰字列出。搬進來之後才結案的照算；FDEPROJECT 與 CS 之間互搬不算搬入；同一張卡也列在 `excluded.json` 的 `cards` 時以排除清單為準（照常列資料檢查）。`prepare` 用 `/rest/api/3/changelog/bulkfetch` 抓完成工作卡的專案異動，搬入時間存在 snapshot 的 `moved_in`（snapshot schema 2）；schema 1 的舊 snapshot 不能重算 metrics，要重跑 `prepare`。
- **整棵排除**：`config/excluded.json` 的 `epics` 列的 epic 連同底下所有卡（一般卡、子任務、Kick off）在算之前就移出：不列專案、不算數字、不做 Kick off 鎖定與資料檢查；`data/<週>/snapshot.json` 照存原始的全部卡片。只能列 epic，列到別的卡直接失敗；`prepare` 剛抓完 Jira 時找不到列的 epic 也直接失敗（卡號打錯或 epic 不見了）；`metrics` 重算與 `finalize` 放行並在 stderr 印一行警告，因為 epic 可能比那一期的 snapshot 晚建。有設定整棵排除時，子任務的父卡不在 snapshot 就直接失敗（父卡一定同專案，找不到是抓取不一致，判斷不出它屬不屬於被排除的 epic）；一般卡掛在 snapshot 外的 epic 底下照常列 parent_missing 資料檢查，掛在排除清單裡的 epic 底下則一起移出。finalize 驗 narrative 卡號時同樣扣掉這些卡；metrics.json 還列著排除的 epic（之後才加進排除）時 finalize 直接失敗，要先重跑 `metrics <週>`；週報頁與首頁都畫完才寫檔。本週有完成卡被拿掉時在資料檢查列一行帶理由。目前列的是 FDEPROJECT-119 行政作業、CS-7 開工確認。已發布的舊期不回溯重產：加進排除清單之前發布的那一期（例：2026-W39 含 CS-7）維持原樣，它的 narrative 可能引用後來才排除的卡，重產要先重寫 narrative。本週重點 2026-10-07 起一個專案一條，W40 以前的 narrative 只有 1 到 3 條，重產那幾期一樣要先照新規則重寫 highlights，finalize 會直接擋下。
- **累計**：snapshot 抓的是兩個專案的全部卡片，累計直接從同一份 snapshot 重算到本期結束（`resolutiondate` 早於本期結束的完成卡），不讀舊期的 metrics。每張完成卡標上它完成的那一週。
- **團隊**（人）：執行人規則同 Owner。完成卡片、工時與「進行中」只算葉節點；一個人的 Token 是他名下所有完成卡（含父卡）的用量。
- **照流程走**（2026-10-28 使用者鎖定，snapshot schema 3；metrics schema 8 起拿掉同日鎖定的補登，使用者裁不算）：結案前待過「進行中」（轉進或轉出都算，直接進審核中的不算）。只看累計完成卡片那批葉節點，逐週算在 metrics 的 `health`；本週頁的比例與上週值取同一份序列。snapshot 照存 `created`，metrics 不再讀。`prepare` 用 changelog bulkfetch 抓完成與進行中工作卡的狀態異動（跟專案異動同一次），存成 snapshot 的 `progress_seen` 與 `status_since`（進入目前狀態的時間，建卡後沒轉過就是建立時間）；schema 2 以前的 snapshot 不能重算 metrics，要用 `prepare --week-ending <週二>` 重抓。
- **停滯**：epic 未結案，底下的卡（不含 epic 自己與 Kick off）14 天沒有任何更新，含只改 label 的更新；底下沒有卡就看 epic 自己。天數是最後更新到本期結束，抓取比期間結束晚幾分鐘時算 0 天。metrics 的 `epics[].idle_days`、`stalled` 與 `totals.stalled`；上週的停滯數讀上一期 metrics.json 的 `totals.stalled`，上一期沒有檔或是 schema 6 就是 null、頁面寫「沒有上週的值」。
- **投入比例**：每人每週先把自己完成的葉節點換成 100%（人內用實際工時當權重，沒填的卡補他這週的平均，整週都沒填就用張數），再對當週有產出的人取平均；只在人內換算，不拿小時跨人比。前 7 個 label 照最近 4 週的占比排，其餘併「其他」。metrics 的 `allocation`；人卡的累計比例在 `people[].shares`。
- **人**（原「專案投入」表，2026-10-07 併成總覽專案列表的一欄）：每個專案累計由幾個人產出，名字列在數字旁；本週一覽表的負責人是本週有產出的人，本週沒人產出就寫 epic 的負責人。人數是完成葉節點的執行人去重（規則同 Owner，跟工時同一個口徑），父卡的執行人沒有自己完成的葉節點就不算；未指派不算人，工時照加並標「另有未指派」；未填工時的張數接在累計工時下面。metrics.json 的 `staffing`（schema 5 起；schema 6 拿掉 `deliverables` 與 `units`，加 `labels`、`excluded` 與新的進度口徑；目前是 schema 8，見照流程走、停滯、投入比例三條）本週與累計都還算，頁面只讀累計人數與本週人名。
- **Token**：讀 `data/tokens/<week>.json`，格式 `{"CS-17": 12000}`（卡號對 token 數）。檔案裡沒有的卡號代表這期沒有記錄到用量。單張卡沒有記錄顯示「—」；專案、父卡、Owner 的彙總只加有記錄的卡，一張都沒有才顯示「—」；Total Token 是檔案裡全部值的總和。這一期的檔不存在、較早期有檔時，這一期全部顯示「—」，不顯示 0；`data/tokens/` 一個檔都沒有時 Token 的 KPI 格與各欄整個不顯示，頁面上不留任何說明（2026-10-07 使用者裁，取代原本「資源區留一行」）。累計 Token 是 `data/tokens/` 裡週次不晚於本期的檔案逐卡相加；檔名不是 `YYYY-Www.json` 就直接失敗，不安靜少算一週。

## 頁面

讀者是團隊負責人本人（2026-10-28 使用者裁，取代 2026-09-23「主管 30 秒看完」；主管看同一頁）。三個頁籤，預設打開「本週」，網址加 `#cumulative`（總覽）、`#week`、`#team` 可以直連：

表格的數字欄固定寬，每一列的欄位都跟表頭對齊；展開記號全頁一種（▸ 收合、▾ 展開）；手機上總覽的人與累計工時排同一行（2026-10-07 總覽 UI 盤點）。

視覺化改版（2026-10-07 使用者裁）：進度條分三段（之前完成｜本週完成｜未完成，深色是本週），總覽與本週同一條；依 label 是累計工時橫條（本週段深色）。團隊績效評估（2026-10-28 使用者鎖定）：人一律照名字排、只跟自己的前幾週比，三個頁籤都不放人與人並排的長條或表（每人填工時與切卡的粒度不同，並排比的是填法）。圖都是 inline SVG 或純 CSS，沒有 JS 也顯示數字與圖例。

- **總覽**（2026-10-07 由「持續累積」改名）：依序是資源（累計工時、Token、累計完成卡片、epic 數，epic 數是專案列表裡的 epic，不含未歸屬 epic）；依 label 橫條；專案列表依 label 分段（含已結案的 epic，未歸屬 epic 放最後），每列有三段進度條、人（累計人數與名字）、累計工時，展開看 Kick off 鎖定的目標五段，預設收合，Kick off 一段都沒填的列不給展開，缺 Kick off 列在資料檢查；完成卡片依週、再依專案分組，兩層都預設收合。
- **本週**（2026-10-28 改版；同日第二輪拿掉補登、改桌機寬版，主欄 1280px）：三個數字（停滯專案是主數字、唯一用警示色並連到下方清單；有完成的專案；照流程走；各附上週值，百分比的差寫「個百分點」）；四格圖（專案地圖：x 完成進度、y 幾天沒動、停滯區與停滯專案的名字和負責人；力氣花在哪：近 6 週投入比例；產出：每週完成張數；資料健康：照流程走逐週）；「這週要你出手的地方」三類原因列（停滯專案、進行中超過 14 天的卡、Kick off 寫了 Decision Needed 的專案），有內容就展開；人卡（負責的未結案專案、近 6 週完成小長條，每根標週次與張數、近 4 週落在哪些 label、進行中的卡與天數）；最後是收合的專案一覽表，依 label 分段，一列一個 epic：負責人（本週有產出的人）、三段進度條、本週 +N、本週工時、訊號（停滯 N 天，否則本週無完成）、下一步（Kick off 的 Next Milestone 第一行），展開看 AI 寫的結果句與進行中的卡。本週重點 highlights 照舊由 routine 寫、finalize 驗，頁面不再獨立列。完成卡片逐張在總覽依週看。
- **團隊**（2026-10-28 改成累計版人卡；同日第二輪桌機兩欄）：一人一張卡，累計張數與累計工時、Token、逐週完成小長條（只跟自己比，每根標週次與張數，超過 12 週改窄格、隔幾根標一次）、負責中的專案逐列列出、已結案的收合、累計投入比例、進行中的卡與天數。「未指派」也有一張，列出沒有執行人的卡。

頁面底部是名詞定義（本週、累計、完成卡片、實際工時、進度、label、停滯、照流程走、投入比例等的口徑，正本在 `render.py` 的 `_glossary`）、資料檢查（未填工時、缺 Kick off、Kick off 鎖定後被修改、label 不一致、排除說明等）與歷史各期。Decision Needed 只在本週頁的「這週要你出手的地方」列，專案列與 Kick off 展開都不顯示。沒有 JS 時三個頁籤全部攤開。

## Kick off 描述格式

每個 epic 底下放一張 Kick off 卡（父系設為該 epic），描述照這個格式寫。標籤要一字不差、放在行首，冒號全半形都可以；標籤也可以單獨當標題，內容寫在下面：

```
Objective：這個專案要達成什麼
Why It Matters：為什麼重要
Before/After
Before：現在的狀況
After：完成後的狀況
Evidence：可行性：開案前查到、證明做得到的事實
完成證明：在什麼環境、看到什麼條件成立就算完成
Decision Needed：需要誰決定什麼
Next Milestone：下一個里程碑
```

Evidence 寫兩行：「可行性：」接開案前實查過的事實（帶查證日期與方式），「完成證明：」接驗收條件。兩個小標都不是段落標籤，第二行是 Evidence 的接續行，頁面原樣顯示兩行。進度（PR、測試數、審查輪次）不要寫進來，目標五段會鎖定，進度寫進去就成了過期資訊。鎖定以整段為單位，兩行要一起寫齊；只寫得出一行就整段留空，先寫一行之後再補，會被標成鎖定後被修改。

第一次讀到時鎖定目標五段（Objective、Why It Matters、Before、After、Evidence）：之後頁面一律顯示鎖定版本。空的段落之後補上算正常填寫；已經填過的段落再被改，資料檢查會列一行「鎖定後被修改」，頁面不顯示提示（2026-10-07 使用者裁）。

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
