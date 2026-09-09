import os
import json
import ssl
from datetime import datetime
from urllib.request import Request, urlopen

import certifi
import psycopg


API_URL = (
    "https://api.taiwanlottery.com/"
    "TLCAPIWeB/Lottery/LastNumber"
)

GAME_CODE_BIGLOTTO = "5118"


def build_ssl_context():
    context = ssl.create_default_context(
        cafile=certifi.where()
    )

    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    return context


def fetch_latest_biglotto():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.taiwanlottery.com/",
    }

    request = Request(
        API_URL,
        headers=headers,
    )

    with urlopen(
        request,
        timeout=30,
        context=build_ssl_context(),
    ) as response:
        raw = response.read().decode("utf-8")

    data = json.loads(raw)

    content = data.get("content", {})

    last_number_list = content.get(
        "lastNumberList",
        [],
    )

    for item in last_number_list:
        game_code = str(item.get("gameCode", ""))

        if game_code == GAME_CODE_BIGLOTTO:
            period = str(
                item.get("period", "")
            ).strip()

            draw_date_raw = str(
                item.get("drawDate", "")
            ).strip()

            numbers = item.get(
                "lotNumber",
                [],
            )

            if not period:
                raise RuntimeError(
                    "大樂透期別為空"
                )

            if len(numbers) != 7:
                raise RuntimeError(
                    f"大樂透號碼數量異常：{numbers}"
                )

            draw_date = datetime.strptime(
                draw_date_raw,
                "%Y-%m-%d %H:%M:%S",
            ).date()

            raw_numbers = [int(n) for n in numbers]
            main_numbers = sorted(raw_numbers[:6])
            special_number = raw_numbers[6]

            return {
                "draw_no": period,
                "draw_date": draw_date,
                "numbers": main_numbers,
                "special_number": special_number,
            }

    raise RuntimeError(
        "官方 API 找不到大樂透資料"
    )


def get_connection():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return psycopg.connect(database_url)

    password = os.getenv(
        "BIGLOTTO_DB_PASSWORD"
    )

    if not password:
        raise RuntimeError(
            "找不到 DATABASE_URL 或 "
            "BIGLOTTO_DB_PASSWORD"
        )

    return psycopg.connect(
        dbname="biglotto",
        user="postgres",
        host="localhost",
        port=5432,
        password=password,
    )


def check_existing(conn, draw_no):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                draw_no,
                draw_date,
                number1,
                number2,
                number3,
                number4,
                number5,
                number6,
                special_number
            FROM biglotto_draws
            WHERE draw_no = %s
            """,
            (draw_no,),
        )

        return cur.fetchone()


def insert_draw(conn, draw):
    numbers = draw["numbers"]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO biglotto_draws (
                draw_no,
                draw_date,
                number1,
                number2,
                number3,
                number4,
                number5,
                number6,
                special_number
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            ON CONFLICT (draw_no)
            DO NOTHING
            """,
            (
                draw["draw_no"],
                draw["draw_date"],
                numbers[0],
                numbers[1],
                numbers[2],
                numbers[3],
                numbers[4],
                numbers[5],
                draw["special_number"],
            ),
        )

    conn.commit()


def main():
    print("=" * 60)
    print("大樂透 自動更新")
    print("=" * 60)

    latest = fetch_latest_biglotto()

    print("官方最新資料：")
    print(
        f"期別：{latest['draw_no']}"
    )
    print(
        f"日期：{latest['draw_date']}"
    )
    print(
        "主號："
        + " ".join(
            f"{n:02d}"
            for n in latest["numbers"]
        )
    )
    print(
        f"特別號：{latest['special_number']:02d}"
    )

    print()

    with get_connection() as conn:
        existing = check_existing(
            conn,
            latest["draw_no"],
        )

        if existing:
            print("資料庫已存在這一期。")
            print("不重複新增。")
            print(existing)
            return

        print("資料庫尚未有這一期。")
        print("準備新增...")

        insert_draw(
            conn,
            latest,
        )

        verify = check_existing(
            conn,
            latest["draw_no"],
        )

        if not verify:
            raise RuntimeError(
                "新增後驗證失敗"
            )

        print()
        print("新增成功：")
        print(verify)

    print()
    print("=" * 60)
    print("大樂透 更新完成")
    print("=" * 60)


if __name__ == "__main__":
    main()