# クリーナー採用予測レポート システムガイド

## 仕組み概要

「レポート送って」と言うと Claude Code が以下を自動実行する。

```
[Claude Code]
    ↓ Google Drive MCP で読み込み
[Google スプレッドシート]
    ↓ 物件データを /tmp/pipeline_props.json に書き出し
[Python スクリプト]
    ↓ Redash から稼働データを取得
[Redash query 3029 / 3023]
    ↓ 採用目安を計算して送信
[Slack #resi_クリーナー採用予測レポート]
```

---

## データソース

| ソース | 役割 | 場所 |
|--------|------|------|
| Google スプレッドシート | パイプライン物件（開業予定・室数・住所） | fileId: `1TMmeh5nw-...` gid=1994830091 |
| Redash query 3029 | エリア別 既存採用目安 | redash.unito.me |
| Redash query 3023 | エリア別 月間CO率・CL完了数（楽観値算出に使用） | redash.unito.me |

---

## スプレッドシートの読み取りルール

抽出対象：ステータス列（col2）= `B_PJT進捗` または `C_口頭合意`

| 列（0始まり） | 内容 |
|--------------|------|
| col2 | ステータス |
| col4 | 物件名 |
| col5 | 対象室数 |
| col8 | 建物住所 |
| col30 | 販売可能日 賃貸 |
| col31 | 販売可能日 宿泊 |

**開業日の優先順位：**
1. col30（賃貸）が未来の YYYY/MM/DD → `type: "賃貸"` として採用
2. col30 が過去または空 → col31（宿泊）が有効なら `type: "宿泊"` として採用（レポートに「（宿泊）」表示）
3. 両方なければ除外

**カットオフ：** 今日から約3ヶ月先の月末まで（動的に計算）

---

## 採用目安の計算式

```
月間CO予測 = 対象室数 × エリアCO率（query 3023 から動的取得）

現行    = CEIL(月間CO予測 ÷ フロア値5.3)
楽観値  = CEIL(月間CO予測 ÷ 上位25%CL完了数)  ← query 3023 から動的算出（現在26件/人）
中間値  = CEIL((現行 + 楽観値) ÷ 2)
```

**フロア値5.3について：**
CL数3人以上エリアの直近12ヶ月平均から設定した政策的下限値。固定。
半年〜1年ごとに手動で見直す（`compute_area_metrics` 関数内の `max(..., 5.3)` を変更）。

---

## 優先度絵文字・urgency

| 条件 | 絵文字 |
|------|--------|
| 合計5人以上 または urgencyあり | 🔴 `:red_circle:` |
| 合計3〜4人 | 🟡 `:large_yellow_circle:` |
| 合計1〜2人 | 🟢 `:large_green_circle:` |

| urgency条件 | ラベル |
|------------|--------|
| 開業まで31日以内 | `急ぎ・開業1ヶ月前` |
| 開業まで60日以内 | `開業2ヶ月前` |

---

## ファイル構成

```
/Users/marina.y/cleaner-report/
├── scripts/
│   └── send_cleaner_report.py   # メインスクリプト
├── .env                          # 環境変数（API キー）
├── routine_prompt_v2.txt         # 「レポート送って」時の手順メモ
└── SYSTEM_GUIDE.md               # このファイル

/tmp/pipeline_props.json          # Claude が毎回書き出す中間ファイル
```

**.env に必要な環境変数：**
```
REDASH_API_KEY=...
SLACK_BOT_TOKEN=...
GROQ_API_KEY=...
```

---

## メンテナンス

### 物件を除外したい
`send_cleaner_report.py` の `EXCLUDED_PROPERTIES` セットに物件名を追加。

```python
EXCLUDED_PROPERTIES = {
    "ミラージュパレス日本橋Cloud",
    # ここに追加
}
```

### エリアマッピングを追加・修正したい
`send_cleaner_report.py` の `get_area(addr)` 関数を編集。
建物住所のキーワードで `(都道府県, エリア)` を返す。

```python
if "文京区" in addr: return ("東京", "文京")  # 例
```

エリアを追加したら Redash query 3029 / 3023 にもそのエリアのデータが入っているか確認。

### フロア値（5.3）を見直したい
`compute_area_metrics` 関数内：

```python
clp = max(sum(cl_list) / len(cl_list) if cl_list else 5.3, 5.3)
#                                                        ^^^ここを変更
```

### Slack 送信先を変えたい
`send_cleaner_report.py` の `SLACK_CHANNEL` を変更。

```python
SLACK_CHANNEL = "C0B3LFX6RLH"  # #resi_クリーナー採用予測レポート
```

### 集計期間（3ヶ月）を変えたい
`three_month_end()` 関数の `timedelta(days=92)` を変更。

---

## 送信先

- **Slack チャンネル：** `#resi_クリーナー採用予測レポート`（`C0B3LFX6RLH`）
- **ダッシュボード：** https://redash.unito.me/dashboard/-_11

## Slack キャンバス

- `F0B3QL62N0N`：運用マニュアル（セクション1〜7）
- `F0B3RAB7HTQ`：CLタイプ別稼働実績＆採用後目標

---

## 手動実行コマンド

```bash
cd /Users/marina.y/cleaner-report && set -a && source .env && set +a && python3 scripts/send_cleaner_report.py
```

※ 「レポート送って」と言えば Claude Code が Google Sheets 読み込みから送信まで全自動で行う。
