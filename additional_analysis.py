"""Big Lotto A4-A7 and smart-selection, preserving the existing App JSON schema.

Uses the existing Big Lotto database loader, V7 engine and A1-A3 signals.
The analysis mode changes statistics, never the six main numbers per ticket.
The smart-selection count above six denotes a mother-number pool, not a ticket.
"""
import random

import momentum_engine as core
from scripts.momentum_engine_v7 import (
    TOTAL_NUMBERS,
    numbers_from_draw,
    numbers_per_draw,
)


def calculate_overheat_score(
    r10,
    r30,
    r50,
    r100,
):
    long_base = (r50 + r100) / 2.0
    short_excess = max(0.0, r10 - r30)
    long_excess = max(0.0, r10 - long_base)

    overheat_score = (
        short_excess * 0.50
        + long_excess * 0.50
    )

    return {
        "overheat_score": round(overheat_score, 4),
        "long_base": round(long_base, 4),
        "short_excess": round(short_excess, 4),
        "long_excess": round(long_excess, 4),
    }


def classify_overheat_stage(
    r10,
    overheat_score,
    previous_overheat_score,
):
    # A4 是風險層級，不以分數高低作為推薦排序。
    # 先把真正短期極強且延伸幅度大的號碼列為最高警示。
    if r10 >= 2.40 and overheat_score >= 1.40:
        return "高度過熱"

    # 前一期已熱、這一期仍維持明顯延伸，代表熱度具有延續性。
    if (
        r10 >= 2.10
        and overheat_score >= 1.10
        and previous_overheat_score >= 0.80
    ):
        return "過熱警示"

    # 中高短期強度 + 明顯超越中長期基準。
    if r10 >= 1.80 and overheat_score >= 0.80:
        return "過熱觀察"

    # 剛進入熱區，但尚不足以判定為正式過熱。
    if r10 >= 1.50 and overheat_score >= 0.50:
        return "升溫偏熱"

    return "無明顯過熱"


def get_overheat_momentum(include_special=False):
    include_special = core.normalize_include_special(include_special)
    draws = core.load_draws_from_db()

    if len(draws) < 101:
        raise ValueError("歷史資料不足101期，無法執行 A4 過熱警示分析")

    current_results = core.build_analysis(draws, include_special=include_special)
    previous_results = core.build_analysis(draws[:-1], include_special=include_special)

    previous_map = {
        int(item["number"]): item
        for item in previous_results
    }

    output = []

    for item in current_results:
        number = int(item["number"])
        previous = previous_map.get(number)

        if not previous:
            continue

        r10 = float(item["r10"])
        r30 = float(item["r30"])
        r50 = float(item["r50"])
        r100 = float(item["r100"])

        previous_r10 = float(previous["r10"])
        previous_r30 = float(previous["r30"])
        previous_r50 = float(previous["r50"])
        previous_r100 = float(previous["r100"])

        current_heat = calculate_overheat_score(
            r10,
            r30,
            r50,
            r100,
        )

        previous_heat = calculate_overheat_score(
            previous_r10,
            previous_r30,
            previous_r50,
            previous_r100,
        )

        overheat_score = current_heat["overheat_score"]
        previous_overheat_score = previous_heat["overheat_score"]

        # A4 主清單只收進入偏熱區的號碼。
        # r10 與 overheat_score 同時設最低門檻，避免一般波動被誤標。
        if not (
            r10 >= 1.50
            and overheat_score >= 0.50
        ):
            continue

        stage = classify_overheat_stage(
            r10,
            overheat_score,
            previous_overheat_score,
        )

        if stage == "無明顯過熱":
            continue

        output.append({
            "number": number,
            "overheat_score": overheat_score,
            "stage": stage,
            "risk": True,
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
                "long_base": current_heat["long_base"],
                "short_excess": current_heat["short_excess"],
                "long_excess": current_heat["long_excess"],
                "previous_overheat_score": previous_overheat_score,
                "overheat_change": round(
                    overheat_score - previous_overheat_score,
                    4,
                ),
            },
        })

    # A4 排的是「風險嚴重度」，不是推薦優先度。
    stage_priority = {
        "高度過熱": 0,
        "過熱警示": 1,
        "過熱觀察": 2,
        "升溫偏熱": 3,
    }

    output.sort(
        key=lambda x: (
            stage_priority.get(x["stage"], 99),
            -x["overheat_score"],
            -x["momentum"]["10"],
            x["number"],
        )
    )

    return {
        "engine_version": core.ENGINE_VERSION,
        "analysis_mode": core.analysis_mode_name(include_special),
        "include_special": include_special,
        "expected_probability": f"{numbers_per_draw(include_special)}/{TOTAL_NUMBERS}",
        "analysis_engine": "overheat_warning_a4",
        "formula_version": "a4_v2.0_biglotto",
        "latest_draw": core.build_latest_draw(draws),
        "previous_draw": core.build_latest_draw(draws[:-1]),
        "rule": {
            "candidate": (
                "r10 >= 1.50 and overheat_score >= 0.50"
            ),
            "formula": (
                "long_base=(r50+r100)/2; "
                "short_excess=max(0,r10-r30); "
                "long_excess=max(0,r10-long_base); "
                "overheat_score=short_excess*0.50 + long_excess*0.50"
            ),
            "note": (
                "A4 為風險警示模組；分數越高代表過度延伸風險越高，"
                "不代表推薦程度越高。前一期熱度用於辨識持續過熱。"
            ),
        },
        "count": len(output),
        "numbers": output,
    }


