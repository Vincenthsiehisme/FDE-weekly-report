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
- **實際工時**：Story point estimate（`customfield_10016`），由執行人完工後回填實際花費時數（2026-09-30 使用者確認），只加葉節點。有子卡的卡自己的點數不算，epic、Kick off 與 Deliverable 卡不算。總覽叫「累計工時」，口徑相同。
- **完成卡片**：只數完成的葉節點，跟實際工時同一批卡；有子卡的父卡不算一張，它的張數與工時都計在子卡（2026-10-07 使用者裁）。交付不再算。
- **進度**：每個專案到本期結束為止算進完成卡片的葉節點數／epic 底下全部葉節點數。排除與改組前的卡不算完成、仍在分母，所以 Jira 上全部完成的專案可能不到 100%（2026-10-07 使用者裁）。本週頁與總覽同一個數。
- **專案**：以 epic 分組；不在 epic 底下的卡歸「未歸屬 epic」。先前就完成的 epic 不再列。
- **Status**：epic 已完成為 Done，其餘 On Track。
- **Owner**：卡片自己的 assignee。子任務沒有 assignee 時用父卡的；一般卡沒有 assignee 就是「未指派」，不往上算給 epic 負責人。
- **label**：專案的 label 取 epic 在 Jira 上的 label，不設白名單；不在 epic 底下的卡用自己的 label；不是恰好一個 label 的歸「未分類」。epic 不是恰好一個 label、或底下卡片的 label 跟 epic 不同，列進資料檢查（2026-10-07 使用者裁，同日 Jira 上已補齊）。label 表每列：本週實際工時、累計工時、epic 數；各列加總等於工時總數，epic 數加總等於總覽專案列表的 epic 數。
- **排除**：`config/excluded.json` 列的卡不算完成，本週與累計的張數、工時、進度、人、label 都不算。每張要寫理由，影響到當週的會在資料檢查列一行。`prepare` 剛抓完 Jira 時找不到列的卡直接失敗（卡號打錯或卡不見了）；`metrics` 重算與 `finalize` 放行並在 stderr 印一行警告。metrics.json 還把排除的卡算成完成（之後才加進排除）時 finalize 直接失敗，要先重跑 `metrics <週>`。目前列 FDEPROJECT-75（2026-09-24 在本專案補建即結案，changelog 看不出搬入）與 FDEPROJECT-114～118（2026-09-30 路線取消，轉完成只為結案，沒有交付）；09-23 從 Squad 8 搬進來時已結案的 7 張改由「改組前」規則處理。
- **改組前**：卡片在 Jira 的結案時間早於或等於它從其他專案搬進 FDEPROJECT／CS 的時間，是改組前在別的專案完成的歷史，本週與累計的完成卡片、實際工時、進度、人、label 都不算，也不列資料檢查，只在總覽「完成」清單最後的「改組前」分隔線之後灰字列出。搬進來之後才結案的照算；FDEPROJECT 與 CS 之間互搬不算搬入；同一張卡也列在 `excluded.json` 的 `cards` 時以排除清單為準（照常列資料檢查）。`prepare` 用 `/rest/api/3/changelog/bulkfetch` 抓完成工作卡的專案異動，搬入時間存在 snapshot 的 `moved_in`（snapshot schema 2）；schema 1 的舊 snapshot 不能重算 metrics，要重跑 `prepare`。
- **整棵排除**：`config/excluded.json` 的 `epics` 列的 epic 連同底下所有卡（一般卡、子任務、Kick off、Deliverable）在算之前就移出：不列專案、不算數字、不做 Kick off 鎖定與資料檢查；`data/<週>/snapshot.json` 照存原始的全部卡片。只能列 epic，列到別的卡直接失敗；`prepare` 剛抓完 Jira 時找不到列的 epic 也直接失敗（卡號打錯或 epic 不見了）；`metrics` 重算與 `finalize` 放行並在 stderr 印一行警告，因為 epic 可能比那一期的 snapshot 晚建。有設定整棵排除時，子任務的父卡不在 snapshot 就直接失敗（父卡一定同專案，找不到是抓取不一致，判斷不出它屬不屬於被排除的 epic）；一般卡掛在 snapshot 外的 epic 底下照常列 parent_missing 資料檢查，掛在排除清單裡的 epic 底下則一起移出。finalize 驗 narrative 卡號時同樣扣掉這些卡；metrics.json 還列著排除的 epic（之後才加進排除）時 finalize 直接失敗，要先重跑 `metrics <週>`；週報頁與首頁都畫完才寫檔。本週有完成卡被拿掉時在資料檢查列一行帶理由。目前列的是 FDEPROJECT-119 行政作業、CS-7 開工確認。已發布的舊期不回溯重產：加進排除清單之前發布的那一期（例：2026-W39 含 CS-7）維持原樣，它的 narrative 可能引用後來才排除的卡，重產要先重寫 narrative。本週重點 2026-10-07 起一個專案一條，W40 以前的 narrative 只有 1 到 3 條，重產那幾期一樣要先照新規則重寫 highlights，finalize 會直接擋下。
- **累計**：snapshot 抓的是兩個專案的全部卡片，累計直接從同一份 snapshot 重算到本期結束（`resolutiondate` 早於本期結束的完成卡），不讀舊期的 metrics。每張完成卡標上它完成的那一週。
- **團隊**（人）：執行人規則同 Owner。完成卡片、工時與「進行中」只算葉節點；一個人的 Token 是他名下所有完成卡（含父卡）的用量。
- **照流程走**（2026-10-28 使用者鎖定，snapshot schema 3；metrics schema 8 起拿掉同日鎖定的補登，使用者裁不算；2026-11-02 起頁面不顯示，metrics 照算）：結案前待過「進行中」（轉進或轉出都算，直接進審核中的不算）。只看累計完成卡片那批葉節點，逐週算在 metrics 的 `health`；本週頁的比例與上週值取同一份序列。snapshot 照存 `created`，metrics 不再讀。`prepare` 用 changelog bulkfetch 抓完成與進行中工作卡的狀態異動（跟專案異動同一次），存成 snapshot 的 `progress_seen` 與 `status_since`（進入目前狀態的時間，建卡後沒轉過就是建立時間）；schema 2 以前的 snapshot 不能重算 metrics，要用 `prepare --week-ending <週二>` 重抓。
- **停滯**：epic 未結案，底下的卡（不含 epic 自己、Kick off 與 Deliverable）14 天沒有任何更新，含只改 label 的更新；底下沒有卡就看 epic 自己。天數是最後更新到本期結束，抓取比期間結束晚幾分鐘時算 0 天。metrics 的 `epics[].idle_days`、`stalled` 與 `totals.stalled`；總覽專案列表讀累計專案的同名欄（`cumulative.epics[]`，schema 13 起，未結案才有；已結案的帶 `closed_week`，結案那一期），同一個函式算；上週的停滯數讀上一期 metrics.json 的 `totals.stalled`，上一期沒有檔或是 schema 6 就是 null、頁面寫「沒有上週的值」。
- **沒動**（2026-11-02）：最後一次更新到本期結束幾天，抓取比期間結束晚幾分鐘時算 0 天。專案看底下的卡（同停滯），卡看它自己（metrics 的 `in_progress[].idle_days`），有 Decision Needed 的 Kick off 看這一期 epic 底下那張 Kick off 卡（`epics[].kickoff.idle_days`，這一期找不到卡是 null）。出手區與人卡都用這個；人的 `in_progress.ages`（進入目前狀態幾天）留在 metrics，頁面不用。
- **單卡工時**（2026-11-02）：一週裡完成且有填實際工時的葉節點，實際工時加總 ÷ 張數，一位小數；沒填的不算進平均，整週都沒填是 null。metrics 的 `health[].hours`、`health[].hours_per_card`。同日下午退出本週頁主圖：它反映那週誰結了卡與各自的填法（人員組成效應），不是快慢；頁面只在圖下資料品質一行寫有填、未填張數。
- **可運用人日**（2026-11-02，metrics schema 11；同日 schema 12 改算 epic、schema 14 改看最後一張卡）：`config/team.json` 成員名單裡每人在職期間（since～until，含）的工作日加總；工作日＝週一到週五扣 `config/holidays.json` 的 `off`、加 `workdays`（補班），一天 8 小時；請假不扣（使用者 2026-11-02 裁）。兩個檔都人維護、走 PR，缺檔、格式錯、期間碰到 `years` 沒列的年份都直接失敗。metrics 的 `health[].roster`（這週在職人數）、`person_days`、`epics_done`（這週完成的 epic 數：epic 在本期結束前已在 Jira 結案、底下至少一張完成工作卡，就算完成，卡都做完、epic 沒關的不算（2026-11-02 使用者裁「Done 才算，算最後卡週」，schema 15）；週次一律取底下最後一張完成卡那週，不看 epic 結案時間，因為集中去 Jira 關 epic 是行政動作；完成卻沒有結案時間的列 `epic_done_without_resolution`，近 6 週結案卻沒有完成工作卡的列 `epic_closed_without_work`；schema 13 以前叫 `epics_closed`、週次看結案時間）、`epics_per_person_week`（`epics_done` ÷ 可運用人週，人週＝可運用人日 ÷ 5）。2026-11-02 使用者裁「改算 epic」：消化速度以 epic 為完成單位，不數卡；同日改看最後一張卡（vault 的 jira-weekly-report · 消化速度口徑修正）；schema 11 的 `cards_per_person_day`、`median_cards` 拿掉，卡片速率要看就用 `completed` ÷ `person_days`。可運用只當產出的分母，不跟實際工時比；近 6 週裡一人一週的實際工時超過他當週可運用時數（工作日 × 8h）列進資料檢查 `hours_over_capacity`（使用者裁：代表填錯），有完成卡卻不在名單的人列 `not_in_team`。
- **探索人力占比**（2026-11-02；同日 metrics schema 15 改算 Kick off，決策在 vault 的 jira-weekly-report · 探索改算 Kick off 與 Spike）：`health[].explore_share`。探索是這週完成、有填工時的 Kick off 卡，其他完成工作卡（含 Spike）算開發；每個有產出的人一週先換成 100%（人內用工時當權重，同投入比例；沒填工時的 Kick off 不算，不補平均），再對有產出的人平均。Kick off 工時只進這裡，完成張數與實際工時照舊不算 Kick off。schema 14 以前探索是「這週第一次有完成卡的 epic」，那組新 epic 同期數字 `health[].epics_active`、`epics_new`、`new_hours_share`、`new_same_day` 照留在 metrics，頁面不用（這週沒有完成卡的人工時看不到、不進平均；`explore_people` 是算了幾個人，頁面寫「N／名單 M 人有完成卡」，這週跟上週人數不同時不寫差值，2026-11-02 使用者裁）；這週沒人產出是 null。
- **卡都做完，epic 沒關**（2026-11-02，同日使用者裁從「100% 待結案」改成白話）：epic 未結案、底下算進進度的卡全部完成。可能是做完忘了關，也可能還有工作沒開卡，資料上分不出來。本週頁未結案 epic 狀態依「卡都做完，epic 沒關」→ 停滯 → 本週有完成卡 → 其他 的順序歸一類，只看本週。程式裡的狀態代號仍是 `full`。
- **投入比例**：每人每週先把自己完成的葉節點換成 100%（人內用實際工時當權重，沒填的卡補他這週的平均，整週都沒填就用張數），再對當週有產出的人取平均；只在人內換算，不拿小時跨人比。前 7 個 label 先排本週有份額的（照本週占比），再照最近 4 週的占比補滿，其餘併「其他」（2026-11-02 起；原本只照近 4 週排，本週主力會被併進其他）。metrics 的 `allocation`；人卡的累計比例在 `people[].shares`。
- **人**（原「專案投入」表，2026-10-07 併成總覽專案列表的一欄）：每個專案累計由幾個人產出，名字列在數字旁；本週一覽表的負責人是本週有產出的人，本週沒人產出就寫 epic 的負責人。人數是完成葉節點的執行人去重（規則同 Owner，跟工時同一個口徑），父卡的執行人沒有自己完成的葉節點就不算；未指派不算人，工時照加並標「另有未指派」；未填工時的張數接在累計工時下面。metrics.json 的 `staffing`（schema 5 起；schema 6 拿掉 `deliverables` 與 `units`，加 `labels`、`excluded` 與新的進度口徑；目前是 schema 14，見照流程走、停滯、沒動、單卡工時、可運用人日、新 epic、投入比例）本週與累計都還算，頁面只讀累計人數與本週人名。
- **Token**：讀 `data/tokens/<week>.json`，格式 `{"CS-17": 12000}`（卡號對 token 數）。檔案裡沒有的卡號代表這期沒有記錄到用量。單張卡沒有記錄顯示「—」；專案、父卡、Owner 的彙總只加有記錄的卡，一張都沒有才顯示「—」；Total Token 是檔案裡全部值的總和。這一期的檔不存在、較早期有檔時，這一期全部顯示「—」，不顯示 0；`data/tokens/` 一個檔都沒有時 Token 的 KPI 格與各欄整個不顯示，頁面上不留任何說明（2026-10-07 使用者裁，取代原本「資源區留一行」）。累計 Token 是 `data/tokens/` 裡週次不晚於本期的檔案逐卡相加；檔名不是 `YYYY-Www.json` 就直接失敗，不安靜少算一週。

