import os

import psycopg

from scripts.backtest_v1 import DB_CONFIG

from scripts.momentum_engine_v7 import (
    build_analysis,
    get_frozen_top5,
    group_by_grade,
)


ENGINE_VERSION = "7.0.0"
FROZEN_STRATEGY = "long_hot_v1"
FROZEN_STRATEGY_VERSION = "1.0.0"


# ============================================================
# 資料庫
# ============================================================

def load_draws_from_db():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        connect_args = (database_url,)
        connect_kwargs = {}
    else:
        password = os.getenv("BIGLOTTO_DB_PASSWORD")

        if not password:
            raise RuntimeError(
                "找不到 DATABASE_URL 或 BIGLOTTO_DB_PASSWORD 環境變數"
            )

        connect_args = ()
        connect_kwargs = {
            **DB_CONFIG,
            "password": password,
        }

    with psycopg.connect(
        *connect_args,
        **connect_kwargs,
    ) as conn:
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
            draws = cur.fetchall()

    if len(draws) < 100:
        raise ValueError(
            "歷史資料不足100期，無法執行正式動能分析"
        )

    return draws


def normalize_include_special(include_special=False):
    if isinstance(include_special, str):
        return include_special.strip().lower() in {
            "1",
            "true",
            "yes",
            "with_special",
            "special",
        }

    return bool(include_special)


def analysis_mode_name(include_special=False):
    return (
        "main_plus_special"
        if normalize_include_special(include_special)
        else "main_only"
    )



# ============================================================
# 最新一期
# ============================================================

def build_latest_draw(
    draws
):
    latest = draws[-1]

    return {
        "draw_no": str(
            latest[0]
        ),
        "draw_date": str(
            latest[1]
        ),
        "numbers": list(
            latest[2:8]
        ),
        "special_number":
            latest[8],
    }



# ============================================================
# 單一號碼轉成 API 格式
# ============================================================

def serialize_number(
    item
):
    return {
        "number":
            item["number"],

        "score":
            item["score"],

        "grade":
            item["grade"],

        "momentum": {
            "10":
                round(
                    item["r10"],
                    2,
                ),

            "30":
                round(
                    item["r30"],
                    2,
                ),

            "50":
                round(
                    item["r50"],
                    2,
                ),

            "100":
                round(
                    item["r100"],
                    2,
                ),
        },

        "momentum_type":
            item[
                "momentum_type"
            ],

        "frozen_score":
            round(
                item[
                    "frozen_score"
                ],
                3,
            ),

        "reasons":
            item["reasons"],

        "warnings":
            item["warnings"],
    }


# ============================================================
# 完整 V7 分析
# ============================================================

def get_full_analysis(include_special=False):
    include_special = normalize_include_special(include_special)
    draws = load_draws_from_db()

    results = build_analysis(
        draws,
        include_special=include_special,
    )

    frozen_top5 = (
        get_frozen_top5(
            results
        )
    )

    grades = group_by_grade(
        results
    )

    return {
        "engine_version":
            ENGINE_VERSION,

        "analysis_engine":
            "momentum_v7_biglotto",

        "analysis_mode":
            analysis_mode_name(
                include_special
            ),

        "include_special":
            include_special,

        "expected_probability":
            (
                "7/49"
                if include_special
                else "6/49"
            ),

        "latest_draw":
            build_latest_draw(
                draws
            ),

        "analysis_top5": [
            x["number"]
            for x in results[:5]
        ],

        "analysis_top10": [
            x["number"]
            for x in results[:10]
        ],

        "frozen_strategy": {
            "name":
                FROZEN_STRATEGY,

            "version":
                FROZEN_STRATEGY_VERSION,

            "rule":
                "50期50% + 100期50%",

            "top5": [
                x["number"]
                for x in frozen_top5
            ],
        },

        "grades":
            grades,

        "numbers": [
            serialize_number(
                x
            )
            for x in results
        ],
    }


# ============================================================
# 舊 momentum API 相容格式
#
# 保留原本：
# engine_version
# latest_draw
# top5
# top10
# grades
# numbers
# ============================================================

