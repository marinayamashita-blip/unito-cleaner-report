import os
import json
import requests
from datetime import datetime, timezone, timedelta

REDASH_BASE_URL = "https://redash.unito.me"
REDASH_API_KEY = os.environ["REDASH_API_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL = "C0B3LFX6RLH"
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

JST = timezone(timedelta(hours=9))


def fetch_query_results(query_id):
    url = f"{REDASH_BASE_URL}/api/queries/{query_id}/results"
    headers = {"Authorization": f"Key {REDASH_API_KEY}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()["query_result"]["data"]["rows"]


def generate_report(data_3029, data_3023, data_3025):
    today = datetime.now(JST).strftime("%Y年%m月%d日")

    prompt = f"""以下はクリーナー採用予測ダッシュボードのデータです（{today}時点）。
エリアごとの採用予測人数と理由を分析し、採用担当者向けのSlackレポートを作成してください。

【合計採用目安（既存＋新規開業）】
{json.dumps(data_3029, ensure_ascii=False, indent=2)}

【エリア別採用目安（既存物件・直近12ヶ月）】
{json.dumps(data_3023, ensure_ascii=False, indent=2)}

【追加採用目安（開業予定3ヶ月先含む）】
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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


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
