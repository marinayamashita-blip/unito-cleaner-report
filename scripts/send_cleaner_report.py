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


def fetch_query_results(query_id):
    url = f"{REDASH_BASE_URL}/api/queries/{query_id}/results"
    headers = {"Authorization": f"Key {REDASH_API_KEY}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()["query_result"]["data"]["rows"]


def aggregate_by_area(rows):
    areas = {}
    for r in rows:
        area = r.get("エリア", "")
        if area not in areas:
            areas[area] = {
                "エリア": area,
                "都道府県": r.get("都道府県", ""),
                "月数": 0,
                "合計CO数": 0,
                "合計タスク数": 0,
                "合計完了数": 0,
                "合計未充足数": 0,
                "クリーナー数": None,
                "採用目安人数_最新": None,
            }
        a = areas[area]
        a["月数"] += 1
        a["合計CO数"] += r.get("チェックアウト数") or 0
        a["合計タスク数"] += r.get("清掃タスク数") or 0
        a["合計完了数"] += r.get("完了数") or 0
        a["合計未充足数"] += r.get("未充足数") or 0
        if r.get("クリーナー数") is not None:
            a["クリーナー数"] = r.get("クリーナー数")
        if r.get("採用目安人数") is not None:
            a["採用目安人数_最新"] = r.get("採用目安人数")

    result = []
    for a in areas.values():
        m = max(a["月数"], 1)
        result.append({
            "エリア": a["エリア"],
            "都道府県": a["都道府県"],
            "月平均CO数": round(a["合計CO数"] / m, 1),
            "月平均タスク数": round(a["合計タスク数"] / m, 1),
            "月平均完了数": round(a["合計完了数"] / m, 1),
            "12ヶ月累計未充足数": a["合計未充足数"],
            "現在クリーナー数": a["クリーナー数"],
            "直近採用目安人数": a["採用目安人数_最新"],
        })
    return result


def build_report_data(data_3029, data_3023_agg, data_3025):
    agg = {r["エリア"]: r for r in data_3023_agg if r.get("エリア")}
    result = []
    for r in data_3029:
        if (r.get("合計採用目安") or 0) <= 0:
            continue
        area = r.get("エリア", "")
        a = agg.get(area, {})
        result.append({
            "エリア": area,
            "都道府県": r.get("都道府県"),
            "合計採用目安": r.get("合計採用目安"),
            "既存採用目安": r.get("既存採用目安"),
            "新規開業追加": r.get("新規開業追加"),
            "新規物件名": r.get("新規物件名"),
            "現在CL数": a.get("現在クリーナー数"),
            "月平均CO数": a.get("月平均CO数"),
            "月平均完了数": a.get("月平均完了数"),
            "12ヶ月累計未充足数": a.get("12ヶ月累計未充足数"),
        })
    return result


def generate_report(data_3029, data_3023, data_3025):
    today = datetime.now(JST).strftime("%Y年%m月%d日")
    data_3023_agg = aggregate_by_area(data_3023)
    hiring_data = build_report_data(data_3029, data_3023_agg, data_3025)

    prompt = f"""以下はクリーナー採用予測データです（{today}時点）。
採用担当者向けのSlackレポートを日本語で作成してください。

【採用目安データ（採用必要エリアのみ）】
{json.dumps(hiring_data, ensure_ascii=False, indent=2)}

【新規開業物件の追加採用（3ヶ月以内）】
{json.dumps(data_3025, ensure_ascii=False, indent=2)}

以下の形式でSlackレポートを作成してください：

📊 *クリーナー採用予測レポート {today}*

*【採用が急ぎのエリア TOP3】*
採用目安人数が多い順に3エリアを記載（エリア名・採用目安・現在CL数・理由）

*【新規開業物件の追加採用】*
直近3ヶ月で開業予定の物件がある場合のみ記載

*【全エリアサマリー】*
各エリアの採用目安人数を一覧で記載

※データがないエリアはスキップしてください。絵文字を効果的に使ってください。"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1500,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def send_slack_message(text):
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
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
    data_3023 = fetch_query_results(3023)
    data_3025 = fetch_query_results(3025)

    print("Generating report with Gemini...")
    report = generate_report(data_3029, data_3023, data_3025)
    print(report)

    print("Sending to Slack...")
    send_slack_message(report)
    print("Done!")
