import os

import clickhouse_connect
from dotenv import load_dotenv

# Load connection settings from our existing .env file
load_dotenv(".env")

# Establish a secure HTTPS connection to ClickHouse Cloud
client = clickhouse_connect.get_client(
    host=os.environ["CH_HOST"],
    port=int(os.getenv("CH_PORT", "8443")),
    username=os.environ["CH_USER"],
    password=os.environ["CH_PASSWORD"],
    database=os.getenv("CH_DATABASE", "merchant_portal"),
    secure=True,
)

# Verify connectivity and query our real analytics data
result = client.query("""
    SELECT count() AS total_orders
    FROM merchant_portal.orders_analytics
""")

print("Successfully connected to PennLogic OLAP!")
print(f"Total orders: {result.result_rows[0][0]:,}")

client.close()