## 頁面

讀者是團隊負責人本人（2026-10-28 使用者裁，取代 2026-09-23「主管 30 秒看完」；主管看同一頁）。四個頁籤（2026-10-29 加成效說明），預設打開「本週」，網址加 `#cumulative`（總覽）、`#week`、`#team`、`#delivered`（成效說明）可以直連：

表格的數字欄固定寬，每一列的欄位都跟表頭對齊；展開記號全頁一種（▸ 收合、▾ 展開）；手機上總覽的人與累計工時排同一行（2026-10-07 總覽 UI 盤點）。

視覺化改版（2026-10-07 使用者裁）：進度條分三段（之前完成｜本週完成｜未完成，深色是本週），總覽與本週同一條；依 label 是累計工時橫條（本週段深色）。團隊績效評估（2026-10-28 使用者鎖定）：人一律照名字排、只跟自己的前幾週比，三個頁籤都不放人與人並排的長條或表（每人填工時與切卡的粒度不同，並排比的是填法）。圖都是 inline SVG 或純 CSS，沒有 JS 也顯示數字與圖例。

- **總覽**（2026-10-07 由「持續累積」改名；2026-11-02 總覽優化，使用者裁版型乙，決策在 vault 的 jira-weekly-report · 總覽優化）：起算說明（成員名單人數變過就接一句分界，例「W41 起名單 3→7 人：W39～W40 名單 3 人、有完成卡的 1～2 人，累計數字含這 2 週」，從 `health[].roster` 推）；資源（累計工時、Token、累計完成卡片、epic 數，epic 數是專案列表裡的 epic，不含未歸屬 epic，下面一定併列進行中、「卡都做完，epic 沒關」、已結案三個數，結案數不單獨出現）；依 label 橫條（右側數字欄固定寬，底軌右端對齊；上方一行寫累計含名單變動前的週）；專案列表依 label 分段（未歸屬 epic 放最後），段內先列進行中、依幾天沒動排（久的在上，跟出手區同一個時鐘，14 天以上警示色），再列「卡都做完，epic 沒關」（名稱旁標籤，也依幾天沒動排），已結案收在段尾的「已結案 N 個」（收合，每列寫「W42 結案」與累計工時，有已完成 Deliverable 的多一個「成效說明 ›」切到成效說明頁籤）；段標題寫各狀態個數與久沒動個數（用「·」分隔），列表前一行寫全部「卡都做完，epic 沒關」個數、進行中與「卡都做完，epic 沒關」裡各自幾個久沒動。未結案的列有三段進度條、幾天沒動、人（累計人數與名字）、累計工時，展開看 Kick off 鎖定的目標五段，預設收合，Kick off 一段都沒填的列不給展開，缺 Kick off 列在資料檢查；完成清單一週一列（週、名單人數、完成張數、實際工時，新的在上，名單人數變的兩週之間畫虛線分界），點開才看依專案分組的逐卡。
- **本週**（2026-10-28 改版；同日第二輪拿掉補登、改桌機寬版，主欄 1280px；2026-11-02 一圖一訊息，決策在 vault 的 jira-weekly-report · 本週頁一圖一訊息）：三個數字（2026-11-02 使用者裁「照審查改」，以消化速率為主軸；同日改算 epic：主數字標題「過去四週平均消化速度（epic／人週）」，4 週完成 epic ÷ 4 週可運用人週，兩位小數，跟上週的四週值比（上週值用這一期的資料重算，epic 後來補開新卡或重開，完成週會往後移，跟上週頁面寫的不同）；探索人力占比，旁邊寫幾個人有完成卡，跟上週人數相同才比、寫個百分點；停滯專案，唯一用警示色並連到下方清單）；兩張圖，標題是程式照數字組的觀察句（不寫原因），副標寫指標、範圍、期間：消化速度（單週每可運用人週完成 epic 數，柱下寫到那週為止的四週值，名單人數變的地方畫虛線分界，副標寫可運用人週、請假不扣、epic 在 Jira 結案才算完成並算在最後一張卡那週、epic 大小不一；名詞定義與頁面一律叫消化速度）、時間怎麼分（每週一根 100% 柱，探索在下、開發在上，柱下寫有完成卡的人數對名單，標題只在跟上週人數相同時比較）；圖下一行資料品質（有填、未填工時張數，超過可運用的人週數、不在名單的人數，連到資料檢查）。專案地圖、資料健康、照流程走 2026-11-02 上午拿掉；同日下午單卡工時、投入比例、未結案 epic 狀態、新 epic 個數的圖都拿掉：投入比例是「各單位分到幾個人」，一人全做某單位就是 1/人數，會被讀成全隊重點。「團隊投入狀況」人卡（2026-11-02 由「人」改名；版型甲，「未指派」不出卡：頂端近 6 週完成小長條，每根標週次與張數；左欄負責的未結案專案，狀態小標籤寫「卡都做完，epic 沒關」、14 天以上沒動、需要決定，超過 3 個收合；右欄近 4 週落在哪些 label，細條顏色每個 label 固定一色；進行中的卡收合，摘要寫幾張 14 天以上沒更新）；「需要關注」（2026-11-02 由「這週要你出手的地方」改名，跟人卡對調到人卡之後）一個專案一列：負責人、幾天沒動、進行中且 14 天沒更新的卡（展開看是哪幾張）、Kick off 寫的 Decision Needed（附 Kick off 卡寫了幾天），前三個條件同一個時鐘（最後一次更新到本期結束），第四個是「卡都做完，epic 沒關」（專案名旁標出），依幾天沒動排；沒動的卡不在任何未結案專案裡（未歸屬 epic、epic 已結案）也自成一列；最後是收合的專案一覽表，依 label 分段，一列一個 epic：負責人（本週有產出的人）、三段進度條、本週 +N、本週工時、訊號（停滯 N 天，否則本週無完成）、下一步（Kick off 的 Next Milestone 第一行），展開看 AI 寫的結果句與進行中的卡。本週重點 highlights 照舊由 routine 寫、finalize 驗，頁面不再獨立列。完成卡片逐張在總覽依週看。
- **團隊**（2026-10-28 改成累計版人卡；同日第二輪桌機兩欄）：一人一張卡，累計張數與累計工時、Token、逐週完成小長條（只跟自己比，每根標週次與張數，超過 12 週改窄格、隔幾根標一次）、負責中的專案逐列列出、已結案的收合、累計投入比例、進行中的卡與天數。「未指派」也有一張，列出沒有執行人的卡。
- **成效說明**（2026-10-29 加，決策在 vault 的 jira-weekly-report · 成效說明頁籤）：只列已完成的 Deliverable 卡，依完成時間所在的那一期分段、新的在上，一排兩張卡。卡上是 epic 卡號與標題、label、交付了什麼、主要成效「基準 → 最新」（其他成效寫「另 N 項」）、驗收幾條通過與未通過、交付日、Deliverable 卡號；epic 與 Deliverable 卡號都連到 Jira。主要成效底下放第一張圖的縮圖（同卡寬、高度上限 240px），點了看原圖，底下的檔名連到 Jira 附件（要登入），有多張寫「另 N 張見完整說明」（2026-11-03 加，推翻 10-29 不放圖，決策在 vault 的 jira-weekly-report · 成效說明顯示截圖）。成效沒填用琥珀色「成效未填」框。「完整說明」展開時那張卡佔整列，列出交付功能、成效日期與來源、驗收逐條、沒做的、畫面（全部圖的縮圖格，每張下面的檔名連 Jira；影片與超過 2 MB 的檔只列檔名連 Jira）、程式碼、開發環境、版本；Code、Env 只把 `https://` 開頭的做成連結。0 張時一句話取代清單；snapshot schema 3 的舊期重算時寫「這一期的資料沒有抓 Deliverable」。

