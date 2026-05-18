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

# 採用予測から除外する物件名（手動管理）
EXCLUDED_PROPERTIES = {
    "ミラージュパレス日本橋Cloud",
    "TakaMatsu Residense 南船場",
}

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(tzinfo=None)


def get_area(addr):
    if not addr:
        return (None, None)
    if "大阪市北区" in addr: return ("大阪", "梅田・北区")
    if "大阪市淀川区" in addr: return ("大阪", "新大阪")
    if "大阪市中央区" in addr: return ("大阪", "心斎橋・本町")
    if "大阪市浪速区" in addr: return ("大阪", "難波")
    if "大阪市都島区" in addr: return ("大阪", "京橋")
    if "大阪市旭区" in addr: return ("大阪", "城北")
    if "大阪市西成区" in addr: return ("大阪", "西成")
    if "大阪市港区" in addr: return ("大阪", "大阪港")
    if "東大阪市" in addr: return ("大阪", "東大阪")
    if "渋谷区" in addr: return ("東京", "渋谷・恵比寿")
    if "新宿区" in addr: return ("東京", "新宿・高田馬場")
    if "豊島区" in addr: return ("東京", "池袋")
    if "墨田区" in addr: return ("東京", "錦糸町・押上")
    if "大田区" in addr: return ("東京", "蒲田・大田")
    if "江戸川区" in addr: return ("東京", "葛西")
    if "江東区" in addr: return ("東京", "亀戸・森下")
    if "板橋区" in addr: return ("東京", "板橋")
    if "港区" in addr: return ("東京", "港区")
    if "世田谷区" in addr: return ("東京", "世田谷")
    if "目黒区" in addr: return ("東京", "目黒")
    if "台東区" in addr: return ("東京", "浅草・上野")
    if "荒川区" in addr: return ("東京", "荒川")
    if "北区" in addr: return ("東京", "北区・田端")
    if "葛飾区" in addr: return ("東京", "葛飾")
    if "品川区" in addr: return ("東京", "品川")
    if "足立区" in addr: return ("東京", "足立")
    if "練馬区" in addr: return ("東京", "練馬")
    if "中野区" in addr: return ("東京", "中野")
    if "千代田区" in addr: return ("東京", "神田・秋葉原")
    if "中央区" in addr and "北海道" not in addr and "福岡" not in addr: return ("東京", "築地・銀座")
    if "横浜市" in addr: return ("神奈川", "横浜")
    if "鎌倉市" in addr or "逗子市" in addr: return ("神奈川", "鎌倉・逗子")
    if "箱根" in addr or "湯河原" in addr or "真鶴" in addr: return ("神奈川", "箱根・湯河原")
    if "藤沢市" in addr: return ("神奈川", "湘南・藤沢")
    if "小田原市" in addr: return ("神奈川", "小田原")
    if "市川市" in addr or "浦安市" in addr: return ("千葉", "行徳・浦安")
    if "富津市" in addr: return ("千葉", "富津")
    if "京都市下京区" in addr: return ("京都", "河原町・五条")
    if "京都市伏見区" in addr: return ("京都", "伏見")
    if "京都" in addr: return ("京都", "京都")
    if "福岡市博多区" in addr: return ("福岡", "博多")
    if "福岡市東区" in addr: return ("福岡", "福岡東区")
    if "福岡" in addr or "博多" in addr: return ("福岡", "福岡")
    if "那覇市" in addr: return ("沖縄", "那覇")
    if "沖縄市" in addr: return ("沖縄", "沖縄市")
    if "沖縄" in addr: return ("沖縄", "沖縄")
    if "札幌市" in addr or "北海道" in addr: return ("北海道", "札幌")
    if "函館市" in addr: return ("北海道", "函館")
    if "名古屋市" in addr or "愛知" in addr: return ("愛知", "名古屋")
    return (None, None)


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
    if total >= 5 or urgency:
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
        clp = max(sum(cl_list) / len(cl_list) if cl_list else 5.3, 5.3)
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
        if p.get("name", "") in EXCLUDED_PROPERTIES:
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

    result = {}
    for (pref, area), props in by_area.items():
        cop, clp = area_metrics.get((pref, area), (None, 5.3))
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
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
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

    all_keys = set(existing_by_area.keys()) | set(pipeline_by_area.keys())

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
        "> • *現行*：エリア平均CL生産性（フロア5.3件/人/月）ベース。保守的な上限値。\n"
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

        if total == 0:
            sections.append(f":information_source: *{area}（{pref}）｜0人*（既存CLで対応可能）")
            continue

        p_cur = pm.get("p_cur", 0)
        p_mid = pm.get("p_mid", 0)
        p_25 = pm.get("p_25", 0)
        props = pm.get("props", [])
        cop = pm.get("cop")

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