def get_momentum_analysis(include_special=False):
    include_special = normalize_include_special(include_special)
    data = get_full_analysis(include_special=include_special)

    return {
        "engine_version":
            data[
                "engine_version"
            ],

        "analysis_mode":
            data[
                "analysis_mode"
            ],

        "include_special":
            data[
                "include_special"
            ],

        "latest_draw":
            data[
                "latest_draw"
            ],

        "top5":
            data[
                "analysis_top5"
            ],

        "top10":
            data[
                "analysis_top10"
            ],

        "grades":
            data[
                "grades"
            ],

        "numbers":
            data[
                "numbers"
            ],

        "frozen_top5":
            data[
                "frozen_strategy"
            ][
                "top5"
            ],
    }



# ============================================================
# 今日動能變化榜
# 以最新一期 V7 與「移除最新一期後」的上一期 V7 做同口徑比較。
# 不寫入資料庫，不改變既有 V7 排名邏輯。
# ============================================================

def _rank_map(results):
    return {
        item["number"]: index + 1
        for index, item in enumerate(results)
    }


def _grade_rank(grade):
    # 數字越小代表等級越高
    return {
        "A": 1,
        "B": 2,
        "C": 3,
        "D": 4,
        "E": 5,
    }.get(str(grade), 99)


def _round_delta(value):
    return round(float(value), 3)


def get_today_momentum_changes(include_special=False):
    include_special = normalize_include_special(include_special)
    draws = load_draws_from_db()

    # build_analysis 本身至少需要足夠歷史資料；正式環境目前已要求 >= 100 期。
    if len(draws) < 101:
        raise ValueError("歷史資料不足101期，無法比較最新一期與上一期動能")

    current_results = build_analysis(
        draws,
        include_special=include_special,
    )
    previous_results = build_analysis(
        draws[:-1],
        include_special=include_special,
    )

    current_map = {
        item["number"]: item
        for item in current_results
    }
    previous_map = {
        item["number"]: item
        for item in previous_results
    }

    current_rank = _rank_map(current_results)
    previous_rank = _rank_map(previous_results)

    current_top5 = [item["number"] for item in current_results[:5]]
    previous_top5 = [item["number"] for item in previous_results[:5]]

    changes = []

    for number in range(1, 50):
        current = current_map.get(number)
        previous = previous_map.get(number)

        if current is None or previous is None:
            continue

        current_score = float(current["score"])
        previous_score = float(previous["score"])
        score_delta = _round_delta(current_score - previous_score)

        current_grade = str(current["grade"])
        previous_grade = str(previous["grade"])

        current_warnings = list(current.get("warnings", []))
        previous_warnings = list(previous.get("warnings", []))

        item = {
            "number": number,
            "score": round(current_score, 3),
            "previous_score": round(previous_score, 3),
            "score_delta": score_delta,
            "grade": current_grade,
            "previous_grade": previous_grade,
            "grade_changed": current_grade != previous_grade,
            "grade_improved": (
                _grade_rank(current_grade) < _grade_rank(previous_grade)
            ),
            "grade_declined": (
                _grade_rank(current_grade) > _grade_rank(previous_grade)
            ),
            "rank": current_rank.get(number),
            "previous_rank": previous_rank.get(number),
            "rank_delta": (
                previous_rank.get(number, 40) - current_rank.get(number, 40)
            ),
            "momentum_delta": {
                "10": _round_delta(float(current["r10"]) - float(previous["r10"])),
                "30": _round_delta(float(current["r30"]) - float(previous["r30"])),
                "50": _round_delta(float(current["r50"]) - float(previous["r50"])),
                "100": _round_delta(float(current["r100"]) - float(previous["r100"])),
            },
            "frozen_score_delta": _round_delta(
                float(current["frozen_score"]) -
                float(previous["frozen_score"])
            ),
            "warning_count": len(current_warnings),
            "previous_warning_count": len(previous_warnings),
            "warning_delta": len(current_warnings) - len(previous_warnings),
            "entered_top5": (
                number in current_top5 and number not in previous_top5
            ),
            "left_top5": (
                number not in current_top5 and number in previous_top5
            ),
        }

        changes.append(item)

    rising_top5 = sorted(
        changes,
        key=lambda item: (-item["score_delta"], item["number"]),
    )[:5]

    falling_top5 = sorted(
        changes,
        key=lambda item: (item["score_delta"], item["number"]),
    )[:5]

    grade_changes = [
        item for item in changes
        if item["grade_changed"]
    ]
    grade_changes.sort(
        key=lambda item: (
            0 if item["grade_improved"] else 1,
            -abs(item["score_delta"]),
            item["number"],
        )
    )

    key_changes = [
        item for item in changes
        if (
            item["entered_top5"]
            or item["left_top5"]
            or item["warning_delta"] != 0
        )
    ]
    key_changes.sort(
        key=lambda item: (
            0 if item["entered_top5"] else
            1 if item["left_top5"] else 2,
            -abs(item["score_delta"]),
            item["number"],
        )
    )

    return {
        "engine_version": ENGINE_VERSION,
        "analysis_mode": analysis_mode_name(include_special),
        "include_special": include_special,
        "comparison": {
            "current_draw": build_latest_draw(draws),
            "previous_draw": build_latest_draw(draws[:-1]),
        },
        "rising_top5": rising_top5,
        "falling_top5": falling_top5,
        "grade_changes": grade_changes,
        "key_changes": key_changes,
        "all_changes": changes,
    }


