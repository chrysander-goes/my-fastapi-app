import os
import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import dotenv_values
import re
import json
import sqlite3
import pandas as pd
import httpx
from typing import List
from pydantic import BaseModel
from fastapi import Header, HTTPException

app = FastAPI()

# Enable CORS for the autograder
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def coerce_types(config: dict) -> dict:
    if "port" in config:
        config["port"] = int(config["port"])
    if "workers" in config:
        config["workers"] = int(config["workers"])
    if "debug" in config:
        val = str(config["debug"]).strip().lower()
        config["debug"] = val in ("true", "1", "yes", "on")
    
    # Ensure all other keys are strings (excluding the ones we just cast)
    for k in list(config.keys()):
        if k not in ("port", "workers", "debug"):
            config[k] = str(config[k])
            
    return config

@app.get("/effective-config")
async def get_effective_config(request: Request):
    # 1. Defaults (Lowest Precedence)
    config = {
        "port": 8000,
        "workers": 1,
        "debug": False,
        "log_level": "info",
        "api_key": "default-secret-000"
    }

    # 2. config.development.yaml
    try:
        with open("config.development.yaml", "r") as f:
            yaml_config = yaml.safe_load(f) or {}
            config.update(yaml_config)
    except FileNotFoundError:
        pass

    # 3. .env file
    env_config = dotenv_values(".env")
    for k, v in env_config.items():
        if k == "NUM_WORKERS":
            config["workers"] = v
        elif k.startswith("APP_"):
            config[k[4:].lower()] = v

    # 4. OS env vars
    for k, v in os.environ.items():
        if k == "NUM_WORKERS":
            config["workers"] = v
        elif k.startswith("APP_"):
            config[k[4:].lower()] = v

    # 5. CLI Overrides (Highest Precedence) via query params (?set=key=value&set=...)
    # request.query_params.multi_items() gets all instances of 'set'
    set_params = [v for k, v in request.query_params.multi_items() if k == "set"]
    for param in set_params:
        if "=" in param:
            key, val = param.split("=", 1)
            config[key.lower()] = val

    # Apply type coercion rules
    config = coerce_types(config)

    # Secret masking rule
    if "api_key" in config:
        config["api_key"] = "****"

    return config


@app.get("/submission.tar.gz")
async def get_submission():
    return FileResponse(
        path="submission.tar.gz",
        filename="submission.tar.gz",
        media_type="application/octet-stream"
    )

# --- QUESTION 8: LEDGER QA AGENT ---

RATES_URL = "https://exam.sanand.workers.dev/questionData?email=23f2003860%40ds.study.iitm.ac.in&quizSign=cNpAhPYReGQn%2BnOcKaN2OO12JY0ntIOVQJuH%2B9l9%2BYQhihZmBctne1145T0XRcp8wiw8Z8MS%2BzqLLUBzoYwVTamNHZEdJ1yHym3da9VGGk8ZWzWLr9%2BYrUDNo8XjPGJwoVlEJVJOOA92SHXs7DwfYvJQFwbT3ljJb1KP9nxKLuUQU6%2BlFMClw09O8JLUCeO7K37%2BbKXc9Alg0j9FVINZpVCCnZgKLwPVp36ywP%2FRflUphhAMLAvCpqxROKo6lsNjMT3diwVoKbcJN6DgSpJ90TE7vzaiZb6jsAeUyg0tX7OBdlMevk%2BGiBS2xUICH%2BrkCmvRT7QjMbBQ6VM4DRfpuA%3D%3D&questionId=q-ledger-agent-server&path=%2Frates"
EXPORT_URL = "https://exam.sanand.workers.dev/questionData?email=23f2003860%40ds.study.iitm.ac.in&quizSign=cNpAhPYReGQn%2BnOcKaN2OO12JY0ntIOVQJuH%2B9l9%2BYQhihZmBctne1145T0XRcp8wiw8Z8MS%2BzqLLUBzoYwVTamNHZEdJ1yHym3da9VGGk8ZWzWLr9%2BYrUDNo8XjPGJwoVlEJVJOOA92SHXs7DwfYvJQFwbT3ljJb1KP9nxKLuUQU6%2BlFMClw09O8JLUCeO7K37%2BbKXc9Alg0j9FVINZpVCCnZgKLwPVp36ywP%2FRflUphhAMLAvCpqxROKo6lsNjMT3diwVoKbcJN6DgSpJ90TE7vzaiZb6jsAeUyg0tX7OBdlMevk%2BGiBS2xUICH%2BrkCmvRT7QjMbBQ6VM4DRfpuA%3D%3D&questionId=q-ledger-agent-server&path=%2Fexport"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

db_conn = sqlite3.connect(":memory:", check_same_thread=False)

def init_database():
    with httpx.Client(timeout=30.0) as client:
        resp_rates = client.get(RATES_URL, headers=HEADERS)
        resp_rates.raise_for_status()
        usd_rates = resp_rates.json()["usd_per_unit"]
        
        resp_export = client.get(EXPORT_URL, headers=HEADERS)
        resp_export.raise_for_status()
        raw_lines = resp_export.text.strip().splitlines()
        rows = [json.loads(line) for line in raw_lines if line.strip()]

    df = pd.DataFrame(rows)
    df["created_at_utc"] = pd.to_datetime(df["created_at"], format="ISO8601", utc=True)
    df["updated_at_utc"] = pd.to_datetime(df["updated_at"], format="ISO8601", utc=True)
    df = df.sort_values("updated_at_utc", kind="mergesort").drop_duplicates(subset=["id"], keep="last").copy()

    ist_created = df["created_at_utc"].dt.tz_convert("Asia/Kolkata")
    df["created_date"] = ist_created.dt.strftime("%Y-%m-%d")
    df["created_month"] = ist_created.dt.strftime("%Y-%m")
    df["created_year"] = ist_created.dt.year

    df["rate_to_usd"] = df["currency"].map(usd_rates)
    df["amount_usd"] = df["amount"] * df["rate_to_usd"]

    df_sql = df.drop(columns=["created_at_utc", "updated_at_utc"])
    df_sql.to_sql("orders", db_conn, index=False, if_exists="replace")

