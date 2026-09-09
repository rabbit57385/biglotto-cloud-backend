import os
import psycopg

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise RuntimeError("DATABASE_URL not found")

with psycopg.connect(database_url) as conn:
    with conn.cursor() as cur:
        print("=" * 50)
        print("539 PostgreSQL Database Check")
        print("=" * 50)

        cur.execute("SELECT COUNT(*) FROM lottery_draws")
        total = cur.fetchone()[0]
        print(f"Total draws: {total}")

        cur.execute("""
            SELECT draw_no, draw_date, number1, number2, number3, number4, number5
            FROM lottery_draws
            ORDER BY draw_date ASC, draw_no ASC
            LIMIT 1
        """)
        first = cur.fetchone()
        print(f"First draw: {first}")

        cur.execute("""
            SELECT draw_no, draw_date, number1, number2, number3, number4, number5
            FROM lottery_draws
            ORDER BY draw_date DESC, draw_no DESC
            LIMIT 1
        """)
        latest = cur.fetchone()
        print(f"Latest draw: {latest}")

        cur.execute("""
            SELECT draw_no, COUNT(*)
            FROM lottery_draws
            GROUP BY draw_no
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        """)
        duplicates = cur.fetchall()

        if duplicates:
            print("Duplicate draw_no found:")
            for row in duplicates:
                print(row)
        else:
            print("Duplicate draw_no: none")

        cur.execute("""
            SELECT draw_date, COUNT(*)
            FROM lottery_draws
            GROUP BY draw_date
            HAVING COUNT(*) > 1
            ORDER BY draw_date
        """)
        duplicate_dates = cur.fetchall()

        if duplicate_dates:
            print("Duplicate draw_date found:")
            for row in duplicate_dates:
                print(row)
        else:
            print("Duplicate draw_date: none")

        print("=" * 50)
        print("Database check completed")
        print("=" * 50)