# ============================================================
# A1 爆發動能（大樂透）
#
# 沿用 539 已定案公式：
# (r10-r30)*50% + (r30-r50)*30% + (r10-r100)*20%
#
# include_special=False：主號 6/49
# include_special=True ：主號+特別號 7/49
# ============================================================

def calculate_explosion_score(r10, r30, r50, r100):
    short_gap = r10 - r30
    mid_gap = r30 - r50
    long_gap = r10 - r100

    explosion_score = (
        short_gap * 0.50
        + mid_gap * 0.30
        + long_gap * 0.20
    )

    return {
        "explosion_score": round(explosion_score, 4),
        "short_gap": round(short_gap, 4),
        "mid_gap": round(mid_gap, 4),
        "long_gap": round(long_gap, 4),
    }


def classify_explosion_stage(explosion_score, r10):
    if r10 < 1.10:
        return "無明顯爆發"
    if explosion_score >= 1.00:
        return "過熱候選"
    if explosion_score >= 0.70:
        return "爆發延續"
    if explosion_score >= 0.50:
        return "升溫觀察"
    if explosion_score >= 0.30:
        return "強爆發"
    if explosion_score >= 0.20:
        return "初步升溫"
    return "無明顯爆發"


def get_explosion_momentum(include_special=False):
    include_special = normalize_include_special(include_special)
    draws = load_draws_from_db()
    results = build_analysis(
        draws,
        include_special=include_special,
    )

    output = []

    for item in results:
        r10 = float(item["r10"])
        r30 = float(item["r30"])
        r50 = float(item["r50"])
        r100 = float(item["r100"])

        explosion = calculate_explosion_score(
            r10, r30, r50, r100
        )
        explosion_score = explosion["explosion_score"]
        stage = classify_explosion_stage(
            explosion_score, r10
        )

        if stage == "無明顯爆發":
            continue

        output.append({
            "number": int(item["number"]),
            "explosion_score": explosion_score,
            "stage": stage,
            "v7_score": item["score"],
            "grade": item["grade"],
            "momentum_type": item["momentum_type"],
            "momentum": {
                "10": round(r10, 2),
                "30": round(r30, 2),
                "50": round(r50, 2),
                "100": round(r100, 2),
            },
            "change": {
                "short_gap": explosion["short_gap"],
                "mid_gap": explosion["mid_gap"],
                "long_gap": explosion["long_gap"],
            },
        })

    stage_priority = {
        "強爆發": 0,
        "爆發延續": 1,
        "升溫觀察": 2,
        "初步升溫": 3,
        "過熱候選": 4,
    }

    output.sort(
        key=lambda x: (
            stage_priority.get(x["stage"], 99),
            -x["explosion_score"],
            -x["momentum"]["10"],
            x["number"],
        )
    )

    return {
        "engine_version": ENGINE_VERSION,
        "analysis_engine": "explosion_momentum_a1_biglotto",
        "formula_version": "a1_v1.0",
        "analysis_mode": analysis_mode_name(include_special),
        "include_special": include_special,
        "expected_probability": (
            "7/49" if include_special else "6/49"
        ),
        "latest_draw": build_latest_draw(draws),
        "rule": {
            "min_r10": 1.10,
            "formula": (
                "(r10-r30)*0.50 + "
                "(r30-r50)*0.30 + "
                "(r10-r100)*0.20"
            ),
        },
        "count": len(output),
        "numbers": output,
    }


