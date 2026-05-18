import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1TMmeh5nw-MjdEOoaFwTp2KZWtlmVlo3XaI6QZWmjbxQ"
WORKSHEET_GID = 1994830091
OUTPUT_PATH = "/tmp/pipeline_props.json"

EXCLUDED = {"ミラージュパレス日本橋Cloud"}
TARGET_STATUS = {"B_PJT進捗", "C_口頭合意"}
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(tzinfo=None)


def parse_date(s):
    try:
        return datetime.strptime(s.strip(), "%Y/%m/%d")
    except Exception:
        return None


def main():
    creds_json = os.environ["GOOGLE_CREDENTIALS"]
    creds_data = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_data,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    ws = None
    for worksheet in sh.worksheets():
        if worksheet.id == WORKSHEET_GID:
            ws = worksheet
            break
    if ws is None:
        print(f"Worksheet gid={WORKSHEET_GID} not found", file=sys.stderr)
        sys.exit(1)

    rows = ws.get_all_values()
    results = []

    for row in rows[1:]:  # skip header
        if len(row) < 32:
            continue

        status = row[2].strip()
        if status not in TARGET_STATUS:
            continue

        name = row[4].strip()
        if not name or name in EXCLUDED:
            continue

        rooms_raw = row[5].strip()
        address = row[8].strip()
        date_chintai = row[30].strip()
        date_shukuhaku = row[31].strip()

        d_chintai = parse_date(date_chintai) if DATE_RE.match(date_chintai) else None
        d_shukuhaku = parse_date(date_shukuhaku) if DATE_RE.match(date_shukuhaku) else None

        if d_chintai and d_chintai > TODAY:
            opening = date_chintai
            ptype = "賃貸"
        elif d_shukuhaku:
            opening = date_shukuhaku
            ptype = "宿泊"
        else:
            continue

        try:
            rooms = int(rooms_raw.replace("室", "").strip())
        except Exception:
            rooms = 0

        results.append({
            "name": name,
            "rooms": rooms,
            "address": address,
            "opening": opening,
            "type": ptype,
        })

    # 物件名＋開業日が同じものは同一データとして重複除去
    seen = set()
    deduped = []
    for p in results:
        key = (p["name"], p["opening"])
        if key not in seen:
            seen.add(key)
            deduped.append(p)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    print(f"{len(deduped)} properties written to {OUTPUT_PATH} (deduped from {len(results)})")


if __name__ == "__main__":
    main()
