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

# 採用目標の前提：週6件以上（1日3件×週2日〜）入れる人を採る → 6件 × 52週 ÷ 12ヶ月 = 26件/月（2026-10-04決定）
TARGET_PER_CL = 26
# 採用期限 = 開業日の何日前か（応募〜稼働3〜5日＋研修）
HIRE_LEAD_DAYS = 7

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


def _days_left(d):
    return (d.date() - TODAY.date()).days


def priority_emoji(hires, deadline):
    if deadline is not None and _days_left(deadline) <= 14:
        return ":red_circle:"
    if hires >= 5:
        return ":red_circle:"
    if hires >= 3:
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


def build_pipeline_by_month(pipeline_props, area_metrics, cutoff):
    """開業月×エリアごとに月間CO予測と採用目標人数（TARGET_PER_CL基準）を集計する。"""
    all_cops = [v[0] for v in area_metrics.values() if v[0] is not None]
    global_cop = sum(all_cops) / len(all_cops) if all_cops else None

    grouped = defaultdict(list)
    for p in pipeline_props:
        if any(ex in p.get("name", "") for ex in EXCLUDED_PROPERTIES):
            continue
        d = parse_date(p.get("opening", ""))
        if not d or d <= TODAY or d > cutoff:
            continue
        pref, area = get_area(p.get("address", ""))
        if not area:
            continue
        cop = area_metrics.get((pref, area), (None, None))[0] or global_cop or 0
        grouped[(d.year, d.month, pref, area)].append({
            "name": p["name"],
            "rooms": int(p.get("rooms", 0)),
            "opening": d,
            "type": p.get("type", "賃貸"),
            "co": int(p.get("rooms", 0)) * cop,
        })

    result = defaultdict(dict)
    for (y, m, pref, area), props in grouped.items():
        monthly_co = sum(p["co"] for p in props)
        hires = math.ceil(monthly_co / TARGET_PER_CL) if monthly_co > 0 else 0
        hires_ref = math.ceil(monthly_co / FLOOR_PER_CL) if monthly_co > 0 else 0
        deadline = min(p["opening"] for p in props) - timedelta(days=HIRE_LEAD_DAYS)
        result[(y, m)][(pref, area)] = {
            "props": sorted(props, key=lambda p: p["opening"]),
            "monthly_co": monthly_co,
            "hires": hires,
            "hires_ref": hires_ref,
            "deadline": deadline,
        }
    return result


