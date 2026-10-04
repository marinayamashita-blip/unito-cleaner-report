import math
import os
import json
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

REDASH_BASE_URL = "https://redash.unito.me"
REDASH_API_KEY = os.environ["REDASH_API_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL = "C0B3LFX6RLH"
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
PIPELINE_PROPS_PATH = "/tmp/pipeline_props.json"

# CL1人あたり月間完了数の下限（フロア値）。半年〜1年ごとに手動で見直す。
# 2026-10-04: ZensWork直近12ヶ月・個人CL・副業型の中央値（直近2ヶ月稼働ゼロの離脱者を除く）
FLOOR_PER_CL = 8.5

# 採用予測から除外する物件名（手動管理）
EXCLUDED_PROPERTIES = {
    "ミラージュパレス日本橋Cloud",
    "TakaMatsu Residense 南船場",
    "ZONE SHINSAIBASHI WEST",
    "TOKYO β 駒沢大学3",
    "Grand STAY 博多駅北",
    "ガーデンザヴィス南蒲田",
    "ガーデンザヴィス木場",
    "ウエリスアーバン水天宮前",
}

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(tzinfo=None)


# 都道府県の短縮形 → 住所中の正式表記 (query_3016の area.都道府県 に相当)
_PREF_FULL = [
    ("北海道", "北海道"), ("青森", "青森県"), ("岩手", "岩手県"), ("宮城", "宮城県"),
    ("秋田", "秋田県"), ("山形", "山形県"), ("福島", "福島県"), ("茨城", "茨城県"),
    ("栃木", "栃木県"), ("群馬", "群馬県"), ("埼玉", "埼玉県"), ("千葉", "千葉県"),
    ("東京", "東京都"), ("神奈川", "神奈川県"), ("新潟", "新潟県"), ("富山", "富山県"),
    ("石川", "石川県"), ("福井", "福井県"), ("山梨", "山梨県"), ("長野", "長野県"),
    ("岐阜", "岐阜県"), ("静岡", "静岡県"), ("愛知", "愛知県"), ("三重", "三重県"),
    ("滋賀", "滋賀県"), ("京都", "京都府"), ("大阪", "大阪府"), ("兵庫", "兵庫県"),
    ("奈良", "奈良県"), ("和歌山", "和歌山県"), ("鳥取", "鳥取県"), ("島根", "島根県"),
    ("岡山", "岡山県"), ("広島", "広島県"), ("山口", "山口県"), ("徳島", "徳島県"),
    ("香川", "香川県"), ("愛媛", "愛媛県"), ("高知", "高知県"), ("福岡", "福岡県"),
    ("佐賀", "佐賀県"), ("長崎", "長崎県"), ("熊本", "熊本県"), ("大分", "大分県"),
    ("宮崎", "宮崎県"), ("鹿児島", "鹿児島県"), ("沖縄", "沖縄県"),
]

def _get_pref(addr):
    """住所から都道府県短縮名を返す。"""
    for short, full in _PREF_FULL:
        if full in addr:
            return short
    # 都道府県名なしで市から始まる場合（例: 福岡市博多区...）
    for short, _ in _PREF_FULL:
        if addr.startswith(short):
            return short
    return None


def _area_from_addr(pref, addr):
    """
    query_3016の非東京エリア計算ロジック（Pythonで再実装）:
    都道府県・市名（区名）形式で返す。
    """
    市_idx = addr.find("市")
    if 市_idx == -1:
        return pref

    区_idx = addr.find("区")

    # start: 県/道/府/都 or 都道府県名の直後
    start = None
    for ch in ["県", "道", "府", "都"]:
        i = addr.find(ch)
        if 0 <= i < 市_idx:
            start = i + 1
            break
    if start is None:
        i = addr.find(pref)
        if 0 <= i < 市_idx:
            start = i
        else:
            start = max(0, 市_idx - len(pref))

    if 区_idx != -1 and 区_idx > 市_idx:
        # 市の後に区あり: 市名+区名まで
        return pref + "・" + addr[start:区_idx + 1]
    elif start == 市_idx:
        # 市川市 などの「市始まり」の市名
        next_市 = addr.find("市", 市_idx + 1)
        if next_市 != -1:
            return pref + "・" + addr[市_idx:next_市 + 1]
        return pref + "・" + addr[市_idx:市_idx + 1]
    else:
        # 通常の市名
        return pref + "・" + addr[start:市_idx + 1]


def get_area(addr):
    if not addr:
        return (None, None)

    pref = _get_pref(addr)
    if not pref:
        return (None, None)

    # 東京: query_3016の東京23区ゾーンCASEを適用（auto-sync対象）
    if pref == "東京":
        # --- BEGIN TOKYO AREA MAPPING (auto-synced from query_3016) ---
        if "渋谷区" in addr or "恵比寿" in addr or "目黒区" in addr or "代官山" in addr: return ("東京", "渋谷・恵比寿")
        if "新宿区" in addr or "高田馬場" in addr or "早稲田" in addr: return ("東京", "新宿・高田馬場")
        if "豊島区" in addr or "池袋" in addr: return ("東京", "池袋")
        if "板橋区" in addr: return ("東京", "板橋")
        if "港区" in addr or "麻布" in addr or "赤坂" in addr or "六本木" in addr: return ("東京", "港区")
        if "中央区" in addr or "築地" in addr or "銀座" in addr or "日本橋" in addr: return ("東京", "築地・銀座")
        if "台東区" in addr or "浅草" in addr or "上野" in addr: return ("東京", "浅草・上野")
        if "品川区" in addr or "大崎" in addr: return ("東京", "品川")
        if "世田谷区" in addr: return ("東京", "世田谷")
        if "杉並区" in addr or "中野区" in addr: return ("東京", "杉並・中野")
        if "文京区" in addr: return ("東京", "文京区")
        if "千代田区" in addr: return ("東京", "千代田区")
        if "北区" in addr: return ("東京", "北区")
        if "練馬区" in addr: return ("東京", "練馬")
        if "墨田区" in addr or "江東区" in addr: return ("東京", "墨田・江東")
        if "荒川区" in addr or "足立区" in addr: return ("東京", "足立・荒川")
        if "葛飾区" in addr or "江戸川区" in addr: return ("東京", "葛飾・江戸川")
        if "大田区" in addr: return ("東京", "大田区")
    # --- END TOKYO AREA MAPPING ---
        return ("東京", "東京")  # 多摩地区など区に該当しない場合

    # 非東京: query_3016の動的市区名抽出
    return (pref, _area_from_addr(pref, addr))


def fetch_query_results(query_id):
    url = f"{REDASH_BASE_URL}/api/queries/{query_id}/results"
    headers = {"Authorization": f"Key {REDASH_API_KEY}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()["query_result"]["data"]["rows"]


def load_pipeline_props():
    """Load pipeline data written by Claude from Google Sheets."""
    if not os.path.exists(PIPELINE_PROPS_PATH):
        raise FileNotFoundError(
            f"パイプラインデータが見つかりません: {PIPELINE_PROPS_PATH}\n"
            "「レポート送って」と言うと Claude が Google Sheets から自動で読み込みます。"
        )
    with open(PIPELINE_PROPS_PATH) as f:
        return json.load(f)


def parse_date(s):
    if not s or str(s).strip() in ("-", "○", "×", ""):
        return None
    s = str(s).strip().split(" ")[0].replace("/", "-")
    if len(s) == 7:  # YYYY-MM
        s += "-01"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def two_month_end():
    target = TODAY + timedelta(days=61)
    next_month = target.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)


def urgency_label(opening_date):
    if not opening_date:
        return None
    days = (opening_date - TODAY).days
    if days <= 31:
        return "急ぎ・開業1ヶ月前"
    if days <= 60:
        return "開業2ヶ月前"
    return None


def priority_emoji(total, urgency):
    if total >= 5 or urgency == "急ぎ・開業1ヶ月前":
        return ":red_circle:"
    if total >= 3:
        return ":large_yellow_circle:"
    return ":large_green_circle:"


def compute_area_metrics(data_3023):
    """Compute CO-per-room and CL-completion-rate per area, plus global optimistic value."""
    raw = defaultdict(lambda: {"co": [], "cl": []})
    for r in data_3023:
        area = r.get("エリア", "")
        pref = r.get("都道府県", "")
        if not area or area == "その他":
            continue
        k = (pref, area)
        co = r.get("チェックアウト数", 0) or 0
        prop = r.get("物件数", 0) or 0
        if prop > 0:
            raw[k]["co"].append(co / prop)
        cl = r.get("クリーナー1人あたり完了数", 0) or 0
        if cl > 0:
            raw[k]["cl"].append(cl)

    metrics = {}
    for k, v in raw.items():
        co_list, cl_list = v["co"], v["cl"]
        cop = sum(co_list) / len(co_list) if co_list else None
        clp = max(sum(cl_list) / len(cl_list) if cl_list else FLOOR_PER_CL, FLOOR_PER_CL)
        metrics[k] = (cop, clp)

    all_cl = sorted([
        r.get("クリーナー1人あたり完了数", 0) or 0
        for r in data_3023
        if (r.get("クリーナー1人あたり完了数") or 0) > 0
        and r.get("エリア", "") not in ("", "その他")
    ])
    top_25 = all_cl[int(len(all_cl) * 0.75):]
    optimistic_val = int(round(sum(top_25) / max(len(top_25), 1))) if top_25 else 25

    return metrics, optimistic_val


def build_pipeline_by_area(pipeline_props, area_metrics, optimistic_val, cutoff):
    """Group pipeline properties by area and compute 3-scenario hiring estimates."""
    by_area = defaultdict(list)
    for p in pipeline_props:
        if any(ex in p.get("name", "") for ex in EXCLUDED_PROPERTIES):
            continue
        d = parse_date(p.get("opening", ""))
        if not d or d <= TODAY or d > cutoff:
            continue
        pref, area = get_area(p.get("address", ""))
        if not area:
            continue
        by_area[(pref, area)].append({
            "name": p["name"],
            "rooms": int(p.get("rooms", 0)),
            "opening": d,
            "type": p.get("type", "賃貸"),
        })

    # エリア固有データがない場合の全エリア平均CO率
    all_cops = [v[0] for v in area_metrics.values() if v[0] is not None]
    global_cop = sum(all_cops) / len(all_cops) if all_cops else None

    result = {}
    for (pref, area), props in by_area.items():
        cop, clp = area_metrics.get((pref, area), (None, FLOOR_PER_CL))
        if not cop:  # None または 0（実績なし）は全エリア平均で推定
            cop = global_cop
        monthly_co = sum(p["rooms"] * (cop or 0) for p in props)
        p_cur = math.ceil(monthly_co / clp) if monthly_co > 0 else 0
        p_25 = math.ceil(monthly_co / optimistic_val) if monthly_co > 0 and optimistic_val > 0 else 0
        p_mid = math.ceil((p_cur + p_25) / 2)
        result[(pref, area)] = {
            "props": props,
            "cop": cop,
            "monthly_co": monthly_co,
            "p_cur": p_cur,
            "p_25": p_25,
            "p_mid": p_mid,
        }
    return result


def generate_comments(areas_data):
    if not areas_data:
        return {}
    prompt = (
        "以下のエリアについて、採用担当者向けに簡潔な採用アクションコメントを1文ずつ生成してください。\n"
        "「エリア名: コメント」の形式で返してください。中間値を基準に採用目標を設定する提案を含め、開業の緊急度も考慮してください。\n\n"
        f"データ:\n{json.dumps(areas_data, ensure_ascii=False)}"
    )
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    comments = {}
    for line in text.strip().split("\n"):
        if ":" in line:
            parts = line.split(":", 1)
            area = parts[0].strip().strip("*").strip("・").strip()
            comments[area] = parts[1].strip()
    return comments


def build_report(data_3029, area_metrics, optimistic_val, pipeline_props):
    cutoff = two_month_end()
    pipeline_by_area = build_pipeline_by_area(pipeline_props, area_metrics, optimistic_val, cutoff)

    existing_by_area = {}
    for r in data_3029:
        area = r.get("エリア", "")
        pref = r.get("都道府県", "")
        if area and pref:
            existing_by_area[(pref, area)] = int(r.get("既存採用目安", 0) or 0)

    # 既存採用目安が0かつパイプラインもないエリアは除外（エリア名変更後の孤立エントリ対策）
    all_keys = {
        k for k in set(existing_by_area.keys()) | set(pipeline_by_area.keys())
        if existing_by_area.get(k, 0) > 0 or k in pipeline_by_area
    }

    def area_total(key):
        return existing_by_area.get(key, 0) + pipeline_by_area.get(key, {}).get("p_cur", 0)

    sorted_keys = sorted(all_keys, key=lambda k: -area_total(k))

    lm_input = []
    for key in sorted_keys:
        pref, area = key
        pm = pipeline_by_area.get(key, {})
        if area_total(key) > 0 and pm:
            nearest = min((p["opening"] for p in pm["props"]), default=None)
            lm_input.append({
                "エリア": area,
                "新規採用目安_現行": pm["p_cur"],
                "新規採用目安_中間値": pm["p_mid"],
                f"新規採用目安_{optimistic_val}件": pm["p_25"],
                "開業月": nearest.strftime("%Y年%m月") if nearest else "",
            })
    comments = generate_comments(lm_input)

    today_str = TODAY.strftime("%Y-%m-%d")
    cutoff_str = f"{cutoff.year}年{cutoff.month}月末"
    header = (
        f":bar_chart: *クリーナー採用予測レポート｜{today_str}*\n"
        f"_集計期間：直近12ヶ月 ／ パイプライン：〜{cutoff_str}の開業予定物件を含む（2ヶ月先末まで）_\n"
        "_ダッシュボード：https://redash.unito.me/dashboard/-_11_\n"
        "\n"
        "> :bulb: *3パターンの見方*\n"
        f"> • *現行*：エリア平均CL生産性（フロア{FLOOR_PER_CL}件/人/月）ベース。保守的な上限値。\n"
        f"> • *中間値*：現行と{optimistic_val}件/人の平均。現実的な目標値として活用可。\n"
        f"> • *{optimistic_val}件/人*：月{optimistic_val}件こなせる想定の楽観値。生産性目標達成時の必要人数。\n"
        "\n"
        ":dart: *エリア別 採用目安（優先度順）*"
    )

    sections = []
    summary_rows = []

    for key in sorted_keys:
        pref, area = key
        ex = existing_by_area.get(key, 0)
        pm = pipeline_by_area.get(key, {})
        total = area_total(key)

        p_cur = pm.get("p_cur", 0)
        p_mid = pm.get("p_mid", 0)
        p_25 = pm.get("p_25", 0)
        props = pm.get("props", [])
        cop = pm.get("cop")

        if total == 0 and not props:
            sections.append(f":information_source: *{area}（{pref}）｜0人*（既存CLで対応可能）")
            continue

        if not props:
            emoji = priority_emoji(ex, None)
            sections.append(f"{emoji} *{area}（{pref}）｜既存のみ {ex}人*")
            summary_rows.append({
                "エリア": area, "既存": ex,
                "新規現行": 0, "新規中間": 0, "新規25": 0,
                "合計現行": ex, "合計中間": ex, "合計25": ex,
            })
            continue

        nearest = min((p["opening"] for p in props), default=None)
        urgency = urgency_label(nearest)
        emoji = priority_emoji(total, urgency)

        lines = [f"{emoji} *{area}（{pref}）*" + (f" :warning: _{urgency}_" if urgency else "")]
        if ex > 0:
            lines.append(f"既存：{ex}人")
        lines.append(f"新規追加　｜　現行：*{p_cur}人*　中間値：*{p_mid}人*　{optimistic_val}件/人：*{p_25}人*")

        for p in props:
            month = f"{p['opening'].year}年{p['opening'].month}月"
            co = int(p["rooms"] * (cop or 0))
            co_str = f"・月間CO +{co}件" if co > 0 else ""
            ptype = p.get("type", "賃貸")
            if ptype == "賃貸開業済み・宿泊":
                type_label = "（賃貸開業済み・宿泊）"
            elif ptype == "宿泊":
                type_label = "（宿泊）"
            else:
                type_label = ""
            lines.append(f":round_pushpin: 新規物件：{p['name']}{type_label}（{p['rooms']}室・{month}開業{co_str}）")

        comment = comments.get(area, "")
        if comment:
            lines.append(f":bulb: {comment}")

        sections.append("\n".join(lines))
        summary_rows.append({
            "エリア": area, "既存": ex,
            "新規現行": p_cur, "新規中間": p_mid, "新規25": p_25,
            "合計現行": ex + p_cur, "合計中間": ex + p_mid, "合計25": ex + p_25,
        })

    table_lines = [":pushpin: *サマリー*"]
    table_lines.append(
        f"エリア\t既存\t新規（現行）\t新規（中間値）\t新規（{optimistic_val}件/人）"
        f"\t合計（現行）\t合計（中間値）\t合計（{optimistic_val}件/人）"
    )
    totals = {k: 0 for k in ["既存", "新規現行", "新規中間", "新規25", "合計現行", "合計中間", "合計25"]}
    for row in summary_rows:
        table_lines.append(
            f"{row['エリア']}\t{row['既存']}人\t{row['新規現行']}人\t{row['新規中間']}人\t{row['新規25']}人"
            f"\t{row['合計現行']}人\t{row['合計中間']}人\t{row['合計25']}人"
        )
        for k in totals:
            totals[k] += row.get(k, 0)
    table_lines.append(
        f"合計\t{totals['既存']}人\t{totals['新規現行']}人\t{totals['新規中間']}人\t{totals['新規25']}人"
        f"\t{totals['合計現行']}人\t{totals['合計中間']}人\t{totals['合計25']}人"
    )
    table_lines.append(
        f"_※ 中間値 = (現行 + {optimistic_val}件/人) ÷ 2 の切り上げ。"
        "新規開業物件の月間CO予測をもとに算出。楽観値は直近12ヶ月の上位25%平均（動的）。_"
    )

    parts = [header] + sections + ["\n".join(table_lines)]
    return "\n\n".join(parts)


def send_slack_message(text):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    payload = {"channel": SLACK_CHANNEL, "text": text, "mrkdwn": True}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Slack error: {result.get('error')}")
    print(f"Slack message sent: ts={result.get('ts')}")


if __name__ == "__main__":
    print("Loading pipeline data from Google Sheets...")
    pipeline_props = load_pipeline_props()
    print(f"  {len(pipeline_props)} properties loaded")

    print("Fetching Redash data...")
    data_3029 = fetch_query_results(3029)
    data_3023 = fetch_query_results(3023)
    area_metrics, optimistic_val = compute_area_metrics(data_3023)
    print(f"  楽観値: {optimistic_val}件/人")

    print("Building report...")
    report = build_report(data_3029, area_metrics, optimistic_val, pipeline_props)
    print(report)

    print("Sending to Slack...")
    send_slack_message(report)
    print("Done!")
