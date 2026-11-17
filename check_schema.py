from dotenv import load_dotenv
import psycopg

load_dotenv(".env")

with psycopg.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                conrelid::regclass AS table_name,
                conname AS constraint_name,
                pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid IN (
                'merchant_portal.orders'::regclass,
                'merchant_portal.deliveries'::regclass
            )
            ORDER BY table_name, constraint_name
        """)

        for table, name, definition in cur.fetchall():
            print(f"{table} | {name} | {definition}")