# ============================================================
# V7 Top10
# ============================================================

def get_top10(include_special=False):
    include_special = normalize_include_special(include_special)
    data = get_full_analysis(include_special=include_special)

    number_map = {
        item["number"]: item
        for item
        in data["numbers"]
    }

    output = []

    for number in data[
        "analysis_top10"
    ]:
        output.append(
            number_map[
                number
            ]
        )

    return {
        "engine_version":
            ENGINE_VERSION,

        "latest_draw":
            data[
                "latest_draw"
            ],

        "top10":
            output,
    }


# ============================================================
# A-E 分級
# ============================================================

def get_grades(include_special=False):
    include_special = normalize_include_special(include_special)
    data = get_full_analysis(include_special=include_special)

    return {
        "engine_version":
            ENGINE_VERSION,

        "latest_draw":
            data[
                "latest_draw"
            ],

        "grades":
            data[
                "grades"
            ],
    }


# ============================================================
# V6 凍結 Top5
# ============================================================

def get_frozen_prediction(include_special=False):
    include_special = normalize_include_special(include_special)
    data = get_full_analysis(include_special=include_special)

    frozen_numbers = data[
        "frozen_strategy"
    ][
        "top5"
    ]

    number_map = {
        item["number"]: item
        for item
        in data["numbers"]
    }

    details = [
        number_map[number]
        for number
        in frozen_numbers
    ]

    return {
        "engine_version":
            ENGINE_VERSION,

        "strategy_name":
            FROZEN_STRATEGY,

        "strategy_version":
            FROZEN_STRATEGY_VERSION,

        "rule":
            "50期50% + 100期50%",

        "latest_draw":
            data[
                "latest_draw"
            ],

        "top5":
            frozen_numbers,

        "details":
            details,
    }

# ============================================================
# 最近 N 期開獎 + 各期「當時」A-E 分級（大樂透）
#
# - 預設最近 10 期，最多 100 期。
# - 每一期只使用截至該期為止的歷史資料計算分級，
#   不會使用該期之後的未來資料。
# - 主號固定顯示 6 顆。
# - 特別號獨立顯示，並附上該期當時的 A-E 分級。
# - include_special=False：A-E 依 6/49 模式計算。
# - include_special=True：A-E 依 7/49（主號+特別號）模式計算。
# ============================================================

def get_recent_draws_with_grades(limit=10, include_special=False):
    include_special = normalize_include_special(include_special)
    draws = load_draws_from_db()

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10

    limit = max(1, min(limit, 100))

    # V7 正式分析至少需要 100 期，因此最早只能從第 100 期開始回推。
    first_index = max(99, len(draws) - limit)
    output = []

    for index in range(len(draws) - 1, first_index - 1, -1):
        # 嚴格使用「截至該期」的資料，避免未來資料洩漏。
        historical_draws = draws[:index]

        results = build_analysis(
            historical_draws,
            include_special=include_special,
        )

        grade_map = {
            int(item["number"]): item["grade"]
            for item in results
        }

        draw = draws[index]
        main_numbers = list(draw[2:8])
        special_number = int(draw[8])

        output.append({
            "draw_no": str(draw[0]),
            "draw_date": str(draw[1]),
            "numbers": [
                {
                    "number": int(number),
                    "grade": grade_map.get(int(number)),
                }
                for number in main_numbers
            ],
            "special_number": {
                "number": special_number,
                "grade": grade_map.get(special_number),
            },
        })

    return {
        "engine_version": ENGINE_VERSION,
        "analysis_mode": analysis_mode_name(include_special),
        "include_special": include_special,
        "count": len(output),
        "draws": output,
    }