def _count_number_in_recent_draws(draws, number, window, include_special=False):
    recent_draws = draws[-window:]
    return sum(
        1
        for draw in recent_draws
        if number in numbers_from_draw(draw, include_special=include_special)
    )


def get_draw_counts(include_special=False):
    include_special = core.normalize_include_special(include_special)
    draws = core.load_draws_from_db()
    results = core.build_analysis(draws, include_special=include_special)

    windows = (10, 30, 50, 100)
    theoretical_average = {
        str(window): round(window * numbers_per_draw(include_special) / TOTAL_NUMBERS, 2)
        for window in windows
    }

    result_map = {
        int(item["number"]): item
        for item in results
    }

    output = []

    for number in range(1, TOTAL_NUMBERS + 1):
        item = result_map.get(number)

        counts = {
            str(window): _count_number_in_recent_draws(
                draws,
                number,
                window,
                include_special=include_special,
            )
            for window in windows
        }

        difference = {
            str(window): round(
                counts[str(window)] - theoretical_average[str(window)],
                2,
            )
            for window in windows
        }

        output.append({
            "number": number,
            "counts": counts,
            "theoretical_average": theoretical_average.copy(),
            "difference_from_average": difference,
            "v7_score": item["score"] if item else None,
            "grade": item["grade"] if item else None,
            "momentum_type": item["momentum_type"] if item else None,
            "momentum": {
                "10": round(float(item["r10"]), 2) if item else None,
                "30": round(float(item["r30"]), 2) if item else None,
                "50": round(float(item["r50"]), 2) if item else None,
                "100": round(float(item["r100"]), 2) if item else None,
            },
        })

    return {
        "engine_version": core.ENGINE_VERSION,
        "analysis_mode": core.analysis_mode_name(include_special),
        "include_special": include_special,
        "expected_probability": f"{numbers_per_draw(include_special)}/{TOTAL_NUMBERS}",
        "analysis_engine": "draw_counts_a5",
        "formula_version": "a5_v1.0_biglotto",
        "latest_draw": core.build_latest_draw(draws),
        "rule": {
            "windows": list(windows),
            "theoretical_average_formula": f"window * {numbers_per_draw(include_special)} / {TOTAL_NUMBERS}",
            "note": (
                "A5 僅顯示實際開出次數與理論平均差異；"
                "不是推薦分數，不改動 V7、A1、A2、A3、A4。"
            ),
        },
        "count": len(output),
        "numbers": output,
    }