def generate_comments(items):
    """items: [{"id": "A1", ...}]。戻り値は {id: コメント}。"""
    if not items:
        return {}
    prompt = (
        "以下の各項目（エリア×開業月）について、採用担当者向けに簡潔な採用アクションコメントを1文ずつ生成してください。\n"
        "「ID: コメント」の形式で、IDはそのまま返してください。採用目標人数と採用期限（今日からの残り日数）を踏まえた行動を書いてください。\n"
        "前提：採用するのは「清掃を週6件以上（1日3件×週2日〜）こなせるクリーナー」。この6件・3件は清掃件数であり、面接の件数ではない。"
        "面接数や応募数など、データにない数字は作らないこと。面談で週何日・1日何件入れるかを確認する、といった見極めの観点を含めてよい。\n\n"
        f"データ:\n{json.dumps(items, ensure_ascii=False)}"
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
        line = line.strip().lstrip("-•*").strip()
        if ":" in line:
            key, val = line.split(":", 1)
            comments[key.strip().strip("*")] = val.strip()
    return comments


def _fmt_md(d):
    return f"{d.month}/{d.day}"


def build_report(data_3029, area_metrics, pipeline_props):
    cutoff = two_month_end()
    by_month = build_pipeline_by_month(pipeline_props, area_metrics, cutoff)

    existing_by_area = {}
    for r in data_3029:
        area = r.get("エリア", "")
        pref = r.get("都道府県", "")
        n = int(r.get("既存採用目安", 0) or 0)
        if area and pref and n > 0:
            existing_by_area[(pref, area)] = n

    months = sorted(by_month.keys())

    # コメント生成用の入力（ID付き）
    lm_input, id_map = [], {}
    for (y, m) in months:
        for (pref, area), v in by_month[(y, m)].items():
            cid = f"A{len(lm_input) + 1}"
            id_map[(y, m, pref, area)] = cid
            lm_input.append({
                "id": cid,
                "エリア": area,
                "開業月": f"{y}年{m}月",
                "採用目標人数": v["hires"],
                "採用期限まで残り日数": _days_left(v["deadline"]),
            })
    comments = generate_comments(lm_input)

    today_str = TODAY.strftime("%Y-%m-%d")
    cutoff_str = f"{cutoff.year}年{cutoff.month}月末"
    header = (
        f":bar_chart: *クリーナー採用予測レポート｜{today_str}*\n"
        f"_対象：〜{cutoff_str}の開業予定物件 ／ ダッシュボード：https://redash.unito.me/dashboard/-_11_\n"
        "\n"
        "> :bulb: *採用目標の前提*\n"
        f"> • 週6件以上（1日3件×週2日〜）入れる人を採用 → 1人あたり月{TARGET_PER_CL}件で計算\n"
        f"> • 採用期限 = そのエリア・月で最も早い開業日の{HIRE_LEAD_DAYS}日前（応募〜稼働3〜5日＋研修）\n"
        "> • :red_circle: 期限まで14日以内 or 5人以上　:large_yellow_circle: 3〜4人　:large_green_circle: 1〜2人"
    )

    parts = [header]
    for (y, m) in months:
        rows = by_month[(y, m)]
        month_total = sum(v["hires"] for v in rows.values())
        sec = [f":calendar: *{y}年{m}月開業分｜採用目標 {month_total}人*"]
        for (pref, area), v in sorted(rows.items(), key=lambda kv: (kv[1]["deadline"], -kv[1]["hires"])):
            days_left = _days_left(v["deadline"])
            if days_left < 0:
                dl = f"期限 {_fmt_md(v['deadline'])}（:warning: 超過）"
            else:
                dl = f"期限 {_fmt_md(v['deadline'])}（あと{days_left}日）"
            emoji = priority_emoji(v["hires"], v["deadline"])
            lines = [f"{emoji} *{area}（{pref}）｜{v['hires']}人*　{dl}"]
            for p in v["props"]:
                ptype = p.get("type", "賃貸")
                type_label = {"賃貸開業済み・宿泊": "（賃貸開業済み・宿泊）", "宿泊": "（宿泊）"}.get(ptype, "")
                co_str = f"・月間CO +{int(p['co'])}件" if p["co"] > 0 else ""
                lines.append(
                    f"　:round_pushpin: {p['name']}{type_label}（{p['rooms']}室・{_fmt_md(p['opening'])}開業{co_str}）"
                )
            comment = comments.get(id_map[(y, m, pref, area)], "")
            if comment:
                lines.append(f"　:bulb: {comment}")
            sec.append("\n".join(lines))
        parts.append("\n\n".join(sec))

    if existing_by_area:
        ex_lines = [":recycle: *既存エリアの補充（開業とは別）*"]
        for (pref, area), n in sorted(existing_by_area.items(), key=lambda kv: -kv[1]):
            ex_lines.append(f"• {area}（{pref}）｜{n}人")
        parts.append("\n".join(ex_lines))

    # サマリー：エリア × 開業月
    areas = sorted(
        {k for mo in months for k in by_month[mo]} | set(existing_by_area),
        key=lambda k: -(sum(by_month[mo].get(k, {}).get("hires", 0) for mo in months) + existing_by_area.get(k, 0)),
    )
    month_cols = [f"{m}月開業" for (_, m) in months]
    table = [":pushpin: *サマリー（採用目標・人）*", "エリア\t" + "\t".join(month_cols) + "\t既存補充\t合計"]
    col_tot = [0] * len(months)
    ex_tot = grand = ref_total = 0
    for key in areas:
        vals = [by_month[mo].get(key, {}).get("hires", 0) for mo in months]
        ex = existing_by_area.get(key, 0)
        tot = sum(vals) + ex
        col_tot = [a + b for a, b in zip(col_tot, vals)]
        ex_tot += ex
        grand += tot
        ref_total += sum(by_month[mo].get(key, {}).get("hires_ref", 0) for mo in months) + ex
        table.append(f"{key[1]}\t" + "\t".join(str(x) for x in vals) + f"\t{ex}\t{tot}")
    table.append("合計\t" + "\t".join(str(x) for x in col_tot) + f"\t{ex_tot}\t{grand}")
    table.append(
        f"_※ 採用目標 = 月間CO予測 ÷ {TARGET_PER_CL}件（切り上げ）。"
        f"参考：平均的な副業CL（月{FLOOR_PER_CL}件）で採った場合は合計{ref_total}人。_"
    )
    parts.append("\n".join(table))

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
    area_metrics, _ = compute_area_metrics(data_3023)

    print("Building report...")
    report = build_report(data_3029, area_metrics, pipeline_props)
    print(report)

    print("Sending to Slack...")
    send_slack_message(report)
    print("Done!")
