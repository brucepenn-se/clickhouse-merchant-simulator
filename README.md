# PennLogic Merchant Intelligence

### Real-time merchant analytics powered by ClickHouse Cloud

**Keep the experience. Upgrade the engine.**

PennLogic Merchant Intelligence is a demonstration of an analytics flow that keeps PostgreSQL as the transactional source and uses ClickPipes CDC to replicate changes into ClickHouse Cloud for reporting. A Python simulator generates synthetic order activity; a Streamlit dashboard queries the ClickHouse analytics tables.

The repository contains the simulator and dashboard. The PostgreSQL service, ClickPipes pipeline, ClickHouse service, and analytics tables or views must be provisioned separately.

## Architecture

```text
Python order simulator
          |
          | writes orders and deliveries
          v
PostgreSQL (merchant_portal schema)
          |
          | ClickPipes CDC (provisioned separately)
          v
ClickHouse Cloud (merchant_portal analytics tables/views)
          |
          | parameterized analytical queries
          v
Streamlit merchant analytics dashboard
```

| Component | Role in this demo |
|---|---|
| PostgreSQL | Transactional source of record for the `merchant_portal.orders` and `merchant_portal.deliveries` tables. |
| ClickPipes CDC | External pipeline that replicates PostgreSQL changes into ClickHouse. Its setup is not included in this repository. |
| ClickHouse Cloud | Analytics database queried by the dashboard through `merchant_portal.orders_analytics` and `merchant_portal.deliveries_analytics`. |
| Python simulator | Generates synthetic merchants, zones, orders, cancellations, and delivery records in PostgreSQL. |
| Streamlit dashboard | Presents merchant analytics from ClickHouse. Merchant Preview is a fixed demo view, not a secured tenant session. |

## Dashboard features

- **Admin Preview:** view all merchants, one merchant, or a comparison of selected merchants.
- **Merchant Preview:** view the fixed demo identity, Merchant 42.
- **Time ranges:** Last 1 Hour (default), Last 4 Hours, Last 24 Hours, Last 7 Days, Last 30 Days, and All Time.
- **Summary metrics:** total orders, gross order value, cancellation rate, and on-time delivery rate. Gross order value includes canceled orders.
- **Charts:** order volume over time and cancellation counts with cancellation rate.
- **Cancellation reasons:** expandable breakdown from the source data.
- **Recent activity:** up to eight latest orders for the current selection.
- **Refresh:** automatic refresh is enabled by default and reruns the analytics every five seconds; it can be switched off. A Refresh Data button is also shown in Recent Activity.

The dashboard expects ClickHouse analytics tables or views with the columns used by its queries. Dashboard filters narrow the results, but they do not provide user authentication or enforce production tenant isolation.

## Prerequisites and configuration

- Python 3.10 or newer.
- A PostgreSQL database with the `merchant_portal.orders` and `merchant_portal.deliveries` tables and the columns used by `simulator.py`.
- A ClickHouse Cloud database with `merchant_portal.orders_analytics` and `merchant_portal.deliveries_analytics`, populated from the PostgreSQL source.
- A configured ClickPipes CDC pipeline, if you want newly simulated PostgreSQL records to appear in ClickHouse.
- Network access and credentials for both databases.