def _get_current_missing_info(draws, number, include_special=False):
    missing_periods = 0

    for draw in reversed(draws):
        if number in numbers_from_draw(draw, include_special=include_special):
            return {
                "missing_periods": missing_periods,
                "last_draw_no": str(draw[0]),
                "last_draw_date": str(draw[1]),
            }

        missing_periods += 1

    return {
        "missing_periods": len(draws),
        "last_draw_no": None,
        "last_draw_date": None,
    }


def get_missing_numbers(include_special=False):
    include_special = core.normalize_include_special(include_special)
    draws = core.load_draws_from_db()
    results = core.build_analysis(draws, include_special=include_special)

    windows = (10, 30, 50, 100)
    theoretical_average = {
        str(window): round(window * numbers_per_draw(include_special) / TOTAL_NUMBERS, 2)
        for window in windows
    }

    result_map = {
        int(item["number"]): item
        for item in results
    }

    output = []

    for number in range(1, TOTAL_NUMBERS + 1):
        item = result_map.get(number)
        missing = _get_current_missing_info(draws, number, include_special=include_special)

        counts = {
            str(window): _count_number_in_recent_draws(
                draws,
                number,
                window,
                include_special=include_special,
            )
            for window in windows
        }

        difference = {
            str(window): round(
                counts[str(window)] - theoretical_average[str(window)],
                2,
            )
            for window in windows
        }

        output.append({
            "number": number,
            "missing_periods": missing["missing_periods"],
            "last_draw_no": missing["last_draw_no"],
            "last_draw_date": missing["last_draw_date"],
            "counts": counts,
            "theoretical_average": theoretical_average.copy(),
            "difference_from_average": difference,
            "v7_score": item["score"] if item else None,
            "grade": item["grade"] if item else None,
            "momentum_type": item["momentum_type"] if item else None,
            "momentum": {
                "10": round(float(item["r10"]), 2) if item else None,
                "30": round(float(item["r30"]), 2) if item else None,
                "50": round(float(item["r50"]), 2) if item else None,
                "100": round(float(item["r100"]), 2) if item else None,
            },
        })

    # API 預設依「目前遺漏期數由高到低」排列；
    # 同遺漏期數時以 V7 分數較高者在前，最後以號碼排序。
    output.sort(
        key=lambda x: (
            -x["missing_periods"],
            -(float(x["v7_score"]) if x["v7_score"] is not None else -999999.0),
            x["number"],
        )
    )

    return {
        "engine_version": core.ENGINE_VERSION,
        "analysis_mode": core.analysis_mode_name(include_special),
        "include_special": include_special,
        "expected_probability": f"{numbers_per_draw(include_special)}/{TOTAL_NUMBERS}",
        "analysis_engine": "missing_numbers_a6",
        "formula_version": "a6_v1.0_biglotto",
        "latest_draw": core.build_latest_draw(draws),
        "rule": {
            "definition": (
                "最新一期有開出=遺漏0期；往前每隔一個未開出期數加1，"
                "直到找到最近一次開出"
            ),
            "windows": list(windows),
            "theoretical_average_formula": f"window * {numbers_per_draw(include_special)} / {TOTAL_NUMBERS}",
            "default_sort": "missing_periods_desc",
            "note": (
                "A6 為統計狀態資訊；遺漏期數較高不代表下一期開出機率提高，"
                "不改動 V7、A1、A2、A3、A4、A5。"
            ),
        },
        "count": len(output),
        "numbers": output,
    }


