from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, HttpUrl

from app.scrapers.amazon import extract_product, is_amazon_in

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "price_tracker.db"
CACHE_TTL = 900
_cache: dict[str, tuple[float, dict]] = {}
_last_fetch = 0.0
_lock = threading.Lock()


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            image_url TEXT, price REAL, seller TEXT NOT NULL, target REAL,
            added_at TEXT NOT NULL, checked_at TEXT, status TEXT NOT NULL DEFAULT 'ok'
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, price REAL NOT NULL,
            captured_at TEXT NOT NULL, FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS alternatives (
            id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, retailer TEXT NOT NULL,
            name TEXT NOT NULL, price REAL NOT NULL, url TEXT NOT NULL,
            confidence TEXT NOT NULL, checked_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """)
        count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if not count:
            now = datetime.now(timezone.utc).isoformat()
            demos = [
                ("https://www.amazon.in/dp/B0DEMO001", "Sony WH-1000XM5 Wireless Headphones", "https://m.media-amazon.com/images/I/61+btxzpfDL._SL1500_.jpg", 24990, "Amazon.in", 22999, now, now, "demo"),
                ("https://www.amazon.in/dp/B0DEMO002", "Kindle Paperwhite (16 GB)", "https://m.media-amazon.com/images/I/61Z+nZ3p7JL._SL1000_.jpg", 13999, "Amazon.in", None, now, now, "demo"),
                ("https://www.amazon.in/dp/B0DEMO003", "Noise ColorFit Pro 5 Smartwatch", None, 3999, "Amazon.in", 3499, now, now, "demo"),
            ]
            for row in demos:
                cur = db.execute("INSERT INTO products(url,name,image_url,price,seller,target,added_at,checked_at,status) VALUES(?,?,?,?,?,?,?,?,?)", row)
                pid = cur.lastrowid
                for days, price in [(14, row[3] * 1.08), (10, row[3] * 1.02), (7, row[3] * .96), (3, row[3] * 1.03), (0, row[3])]:
                    captured = (datetime.now(timezone.utc).timestamp() - days*86400)
                    stamp = datetime.fromtimestamp(captured, timezone.utc).isoformat()
                    db.execute("INSERT INTO snapshots(product_id,price,captured_at) VALUES(?,?,?)", (pid, price, stamp))
            db.execute("INSERT INTO alternatives(product_id,retailer,name,price,url,confidence,checked_at) VALUES(1,'Croma','Sony WH-1000XM5 Wireless Noise Cancelling Headphones',23999,'https://www.croma.com/','Likely match · model name aligns',?)", (now,))


def read_product(url: str) -> dict:
    global _last_fetch
    cached = _cache.get(url)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return dict(cached[1])
    with _lock:
        wait = 2.0 - (time.time() - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        _last_fetch = time.time()
        result = extract_product(url)
    _cache[url] = (time.time(), result)
    return result


def refresh_due(db=None):
    own = db is None
    db = db or connect()
    try:
        rows = db.execute("SELECT * FROM products WHERE status != 'demo' AND (checked_at IS NULL OR julianday(checked_at) < julianday('now','-6 hours'))").fetchall()
        for row in rows:
            try:
                data = read_product(row["url"])
                status = "ok" if data["price"] is not None else "price unavailable"
                db.execute("UPDATE products SET name=?,image_url=COALESCE(?,image_url),price=COALESCE(?,price),seller=?,checked_at=?,status=? WHERE id=?", (data["name"], data["image_url"], data["price"], data["seller"], datetime.now(timezone.utc).isoformat(), status, row["id"]))
                if data["price"] is not None:
                    db.execute("INSERT INTO snapshots(product_id,price,captured_at) VALUES(?,?,?)", (row["id"], data["price"], datetime.now(timezone.utc).isoformat()))
            except Exception as exc:
                db.execute("UPDATE products SET checked_at=?,status=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), str(exc)[:140], row["id"]))
        db.commit()
    finally:
        if own:
            db.close()


def background_refresh():
    while True:
        try:
            refresh_due()
        except Exception:
            pass
        time.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    threading.Thread(target=background_refresh, daemon=True).start()
    yield


app = FastAPI(title="Pricewise", lifespan=lifespan)


class AddProduct(BaseModel):
    url: HttpUrl
    target: float | None = None


class TargetUpdate(BaseModel):
    target: float | None


def product_json(db, row):
    history = db.execute("SELECT price,captured_at FROM snapshots WHERE product_id=? ORDER BY captured_at", (row["id"],)).fetchall()
    low = min((h["price"] for h in history), default=row["price"])
    high = max((h["price"] for h in history), default=row["price"])
    first = history[0]["price"] if history else row["price"]
    alts = db.execute("SELECT * FROM alternatives WHERE product_id=? AND julianday(checked_at) > julianday('now','-1 day') ORDER BY price", (row["id"],)).fetchall()
    cheaper = [dict(a) for a in alts if row["price"] is not None and a["price"] < row["price"]]
    return {"id": row["id"], "url": row["url"], "name": row["name"], "image_url": row["image_url"], "price": row["price"], "seller": row["seller"], "target": row["target"], "added_at": row["added_at"], "checked_at": row["checked_at"], "status": row["status"], "lowest": low, "highest": high, "change": (row["price"] - first) if row["price"] is not None and first is not None else None, "history": [dict(h) for h in history], "alternatives": cheaper, "alert": row["target"] is not None and row["price"] is not None and row["price"] <= row["target"]}


@app.get("/api/products")
def list_products():
    with connect() as db:
        return [product_json(db, row) for row in db.execute("SELECT * FROM products ORDER BY added_at DESC")]


@app.post("/api/products")
def add_product(body: AddProduct):
    url = str(body.url)
    if not is_amazon_in(url):
        raise HTTPException(400, "The first version supports Amazon.in links. Other sites are coming later.")
    try:
        data = read_product(url)
    except requests.RequestException as exc:
        raise HTTPException(422, f"Amazon could not be reached: {exc}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    now = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        existing = db.execute("SELECT id FROM products WHERE url=?", (url,)).fetchone()
        if existing:
            raise HTTPException(409, "That product is already on your dashboard.")
        cur = db.execute("INSERT INTO products(url,name,image_url,price,seller,target,added_at,checked_at,status) VALUES(?,?,?,?,?,?,?,?,?)", (url,data["name"],data["image_url"],data["price"],data["seller"],body.target,now,now,"ok" if data["price"] is not None else "price unavailable"))
        if data["price"] is not None:
            db.execute("INSERT INTO snapshots(product_id,price,captured_at) VALUES(?,?,?)", (cur.lastrowid,data["price"],now))
        row = db.execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone()
        return product_json(db,row)


@app.patch("/api/products/{product_id}/target")
def set_target(product_id: int, body: TargetUpdate):
    with connect() as db:
        cur = db.execute("UPDATE products SET target=? WHERE id=?", (body.target,product_id))
        if not cur.rowcount:
            raise HTTPException(404,"Product not found")
        return product_json(db, db.execute("SELECT * FROM products WHERE id=?",(product_id,)).fetchone())


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int):
    with connect() as db:
        db.execute("DELETE FROM snapshots WHERE product_id=?", (product_id,))
        db.execute("DELETE FROM alternatives WHERE product_id=?", (product_id,))
        if not db.execute("DELETE FROM products WHERE id=?", (product_id,)).rowcount:
            raise HTTPException(404,"Product not found")
        return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "static" / "index.html").read_text()