init_database()

MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12"
}
PRODUCTS = ["Air Fryer", "Rice Cooker", "Juicer", "Toaster", "Mixer", "Kettle", "Blender", "Grinder"]
REGIONS = ["North", "South", "East", "West", "Central"]

def parse_question_to_sql(question: str):
    q_low = question.lower()
    conditions = []
    
    if re.search(r"\brefund(ed|s)?\b", q_low):
        conditions.append("status = 'refunded'")
    elif re.search(r"\b(void|voided|cancel|cancelled)\b", q_low):
        conditions.append("status = 'void'")
    else:
        conditions.append("status = 'paid'")

    for reg in REGIONS:
        if re.search(rf"\b{reg.lower()}\b", q_low):
            conditions.append(f"region = '{reg}'")
            break

    for prod in PRODUCTS:
        if re.search(rf"\b{re.escape(prod.lower())}s?\b", q_low):
            conditions.append(f"product = '{prod}'")
            break

    cust_match = re.search(r"\b(c\d{4})\b", q_low)
    if cust_match:
        conditions.append(f"customer = '{cust_match.group(1).upper()}'")

    year_match = re.search(r"\b(202\d)\b", q_low)
    year_str = year_match.group(1) if year_match else "2026"
    month_num = None
    
    for m_name, m_val in MONTH_MAP.items():
        if re.search(rf"\b{m_name}\b", q_low):
            month_num = m_val
            break

    if month_num:
        conditions.append(f"created_month = '{year_str}-{month_num}'")
    elif year_match and "month" not in q_low:
        conditions.append(f"created_year = {year_str}")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    if any(p in q_low for p in ["which product", "what product", "top-selling product", "best-selling product", "product earned the most"]):
        order_metric = "SUM(qty)" if any(w in q_low for w in ["qty", "quantity", "units"]) else "SUM(amount_usd)"
        sql = f"SELECT product FROM orders WHERE {where_clause} GROUP BY product ORDER BY {order_metric} DESC LIMIT 1"
        return sql, "string"

    if any(p in q_low for p in ["which customer", "what customer", "top customer", "customer spent the most"]):
        sql = f"SELECT customer FROM orders WHERE {where_clause} GROUP BY customer ORDER BY SUM(amount_usd) DESC LIMIT 1"
        return sql, "string"

    if any(p in q_low for p in ["which region", "what region", "top region", "region earned the most"]):
        sql = f"SELECT region FROM orders WHERE {where_clause} GROUP BY region ORDER BY SUM(amount_usd) DESC LIMIT 1"
        return sql, "string"

    if any(p in q_low for p in ["distinct customer", "different customer", "unique customer", "how many customer"]):
        sql = f"SELECT COUNT(DISTINCT customer) FROM orders WHERE {where_clause}"
        return sql, "int"

    if "average" in q_low:
        sql = f"SELECT ROUND(COALESCE(AVG(amount_usd), 0), 2) FROM orders WHERE {where_clause}"
        return sql, "money"

    if any(p in q_low for p in ["how many orders", "count the", "number of orders", "how many refunded"]):
        sql = f"SELECT COUNT(*) FROM orders WHERE {where_clause}"
        return sql, "int"

    if any(p in q_low for p in ["how many units", "total quantity", "total units"]):
        sql = f"SELECT COALESCE(SUM(qty), 0) FROM orders WHERE {where_clause}"
        return sql, "int"

    sql = f"SELECT ROUND(COALESCE(SUM(amount_usd), 0), 2) FROM orders WHERE {where_clause}"
    return sql, "money"

@app.post("/ask")
async def answer_question(request: Request):
    body = await request.json()
    question = body.get("question", "")
    sql_query, ans_type = parse_question_to_sql(question)
    
    cursor = db_conn.cursor()
    cursor.execute(sql_query)
    row = cursor.fetchone()
    val = row[0] if row and row[0] is not None else 0

    if ans_type == "string":
        result = str(val)
    elif ans_type == "int":
        result = int(val)
    else:
        result = round(float(val), 2)

    return {"answer": result}

class Event(BaseModel):
    user: str
    amount: float
    ts: int

class AnalyticsRequest(BaseModel):
    events: List[Event]

@app.post("/analytics")
async def get_analytics(
    request: AnalyticsRequest,
    x_api_key: str = Header(None, alias="X-API-Key")
):
    # 1. Missing or wrong key -> 401
    if x_api_key != "ak_isygcxa65amwjz2aq1xhlfmc":
        raise HTTPException(status_code=401, detail="Unauthorized API Key")
    
    events = request.events
    total_events = len(events)
    unique_users = set()
    revenue = 0.0
    user_revenue = {}
    
    # Aggregate data
    for event in events:
        unique_users.add(event.user)
        if event.amount > 0:
            revenue += event.amount
            # Track positive revenue per user to find top_user
            user_revenue[event.user] = user_revenue.get(event.user, 0.0) + event.amount
            
    top_user = max(user_revenue, key=user_revenue.get) if user_revenue else ""

    return {
        "email": "23f2003860@ds.study.iitm.ac.in",
        "total_events": total_events,
        "unique_users": len(unique_users),
        "revenue": revenue,
        "top_user": top_user
    }