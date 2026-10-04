"""
query_3016の東京23区ゾーンCASE条件を取得し、send_cleaner_report.py の
get_area() 東京セクション（BEGINマーカー〜ENDマーカー間）を自動更新する。

SQLが変わっていなければ何もしない（ハッシュ比較）。
"""
import hashlib
import json
import os
import re
import sys

import requests

REDASH_BASE_URL = os.environ.get("REDASH_BASE_URL", "https://redash.unito.me")
REDASH_API_KEY = os.environ["REDASH_API_KEY"]
QUERY_ID = 3016
HASH_PATH = "/tmp/query_3016_area_hash.txt"
TARGET = os.path.join(os.path.dirname(__file__), "send_cleaner_report.py")

BEGIN_MARKER = "    # --- BEGIN TOKYO AREA MAPPING (auto-synced from query_3016) ---"
END_MARKER = "    # --- END TOKYO AREA MAPPING ---"


def fetch_sql():
    resp = requests.get(
        f"{REDASH_BASE_URL}/api/queries/{QUERY_ID}",
        headers={"Authorization": f"Key {REDASH_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["query"]


def extract_tokyo_zone(sql):
    """東京23区ゾーンのCASE WHEN〜ELSE直前までを抽出する。"""
    m = re.search(r"-- 東京23区ゾーン\n(.*?)(?=\n\s*ELSE)", sql, re.DOTALL)
    if not m:
        return None
    return m.group(1)


def parse_when_blocks(zone_sql):
    """
    WHEN z.住所 LIKE '%X%' OR ... THEN 'エリア名'
    を (keywords_list, area_name) のリストに変換する。
    """
    results = []
    pattern = re.compile(
        r"WHEN\s+((?:z\.住所 LIKE '[^']+'\s*(?:OR\s*)?)+)\s*THEN\s+'([^']+)'",
        re.DOTALL,
    )
    for m in pattern.finditer(zone_sql):
        conditions = m.group(1)
        area = m.group(2)
        keywords = re.findall(r"LIKE '%([^%']+)%'", conditions)
        if keywords:
            results.append((keywords, area))
    return results


def build_python_lines(blocks):
    lines = [BEGIN_MARKER]
    for keywords, area in blocks:
        checks = " or ".join(f'"{kw}" in addr' for kw in keywords)
        lines.append(f'        if {checks}: return ("東京", "{area}")')
    lines.append(END_MARKER)
    return lines


def update_file(new_lines):
    with open(TARGET) as f:
        src = f.read()

    begin_idx = src.find(BEGIN_MARKER)
    end_idx = src.find(END_MARKER)
    if begin_idx == -1 or end_idx == -1:
        print("ERROR: markers not found in send_cleaner_report.py", file=sys.stderr)
        sys.exit(1)

    end_idx += len(END_MARKER)
    replacement = "\n".join(new_lines)
    new_src = src[:begin_idx] + replacement + src[end_idx:]

    with open(TARGET, "w") as f:
        f.write(new_src)


def main():
    sql = fetch_sql()
    zone_sql = extract_tokyo_zone(sql)
    if not zone_sql:
        print("WARNING: 東京23区ゾーンが見つかりません。スキップします。")
        return

    current_hash = hashlib.md5(zone_sql.encode()).hexdigest()

    stored_hash = None
    if os.path.exists(HASH_PATH):
        with open(HASH_PATH) as f:
            stored_hash = f.read().strip()

    if current_hash == stored_hash:
        print("area mapping: query_3016 unchanged, skipping update.")
        return

    blocks = parse_when_blocks(zone_sql)
    if not blocks:
        print("WARNING: WHEN条件が解析できませんでした。スキップします。")
        return

    new_lines = build_python_lines(blocks)
    update_file(new_lines)

    with open(HASH_PATH, "w") as f:
        f.write(current_hash)

    print(f"area mapping: updated {len(blocks)} Tokyo zones from query_3016.")


if __name__ == "__main__":
    main()