def _median(values):
    ordered = sorted(values)
    size = len(ordered)
    if size == 0:
        return 0.0
    middle = size // 2
    if size % 2 == 1:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def get_count_missing_cross(window=30, include_special=False):
    include_special = core.normalize_include_special(include_special)
    if window not in (10, 30, 50, 100):
        raise ValueError("window 僅支援 10、30、50、100")

    draws = core.load_draws_from_db()
    results = core.build_analysis(draws, include_special=include_special)

    theoretical_average = window * numbers_per_draw(include_special) / TOTAL_NUMBERS
    result_map = {
        int(item["number"]): item
        for item in results
    }

    missing_map = {}
    missing_values = []
    for number in range(1, TOTAL_NUMBERS + 1):
        missing = _get_current_missing_info(draws, number, include_special=include_special)
        missing_map[number] = missing
        missing_values.append(int(missing["missing_periods"]))

    missing_median = _median(missing_values)

    # 直接沿用既有 A2 / A3 / A4 的正式候選判定，不重寫其公式。
    recovery_numbers = {
        int(item["number"]): item
        for item in core.get_recovery_momentum(include_special=include_special)["numbers"]
    }
    reversal_numbers = {
        int(item["number"]): item
        for item in core.get_reversal_momentum(include_special=include_special)["numbers"]
    }
    overheat_numbers = {
        int(item["number"]): item
        for item in get_overheat_momentum(include_special=include_special)["numbers"]
    }

    output = []

    for number in range(1, TOTAL_NUMBERS + 1):
        item = result_map.get(number)
        missing = missing_map[number]
        missing_periods = int(missing["missing_periods"])
        count = _count_number_in_recent_draws(draws, number, window, include_special=include_special)

        frequency_ratio = (
            count / theoretical_average
            if theoretical_average > 0
            else 0.0
        )

        # 次數高低：相對該視窗理論平均。
        frequency_level = (
            "高頻"
            if count >= theoretical_average
            else "低頻"
        )

        # A7 v1.1：遺漏改為三層，避免中位數只有 4 期時，
        # 把遺漏 4～5 期直接稱為「長漏」而造成誤解。
        # 短漏：低於中位數
        # 一般遺漏：中位數 ～ 未滿 2 倍中位數
        # 高遺漏：至少 2 倍中位數
        high_missing_threshold = (
            missing_median * 2.0
            if missing_median > 0
            else 1.0
        )

        if missing_periods < missing_median:
            missing_level = "短漏"
        elif missing_periods < high_missing_threshold:
            missing_level = "一般遺漏"
        else:
            missing_level = "高遺漏"

        cross_type = f"{frequency_level}{missing_level}"

        has_recovery = number in recovery_numbers
        has_reversal = number in reversal_numbers
        has_overheat = number in overheat_numbers

        # 觀察標籤有優先順序：
        # A4 是風險層，優先標示；A2/A3 是轉強訊號；
        # 若都沒有，再回到次數 × 遺漏的純狀態描述。
        if has_overheat:
            observation = "過熱注意"
            observation_priority = 5
        elif has_recovery and missing_level == "高遺漏":
            observation = "回補觀察"
            observation_priority = 4
        elif has_reversal:
            observation = "反轉觀察"
            observation_priority = 3
        elif cross_type == "高頻短漏":
            observation = "持續活躍"
            observation_priority = 2
        elif cross_type == "高頻一般遺漏":
            observation = "活躍觀察"
            observation_priority = 2
        elif cross_type == "高頻高遺漏":
            observation = "活躍後沉寂"
            observation_priority = 2
        elif cross_type == "低頻高遺漏":
            observation = "弱勢沉寂"
            observation_priority = 1
        elif cross_type == "低頻一般遺漏":
            observation = "低頻觀察"
            observation_priority = 1
        else:
            observation = "低頻近期出現"
            observation_priority = 1

        # 交叉指標只用於頁面排序，不取代 V7。
        # frequency_ratio 代表開出次數相對理論平均；
        # missing_ratio 代表遺漏相對當期中位數。
        missing_ratio = (
            missing_periods / missing_median
            if missing_median > 0
            else float(missing_periods)
        )
        v7_score = float(item["score"]) if item else 0.0
        v7_ratio = max(0.0, min(v7_score / 100.0, 1.0))

        cross_score = (
            min(frequency_ratio, 2.5) * 0.40
            + min(missing_ratio, 3.0) * 0.30
            + v7_ratio * 0.30
        )

        # 過熱是風險提示，不把它當加分推薦。
        if has_overheat:
            cross_score -= 0.20

        output.append({
            "number": number,
            "window": window,
            "count": count,
            "theoretical_average": round(theoretical_average, 2),
            "difference_from_average": round(
                count - theoretical_average,
                2,
            ),
            "frequency_ratio": round(frequency_ratio, 3),
            "frequency_level": frequency_level,
            "missing_periods": missing_periods,
            "missing_median": round(missing_median, 2),
            "missing_ratio": round(missing_ratio, 3),
            "missing_level": missing_level,
            "cross_type": cross_type,
            "observation": observation,
            "signals": {
                "recovery_a2": has_recovery,
                "reversal_a3": has_reversal,
                "overheat_a4": has_overheat,
            },
            "cross_score": round(cross_score, 4),
            "last_draw_no": missing["last_draw_no"],
            "last_draw_date": missing["last_draw_date"],
            "v7_score": item["score"] if item else None,
            "grade": item["grade"] if item else None,
            "momentum_type": item["momentum_type"] if item else None,
            "momentum": {
                "10": round(float(item["r10"]), 2) if item else None,
                "30": round(float(item["r30"]), 2) if item else None,
                "50": round(float(item["r50"]), 2) if item else None,
                "100": round(float(item["r100"]), 2) if item else None,
            },
            "_observation_priority": observation_priority,
        })

    # 預設先看有明確訊號者，再看交叉指標。
    output.sort(
        key=lambda x: (
            -x["_observation_priority"],
            -x["cross_score"],
            -x["v7_score"] if x["v7_score"] is not None else 999999,
            x["number"],
        )
    )

    for item in output:
        item.pop("_observation_priority", None)

    type_counts = {
        cross_type: sum(
            1 for x in output
            if x["cross_type"] == cross_type
        )
        for cross_type in (
            "高頻短漏",
            "高頻一般遺漏",
            "高頻高遺漏",
            "低頻短漏",
            "低頻一般遺漏",
            "低頻高遺漏",
        )
    }

    return {
        "engine_version": core.ENGINE_VERSION,
        "analysis_mode": core.analysis_mode_name(include_special),
        "include_special": include_special,
        "expected_probability": f"{numbers_per_draw(include_special)}/{TOTAL_NUMBERS}",
        "analysis_engine": "count_missing_cross_a7",
        "formula_version": "a7_v1.1_biglotto",
        "latest_draw": core.build_latest_draw(draws),
        "window": window,
        "rule": {
            "frequency": (
                f"高頻=count >= window*{numbers_per_draw(include_special)}/{TOTAL_NUMBERS}；"
                f"低頻=count < window*{numbers_per_draw(include_special)}/{TOTAL_NUMBERS}"
            ),
            "missing": (
                "短漏=目前遺漏期數 < 49號遺漏中位數；"
                "一般遺漏=中位數 <= 遺漏期數 < 2倍中位數；"
                "高遺漏=遺漏期數 >= 2倍中位數"
            ),
            "cross_types": [
                "高頻短漏",
                "高頻一般遺漏",
                "高頻高遺漏",
                "低頻短漏",
                "低頻一般遺漏",
                "低頻高遺漏",
            ],
            "signal_overlay": (
                "沿用 A2 回補、A3 反轉、A4 過熱正式候選結果；"
                "A4 為風險優先提示"
            ),
            "cross_score_note": (
                "cross_score 僅供 A7 頁面排序，不取代 V7，"
                "也不代表下一期中獎機率。"
            ),
        },
        "missing_median": round(missing_median, 2),
        "high_missing_threshold": round(
            missing_median * 2.0 if missing_median > 0 else 1.0,
            2,
        ),
        "theoretical_average": round(theoretical_average, 2),
        "type_counts": type_counts,
        "count": len(output),
        "numbers": output,
    }


