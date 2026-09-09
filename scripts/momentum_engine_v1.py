from collections import Counter
import os
from getpass import getpass

import psycopg


DB_CONFIG = {
    "dbname": "app539",
    "user": "postgres",
    "host": "localhost",
    "port": 5432,
}

TOTAL_NUMBERS = 49
MAIN_NUMBERS_PER_DRAW = 6
WITH_SPECIAL_NUMBERS_PER_DRAW = 7
LOOKBACK = 100


def load_latest_draws(conn, limit=100):
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
            ORDER BY draw_date DESC, draw_no DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()

def calculate_ratios(draws, window, include_special=False):
    selected = draws[:window]
    nums = []

    for row in selected:
        nums.extend(row[2:8])  # 6個主號
        if include_special:
            nums.append(row[8])  # 特別號

    counter = Counter(nums)

    numbers_per_draw = (
        WITH_SPECIAL_NUMBERS_PER_DRAW
        if include_special
        else MAIN_NUMBERS_PER_DRAW
    )

    expected = len(selected) * numbers_per_draw / TOTAL_NUMBERS
    result = {}

    for number in range(1, TOTAL_NUMBERS + 1):
        count = counter.get(number, 0)
        ratio = count / expected if expected > 0 else 0
        result[number] = {
            "count": count,
            "ratio": ratio,
        }

    return result

def score_number(r10, r30, r50, r100):
    score = 50.0
    reasons = []
    warnings = []

    # =====================================================
    # 1. 50期核心動能
    # V8 最穩定訊號
    # =====================================================

    if 1.10 <= r50 <= 1.20:
        score += 18
        reasons.append("50期穩定偏熱")

    elif 1.20 < r50 <= 1.30:
        score += 14
        reasons.append("50期強勢")

    elif 1.05 <= r50 < 1.10:
        score += 8
        reasons.append("50期略強")

    elif r50 < 0.90:
        score -= 5
        warnings.append("50期偏弱")

    # =====================================================
    # 2. 長期動能
    # =====================================================

    if 1.05 <= r100 <= 1.15:
        score += 10
        reasons.append("長期偏熱")

    elif 1.15 < r100 <= 1.25:
        score += 7
        reasons.append("長期強勢")

    elif r100 < 0.90:
        score -= 5
        warnings.append("長期偏冷")

    # =====================================================
    # 3. 短中期共振
    # 只做輔助，不當核心
    # =====================================================

    if r10 >= 1.05 and r30 >= 1.05:
        score += 6
        reasons.append("短中共振")

    if (
        r10 >= 1.05
        and r30 >= 1.05
        and r50 >= 1.00
    ):
        score += 4
        reasons.append("三週期共振")

    # =====================================================
    # 4. 中長期一致
    # =====================================================

    if (
        r30 >= 1.00
        and r50 >= 1.05
        and r100 >= 1.00
    ):
        score += 8
        reasons.append("中長期一致")

    # =====================================================
    # 5. 極端過熱
    #
    # 一般熱不扣分
    # 只有短中期同時非常極端才扣
    # =====================================================

    if (
        r10 >= 1.60
        and r30 >= 1.15
    ):
        score -= 12
        warnings.append("極端短中過熱")

    if (
        r10 >= 1.70
        and r30 >= 1.15
    ):
        score -= 6
        warnings.append("短期暴衝")

    # =====================================================
    # 6. 回補型
    #
    # V7 / V8 沒有支持回補是正向訊號
    # 所以不再加分
    # =====================================================

    if (
        r100 < 0.95
        and r10 >= 1.15
    ):
        score -= 4
        warnings.append("長冷短熱回補型")

    if (
        r100 < 0.90
        and r10 >= 1.15
    ):
        score -= 6
        warnings.append("深冷回補風險")

    # =====================================================
    # 7. 嚴格加速
    # V7 / V8 表現偏弱
    # =====================================================

    if (
        r10 > r30 > r50 > r100
    ):
        score -= 8
        warnings.append("嚴格加速上升")

    # =====================================================
    # 8. 短期高於長期
    # 小幅加分即可
    # =====================================================

    if r10 > r100:
        score += 3
        reasons.append("短期高於長期")

    # 分數限制
    score = max(
        0,
        min(
            100,
            round(score, 1)
        )
    )

    return (
        score,
        reasons,
        warnings
    )


def classify(score):
    if score >= 75:
        return "A"

    elif score >= 65:
        return "B"

    elif score >= 55:
        return "C"

    elif score >= 45:
        return "D"

    else:
        return "E"


