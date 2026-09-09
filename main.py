from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import os
import psycopg
from psycopg.rows import dict_row

from momentum_engine import (
    get_momentum_analysis,
    get_today_momentum_changes,
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

@app.get("/api/v1/biglotto/recent-draws")
def recent_draws(limit: int = 10):
    safe_limit = max(1, min(limit, 100))
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")

    sql = """
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
        ORDER BY draw_no::bigint DESC
        LIMIT %s
    """

    try:
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (safe_limit,))
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    draws = []
    for row in rows:
        draws.append({
            "draw_no": str(row["draw_no"]),
            "draw_date": row["draw_date"].isoformat(),
            "numbers": [
                row["number1"],
                row["number2"],
                row["number3"],
                row["number4"],
                row["number5"],
                row["number6"],
            ],
            "special_number": row["special_number"],
        })

    return {
        "status": "ok",
        "data": {
            "count": len(draws),
            "draws": draws,
        },
    }

