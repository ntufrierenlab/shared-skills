
## 專案進度報表（project-status skill）

`STATUS.md` 是給外部蒐集器每小時讀的專案進度報表，由
`~/.claude/skills/project-status/scripts/status_report.py`（Codex：`~/.codex/skills/project-status/...`）
從 logbook、jobs、`status_sources.json` 自動產生，**不要手改**。

root session 在這四個時點必須執行 `note`（只有 root 寫，worker 不寫）：
1. 開新一輪 → `note --next "..."`
2. 派出長時間訓練前 → `note --next "..."`
3. 遇到錯誤、停損、等待使用者或 GPU → `note --blocked "..."`
4. 一輪 accept 完成或阻礙解除 → `note --clear-blocked --next "..."`

數字不手打：常看的指標加進 `status_sources.json`（檔案 + 欄位路徑），由腳本抄。
STATUS.md 的數字未經盲重算，不得作為提案依據。詳見 skill `project-status`。
