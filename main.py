from additional_analysis import (
    get_overheat_momentum,
    get_draw_counts,
    get_missing_numbers,
    get_count_missing_cross,
    get_smart_selection,
)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import os
import psycopg
from psycopg.rows import dict_row

from momentum_engine import (
    get_momentum_analysis,
    get_today_momentum_changes,
    get_recent_draws_with_grades,
    get_explosion_momentum,
    get_recovery_momentum,
    get_reversal_momentum,
)


app = FastAPI(
    title="大樂透 Momentum API",
    version="7.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "status": "ok",
        "app": "大樂透 Momentum API",
        "version": "7.0.0",
    }


@app.get("/api/v1/biglotto/health")
def health():
    return {
        "status": "ok",
        "message": "Big Lotto Backend API is running",
    }


def load_analysis(include_special=False):
    try:
        return get_momentum_analysis(
            include_special=include_special
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@app.get("/api/v1/biglotto/analysis")
def analysis(
    include_special: bool = False
):
    result = load_analysis(
        include_special=include_special
    )

    return {
        "status": "ok",
        "data": result,
    }


@app.get("/api/v1/biglotto/momentum-changes")
def momentum_changes(
    include_special: bool = False
):
    result = get_today_momentum_changes(
        include_special=include_special
    )

    return {
        "status": "ok",
        "data": result,
    }


@app.get("/api/v1/biglotto/momentum")
def momentum(
    include_special: bool = False
):
    result = load_analysis(
        include_special=include_special
    )

    return {
        "status": "ok",
        "data": result,
    }


@app.get("/api/v1/biglotto/top10")
def top10(
    include_special: bool = False
):
    result = load_analysis(
        include_special=include_special
    )

    return {
        "status": "ok",
        "data": {
            "engine_version": result.get("engine_version"),
            "latest_draw": result.get("latest_draw"),
            "top10": result.get("top10", []),
        },
    }


@app.get("/api/v1/biglotto/grades")
def grades(
    include_special: bool = False
):
    result = load_analysis(
        include_special=include_special
    )

    return {
        "status": "ok",
        "data": {
            "engine_version": result.get("engine_version"),
            "latest_draw": result.get("latest_draw"),
            "grades": result.get("grades", {}),
        },
    }


@app.get("/api/v1/biglotto/frozen-top5")
def frozen_top5(
    include_special: bool = False
):
    result = load_analysis(
        include_special=include_special
    )

    return {
        "status": "ok",
        "data": {
            "engine_version": result.get("engine_version"),
            "latest_draw": result.get("latest_draw"),
            "top5": result.get("frozen_top5", []),
        },
    }


@app.get("/api/v1/biglotto/explosion-momentum")
def explosion_momentum(
    include_special: bool = False,
):
    try:
        result = get_explosion_momentum(
            include_special=include_special
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

    return {
        "status": "ok",
        "data": result,
    }


@app.get("/api/v1/biglotto/recovery-momentum")
def recovery_momentum(
    include_special: bool = False,
):
    try:
        result = get_recovery_momentum(
            include_special=include_special
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

    return {
        "status": "ok",
        "data": result,
    }


@app.get("/api/v1/biglotto/reversal-momentum")
def reversal_momentum(
    include_special: bool = False,
):
    try:
        result = get_reversal_momentum(
            include_special=include_special
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

    return {
        "status": "ok",
        "data": result,
    }


@app.get("/api/v1/biglotto/recent-draws")
def recent_draws(
    limit: int = 10,
    include_special: bool = False,
):
    try:
        result = get_recent_draws_with_grades(
            limit=limit,
            include_special=include_special,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

    return {
        "status": "ok",
        "data": result,
    }


@app.get("/api/v1/biglotto/overheat-warning")
def overheat_warning(include_special: bool = False):
    try:
        data = get_overheat_momentum(include_special=include_special)
        return {
            "status": "ok",
            "data": data,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@app.get("/api/v1/biglotto/draw-counts")
def draw_counts(include_special: bool = False):
    try:
        data = get_draw_counts(include_special=include_special)
        return {
            "status": "ok",
            "data": data,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@app.get("/api/v1/biglotto/missing-numbers")
def missing_numbers(include_special: bool = False):
    try:
        data = get_missing_numbers(include_special=include_special)
        return {
            "status": "ok",
            "data": data,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@app.get("/api/v1/biglotto/count-missing-cross")
def count_missing_cross(window: int = 30, include_special: bool = False):
    if window not in (10, 30, 50, 100):
        raise HTTPException(
            status_code=400,
            detail="window 僅支援 10、30、50、100",
        )

    try:
        data = get_count_missing_cross(window=window, include_special=include_special)
        return {
            "status": "ok",
            "data": data,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@app.get("/api/v1/biglotto/smart-selection")
def smart_selection(
    count: int = 6,
    strategy: str = "balanced",
    locked: str = "",
    excluded: str = "",
    include_special: bool = False,
):
    try:
        data = get_smart_selection(
            count=count,
            strategy=strategy,
            locked=locked,
            excluded=excluded,
            include_special=include_special,
        )
        return {"status": "ok", "data": data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

