from fastapi import FastAPI, Header, HTTPException, Depends, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import contextmanager
from datetime import datetime, timedelta
from collections import defaultdict
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import xlrd
import io
import os
import bcrypt
import secrets

# ─── CONFIG ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://espe:espe123@localhost:5432/espe")

BLN_MONTHS = {
    1:"Jan 2026", 2:"Feb 2026", 3:"Mar 2026", 4:"Apr 2026",
    5:"May 2026", 6:"Jun 2026", 7:"Jul 2026", 8:"Aug 2026",
    9:"Sep 2026", 10:"Oct 2026", 11:"Nov 2026", 12:"Dec 2026",
    13:"Jan 2027", 14:"Feb 2027", 15:"Mar 2027", 16:"Apr 2027",
    17:"May 2027", 18:"Jun 2027",
}

MONTH_ORDER = {
    "Jan 2026":1,  "Feb 2026":2,  "Mar 2026":3,  "Apr 2026":4,
    "May 2026":5,  "Jun 2026":6,  "Jul 2026":7,  "Aug 2026":8,
    "Sep 2026":9,  "Oct 2026":10, "Nov 2026":11, "Dec 2026":12,
    "Jan 2027":13, "Feb 2027":14, "Mar 2027":15, "Apr 2027":16,
    "May 2027":17, "Jun 2027":18, "—":99,
}

KNOWN_SUPPLIERS = {
    "5cb-hft":"CYG", "5cb-sgs":"CYG", "6awm":"XINYA",
    "5ehr":"LHE", "5ph-":"LHE", "5san":"LHE", "5eh-":"LHE",
    "5tjc":"LHE", "5vh-":"LHE", "5sr1":"ZUCH", "5tp1":"ZUCH",
    "5tr4":"ZUCH", "599752":"GEM", "599751":"GEM",
    "h11601":"LAIN", "5h1160":"LAIN",
}

# ─── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── DB ──────────────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

def fetchall(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params or ())
        return cur.fetchall()

def fetchone(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params or ())
        return cur.fetchone()

# ─── AUTH ────────────────────────────────────────────────────────────────────
def verify_session(x_session_token: str = Header(None)):
    if not x_session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    with get_db() as conn:
        session = fetchone(conn, """
            SELECT s.token, s.role, s.expires_at, u.username
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token = %s AND s.expires_at > NOW()
        """, [x_session_token])
    if not session:
        raise HTTPException(status_code=401, detail="Session expired atau tidak valid")
    return session

@app.post("/auth/login")
async def login(request: Request):
    data     = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    with get_db() as conn:
        user = fetchone(conn, "SELECT id, username, password_hash, role FROM users WHERE username = %s", [username])
        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            raise HTTPException(status_code=401, detail="Username atau password salah")
        token      = secrets.token_hex(32)
        expires_at = datetime.now() + timedelta(hours=12)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE user_id = %s", [user["id"]])
            cur.execute("INSERT INTO sessions (token, user_id, role, expires_at) VALUES (%s,%s,%s,%s)",
                        (token, user["id"], user["role"], expires_at))
        conn.commit()
    return {"token": token, "role": user["role"], "username": user["username"]}

@app.post("/auth/logout")
async def logout(request: Request):
    token = request.headers.get("X-Session-Token")
    if token:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE token = %s", [token])
            conn.commit()
    return {"message": "Logged out"}

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def month_num(m):
    if not m or m == "—": return 99
    mo = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
          "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
    return mo.get(m.split(" ")[0], 99)

def convert_month(val):
    try: return BLN_MONTHS.get(int(float(val)))
    except: return None

def safe_float(val):
    try: return float(str(val).split()[0].replace(',', '.'))
    except: return 0.0

def clean_code(val):
    return str(val).replace(' (E)', '').replace('(E)', '').strip()

def safe_date(val):
    try:
        if val is None or str(val) == "NaT": return None
        if pd.isna(val): return None
        return val.date() if hasattr(val, 'date') else None
    except:
        return None