def main():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        conn_args = (database_url,)
        conn_kwargs = {}
    else:
        password = getpass(
            "請輸入 PostgreSQL postgres 密碼："
        )
        conn_args = ()
        conn_kwargs = {
            **DB_CONFIG,
            "password": password,
        }

    with psycopg.connect(
        *conn_args,
        **conn_kwargs,
    ) as conn:

        draws = load_latest_draws(
            conn,
            LOOKBACK
        )

    if len(draws) < LOOKBACK:
        print(
            "資料不足100期，無法執行正式動能分析。"
        )
        return

    mode = input(
        "統計模式 [1=主號動能(預設) / 2=含特別號動能]："
    ).strip()
    include_special = mode == "2"

    stats10 = calculate_ratios(
        draws,
        10,
        include_special=include_special
    )

    stats30 = calculate_ratios(
        draws,
        30,
        include_special=include_special
    )

    stats50 = calculate_ratios(
        draws,
        50,
        include_special=include_special
    )

    stats100 = calculate_ratios(
        draws,
        100,
        include_special=include_special
    )

    results = []

    for number in range(1, TOTAL_NUMBERS + 1):

        r10 = stats10[
            number
        ]["ratio"]

        r30 = stats30[
            number
        ]["ratio"]

        r50 = stats50[
            number
        ]["ratio"]

        r100 = stats100[
            number
        ]["ratio"]

        score, reasons, warnings = (
            score_number(
                r10,
                r30,
                r50,
                r100
            )
        )

        grade = classify(
            score
        )

        results.append(
            {
                "number": number,
                "r10": r10,
                "r30": r30,
                "r50": r50,
                "r100": r100,
                "score": score,
                "grade": grade,
                "reasons": reasons,
                "warnings": warnings,
            }
        )

    results.sort(
        key=lambda x: (
            -x["score"],
            x["number"]
        )
    )

    print()
    print("=" * 120)
    print(
        "大樂透 Momentum Engine V1 - 正式分析核心"
    )
    print("=" * 120)
    print("統計模式：" + ("6主號＋特別號" if include_special else "6主號"))

    print(
        f"最新期別：{draws[0][0]}"
    )

    print(
        f"最新日期：{draws[0][1]}"
    )

    print()

    print(
        f"{'排名':>4} "
        f"{'號碼':>4} "
        f"{'10期':>7} "
        f"{'30期':>7} "
        f"{'50期':>7} "
        f"{'100期':>7} "
        f"{'分數':>7} "
        f"{'級別':>5} "
        f"{'分析':<35}"
    )

    print("-" * 120)

    for rank, item in enumerate(
        results,
        start=1
    ):

        reason_text = (
            "、".join(
                item["reasons"][:3]
            )
            if item["reasons"]
            else "-"
        )

        warning_text = (
            " / ".join(
                item["warnings"][:2]
            )
        )

        if warning_text:
            reason_text += (
                f" ⚠{warning_text}"
            )

        print(
            f"{rank:>4} "
            f"{item['number']:>4} "
            f"{item['r10']:>7.2f} "
            f"{item['r30']:>7.2f} "
            f"{item['r50']:>7.2f} "
            f"{item['r100']:>7.2f} "
            f"{item['score']:>7.1f} "
            f"{item['grade']:>5} "
            f"{reason_text}"
        )

    print()
    print("=" * 120)
    print("【Top 5 核心號碼】")
    print("=" * 120)

    for i, item in enumerate(
        results[:5],
        start=1
    ):
        print(
            f"{i}. "
            f"{item['number']:02d} "
            f"| {item['score']:.1f}分 "
            f"| {item['grade']}級"
        )

    print()
    print("=" * 120)
    print("【Top 10 動能池】")
    print("=" * 120)

    print(
        " ".join(
            f"{x['number']:02d}"
            for x in results[:10]
        )
    )

    print()
    print("=" * 120)
    print("【A～E 分級】")
    print("=" * 120)

    for grade in [
        "A",
        "B",
        "C",
        "D",
        "E",
    ]:

        numbers = [
            x["number"]
            for x in results
            if x["grade"] == grade
        ]

        text = (
            " ".join(
                f"{n:02d}"
                for n in numbers
            )
            if numbers
            else "-"
        )

        print(
            f"{grade}級：{text}"
        )

    print()
    print("=" * 120)
    print(
        "Momentum Engine V1 Completed"
    )
    print("=" * 120)


if __name__ == "__main__":
    main()