頁面底部是名詞定義（本週、累計、完成卡片、實際工時、進度、label、停滯、沒動、可運用人日、消化速度、探索人力占比、卡都做完，epic 沒關、投入比例等的口徑，正本在 `render.py` 的 `_glossary`）、資料檢查（未填工時、缺 Kick off、Kick off 鎖定後被修改、label 不一致、排除說明等）與歷史各期。Decision Needed 只在本週頁的「需要關注」列，專案列與 Kick off 展開都不顯示。沒有 JS 時三個頁籤全部攤開。

## Deliverable 欄位

Deliverable 是 epic 交付時的成效說明，一個 epic 一張，規則正本在 jira-ops `references/deliverable.md`。`prepare` 跟 Kick off 一樣另查一次 Deliverable 卡，存進 snapshot 的 `deliverable`（snapshot schema 5；4 的附件只有檔名）；欄位 id 只在 FDEPROJECT 有效，對照在 `scripts/lib/deliverable.py` 的 `FIELDS`：交付了什麼、交付功能（一行一項）、沒做的（一行一項）、成效指標、成效基準值與日、成效最新值與日、成效資料來源、其他成效（「；」分隔）。畫面取附件：已完成 Deliverable 的 png、jpg、gif 附件、單張不超過 2 MB 的，`prepare` 下載到 `site/assets/deliverables/<附件 id>.<副檔名>` 公開顯示，同一個 id 存過就不重抓，下載的位元數或檔頭對不上就整個 `prepare` 失敗、這一期一個檔都不寫；其他附件只列檔名連 Jira。`finalize` 發現要顯示的圖不在 `site/` 就失敗，重算舊期之後用 `weekly.py images <週>` 補。圖是公開網址上的檔，個資守門在 Deliverable 規則（上傳前遮客人資料、轉完成前檢查），週報不另外過濾。程式只新增不刪圖：已發布的截圖要撤（例如發現沒遮），在 Jira 換附件（新 id），再走 PR 刪掉 `site/assets/deliverables/<舊 id>.<副檔名>`；舊期頁面那張會變破圖，git 歷史裡仍有，repo 是 private。附件下載那一跳 Jira 轉址到 api.media.atlassian.com，不帶憑證。版本取修正版本。描述只剩三行標籤 `Acceptance：`、`Code：`、`Env：`，寫法比照 Kick off。全部欄位選填；Deliverable 已完成卡沒填交付了什麼、交付功能、成效指標、基準值、最新值，描述缺標籤、重複、留著 `< >` 提示，母卡不是 epic，一個 epic 多張，2026-10-29 之後完成的 epic 沒有完成的 Deliverable，都列資料檢查、不擋週報。Deliverable 不算工作卡（PR #27）。

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

已發布的舊週不要為了補欄位整期重抓：`prepare` 拿的是當下的 Jira 狀態，期末後建立的卡、狀態與更新時間都會混進那一週（epic 列、進行中、幾天沒動、停滯、進度分母）；只有完成卡、工時與消化速度看結案時間不受影響（2026-11-02 W44）。Kick off 工時事後回填時用 `kickoff-hours`：只把那一期 snapshot 裡 Kick off 卡的工時換成 Jira 現值、重算 metrics，卡的範圍與狀態不動，snapshot 另記 `kickoff_points_fetched_at`；之後照常 `finalize`。

```bash
python3 scripts/weekly.py kickoff-hours 2026-W44
python3 scripts/weekly.py finalize 2026-W44
```

## Vercel 設定

Import 這個 repo，Framework Preset 選 Other，Output Directory 由 `vercel.json` 指定為 `site`，不需要 build command。只有 `site/` 會公開，`data/` 不會。