# ─── IMPORT HELPERS ──────────────────────────────────────────────────────────
def import_orders(file_bytes: bytes, conn):
    df = pd.read_excel(io.BytesIO(file_bytes), engine='xlrd', sheet_name='po cust', header=2)
    df = df.iloc[:, :46]
    df.columns = [
        "customer_name","po_number","part_number_customer","maker",
        "part_number_espe","price_usd","delivery_date","ready_month",
        "stock_ok","po_due_date","order_qty","delivery_qty",
        "qty_sent","outstanding","po_date_customer","price_rp",
        "delivery_bln_1","nilai_del_bln_1","delivery_bln_2","nilai_del_bln_2",
        "delivery_bln_3","nilai_del_bln_3","delivery_bln_4","nilai_del_bln_4",
        "delivery_bln_5","nilai_del_bln_5","delivery_bln_6","nilai_del_bln_6",
        "delivery_bln_7","nilai_del_bln_7","delivery_bln_8","nilai_del_bln_8",
        "delivery_bln_9","nilai_del_bln_9","delivery_bln_10","nilai_del_bln_10",
        "delivery_bln_11","nilai_del_bln_11","delivery_bln_12","nilai_del_bln_12",
        "delivery_bln_13","nilai_del_bln_13","delivery_bln_14","nilai_del_bln_14",
        "delivery_bln_15","nilai_del_bln_15",
    ]
    for col in ["customer_name","po_number","part_number_espe","part_number_customer","maker"]:
        df[col] = df[col].astype(str).str.strip()
    df["part_number_espe"] = df["part_number_espe"].str.replace(r'\s*\(\s*E\s*\)\s*$', '', regex=True).str.strip()
    df["part_number_espe_clean"] = df["part_number_espe"].str.lower().str.strip()
    for col in ["order_qty","qty_sent","outstanding","price_rp","price_usd","delivery_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for i in range(1, 16):
        df[f"nilai_del_bln_{i}"] = pd.to_numeric(df[f"nilai_del_bln_{i}"], errors="coerce").fillna(0)
        df[f"delivery_bln_{i}"]  = pd.to_numeric(df[f"delivery_bln_{i}"], errors="coerce").fillna(0)
    df["ready_month"]      = df["ready_month"].apply(convert_month)
    df["po_date_customer"] = pd.to_datetime(df["po_date_customer"], errors="coerce")
    df["delivery_date"]    = pd.to_datetime(df["delivery_date"], errors="coerce")
    df = df.dropna(subset=["customer_name","po_number"])
    df = df[df["customer_name"].str.strip().str.lower() != "nan"]
    df = df[df["customer_name"].str.strip() != ""]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM order_deliveries")
        cur.execute("DELETE FROM orders")
        for _, row in df.iterrows():
            cur.execute("""
                INSERT INTO orders (
                    customer_name, po_number, part_number_customer, maker,
                    part_number_espe, part_number_espe_clean, price_usd, price_rp,
                    delivery_date, ready_month, stock_ok, po_due_date,
                    order_qty, delivery_qty, qty_sent, outstanding, po_date_customer
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (po_number, part_number_espe_clean) DO UPDATE SET
                    outstanding=EXCLUDED.outstanding, qty_sent=EXCLUDED.qty_sent,
                    price_rp=EXCLUDED.price_rp, ready_month=EXCLUDED.ready_month,
                    delivery_date=EXCLUDED.delivery_date
                RETURNING id
            """, (
                row["customer_name"], row["po_number"], row["part_number_customer"],
                row["maker"], row["part_number_espe"], row["part_number_espe_clean"],
                float(row["price_usd"]), float(row["price_rp"]),
                safe_date(row["delivery_date"]),
                row["ready_month"], row["stock_ok"], row["po_due_date"],
                float(row["order_qty"]), float(row["delivery_qty"]),
                float(row["qty_sent"]), float(row["outstanding"]),
                safe_date(row["po_date_customer"]),
            ))
            order_id = cur.fetchone()["id"]
            for i in range(1, 16):
                qty   = float(row.get(f"delivery_bln_{i}", 0) or 0)
                nilai = float(row.get(f"nilai_del_bln_{i}", 0) or 0)
                if qty > 0 or nilai > 0:
                    cur.execute("""
                        INSERT INTO order_deliveries (order_id, month_index, month_label, qty, nilai)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (order_id, month_index) DO UPDATE SET qty=EXCLUDED.qty, nilai=EXCLUDED.nilai
                    """, (order_id, i, BLN_MONTHS.get(i), qty, nilai))
    conn.commit()
    return len(df)


def import_stock(file_bytes: bytes, conn, filename: str = ""):
    is_xlsx = filename.lower().endswith('.xlsx')
    engine  = 'openpyxl' if is_xlsx else 'xlrd'
    header  = 0 if is_xlsx else 9
    wb      = None if is_xlsx else xlrd.open_workbook(file_contents=file_bytes)
    ws      = None if is_xlsx else wb.sheet_by_index(0)
    df      = pd.read_excel(io.BytesIO(file_bytes), engine=engine, sheet_name=0, header=header)
    if not is_xlsx:
        df = df.iloc[1:].reset_index(drop=True)
    df = df.iloc[:, :4]
    df.columns = ["item_code","item_name","on_hand","current_value_usd"]
    df["item_code"] = df["item_code"].astype(str).str.strip()
    df["item_code"] = df["item_code"].str.replace(r'\s*\(\s*E\s*\)\s*$', '', regex=True).str.strip()
    df["item_code_clean"]   = df["item_code"].str.lower().str.strip()
    df["on_hand"]           = pd.to_numeric(df["on_hand"], errors="coerce").fillna(0)
    df["current_value_usd"] = pd.to_numeric(df["current_value_usd"], errors="coerce").fillna(0)
    df = df[df["item_code"].str.strip().str.lower().notna()]
    df = df[df["item_code"].str.strip() != ""]
    df = df[df["item_code"].str.lower() != "nan"]
    myob_date_str = None
    if wb and ws:
        try:
            date_val = ws.row_values(6)[0]
            if isinstance(date_val, float) and date_val > 0:
                myob_date_str = xlrd.xldate_as_datetime(date_val, wb.datemode).strftime("%d/%m/%Y")
        except: pass
    if not myob_date_str:
        myob_date_str = datetime.now().strftime("%d/%m/%Y")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM stock")
        for _, row in df.iterrows():
            cur.execute("""
                INSERT INTO stock (item_code, item_code_clean, item_name, on_hand, current_value_usd)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (item_code_clean) DO UPDATE SET
                    on_hand=EXCLUDED.on_hand, current_value_usd=EXCLUDED.current_value_usd
            """, (row["item_code"], row["item_code_clean"], str(row.get("item_name","")),
                  float(row["on_hand"]), float(row["current_value_usd"])))
    conn.commit()
    return len(df), myob_date_str


def import_po_supplier(file_bytes: bytes, conn):
    wb = xlrd.open_workbook(file_contents=file_bytes)
    ws = wb.sheet_by_name('po supplier')
    rows = []
    for i in range(6, ws.nrows):
        row = ws.row_values(i)
        if len(row) < 10: continue
        po_number = str(row[6]).strip()
        if isinstance(row[6], float) and row[6] == int(row[6]):
            po_number = str(int(row[6]))
        supplier  = str(row[7]).strip()
        maker     = str(row[8]).strip()
        item_code = clean_code(str(row[9]).strip())
        if not po_number or po_number in ('','nan','None'): continue
        if not item_code or item_code in ('','nan','None'): continue
        if po_number.startswith(('kol','LIST','hint','DATA','PO NO')): continue
        etd_str = None
        try:
            etd_val = row[16]
            if isinstance(etd_val, float) and etd_val > 0:
                etd_str = xlrd.xldate_as_datetime(etd_val, wb.datemode).strftime("%Y-%m-%d")
        except: pass
        qty         = safe_float(row[10])
        unit_price  = safe_float(row[11])
        ready_month = convert_month(row[17])
        qty_recv    = safe_float(row[26]) if len(row) > 26 else 0
        outstanding = safe_float(row[28]) if len(row) > 28 else 0
        status = "cancelled"
        if qty > 0:
            if outstanding <= 0:   status = "sent"
            elif qty_recv > 0:     status = "partial"
            else:                  status = "open"
        rows.append({
            "po_number": po_number, "supplier": supplier, "maker": maker,
            "item_code": item_code, "item_code_clean": item_code.lower().strip(),
            "qty": qty, "unit_price": unit_price, "etd": etd_str,
            "ready_month": ready_month, "qty_received": qty_recv,
            "outstanding": outstanding, "status": status,
        })
    ws_master = wb.sheet_by_name('master')
    master_rows = []
    for i in range(ws_master.nrows):
        row = ws_master.row_values(i)
        if len(row) < 2: continue
        maker = str(row[0]).strip().upper()
        part  = clean_code(str(row[1]).strip())
        if not maker or not part: continue
        if maker in ('MAKER','NAN',''): continue
        if part in ('P/N','NAN',''): continue
        master_rows.append({"maker": maker, "part_number": part, "part_number_clean": part.lower().strip()})
    with conn.cursor() as cur:
        cur.execute("DELETE FROM po_supplier")
        for r in rows:
            cur.execute("""
                INSERT INTO po_supplier (
                    po_number, supplier, maker, item_code, item_code_clean,
                    qty, unit_price, etd, ready_month, qty_received, outstanding, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (po_number, item_code_clean) DO UPDATE SET
                    outstanding=EXCLUDED.outstanding, status=EXCLUDED.status,
                    etd=EXCLUDED.etd, unit_price=EXCLUDED.unit_price
            """, (r["po_number"], r["supplier"], r["maker"], r["item_code"], r["item_code_clean"],
                  r["qty"], r["unit_price"], r["etd"], r["ready_month"],
                  r["qty_received"], r["outstanding"], r["status"]))
        cur.execute("DELETE FROM maker_master")
        for r in master_rows:
            cur.execute("""
                INSERT INTO maker_master (maker, part_number, part_number_clean)
                VALUES (%s,%s,%s)
                ON CONFLICT (part_number_clean) DO UPDATE SET maker=EXCLUDED.maker
            """, (r["maker"], r["part_number"], r["part_number_clean"]))
    conn.commit()
    return len(rows), len(master_rows)


# ─── UPLOAD ENDPOINTS ────────────────────────────────────────────────────────
@app.post("/upload/po-cust")
async def upload_po_cust(file: UploadFile = File(...), auth=Depends(verify_session)):
    if not file.filename.endswith('.xls'):
        raise HTTPException(400, "File harus .xls")
    content = await file.read()
    with get_db() as conn:
        count = import_orders(content, conn)
        last_update = datetime.now().strftime("%a, %d %b %H:%M")
        with conn.cursor() as cur:
            cur.execute("INSERT INTO app_config (key,value) VALUES ('last_update',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (last_update,))
        conn.commit()
    return {"message": f"✅ {count} orders imported", "last_update": last_update}


@app.post("/upload/stock")
async def upload_stock(file: UploadFile = File(...), auth=Depends(verify_session)):
    if not file.filename.endswith(('.xls', '.xlsx')):
        raise HTTPException(400, "File harus .xls atau .xlsx")
    content = await file.read()
    with get_db() as conn:
        count, myob_date = import_stock(content, conn, file.filename)
        last_update = datetime.now().strftime("%a, %d %b %H:%M")
        with conn.cursor() as cur:
            cur.execute("INSERT INTO app_config (key,value) VALUES ('myob_date',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (myob_date,))
            cur.execute("INSERT INTO app_config (key,value) VALUES ('last_update',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (last_update,))
        conn.commit()
    return {"message": f"✅ {count} stock items imported", "myob_date": myob_date, "last_update": last_update}


@app.post("/upload/po-supplier")
async def upload_po_supplier(file: UploadFile = File(...), auth=Depends(verify_session)):
    if not file.filename.endswith('.xls'):
        raise HTTPException(400, "File harus .xls")
    content = await file.read()
    with get_db() as conn:
        po_count, master_count = import_po_supplier(content, conn)
        last_update = datetime.now().strftime("%a, %d %b %H:%M")
        with conn.cursor() as cur:
            cur.execute("INSERT INTO app_config (key,value) VALUES ('last_update',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (last_update,))
        conn.commit()
    return {"message": f"✅ {po_count} PO + {master_count} maker master imported", "last_update": last_update}


# ─── DASHBOARD ENDPOINTS ─────────────────────────────────────────────────────
@app.get("/config")
def get_config(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, "SELECT key, value FROM app_config")
    return {r["key"]: r["value"] for r in rows}


@app.get("/summary")
def get_summary(auth=Depends(verify_session)):
    with get_db() as conn:
        row = fetchone(conn, """
            SELECT COUNT(DISTINCT customer_name) AS total_customers,
                   COUNT(*) AS total_orders,
                   SUM(CASE WHEN outstanding > 0 THEN 1 ELSE 0 END) AS pending_orders,
                   CAST(SUM(outstanding) AS INTEGER) AS total_outstanding
            FROM orders
        """)
    return dict(row)


@app.get("/customers")
def get_customers(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT o.customer_name,
                   COUNT(*) AS total_orders,
                   SUM(CASE WHEN o.outstanding > 0 THEN 1 ELSE 0 END) AS pending_orders,
                   CAST(SUM(o.outstanding) AS INTEGER) AS total_outstanding,
                   COUNT(CASE WHEN o.outstanding > 0 AND COALESCE(s.on_hand,0) < o.order_qty THEN 1 END) AS needs_stock
            FROM orders o
            LEFT JOIN stock s ON s.item_code_clean = o.part_number_espe_clean
            GROUP BY o.customer_name ORDER BY SUM(o.outstanding) DESC
        """)
    return [dict(r) for r in rows]


@app.get("/customers/{customer_name}/orders")
def get_orders(customer_name: str, status: str = "all", auth=Depends(verify_session)):
    query = """
        SELECT po_number, part_number_customer, part_number_espe, maker, ready_month,
               po_date_customer,
               CAST(order_qty AS INTEGER) AS order_qty,
               CAST(qty_sent AS INTEGER) AS qty_sent,
               CAST(outstanding AS INTEGER) AS outstanding, price_rp
        FROM orders WHERE customer_name = %s
    """
    params = [customer_name]
    if status == "pending": query += " AND outstanding > 0"
    elif status == "done":  query += " AND outstanding = 0"
    query += " ORDER BY outstanding DESC"
    with get_db() as conn:
        rows = fetchall(conn, query, params)
    return [dict(r) for r in rows]


@app.get("/customers/{customer_name}/months")
def get_months(customer_name: str, auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT DISTINCT ready_month FROM orders
            WHERE customer_name = %s AND ready_month IS NOT NULL ORDER BY ready_month DESC
        """, [customer_name])
        no_month = fetchone(conn, """
            SELECT COUNT(*) AS count FROM orders
            WHERE customer_name = %s AND ready_month IS NULL AND outstanding > 0
        """, [customer_name])
    months = [r["ready_month"] for r in rows]
    if no_month["count"] > 0: months.append("No Month")
    return months


@app.get("/customers/{customer_name}/months/{month}")
def get_month_detail(customer_name: str, month: str, auth=Depends(verify_session)):
    with get_db() as conn:
        if month == "No Month":
            orders = fetchall(conn, """
                SELECT part_number_espe, part_number_espe_clean, part_number_customer,
                       maker, po_number, order_qty
                FROM orders WHERE customer_name = %s AND ready_month IS NULL AND outstanding > 0
            """, [customer_name])
        else:
            orders = fetchall(conn, """
                SELECT part_number_espe, part_number_espe_clean, part_number_customer,
                       maker, po_number, order_qty
                FROM orders WHERE customer_name = %s AND ready_month = %s AND outstanding > 0
            """, [customer_name, month])
        stock_map = {r["item_code_clean"]: int(r["on_hand"] or 0)
                     for r in fetchall(conn, "SELECT item_code_clean, on_hand FROM stock")}
        po_map = {r["item_code_clean"]: r for r in fetchall(conn, """
            SELECT item_code_clean,
                MAX(CASE WHEN status='partial' THEN 'partial' END) AS has_partial,
                MAX(CASE WHEN status='open'    THEN 'open'    END) AS has_open,
                MAX(CASE WHEN status='sent'    THEN 'sent'    END) AS has_sent,
                SUM(CASE WHEN status NOT IN ('sent','cancelled') THEN outstanding ELSE 0 END) AS po_outstanding
            FROM po_supplier GROUP BY item_code_clean
        """)}
    result = []
    for o in orders:
        clean     = o["part_number_espe_clean"]
        on_hand   = stock_map.get(clean, 0)
        order_qty = int(o["order_qty"])
        need      = on_hand - order_qty
        p         = po_map.get(clean)
        if p:
            po_status      = p["has_partial"] or p["has_open"] or p["has_sent"] or "no po"
            po_outstanding = int(p["po_outstanding"] or 0)
        else:
            po_status, po_outstanding = "no po", 0
        if need >= 0:                more_stock = "✓"
        elif po_status in ("sent","partial") and po_outstanding >= abs(need): more_stock = "✓ (PO sent)"
        else:                        more_stock = f"{abs(need):,} short"
        result.append({
            "po_number": o["po_number"], "part_number_espe": o["part_number_espe"],
            "part_number_customer": o["part_number_customer"], "maker": o["maker"],
            "order_qty": order_qty, "stock": on_hand, "need": need,
            "po_supplier_status": po_status, "more_stock_needed": more_stock,
        })
    result.sort(key=lambda x: x["need"])
    return result


@app.get("/customers/{customer_name}/needs-stock")
def get_needs_stock(customer_name: str, auth=Depends(verify_session)):
    with get_db() as conn:
        row = fetchone(conn, """
            SELECT COUNT(*) AS needs_stock_count FROM orders o
            LEFT JOIN stock s ON s.item_code_clean = o.part_number_espe_clean
            WHERE o.customer_name = %s AND o.outstanding > 0 AND COALESCE(s.on_hand,0) < o.order_qty
        """, [customer_name])
    return {"needs_stock_count": row["needs_stock_count"]}


@app.get("/customers/{customer_name}/pending-items")
def get_pending_items(customer_name: str, auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT customer_name, po_date_customer, po_number, part_number_espe,
                   CAST(order_qty AS INTEGER) AS order_qty, delivery_date, ready_month
            FROM orders WHERE customer_name = %s AND outstanding > 0 ORDER BY po_date_customer ASC
        """, [customer_name])
    result = []
    for r in rows:
        po_date = r["po_date_customer"]
        result.append({
            "customer_name": r["customer_name"],
            "po_date": po_date.strftime("%d %b %Y") if po_date else "—",
            "po_number": r["po_number"], "part_number": r["part_number_espe"],
            "order_qty": r["order_qty"],
            "delivery_date": r["delivery_date"].strftime("%d %b %Y") if r["delivery_date"] else "—",
            "last_delivery_month": r["ready_month"] or "—", "delta": "—",
        })
    return result


@app.get("/dashboard/overdue-customer-pos")
def get_overdue_customer_pos(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT o.customer_name, o.po_number, o.part_number_espe, o.part_number_espe_clean,
                   CAST(o.outstanding AS INTEGER) AS outstanding,
                   o.delivery_date, o.po_date_customer, m.maker AS supplier_name
            FROM orders o
            LEFT JOIN maker_master m ON m.part_number_clean = o.part_number_espe_clean
            WHERE o.outstanding > 0 ORDER BY o.customer_name ASC, o.po_date_customer ASC
        """)
    return [{
        "customer": r["customer_name"], "supplier": r["supplier_name"] or "—",
        "part_number": r["part_number_espe"], "po_number": r["po_number"],
        "outstanding_qty": r["outstanding"],
        "requested_delivery_date": r["delivery_date"].strftime("%d %b %Y") if r["delivery_date"] else "—",
        "days_late": "—",
    } for r in rows]


@app.get("/dashboard/overdue-customer-detail")
def get_overdue_customer_detail(auth=Depends(verify_session)):
    with get_db() as conn:
        stock_map = {r["item_code_clean"]: int(r["on_hand"] or 0)
                     for r in fetchall(conn, "SELECT item_code_clean, on_hand FROM stock")}
        po_map = defaultdict(list)
        for r in fetchall(conn, """
            SELECT item_code_clean, po_number, supplier,
                   CAST(outstanding AS INTEGER) AS po_qty, status, etd
            FROM po_supplier WHERE status IN ('open','partial')
        """):
            po_map[r["item_code_clean"]].append(dict(r))
        orders = fetchall(conn, """
            SELECT customer_name, po_number, part_number_espe, part_number_espe_clean,
                   ready_month,
                   CAST(order_qty AS INTEGER) AS order_qty,
                   CAST(outstanding AS INTEGER) AS outstanding
            FROM orders WHERE outstanding > 0 AND po_number NOT LIKE 'FC%%'
            ORDER BY ready_month ASC, customer_name ASC
        """)
    now = datetime.now()
    result = []
    for o in orders:
        clean    = o["part_number_espe_clean"]
        on_hand  = stock_map.get(clean, 0)
        shortage = on_hand - o["outstanding"]
        pos      = po_map.get(clean, [])
        active   = pos[0] if pos else None
        etd_status = "—"
        if active and active["etd"]:
            try:
                etd  = active["etd"]
                diff = (etd.replace(tzinfo=None) - now).days
                if diff < 0:    etd_status = f"Overdue {abs(diff)}d"
                elif diff == 0: etd_status = "Due today"
                else:           etd_status = f"In {diff}d"
            except: pass
        result.append({
            "customer": o["customer_name"], "required_delivery_month": o["ready_month"] or "—",
            "customer_po": o["po_number"], "part_number": o["part_number_espe"],
            "order_qty": o["outstanding"], "stock": on_hand, "shortage": shortage,
            "active_supplier_po": active["po_number"] if active else "—",
            "po_qty": active["po_qty"] if active else 0,
            "etd_status": etd_status, "supplier_name": active["supplier"] if active else "—",
        })
    return result


@app.get("/dashboard/overdue-with-stock")
def get_overdue_with_stock(auth=Depends(verify_session)):
    with get_db() as conn:
        stock_map = {r["item_code_clean"]: int(r["on_hand"] or 0)
                     for r in fetchall(conn, "SELECT item_code_clean, on_hand FROM stock")}
        orders = fetchall(conn, """
            SELECT customer_name, po_number, part_number_espe, part_number_espe_clean,
                   CAST(order_qty AS INTEGER) AS order_qty, delivery_date,
                   CAST(price_rp AS FLOAT) AS price_rp
            FROM orders WHERE outstanding > 0 AND po_number NOT LIKE 'FC%%'
            ORDER BY delivery_date ASC NULLS LAST
        """)
    now = datetime.now()
    running = {}
    result  = []
    for o in orders:
        clean = o["part_number_espe_clean"]
        if not clean or clean == "nan": continue
        if clean not in running: running[clean] = stock_map.get(clean, 0)
        if running[clean] < o["order_qty"]: continue
        running[clean] -= o["order_qty"]
        days_past = "—"; days_past_num = 0; delivery_date_fmt = "—"
        if o["delivery_date"]:
            try:
                dl = o["delivery_date"]
                delivery_date_fmt = dl.strftime("%m/%y")
                diff = (now.date() - dl).days
                if diff > 0:    days_past = f"{diff}d"; days_past_num = diff
                elif diff == 0: days_past = "Due today"
            except: pass
        result.append({
            "customer": o["customer_name"], "customer_po": o["po_number"],
            "part_number": o["part_number_espe"], "order_qty": o["order_qty"],
            "stock": running[clean] + o["order_qty"],
            "price_rp": float(o["price_rp"] or 0),
            "delivery_date": delivery_date_fmt, "days_past": days_past, "days_past_num": days_past_num,
        })
    result.sort(key=lambda x: (x["part_number"].lower(), -x["days_past_num"]))
    return result


@app.get("/dashboard/urgent-supplier-pos")
def get_urgent_supplier_pos(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT supplier, po_number, item_code, item_code_clean,
                   CAST(qty AS INTEGER) AS qty, CAST(outstanding AS INTEGER) AS po_outstanding,
                   status, etd
            FROM po_supplier WHERE status IN ('open','partial') AND etd IS NOT NULL
            ORDER BY outstanding DESC
        """)
        stock_map = {r["item_code_clean"]: int(r["on_hand"] or 0)
                     for r in fetchall(conn, "SELECT item_code_clean, on_hand FROM stock")}
        cust_map = defaultdict(list)
        for r in fetchall(conn, """
            SELECT part_number_espe_clean, customer_name,
                   CAST(SUM(outstanding) AS INTEGER) AS outstanding, MIN(ready_month) AS need_by
            FROM orders WHERE outstanding > 0 GROUP BY part_number_espe_clean, customer_name
        """):
            cust_map[r["part_number_espe_clean"]].append({
                "customer": r["customer_name"], "outstanding": r["outstanding"], "need_by": r["need_by"] or "—"})
    result = []
    for r in rows:
        clean     = r["item_code_clean"]
        on_hand   = stock_map.get(clean, 0)
        customers = cust_map.get(clean, [])
        total_os  = sum(c["outstanding"] for c in customers)
        shortage  = on_hand + r["po_outstanding"] - total_os
        need_by_list = [c["need_by"] for c in customers if c["need_by"] != "—"]
        need_by_fmt  = min(need_by_list, key=month_num) if need_by_list else "—"
        if " " in need_by_fmt:
            parts = need_by_fmt.split(" ")
            need_by_fmt = f"{parts[0]} {parts[1][-2:]}"
        result.append({
            "po_number": r["po_number"], "status": r["status"], "item_code": r["item_code"],
            "qty": r["qty"], "shortage": shortage, "stock": on_hand,
            "customers": customers, "total_os_cust": total_os,
            "need_by": need_by_fmt, "po_outstanding": r["po_outstanding"],
        })
    result.sort(key=lambda x: (-x["total_os_cust"], -x["po_outstanding"], x["item_code"].lower()))
    return result


@app.get("/dashboard/urgent-supplier-pos-v2")
def get_urgent_supplier_pos_v2(auth=Depends(verify_session)):
    with get_db() as conn:
        po_rows   = fetchall(conn, """
            SELECT supplier, po_number, item_code, item_code_clean,
                   CAST(qty AS INTEGER) AS qty, CAST(outstanding AS INTEGER) AS po_outstanding, status, etd
            FROM po_supplier WHERE status IN ('open','partial')
        """)
        stock_map = {r["item_code_clean"]: int(r["on_hand"] or 0)
                     for r in fetchall(conn, "SELECT item_code_clean, on_hand FROM stock")}
        cust_map  = defaultdict(list)
        for r in fetchall(conn, """
            SELECT part_number_espe_clean, customer_name,
                   CAST(SUM(outstanding) AS INTEGER) AS outstanding, MIN(ready_month) AS need_by
            FROM orders WHERE outstanding > 0
            GROUP BY part_number_espe_clean, customer_name ORDER BY MIN(ready_month) ASC
        """):
            cust_map[r["part_number_espe_clean"]].append({
                "customer": r["customer_name"], "outstanding": r["outstanding"], "need_by": r["need_by"] or "—"})
    po_groups = defaultdict(list)
    for r in po_rows: po_groups[r["item_code_clean"]].append(dict(r))
    result = []
    for clean, pos in po_groups.items():
        on_hand   = stock_map.get(clean, 0)
        customers = cust_map.get(clean, [])
        total_os  = sum(c["outstanding"] for c in customers)
        total_po  = sum(p["po_outstanding"] for p in pos)
        shortage  = on_hand + total_po - total_os
        pos.sort(key=lambda p: str(p["etd"] or "9999"))
        result.append({
            "item_code": pos[0]["item_code"], "item_code_clean": clean,
            "stock": on_hand, "shortage": shortage, "total_os_cust": total_os,
            "customers": customers,
            "po_rows": [{"po_number": p["po_number"], "status": p["status"], "qty": p["qty"],
                         "po_outstanding": p["po_outstanding"], "etd": str(p["etd"]) if p["etd"] else None} for p in pos],
        })
    result.sort(key=lambda x: -x["total_os_cust"])
    return result


@app.get("/dashboard/items-to-order")
def get_items_to_order(auth=Depends(verify_session)):
    with get_db() as conn:
        maker_map  = {r["part_number_clean"]: r["maker"] for r in fetchall(conn, "SELECT part_number_clean, maker FROM maker_master")}
        stock_map  = {r["item_code_clean"]: int(r["on_hand"] or 0) for r in fetchall(conn, "SELECT item_code_clean, on_hand FROM stock")}
        po_map     = {}
        for r in fetchall(conn, """
            SELECT item_code_clean, MAX(supplier) AS supplier,
                   CAST(SUM(CASE WHEN status IN ('open','partial') THEN outstanding ELSE 0 END) AS INTEGER) AS po_qty,
                   STRING_AGG(CASE WHEN status IN ('open','partial') THEN po_number END, ',') AS po_numbers
            FROM po_supplier GROUP BY item_code_clean
        """):
            po_map[r["item_code_clean"]] = {"supplier": r["supplier"], "po_qty": int(r["po_qty"] or 0), "po_numbers": r["po_numbers"] or ""}
        order_rows = fetchall(conn, """
            SELECT part_number_espe_clean, part_number_espe,
                   CAST(SUM(outstanding) AS INTEGER) AS total_outstanding,
                   MIN(ready_month) AS earliest_month,
                   STRING_AGG(DISTINCT customer_name, ',') AS customers,
                   STRING_AGG(DISTINCT po_number, ',') AS cust_po_numbers
            FROM orders WHERE outstanding > 0 GROUP BY part_number_espe_clean, part_number_espe
        """)
        monthly_map = defaultdict(list)
        for r in fetchall(conn, """
            SELECT part_number_espe_clean, ready_month, CAST(SUM(outstanding) AS INTEGER) AS demand
            FROM orders WHERE outstanding > 0 AND ready_month IS NOT NULL
            GROUP BY part_number_espe_clean, ready_month
        """):
            monthly_map[r["part_number_espe_clean"]].append({"month": r["ready_month"], "demand": r["demand"]})
    result = []
    for o in order_rows:
        clean    = o["part_number_espe_clean"]
        if not clean or clean == "nan": continue
        stock_qty = stock_map.get(clean, 0)
        po_info   = po_map.get(clean, {})
        po_qty    = po_info.get("po_qty", 0)
        shortage  = stock_qty + po_qty - o["total_outstanding"]
        if shortage >= 0: continue
        supplier = maker_map.get(clean)
        if not supplier and po_info.get("supplier"): supplier = po_info["supplier"].upper()
        if not supplier:
            for prefix, sup in KNOWN_SUPPLIERS.items():
                if clean.startswith(prefix): supplier = sup; break
        if not supplier: supplier = "—"
        monthly    = sorted(monthly_map.get(clean, []), key=lambda x: month_num(x["month"]))
        running    = stock_qty + po_qty
        need_month = "—"
        for m in monthly:
            running -= m["demand"]
            if running < 0: need_month = m["month"]; break
        result.append({
            "supplier": supplier, "part_number": o["part_number_espe"],
            "stock_qty": stock_qty, "po_supplier_qty": po_qty,
            "po_numbers": po_info.get("po_numbers",""),
            "customer_demand": o["total_outstanding"], "shortage": shortage,
            "needed_by": o["earliest_month"] or "—",
            "customers": o["customers"] or "—",
            "cust_po_numbers": o["cust_po_numbers"] or "", "need_month": need_month,
        })
    result.sort(key=lambda x: (MONTH_ORDER.get(x["needed_by"], 99), x["shortage"], x["supplier"].lower()))
    return result


@app.get("/dashboard/delivery-value")
def get_delivery_value(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT o.customer_name, od.month_label, SUM(od.nilai) AS total_nilai
            FROM orders o JOIN order_deliveries od ON od.order_id = o.id
            GROUP BY o.customer_name, od.month_label, od.month_index
            ORDER BY o.customer_name, od.month_index
        """)
    grouped = defaultdict(dict)
    for r in rows: grouped[r["customer_name"]][r["month_label"]] = int(r["total_nilai"] or 0)
    return [{"customer": c, "monthly": m, "total": sum(m.values())} for c, m in grouped.items()]


@app.get("/dashboard/forecast")
def get_forecast(auth=Depends(verify_session)):
    MONTH_MAP = {"JAN":"Jan","FEB":"Feb","MAR":"Mar","APR":"Apr","MEI":"May","MAY":"May",
                 "JUN":"Jun","JUNI":"Jun","JUL":"Jul","JULI":"Jul","AGU":"Aug","AGUST":"Aug",
                 "SEP":"Sep","SEPT":"Sep","OCT":"Oct","OKT":"Oct","NOV":"Nov","DEC":"Dec","DES":"Dec"}
    with get_db() as conn:
        maker_map = {r["part_number_clean"]: r["maker"] for r in fetchall(conn, "SELECT part_number_clean, maker FROM maker_master")}
        rows = fetchall(conn, "SELECT customer_name, po_number, part_number_espe, part_number_espe_clean, CAST(order_qty AS INTEGER) AS qty FROM orders WHERE po_number LIKE 'FC%%' ORDER BY customer_name, po_number")
    result = []
    for r in rows:
        po    = r["po_number"].strip()
        parts = po[3:].strip().split()
        if len(parts) >= 2:
            month = MONTH_MAP.get(parts[0].upper(), parts[0])
            year  = "20" + parts[1] if len(parts[1]) == 2 else parts[1]
            forecast_month = f"{month} {year}"
        else: forecast_month = po[3:].strip()
        result.append({
            "customer": r["customer_name"],
            "supplier": maker_map.get(str(r["part_number_espe_clean"] or "").lower().strip(), "—"),
            "part_number": r["part_number_espe"], "qty": r["qty"], "forecast_month": forecast_month,
        })
    return result


@app.get("/suppliers")
def get_suppliers(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT supplier, COUNT(*) AS total_pos,
                   SUM(CASE WHEN status IN ('open','partial') THEN 1 ELSE 0 END) AS pending_pos,
                   CAST(SUM(CASE WHEN status IN ('open','partial') THEN outstanding ELSE 0 END) AS INTEGER) AS total_outstanding
            FROM po_supplier GROUP BY supplier ORDER BY total_outstanding DESC
        """)
    return [dict(r) for r in rows]


@app.get("/suppliers/{supplier_name}/pending")
def get_supplier_pending(supplier_name: str, auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT po_number, item_code, CAST(qty AS INTEGER) AS qty,
                   unit_price, etd, status, CAST(outstanding AS INTEGER) AS outstanding
            FROM po_supplier WHERE supplier = %s AND status IN ('open','partial') ORDER BY etd ASC
        """, [supplier_name])
    now = datetime.now().date()
    result = []
    for r in rows:
        etd_fmt = "—"; days_to_etd = "—"
        if r["etd"]:
            etd = r["etd"]
            etd_fmt = etd.strftime("%d %b %Y")
            diff = (etd - now).days
            if diff < 0:    days_to_etd = f"{abs(diff)}d late"
            elif diff == 0: days_to_etd = "Due today"
            else:           days_to_etd = f"In {diff}d"
        result.append({"po_number": r["po_number"], "item_code": r["item_code"], "qty": r["qty"],
                        "unit_price": r["unit_price"], "etd": etd_fmt, "days_to_etd": days_to_etd,
                        "status": r["status"], "outstanding": r["outstanding"]})
    return result


@app.get("/suppliers/{supplier_name}/overdue")
def get_supplier_overdue(supplier_name: str, auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT po_number, item_code, CAST(qty AS INTEGER) AS qty,
                   unit_price, etd, status, CAST(outstanding AS INTEGER) AS outstanding
            FROM po_supplier WHERE supplier = %s AND status IN ('open','partial')
              AND etd IS NOT NULL AND etd < NOW() ORDER BY etd ASC
        """, [supplier_name])
    now = datetime.now().date()
    result = []
    for r in rows:
        etd_fmt = "—"; days_late = "—"
        if r["etd"]:
            etd = r["etd"]
            etd_fmt = etd.strftime("%d %b %Y")
            diff = (now - etd).days
            days_late = f"{diff}d" if diff > 0 else "Due today"
        result.append({"po_number": r["po_number"], "item_code": r["item_code"], "qty": r["qty"],
                        "unit_price": r["unit_price"], "etd": etd_fmt, "days_late": days_late,
                        "status": r["status"], "outstanding": r["outstanding"]})
    return result


@app.get("/suppliers/{supplier_name}/delivery-value")
def get_supplier_delivery_value(supplier_name: str, auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, "SELECT etd, qty, unit_price FROM po_supplier WHERE supplier = %s AND status = 'sent' AND etd IS NOT NULL", [supplier_name])
    monthly = {}
    for r in rows:
        key = r["etd"].strftime("%b %Y")
        monthly[key] = monthly.get(key, 0) + float(r["qty"] or 0) * float(r["unit_price"] or 0)
    sorted_months = sorted(monthly, key=lambda m: datetime.strptime(m, "%b %Y"))
    return {"supplier": supplier_name, "monthly": {m: round(monthly[m], 2) for m in sorted_months}}


@app.get("/suppliers/{supplier_name}/forecast")
def get_supplier_forecast(supplier_name: str, auth=Depends(verify_session)):
    MONTH_MAP = {"JAN":"Jan","FEB":"Feb","MAR":"Mar","APR":"Apr","MEI":"May","MAY":"May",
                 "JUN":"Jun","JUNI":"Jun","JUL":"Jul","JULI":"Jul","AGU":"Aug","AGUST":"Aug",
                 "SEP":"Sep","SEPT":"Sep","OCT":"Oct","OKT":"Oct","NOV":"Nov","DEC":"Dec","DES":"Dec"}
    with get_db() as conn:
        supplier_parts = {r["part_number_clean"] for r in fetchall(conn, "SELECT part_number_clean FROM maker_master WHERE maker = %s", [supplier_name.upper()])}
        rows = fetchall(conn, "SELECT customer_name, po_number, part_number_espe, part_number_espe_clean, CAST(order_qty AS INTEGER) AS qty FROM orders WHERE po_number LIKE 'FC%%' ORDER BY po_number")
    result = []
    for r in rows:
        clean = str(r["part_number_espe_clean"] or "").lower().strip()
        if clean not in supplier_parts: continue
        po    = r["po_number"].strip()
        parts = po[3:].strip().split()
        if len(parts) >= 2:
            month = MONTH_MAP.get(parts[0].upper(), parts[0])
            year  = "20" + parts[1] if len(parts[1]) == 2 else parts[1]
            forecast_month = f"{month} {year}"
        else: forecast_month = po[3:].strip()
        result.append({"customer": r["customer_name"], "part_number": r["part_number_espe"], "qty": r["qty"], "forecast_month": forecast_month})
    return result


@app.get("/dashboard/supplier-summary")
def get_supplier_summary(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT supplier, COUNT(*) AS total_pos,
                   SUM(CASE WHEN status IN ('open','partial') THEN 1 ELSE 0 END) AS pending_pos,
                   CAST(SUM(CASE WHEN status IN ('open','partial') THEN outstanding ELSE 0 END) AS INTEGER) AS total_outstanding,
                   SUM(CASE WHEN status IN ('open','partial') AND etd < NOW() THEN 1 ELSE 0 END) AS overdue_pos
            FROM po_supplier GROUP BY supplier ORDER BY total_outstanding DESC
        """)
    return [dict(r) for r in rows]


@app.get("/dashboard/price-analysis")
def get_price_analysis(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT DISTINCT ON (customer_name, part_number_espe)
                   customer_name, part_number_espe, part_number_espe_clean, price_rp, po_date_customer
            FROM orders WHERE price_rp > 0
            ORDER BY customer_name, part_number_espe, po_date_customer DESC
        """)
        buy_map = {}
        for r in fetchall(conn, "SELECT item_code_clean, unit_price, po_number FROM po_supplier WHERE unit_price > 0"):
            buy_map.setdefault(r["item_code_clean"], []).append({"unit_price": r["unit_price"], "po_number": r["po_number"]})
    def po_sort_key(pn):
        has_r = pn.upper().endswith('R')
        try: num = int(''.join(filter(str.isdigit, pn)))
        except: num = 0
        return (has_r, num)
    last_buy = {}
    for clean, pos in buy_map.items():
        pos.sort(key=lambda x: po_sort_key(x["po_number"]), reverse=True)
        last_buy[clean] = pos[0]["unit_price"]
    result = [{
        "customer_name": r["customer_name"], "part_number": r["part_number_espe"],
        "last_sell_price": float(r["price_rp"]),
        "last_buy_price": float(last_buy[r["part_number_espe_clean"]]) if r["part_number_espe_clean"] in last_buy else None,
    } for r in rows]
    result.sort(key=lambda x: (x["customer_name"], x["part_number"]))
    return result


@app.get("/dashboard/po-supplier-info")
def get_po_supplier_info(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT supplier, item_code, CAST(outstanding AS INTEGER) AS qty_os, status, po_number
            FROM po_supplier WHERE status IN ('open','partial') ORDER BY item_code ASC
        """)
    return [dict(r) for r in rows]


@app.get("/dashboard/po-supplier-summary-detail")
def get_po_supplier_summary_detail(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT supplier, item_code, maker, CAST(outstanding AS INTEGER) AS qty_os,
                   unit_price, status, etd, po_number
            FROM po_supplier WHERE status IN ('open','partial') ORDER BY etd ASC NULLS LAST
        """)
    now = datetime.now().date()
    result = []
    for r in rows:
        days_late = "—"; days_late_num = 0; due_date = "—"; is_late = False
        if r["etd"]:
            etd = r["etd"]
            due_date = etd.strftime("%d/%m/%y")
            diff = (now - etd).days
            if diff > 0:    days_late = f"{diff}d"; days_late_num = diff; is_late = True
            elif diff == 0: days_late = "Due today"
        result.append({
            "supplier": r["supplier"], "part_number": r["item_code"], "maker": r["maker"],
            "qty_os": r["qty_os"], "status": r["status"], "days_late": days_late,
            "days_late_num": days_late_num, "due_date": due_date, "po_number": r["po_number"],
            "is_late": is_late, "unit_price": float(r["unit_price"] or 0),
        })
    result.sort(key=lambda x: -x["days_late_num"])
    return result


@app.get("/dashboard/po-cust-summary-detail")
def get_po_cust_summary_detail(auth=Depends(verify_session)):
    with get_db() as conn:
        rows = fetchall(conn, """
            SELECT customer_name, part_number_espe, CAST(outstanding AS INTEGER) AS qty_os,
                   price_rp, delivery_date, po_number
            FROM orders WHERE outstanding > 0 ORDER BY part_number_espe ASC
        """)
    return [dict(r) for r in rows]
