import os
import psycopg

from scripts.momentum_engine_v1 import (
    DB_CONFIG,
    calculate_ratios,
    score_number,
    classify,
)

TOTAL_NUMBERS = 39
LOOKBACK = 100

def load_all_draws(conn):
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
                number5
            FROM lottery_draws
            ORDER BY draw_date ASC, draw_no ASC
            """
        )
        return cur.fetchall()

def build_rankings(history):
    stats10 = calculate_ratios(history, 10)
    stats30 = calculate_ratios(history, 30)
    stats50 = calculate_ratios(history, 50)
    stats100 = calculate_ratios(history, 100)

    results = []

    for number in range(1, 40):
        r10 = stats10[number]["ratio"]
        r30 = stats30[number]["ratio"]
        r50 = stats50[number]["ratio"]
        r100 = stats100[number]["ratio"]

        score, reasons, warnings, momentum_type = score_number(
            r10,
            r30,
            r50,
            r100
        )

        results.append(
            {
                "number": number,
                "score": score,
                "grade": classify(score),
                "momentum_type": momentum_type,
            }
        )

    results.sort(
        key=lambda x: (
            -x["score"],
            x["number"]
        )
    )

    return results

def run_backtest(draws):
    if len(draws) <= LOOKBACK:
        print("歷史資料不足，無法進行回測。")
        return []

    backtest_results = []

    for i in range(LOOKBACK, len(draws)):
        history = draws[i - LOOKBACK:i]
        target_draw = draws[i]

        rankings = build_rankings(history)

        top5 = [
            x["number"]
            for x in rankings[:5]
        ]

        top10 = [
            x["number"]
            for x in rankings[:10]
        ]

        actual_numbers = set(target_draw[2:7])

        top5_hits = [
            n for n in top5
            if n in actual_numbers
        ]

        top10_hits = [
            n for n in top10
            if n in actual_numbers
        ]

        backtest_results.append(
            {
                "draw_no": target_draw[0],
                "draw_date": target_draw[1],
                "actual": sorted(actual_numbers),
                "top5": top5,
                "top5_hits": top5_hits,
                "top10": top10,
                "top10_hits": top10_hits,
            }
        )

    return backtest_results

def print_backtest_summary(backtest_results):
    total = len(backtest_results)

    if total == 0:
        print("沒有可統計的回測結果。")
        return

    top5_total_hits = sum(
        len(x["top5_hits"])
        for x in backtest_results
    )

    top10_total_hits = sum(
        len(x["top10_hits"])
        for x in backtest_results
    )

    top5_hit_periods = sum(
        1 for x in backtest_results
        if len(x["top5_hits"]) >= 1
    )

    top10_hit_periods = sum(
        1 for x in backtest_results
        if len(x["top10_hits"]) >= 1
    )

    top5_avg = top5_total_hits / total
    top10_avg = top10_total_hits / total

    random_top5_avg = 5 * 5 / 39
    random_top10_avg = 10 * 5 / 39

    print()
    print("=" * 70)
    print("539 Momentum Engine V1 - 歷史回測統計")
    print("=" * 70)

    print(f"回測期數：{total}")
    print()

    print("[Top 5]")
    print(f"總命中數：{top5_total_hits}")
    print(f"平均每期命中：{top5_avg:.3f}")
    print(f"至少命中1號期數：{top5_hit_periods}")
    print(f"至少命中1號比例：{top5_hit_periods / total:.2%}")
    print(f"隨機理論平均：{random_top5_avg:.3f}")
    print()

    print("[Top 10]")
    print(f"總命中數：{top10_total_hits}")
    print(f"平均每期命中：{top10_avg:.3f}")
    print(f"至少命中1號期數：{top10_hit_periods}")
    print(f"至少命中1號比例：{top10_hit_periods / total:.2%}")
    print(f"隨機理論平均：{random_top10_avg:.3f}")

    print("=" * 70)

if __name__ == "__main__":
    password = os.getenv("APP539_DB_PASSWORD")

    with psycopg.connect(
        **DB_CONFIG,
        password=password
    ) as conn:
        draws = load_all_draws(conn)

    backtest_results = run_backtest(draws)
    print_backtest_summary(backtest_results)