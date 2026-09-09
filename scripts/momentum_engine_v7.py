import os
from collections import Counter

import psycopg

from scripts.backtest_v1 import DB_CONFIG


TOTAL_NUMBERS = 49
MAIN_NUMBERS_PER_DRAW = 6
WITH_SPECIAL_NUMBERS_PER_DRAW = 7

WINDOWS = [
    10,
    30,
    50,
    100,
]



# ============================================================
# 大樂透資料讀取
# row = (draw_no, draw_date, number1..number6, special_number)
# ============================================================

def load_biglotto_draws(conn):
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
            ORDER BY draw_date ASC, draw_no ASC
            """
        )
        return cur.fetchall()


def numbers_from_draw(row, include_special=False):
    numbers = list(row[2:8])

    if include_special:
        numbers.append(row[8])

    return numbers


def numbers_per_draw(include_special=False):
    return (
        WITH_SPECIAL_NUMBERS_PER_DRAW
        if include_special
        else MAIN_NUMBERS_PER_DRAW
    )


# ============================================================
# 基礎動能計算
# ============================================================

def calculate_ratios(
    draws,
    window,
    include_special=False,
):
    selected = draws[-window:]
    numbers = []

    for row in selected:
        numbers.extend(
            numbers_from_draw(
                row,
                include_special=include_special,
            )
        )

    counter = Counter(numbers)

    expected = (
        len(selected)
        * numbers_per_draw(include_special)
        / TOTAL_NUMBERS
    )

    result = {}

    for number in range(
        1,
        TOTAL_NUMBERS + 1,
    ):
        count = counter.get(
            number,
            0,
        )

        ratio = (
            count / expected
            if expected > 0
            else 0
        )

        result[number] = {
            "count": count,
            "ratio": ratio,
        }

    return result


def calculate_all_windows(
    draws,
    include_special=False,
):
    output = {}

    for window in WINDOWS:
        output[window] = (
            calculate_ratios(
                draws,
                window,
                include_special=include_special,
            )
        )

    return output



# ============================================================
# 動能型態判定
# ============================================================

def classify_momentum_type(
    r10,
    r30,
    r50,
    r100,
):
    if (
        r10 >= 1.80
        and r30 >= 1.50
        and r50 >= 1.40
    ):
        return "過熱警戒"

    if (
        r10 >= 1.50
        and r30 >= 1.30
        and r50 >= 1.20
    ):
        return "高檔延續"

    if (
        r10 >= 1.50
        and r30 >= 1.20
        and r100 < 1.00
    ):
        return "加速上升"

    if (
        r10 >= 1.20
        and r30 >= 1.10
        and r50 >= 1.00
        and r100 >= 1.00
    ):
        return "穩定強勢"

    if (
        r10 >= 1.20
        and r30 >= 1.00
        and r100 < 1.00
    ):
        return "回補啟動"

    if (
        r10 < 1.00
        and r30 >= 1.10
    ):
        return "動能衰退"

    return "一般"


# ============================================================
# 分析理由
# ============================================================

def build_reasons(
    r10,
    r30,
    r50,
    r100,
):
    reasons = []
    warnings = []

    if (
        r10 >= 1.05
        and r30 >= 1.05
    ):
        reasons.append(
            "短中共振"
        )

    if (
        r30 >= 1.00
        and r50 >= 1.00
        and r100 >= 1.00
    ):
        reasons.append(
            "三週期共振"
        )

    if (
        r50 >= 1.10
    ):
        reasons.append(
            "50期偏強"
        )

    if (
        r100 >= 1.05
    ):
        reasons.append(
            "長期偏熱"
        )

    if (
        r100 < 1.00
        and r50 <= 1.05
        and r30 >= 1.00
        and r10 >= r30
    ):
        reasons.append(
            "長冷短熱回補"
        )

    if (
        r10 > r30
        and r30 > r50
    ):
        reasons.append(
            "動能加速"
        )

    if (
        r10 > r100
    ):
        reasons.append(
            "短期高於長期"
        )

    if (
        r50 < 0.90
    ):
        warnings.append(
            "50期偏弱"
        )

    if (
        r100 < 0.90
    ):
        warnings.append(
            "長期偏冷"
        )

    if (
        r10 >= 1.80
        and r30 >= 1.40
    ):
        warnings.append(
            "短中期過熱"
        )

    if (
        r10 >= 2.20
        and r30 >= 1.60
    ):
        warnings.append(
            "嚴重過熱"
        )

    return (
        reasons,
        warnings,
    )


# ============================================================
# V7 評分
#
# 注意：
# 這裡不是取代 V6 凍結模型
# 是 App 顯示用分析評分
# ============================================================

def score_number(
    r10,
    r30,
    r50,
    r100,
):
    score = 50.0

    reasons = []
    warnings = []

    # --------------------------------------------------------
    # 50期核心
    # --------------------------------------------------------

    if (
        1.10 <= r50 <= 1.20
    ):
        score += 18
        reasons.append(
            "50期穩定偏熱"
        )

    elif (
        1.20 < r50 < 1.30
    ):
        score += 14
        reasons.append(
            "50期強勢"
        )

    elif (
        1.05 <= r50 < 1.10
    ):
        score += 8
        reasons.append(
            "50期略強"
        )

    elif (
        r50 < 0.90
    ):
        score -= 5
        warnings.append(
            "50期偏弱"
        )


    # --------------------------------------------------------
    # 100期長期動能
    # --------------------------------------------------------

    if (
        1.05 <= r100 <= 1.15
    ):
        score += 10
        reasons.append(
            "長期偏熱"
        )

    elif (
        1.15 < r100 <= 1.25
    ):
        score += 7
        reasons.append(
            "長期強勢"
        )

    elif (
        r100 < 0.90
    ):
        score -= 5
        warnings.append(
            "長期偏冷"
        )


    # --------------------------------------------------------
    # 短中期共振
    # --------------------------------------------------------

    if (
        r10 >= 1.05
        and r30 >= 1.05
    ):
        score += 6
        reasons.append(
            "短中共振"
        )


    # --------------------------------------------------------
    # 三週期共振
    # --------------------------------------------------------

    if (
        r30 >= 1.00
        and r50 >= 1.00
        and r100 >= 1.00
    ):
        score += 5
        reasons.append(
            "三週期共振"
        )


    # --------------------------------------------------------
    # 中長期一致
    # --------------------------------------------------------

    if (
        r30 >= 1.00
        and r50 >= 1.00
        and r100 >= 1.00
    ):
        score += 4
        reasons.append(
            "中長期一致"
        )


    # --------------------------------------------------------
    # 回補
    # --------------------------------------------------------

    if (
        r100 < 1.00
        and r50 <= 1.05
        and r30 >= 1.00
        and r10 >= r30
    ):
        score += 3
        reasons.append(
            "回補訊號"
        )


    # --------------------------------------------------------
    # 加速
    # --------------------------------------------------------

    if (
        r10 > r30
        and r30 > r50
    ):
        score += 3
        reasons.append(
            "動能加速"
        )


    # --------------------------------------------------------
    # 過熱懲罰
    # --------------------------------------------------------

    if (
        r10 >= 1.80
        and r30 >= 1.40
    ):
        score -= 8
        warnings.append(
            "短中期過熱"
        )

    if (
        r10 >= 2.20
        and r30 >= 1.60
    ):
        score -= 6
        warnings.append(
            "嚴重過熱"
        )


    score = max(
        0,
        min(
            100,
            round(
                score,
                1,
            ),
        ),
    )

    return (
        score,
        reasons,
        warnings,
    )


# ============================================================
# 分級
# ============================================================

def classify_grade(
    score
):
    if score >= 75:
        return "A"

    if score >= 65:
        return "B"

    if score >= 55:
        return "C"

    if score >= 45:
        return "D"

    return "E"


# ============================================================
# V6 凍結長期熱門分數
#
# 50期 * 0.5
# 100期 * 0.5
# ============================================================

def frozen_long_hot_score(
    r50,
    r100,
):
    return (
        r50 * 0.5
        +
        r100 * 0.5
    )


# ============================================================
# 建立49號完整分析
# ============================================================

def build_analysis(
    draws,
    include_special=False,
):
    stats = (
        calculate_all_windows(
            draws,
            include_special=include_special,
        )
    )

    results = []

    for number in range(
        1,
        TOTAL_NUMBERS + 1,
    ):
        r10 = (
            stats[10][number][
                "ratio"
            ]
        )

        r30 = (
            stats[30][number][
                "ratio"
            ]
        )

        r50 = (
            stats[50][number][
                "ratio"
            ]
        )

        r100 = (
            stats[100][number][
                "ratio"
            ]
        )

        (
            score,
            score_reasons,
            score_warnings,
        ) = score_number(
            r10,
            r30,
            r50,
            r100,
        )

        (
            extra_reasons,
            extra_warnings,
        ) = build_reasons(
            r10,
            r30,
            r50,
            r100,
        )

        reasons = list(
            dict.fromkeys(
                score_reasons
                +
                extra_reasons
            )
        )

        warnings = list(
            dict.fromkeys(
                score_warnings
                +
                extra_warnings
            )
        )

        momentum_type = (
            classify_momentum_type(
                r10,
                r30,
                r50,
                r100,
            )
        )

        grade = classify_grade(
            score
        )

        frozen_score = (
            frozen_long_hot_score(
                r50,
                r100,
            )
        )

        results.append(
            {
                "number":
                    number,

                "r10":
                    r10,

                "r30":
                    r30,

                "r50":
                    r50,

                "r100":
                    r100,

                "score":
                    score,

                "grade":
                    grade,

                "momentum_type":
                    momentum_type,

                "frozen_score":
                    frozen_score,

                "reasons":
                    reasons,

                "warnings":
                    warnings,
            }
        )

    results.sort(
        key=lambda x: (
            -x["score"],
            -x["frozen_score"],
            x["number"],
        )
    )

    return results


# ============================================================
# V6 凍結策略 Top5
# ============================================================

def get_frozen_top5(
    results
):
    ordered = sorted(
        results,
        key=lambda x: (
            -x["frozen_score"],
            x["number"],
        )
    )

    return ordered[:5]


# ============================================================
# V7分析Top10
# ============================================================

def get_analysis_top10(
    results
):
    return results[:10]


# ============================================================
# 分級整理
# ============================================================

def group_by_grade(
    results
):
    output = {
        "A": [],
        "B": [],
        "C": [],
        "D": [],
        "E": [],
    }

    for item in results:
        output[
            item["grade"]
        ].append(
            item["number"]
        )

    return output


# ============================================================
# 顯示完整排名
# ============================================================

def print_full_ranking(
    results
):
    print()
    print("=" * 140)

    print(
        "大樂透 Momentum Engine V7 "
        "- 正式分析排名"
    )

    print("=" * 140)

    print(
        f"{'排名':<5}"
        f"{'號碼':>5}"
        f"{'10期':>8}"
        f"{'30期':>8}"
        f"{'50期':>8}"
        f"{'100期':>8}"
        f"{'分數':>8}"
        f"{'級別':>7}"
        f"{'凍結分':>9}"
        f"  {'動能型態':<12}"
        f"分析"
    )

    print("-" * 140)

    for rank, item in enumerate(
        results,
        start=1,
    ):
        reason_text = "、".join(
            item["reasons"]
        )

        warning_text = ""

        if item["warnings"]:
            warning_text = (
                "  ⚠ "
                +
                "、".join(
                    item["warnings"]
                )
            )

        print(
            f"{rank:<5}"
            f"{item['number']:>5}"
            f"{item['r10']:>8.2f}"
            f"{item['r30']:>8.2f}"
            f"{item['r50']:>8.2f}"
            f"{item['r100']:>8.2f}"
            f"{item['score']:>8.1f}"
            f"{item['grade']:>7}"
            f"{item['frozen_score']:>9.3f}"
            f"  {item['momentum_type']:<12}"
            f"{reason_text}"
            f"{warning_text}"
        )


# ============================================================
# 顯示V7 Top10
# ============================================================

def print_analysis_top10(
    results
):
    top10 = get_analysis_top10(
        results
    )

    print()
    print("=" * 100)

    print(
        "V7 分析 Top10"
    )

    print("=" * 100)

    for rank, item in enumerate(
        top10,
        start=1,
    ):
        print(
            f"{rank:>2}. "
            f"{item['number']:02d}  "
            f"{item['score']:.1f}分  "
            f"{item['grade']}級  "
            f"{item['momentum_type']}"
        )


# ============================================================
# 顯示V6凍結Top5
# ============================================================

def print_frozen_top5(
    results
):
    top5 = get_frozen_top5(
        results
    )

    print()
    print("=" * 100)

    print(
        "V6 凍結核心 Top5"
    )

    print("=" * 100)

    print(
        "規則："
        "50期50% + 100期50%"
    )

    print(
        "此區維持V6凍結策略，"
        "不受V7分析分數影響。"
    )

    print()

    for rank, item in enumerate(
        top5,
        start=1,
    ):
        print(
            f"{rank}. "
            f"{item['number']:02d}  "
            f"50期={item['r50']:.2f}  "
            f"100期={item['r100']:.2f}  "
            f"凍結分={item['frozen_score']:.3f}"
        )


# ============================================================
# 顯示A-E
# ============================================================

def print_grades(
    results
):
    groups = group_by_grade(
        results
    )

    print()
    print("=" * 100)

    print(
        "V7 A～E 分級"
    )

    print("=" * 100)

    for grade in [
        "A",
        "B",
        "C",
        "D",
        "E",
    ]:
        numbers = groups[
            grade
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
            f"{grade}級："
            f"{text}"
        )


# ============================================================
# 顯示最新一期
# ============================================================

def print_latest_draw(
    draws
):
    latest = draws[-1]

    print()
    print("=" * 100)
    print("最新開獎資料")
    print("=" * 100)
    print(f"期別：{latest[0]}")
    print(f"日期：{latest[1]}")
    print(
        "主號：",
        " ".join(
            f"{n:02d}"
            for n in latest[2:8]
        )
    )
    print(
        f"特別號：{latest[8]:02d}"
    )



# ============================================================
# 主程式
# ============================================================

def main():
    database_url = os.getenv(
        "DATABASE_URL"
    )

    if database_url:
        conn_args = (database_url,)
        conn_kwargs = {}
    else:
        password = os.getenv(
            "BIGLOTTO_DB_PASSWORD"
        )

        if not password:
            print(
                "找不到 DATABASE_URL 或 "
                "BIGLOTTO_DB_PASSWORD 環境變數。"
            )
            return

        conn_args = ()
        conn_kwargs = {
            **DB_CONFIG,
            "password": password,
        }

    with psycopg.connect(
        *conn_args,
        **conn_kwargs,
    ) as conn:
        draws = load_biglotto_draws(
            conn
        )

    if len(draws) < 100:
        print(
            "歷史資料不足100期。"
        )
        return

    print()
    print("=" * 100)
    print(
        "大樂透 Momentum Engine V7 "
        "- App 正式分析核心"
    )
    print("=" * 100)

    mode = input(
        "統計模式 "
        "[1=6主號(預設) / "
        "2=6主號＋特別號]："
    ).strip()

    include_special = (
        mode == "2"
    )

    print(
        "目前模式："
        + (
            "6主號＋特別號"
            if include_special
            else "6主號"
        )
    )

    print(
        f"資料總期數："
        f"{len(draws)}"
    )
    print(
        "分析視窗："
        "10 / 30 / 50 / 100"
    )
    print(
        "期望值基準："
        + (
            "7/49"
            if include_special
            else "6/49"
        )
    )
    print(
        "V6凍結核心："
        "50期50% + 100期50%"
    )

    print_latest_draw(
        draws
    )

    results = build_analysis(
        draws,
        include_special=include_special,
    )

    print_full_ranking(
        results
    )
    print_analysis_top10(
        results
    )
    print_frozen_top5(
        results
    )
    print_grades(
        results
    )

    print()
    print("=" * 100)
    print(
        "Momentum Engine V7 Completed"
    )
    print("=" * 100)


if __name__ == "__main__":
    main()