SMART_SELECTION_STRATEGIES = {
    "v7": "V7 動能",
    "explosion": "爆發",
    "recovery": "回補",
    "reversal": "反轉",
    "balanced": "均衡",
}


def _smart_base_item(item):
    return {
        "number": int(item["number"]),
        "v7_score": item.get("score"),
        "grade": item.get("grade"),
        "momentum_type": item.get("momentum_type"),
        "momentum": {
            "10": round(float(item["r10"]), 2),
            "30": round(float(item["r30"]), 2),
            "50": round(float(item["r50"]), 2),
            "100": round(float(item["r100"]), 2),
        },
    }


def _parse_number_constraint(value):
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        text = str(value).strip()
        if not text:
            return []
        raw_items = text.split(",")

    output = []
    seen = set()

    for raw in raw_items:
        try:
            number = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError("鎖定／排除號碼必須是 1～49 的整數")

        if number < 1 or number > TOTAL_NUMBERS:
            raise ValueError("鎖定／排除號碼必須介於 1 到 49")

        if number not in seen:
            seen.add(number)
            output.append(number)

    return output


def get_smart_selection(count=6, strategy="balanced", locked=None, excluded=None, include_special=False):
    include_special = core.normalize_include_special(include_special)
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ValueError("母號數量必須是 6 到 10 的整數")

    if count < 6 or count > 10:
        raise ValueError("母號數量必須介於 6 到 10")

    strategy = str(strategy or "balanced").strip().lower()
    if strategy not in SMART_SELECTION_STRATEGIES:
        raise ValueError(
            "strategy 必須是 v7、explosion、recovery、reversal、balanced 其中之一"
        )

    locked_numbers = _parse_number_constraint(locked)
    excluded_numbers = _parse_number_constraint(excluded)
    locked_set = set(locked_numbers)
    excluded_set = set(excluded_numbers)

    overlap = locked_set & excluded_set
    if overlap:
        raise ValueError(
            "同一號碼不能同時鎖定與排除：" +
            ",".join(str(n) for n in sorted(overlap))
        )

    if len(locked_numbers) > count:
        raise ValueError("鎖定號碼數量不能超過母號數量")

    draws = core.load_draws_from_db()
    results = core.build_analysis(draws, include_special=include_special)
    result_map = {int(item["number"]): item for item in results}
    v7_order = [int(item["number"]) for item in results]

    strategy_details = {}

    if strategy == "v7":
        ranked_numbers = list(v7_order)

    elif strategy == "explosion":
        source = core.get_explosion_momentum(include_special=include_special)["numbers"]
        primary = [int(item["number"]) for item in source]
        ranked_numbers = primary + [n for n in v7_order if n not in set(primary)]
        strategy_details = {
            int(item["number"]): {
                "source_stage": item.get("stage"),
                "source_score": item.get("explosion_score"),
            } for item in source
        }

    elif strategy == "recovery":
        source = core.get_recovery_momentum(include_special=include_special)["numbers"]
        primary = [int(item["number"]) for item in source]
        ranked_numbers = primary + [n for n in v7_order if n not in set(primary)]
        strategy_details = {
            int(item["number"]): {
                "source_stage": item.get("stage"),
                "source_score": item.get("recovery_score"),
            } for item in source
        }

    elif strategy == "reversal":
        source = core.get_reversal_momentum(include_special=include_special)["numbers"]
        primary = [int(item["number"]) for item in source]
        ranked_numbers = primary + [n for n in v7_order if n not in set(primary)]
        strategy_details = {
            int(item["number"]): {
                "source_stage": item.get("stage"),
                "source_score": item.get("reversal_score"),
            } for item in source
        }

    else:
        explosion = core.get_explosion_momentum(include_special=include_special)["numbers"]
        recovery = core.get_recovery_momentum(include_special=include_special)["numbers"]
        reversal = core.get_reversal_momentum(include_special=include_special)["numbers"]
        overheat = get_overheat_momentum(include_special=include_special)["numbers"]

        ranking_points = {number: 0.0 for number in range(1, TOTAL_NUMBERS + 1)}
        sources = {number: [] for number in range(1, TOTAL_NUMBERS + 1)}

        def add_ranked_points(items, label, weight):
            total = len(items)
            if total <= 0:
                return
            for rank, item in enumerate(items, start=1):
                number = int(item["number"])
                rank_value = (total - rank + 1) / total
                ranking_points[number] += rank_value * weight
                sources[number].append(label)

        add_ranked_points(results, "V7", 0.40)
        add_ranked_points(explosion, "A1爆發", 0.20)
        add_ranked_points(recovery, "A2回補", 0.20)
        add_ranked_points(reversal, "A3反轉", 0.20)

        heat_penalty = {
            "高度過熱": 0.30,
            "過熱警示": 0.22,
            "過熱觀察": 0.14,
            "升溫偏熱": 0.07,
        }
        heat_map = {}

        for item in overheat:
            number = int(item["number"])
            stage = item.get("stage")
            penalty = heat_penalty.get(stage, 0.0)
            ranking_points[number] -= penalty
            heat_map[number] = {
                "stage": stage,
                "overheat_score": item.get("overheat_score"),
                "penalty": penalty,
            }

        ranked_numbers = sorted(
            range(1, TOTAL_NUMBERS + 1),
            key=lambda number: (
                -ranking_points[number],
                -float(result_map[number]["score"]),
                number,
            ),
        )

        strategy_details = {
            number: {
                "balanced_score": round(ranking_points[number], 4),
                "sources": sources[number],
                "overheat": heat_map.get(number),
            } for number in range(1, TOTAL_NUMBERS + 1)
        }

    # B3 重新產生：
    # - 鎖定號碼永遠保留。
    # - 排除號碼永遠不加入。
    # - 其餘位置不是 1~49 純隨機，而是從「目前策略排名前段」抽取。
    # - 排名越前面的號碼權重越高，因此保留策略方向，同時讓重新產生有變化。
    selected_numbers = list(locked_numbers)
    selected_set = set(selected_numbers)
    remaining_count = count - len(selected_numbers)

    eligible_ranked = [
        number
        for number in ranked_numbers
        if number not in excluded_set and number not in selected_set
    ]

    if len(eligible_ranked) < remaining_count:
        raise ValueError("排除條件過多，無法產生指定數量的母號")

    # 候選池維持在策略排名前段；母號越多，候選池同步放大。
    # 例如 count=6 時通常從前 17 名抽取，count=10 時從前 25 名抽取。
    candidate_pool_size = min(
        len(eligible_ranked),
        max(15, count * 2 + 5),
    )
    candidate_pool = eligible_ranked[:candidate_pool_size]

    rng = random.SystemRandom()

    # 依策略名次做加權不放回抽樣。
    # 第一名權重最高、候選池最後一名權重最低。
    for _ in range(remaining_count):
        weights = list(range(len(candidate_pool), 0, -1))
        chosen = rng.choices(candidate_pool, weights=weights, k=1)[0]
        selected_numbers.append(chosen)
        selected_set.add(chosen)
        candidate_pool.remove(chosen)

    output = []
    for number in selected_numbers[:count]:
        base = _smart_base_item(result_map[number])
        detail = dict(strategy_details.get(number) or {})
        if number in locked_set:
            detail["locked"] = True
        if detail:
            base["strategy_detail"] = detail
        output.append(base)

    return {
        "engine_version": core.ENGINE_VERSION,
        "analysis_mode": core.analysis_mode_name(include_special),
        "include_special": include_special,
        "expected_probability": f"{numbers_per_draw(include_special)}/{TOTAL_NUMBERS}",
        "analysis_engine": "smart_selection_b3",
        "formula_version": "b3_v1.0_biglotto",
        "latest_draw": core.build_latest_draw(draws),
        "strategy": strategy,
        "strategy_name": SMART_SELECTION_STRATEGIES[strategy],
        "requested_count": count,
        "count": len(output),
        "locked": locked_numbers,
        "excluded": excluded_numbers,
        "numbers": output,
        "rule": {
            "mother_number_range": [6, 10],
            "strategies": SMART_SELECTION_STRATEGIES,
            "locked": "鎖定號碼必定保留，且鎖定數量不可超過母號數量",
            "excluded": "排除號碼不會出現在結果中，且不能與鎖定號碼重複",
            "regenerate": (
                "未鎖定位置從目前策略排名前段做加權不放回抽樣；"
                "排名越前權重越高，因此每次重新產生可變化但仍保留策略方向"
            ),
            "balanced": (
                "依 V7/A1/A2/A3 正式排名整合；"
                "A4 過熱只作風險降權；A5 開出次數不直接納入推薦"
            ),
            "note": "智慧選牌為資料分析與組牌輔助，不代表或保證未來開獎結果。",
        },
    }
