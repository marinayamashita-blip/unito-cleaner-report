import math
import os
import json
import requests
from datetime import datetime, timezone, timedelta

REDASH_BASE_URL = "https://redash.unito.me"
REDASH_API_KEY = os.environ["REDASH_API_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL = "C0B3LFX6RLH"
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(tzinfo=None)


def fetch_query_results(query_id):
    url = f"{REDASH_BASE_URL}/api/queries/{query_id}/results"
    headers = {"Authorization": f"Key {REDASH_API_KEY}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()["query_result"]["data"]["rows"]


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.split(" ")[0])
    except Exception:
        return None


def three_month_end():
    target = TODAY + timedelta(days=92)
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


def compute_patterns(current, monthly_co):
    p25 = math.ceil((monthly_co or 0) / 25)
    middle = math.ceil((current + p25) / 2)
    return int(current), int(middle), int(p25)


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
            comment = parts[1].strip()
            comments[area] = comment
    return comments


def build_report(data_3029, data_3024):
    cutoff = three_month_end()

    # Group new properties by area (filter to opening within 3 months)
    new_props_by_area = {}
    for r in data_3024:
        d = parse_date(r.get("開業見込", ""))
        if not d:
            continue
        if d <= TODAY or d > cutoff:
            continue
        area = r.get("エリア", "")
        new_props_by_area.setdefault(area, []).append(r)

    # LLM comments for areas with new properties
    lm_input = []
    for r in data_3029:
        area = r.get("エリア", "")
        if area in new_props_by_area and (r.get("合計採用目安") or 0) > 0:
            props = new_props_by_area[area]
            total_co = sum(p.get("追加月間CO予測") or 0 for p in props)
            new_current = r.get("新規開業追加") or 0
            _, middle, p25 = compute_patterns(new_current, total_co)
            opening_months = [parse_date(p.get("開業見込", "")) for p in props]
            nearest = min((d for d in opening_months if d), default=None)
            lm_input.append({
                "エリア": area,
                "新規採用目安_現行": new_current,
                "新規採用目安_中間値": middle,
                "新規採用目安_25件": p25,
                "開業月": nearest.strftime("%Y年%m月") if nearest else "",
            })
    comments = generate_comments(lm_input)

    # Build area sections
    today_str = TODAY.strftime("%Y-%m-%d")
    cutoff_str = f"{cutoff.year}年{cutoff.month}月末"

    header = (
        f":bar_chart: *クリーナー採用予測レポート｜{today_str}*\n"
        f"_集計期間：直近12ヶ月 ／ パイプライン：〜{cutoff_str}の開業予定物件を含む_\n"
        "_ダッシュボード：https://redash.unito.me/dashboard/-_11_\n"
        "\n"
        "> :bulb: *3パターンの見方*\n"
        "> • *現行*：エリア平均CL生産性（フロア5.3件/人/月）ベース。保守的な上限値。\n"
        "> • *中間値*：現行と25件/人の平均。現実的な目標値として活用可。\n"
        "> • *25件/人*：月25件こなせる想定の楽観値。生産性目標達成時の必要人数。\n"
        "\n"
        ":dart: *エリア別 採用目安（優先度順）*"
    )

    sections = []
    summary_rows = []

    for r in sorted(data_3029, key=lambda x: -(x.get("合計採用目安") or 0)):
        total = r.get("合計採用目安") or 0
        existing = r.get("既存採用目安") or 0
        new_current_raw = r.get("新規開業追加") or 0
        area = r.get("エリア", "")
        pref = r.get("都道府県", "")
        props = new_props_by_area.get(area, [])

        if total == 0:
            sections.append(f":information_source: *{area}（{pref}）｜0人*（既存CLで対応可能）")
            continue

        if not props:
            emoji = priority_emoji(existing, None)
            sections.append(f"{emoji} *{area}（{pref}）｜既存のみ {existing}人*")
            summary_rows.append({
                "エリア": area,
                "既存": existing,
                "新規現行": 0, "新規中間": 0, "新規25": 0,
                "合計現行": existing, "合計中間": existing, "合計25": existing,
            })
            continue

        total_co = sum(p.get("追加月間CO予測") or 0 for p in props)
        p_current, p_middle, p_25 = compute_patterns(new_current_raw, total_co)

        opening_dates = [parse_date(p.get("開業見込", "")) for p in props]
        nearest = min((d for d in opening_dates if d), default=None)
        urgency = urgency_label(nearest)
        emoji = priority_emoji(total, urgency)

        lines = [f"{emoji} *{area}（{pref}）*" + (f" :warning: _{urgency}_" if urgency else "")]
        lines.append(f"既存：{existing}人")
        lines.append(f"新規追加　｜　現行：*{p_current}人*　中間値：*{p_middle}人*　25件/人：*{p_25}人*")

        for p in props:
            name = p.get("物件名", "")
            rooms = int(p.get("対象室数") or 0)
            d = parse_date(p.get("開業見込", ""))
            month = f"{d.year}年{d.month}月" if d else ""
            co = int(p.get("追加月間CO予測") or 0)
            lines.append(f":round_pushpin: 新規物件：{name}（{rooms}室・{month}開業・月間CO +{co}件）")

        comment = comments.get(area, "")
        if comment:
            lines.append(f":bulb: {comment}")

        sections.append("\n".join(lines))
        summary_rows.append({
            "エリア": area,
            "既存": existing,
            "新規現行": p_current, "新規中間": p_middle, "新規25": p_25,
            "合計現行": existing + p_current, "合計中間": existing + p_middle, "合計25": existing + p_25,
        })

    # Summary table
    table_lines = [":pushpin: *サマリー*"]
    table_lines.append("エリア\t既存\t新規（現行）\t新規（中間値）\t新規（25件/人）\t合計（現行）\t合計（中間値）\t合計（25件/人）")
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
    table_lines.append("_※ 中間値 = (現行 + 25件/人) ÷ 2 の切り上げ。新規開業物件の追加月間CO予測をもとに算出。_")

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
    print("Fetching Redash data...")
    data_3029 = fetch_query_results(3029)
    data_3024 = fetch_query_results(3024)

    print("Building report...")
    report = build_report(data_3029, data_3024)
    print(report)

    print("Sending to Slack...")
    send_slack_message(report)
    print("Done!")