Install the Python dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'psycopg[binary]' python-dotenv clickhouse-connect streamlit plotly
```

Create a local `.env` file (ignored by Git) for the connections. PostgreSQL uses the standard libpq variables, for example `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`. The simulator calls `psycopg.connect()` using those settings. The dashboard requires:

```dotenv
CH_HOST=your-clickhouse-host
CH_PORT=8443
CH_USER=your-clickhouse-user
CH_PASSWORD=your-clickhouse-password
CH_DATABASE=merchant_portal
```

`CH_PORT` defaults to `8443` and `CH_DATABASE` to `merchant_portal`. The dashboard requests a secure ClickHouse connection. No TLS certificate file is read directly by either application script.

## Run the dashboard

```bash
source .venv/bin/activate
python -m streamlit run dashboard.py
```

Streamlit normally serves the dashboard at [http://localhost:8501](http://localhost:8501).

## Simulator commands

Run these from the project directory with the virtual environment active and PostgreSQL configured. `--interval` is measured in seconds. Without `--count`, the simulator continues until interrupted; `--count 0` also means unlimited. The default interval is 10 seconds, cancellation probability is 2%, and on-time delivery target is 85%.

### Generate continuous healthy baseline activity

```bash
python simulator.py --interval 2 --cancel-rate 0.02 --on-time-rate 0.85
```

This sets a 2-second order interval, 2% cancellation probability, and 85% on-time delivery target. The 2-second interval is an explicit demo setting; it is not the simulator default. Rates are probabilistic or simulated targets, so observed dashboard percentages will vary with sample size and the selected time range.

Generate a finite batch by setting a positive count:

```bash
python simulator.py --count 100 --interval 2 --cancel-rate 0.02 --on-time-rate 0.85
```

### Demonstrate a 20% cancellation incident

Stop the baseline process with `Ctrl+C`, then start the incident run:

```bash
python simulator.py --interval 2 --cancel-rate 0.20 --on-time-rate 0.85
```

The simulator assigns a 20% cancellation probability to new orders. Canceled orders use the `LONG_DELIVERY_ESTIMATE` reason and receive longer estimated delivery times. Once CDC has replicated the new records, view a suitable time range in the dashboard. The aggregate rate rises as incident orders accumulate alongside earlier activity; short time ranges make the change easier to isolate. It will not necessarily display exactly 20%, especially with a small number of orders.

### Backfill a simulator activity gap

```bash
python simulator.py --catchup --interval 2 --cancel-rate 0.02 --on-time-rate 0.85
```

Catchup spreads a bounded set of synthetic orders between the latest simulator-generated order and database time. It skips if no earlier simulator orders exist or no gap is found. Its default cap is 5,000 orders and the hard maximum is 10,000. Review the plan without writing rows by adding `--dry-run`:

```bash
python simulator.py --catchup --interval 2 --cancel-rate 0.02 --on-time-rate 0.85 --dry-run
```

### Complete eligible pending simulator orders

```bash
python simulator.py --complete-pending --on-time-rate 0.85
```

This completes due `IN_PROGRESS` orders created by the simulator when they do not already have a delivery record. Preview the eligible count without writes:

```bash
python simulator.py --complete-pending --on-time-rate 0.85 --dry-run
```

Delivery durations are derived deterministically from the order ID and promised estimate. The configured on-time rate controls the classification target. Catchup and pending-completion modes apply only to rows with a non-null `simulation_run_id`; they do not backfill unrelated historical orders.

## Security considerations

- Keep `.env`, database credentials, and private keys out of version control. The repository `.gitignore` excludes `.env`, virtual environments, and `certs/`.
- Use least-privilege database credentials and protect the local environment file. Do not put production secrets in shell history, source control, or shared logs.
- The dashboard connects to ClickHouse with TLS enabled by the client (`secure=True`). PostgreSQL connection security is governed by the libpq configuration and server settings; configure it according to your environment.
- Merchant Preview always selects Merchant 42 and is explicitly presentation-only. It is not authentication or authorization. A deployed multi-tenant application needs authenticated identity and server-side tenant enforcement.
- Simulator records are synthetic demo data, not real merchant transactions.

## Project scope

This repository provides a synthetic PostgreSQL order simulator and a Streamlit analytics dashboard for technical demonstrations. It assumes the database schema, ClickPipes configuration, ClickHouse analytics tables or views, and cloud infrastructure already exist. It does not provision those services or implement a production ordering platform, identity system, tenant authorization, or a database benchmark. The metrics illustrate a data flow and simulated operating scenarios; they are not production performance guarantees.

---

**PennLogic Merchant Intelligence**

*Keep the experience. Upgrade the engine.*
