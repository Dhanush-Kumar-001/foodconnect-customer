import csv, io, json, os, sqlite3
from datetime import datetime, date, timedelta
import requests
from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", BASE)
RESTAURANT_API_URL = os.environ.get("RESTAURANT_API_URL", "http://127.0.0.1:5001").rstrip("/")
API_KEY = os.environ.get("API_KEY", "demo-key")

DEMO_USERS = {
    "C001": {
        "id": "C001",
        "name": "Dhanush Kumar",
        "role": "Software Engineer & Team Lead",
        "avatar": "👨‍💻",
        "email": "dhanush@foodconnect.demo",
        "initial_balance": 1500.0,
    },
    "C002": {
        "id": "C002",
        "name": "Priya Sharma",
        "role": "UI/UX Product Designer",
        "avatar": "👩‍🎨",
        "email": "priya@foodconnect.demo",
        "initial_balance": 1200.0,
    },
    "C003": {
        "id": "C003",
        "name": "Rahul Verma",
        "role": "Student & Fitness Enthusiast",
        "avatar": "🏃‍♂️",
        "email": "rahul@foodconnect.demo",
        "initial_balance": 800.0,
    },
    "C004": {
        "id": "C004",
        "name": "Ananya Patel",
        "role": "Marketing Campaign Specialist",
        "avatar": "👩‍💼",
        "email": "ananya@foodconnect.demo",
        "initial_balance": 2000.0,
    },
    "C005": {
        "id": "C005",
        "name": "Vikram Singh",
        "role": "Food Critic & Community Host",
        "avatar": "👨‍🍳",
        "email": "vikram@foodconnect.demo",
        "initial_balance": 1000.0,
    },
}

def get_current_user():
    cid = session.get("customer_id")
    if not cid or cid not in DEMO_USERS:
        cid = "C001"
        session["customer_id"] = "C001"
        session["customer_name"] = DEMO_USERS["C001"]["name"]
    return cid, session.get("customer_name", DEMO_USERS[cid]["name"])

CUSTOMER_ID = "C001"
CUSTOMER_NAME = "Dhanush Kumar"
OUT, IN = (os.path.join(DATA, d) for d in ("generated_orders", "received_results"))
DB = os.path.join(DATA, "customer.db")
for d in (OUT, IN):
    os.makedirs(d, exist_ok=True)

RESTAURANTS = {
    "R001": {
        "name": "Royal Restaurant",
        "menu": {
            "Chicken Biryani": (180, "Basmati rice slow-cooked with spiced chicken"),
            "Paneer Rice": (150, "Fried rice with paneer and vegetables"),
            "Coke": (40, "Chilled 300 ml bottle"),
        },
    },
    "R002": {
        "name": "South Indian Restaurant",
        "menu": {
            "Masala Dosa": (90, "Crisp dosa with potato masala"),
            "Idli Sambar": (60, "Three idlis with sambar and chutney"),
            "Filter Coffee": (30, "Fresh decoction coffee"),
        },
    },
    "R003": {
        "name": "Fast Food Corner",
        "menu": {
            "Veg Burger": (99, "Veg patty, lettuce, cheese"),
            "French Fries": (70, "Salted, with ketchup"),
            "Cold Coffee": (80, "Blended with ice cream"),
        },
    },
}

app = Flask(__name__, template_folder=os.path.join(BASE, "templates"), static_folder=os.path.join(BASE, "static"))
app.secret_key = os.environ.get("SECRET_KEY", "foodconnect-demo-secret-key-2026")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 1_000_000
app.jinja_env.globals.update(min=min, max=max)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def now():
    return datetime.now().isoformat(timespec="seconds")


with sqlite3.connect(DB) as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, customer_id TEXT, restaurant_id TEXT,
        input_file TEXT, result_file TEXT, stage TEXT DEFAULT 'CREATED', status TEXT DEFAULT 'PENDING',
        payment_method TEXT DEFAULT 'COD', created_at TEXT, updated_at TEXT);

    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT,
        item_name TEXT, quantity INT, unit_price REAL, status TEXT DEFAULT 'PENDING');

    CREATE TABLE IF NOT EXISTS wallets(
        customer_id TEXT PRIMARY KEY,
        balance REAL DEFAULT 1500.0,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS wallet_transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id TEXT,
        amount REAL,
        type TEXT,
        description TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS scheduled_orders(
        id TEXT PRIMARY KEY,
        customer_id TEXT,
        restaurant_id TEXT,
        meal_type TEXT,
        scheduled_date TEXT,
        scheduled_time TEXT,
        items_json TEXT,
        total_amount REAL,
        status TEXT DEFAULT 'SCHEDULED',
        failure_reason TEXT,
        created_order_id TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id TEXT,
        title TEXT,
        message TEXT,
        type TEXT DEFAULT 'INFO',
        is_read INT DEFAULT 0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS group_participations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        offer_id TEXT,
        customer_id TEXT,
        item_name TEXT,
        amount_paid REAL,
        status TEXT DEFAULT 'JOINED',
        joined_at TEXT,
        refunded_at TEXT
    );
    """)

    # Add column payment_method if upgrading existing database
    cur = c.cursor()
    cur.execute("PRAGMA table_info(orders)")
    cols = [r[1] for r in cur.fetchall()]
    if "payment_method" not in cols:
        cur.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT 'COD'")

    # Seed initial wallets for all 5 demo personas if not existing
    for uid, uinfo in DEMO_USERS.items():
        cur.execute("SELECT balance FROM wallets WHERE customer_id=?", (uid,))
        if not cur.fetchone():
            ts = datetime.now().isoformat(timespec="seconds")
            bal = uinfo["initial_balance"]
            cur.execute("INSERT INTO wallets(customer_id, balance, updated_at) VALUES(?, ?, ?)", (uid, bal, ts))
            cur.execute(
                """INSERT INTO wallet_transactions(customer_id, amount, type, description, created_at)
                VALUES(?, ?, ?, ?, ?)""",
                (uid, bal, "CREDIT", f"Welcome Promotional Credits for {uinfo['name']}", ts),
            )
    c.commit()


def err(msg, code):
    return jsonify(success=False, message=msg), code


def get_order(oid):
    return db().execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()


def items_of(oid):
    return db().execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()


def overall(statuses):
    s = set(statuses)
    if not s or "PENDING" in s:
        return "PENDING"
    if s == {"ACCEPTED"}:
        return "ACCEPTED"
    if s == {"REJECTED"}:
        return "REJECTED"
    return "PARTIALLY ACCEPTED"


# ---------- Multi-User Wallet Helpers ----------
def get_wallet(cid=None):
    if not cid:
        cid, _ = get_current_user()
    row = db().execute("SELECT * FROM wallets WHERE customer_id=?", (cid,)).fetchone()
    if not row:
        ts = now()
        bal = DEMO_USERS.get(cid, {}).get("initial_balance", 1500.0)
        db().execute("INSERT INTO wallets(customer_id, balance, updated_at) VALUES(?, ?, ?)", (cid, bal, ts))
        db().commit()
        return {"customer_id": cid, "balance": bal}
    return dict(row)


def deduct_wallet(amount, desc, cid=None):
    if not cid:
        cid, _ = get_current_user()
    w = get_wallet(cid)
    if w["balance"] < amount:
        return False, "Insufficient wallet balance"
    new_bal = w["balance"] - amount
    ts = now()
    db().execute("UPDATE wallets SET balance=?, updated_at=? WHERE customer_id=?", (new_bal, ts, cid))
    db().execute(
        "INSERT INTO wallet_transactions(customer_id, amount, type, description, created_at) VALUES(?,?,?,?,?)",
        (cid, amount, "DEBIT", desc, ts),
    )
    db().commit()
    return True, new_bal


def credit_wallet(amount, desc, cid=None):
    if not cid:
        cid, _ = get_current_user()
    w = get_wallet(cid)
    new_bal = w["balance"] + amount
    ts = now()
    db().execute("UPDATE wallets SET balance=?, updated_at=? WHERE customer_id=?", (new_bal, ts, cid))
    db().execute(
        "INSERT INTO wallet_transactions(customer_id, amount, type, description, created_at) VALUES(?,?,?,?,?)",
        (cid, amount, "CREDIT", desc, ts),
    )
    db().commit()
    return True, new_bal


# ---------- Multi-User Notification & Refund Helpers ----------
def add_notification(title, message, ntype="INFO", cid=None):
    if not cid:
        cid, _ = get_current_user()
    ts = now()
    db().execute(
        "INSERT INTO notifications(customer_id, title, message, type, is_read, created_at) VALUES(?,?,?,?,0,?)",
        (cid, title, message, ntype, ts),
    )
    db().commit()


def check_group_deals_refunds():
    """Checks if any group offers joined by any customer have expired without meeting participant quotas.
    If expired, automatically refunds the paid amount to each participant's website wallet,
    records a transaction, and creates a customer notification confirming the refund."""
    try:
        r = requests.get(f"{RESTAURANT_API_URL}/api/group-offers", timeout=3)
        if not r.ok:
            return
        offers = {o["id"]: o for o in r.json().get("offers", [])}
    except Exception:
        return

    joined = db().execute(
        "SELECT * FROM group_participations WHERE status='JOINED'"
    ).fetchall()

    for p in joined:
        offer = offers.get(p["offer_id"])
        if not offer:
            continue

        cid = p["customer_id"]
        if offer.get("status") == "EXPIRED":
            amount = float(p["amount_paid"])
            item = p["item_name"]
            # Refund wallet immediately for this participant
            credit_wallet(amount, f"Refund: Group Offer '{item}' expired without meeting target", cid=cid)
            ts = now()
            db().execute("UPDATE group_participations SET status='REFUNDED', refunded_at=? WHERE id=?", (ts, p["id"]))
            db().commit()
            # Customer notification
            add_notification(
                title="Group Deal Refund Confirmed",
                message=f"The group-buying offer for '{item}' expired without reaching the required participants. ₹{int(amount)} has been automatically refunded to your website wallet.",
                ntype="REFUND",
                cid=cid,
            )
        elif offer.get("status") == "SUCCESS":
            db().execute("UPDATE group_participations SET status='ACTIVATED' WHERE id=?", (p["id"],))
            db().commit()
            add_notification(
                title="Group Deal Activated!",
                message=f"The group-buying offer for '{p['item_name']}' reached its target and is now successfully activated!",
                ntype="SUCCESS",
                cid=cid,
            )


@app.context_processor
def inject_common():
    cid, cname = get_current_user()
    w = get_wallet(cid)
    try:
        unread = db().execute("SELECT COUNT(*) FROM notifications WHERE customer_id=? AND is_read=0", (cid,)).fetchone()[0]
    except Exception:
        unread = 0
    return {
        "current_user": DEMO_USERS.get(cid, {"id": cid, "name": cname, "avatar": "👤", "role": "Customer"}),
        "demo_users": DEMO_USERS,
        "wallet": w,
        "unread_notifications_count": unread,
    }


# ---------- Authentication & Persona Switching ----------
@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        cid = request.form.get("customer_id") or (request.get_json(silent=True) or {}).get("customer_id")
        if cid in DEMO_USERS:
            session["customer_id"] = cid
            session["customer_name"] = DEMO_USERS[cid]["name"]
            next_url = request.args.get("next") or "/"
            if request.is_json:
                return jsonify(success=True, customer_id=cid, customer_name=DEMO_USERS[cid]["name"], redirect=next_url)
            return redirect(next_url)
        return render_template("login.html", users=list(DEMO_USERS.values()), error="Please select a valid persona.", next_url=request.args.get("next", "/"))

    users_with_balance = []
    for uid, uinfo in DEMO_USERS.items():
        w = db().execute("SELECT balance FROM wallets WHERE customer_id=?", (uid,)).fetchone()
        bal = w["balance"] if w else uinfo["initial_balance"]
        users_with_balance.append({**uinfo, "balance": bal})

    return render_template("login.html", users=users_with_balance, current_id=session.get("customer_id", "C001"), next_url=request.args.get("next", "/"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.post("/api/switch-user")
def api_switch_user():
    data = request.get_json(silent=True) or {}
    cid = data.get("customer_id")
    if cid in DEMO_USERS:
        session["customer_id"] = cid
        session["customer_name"] = DEMO_USERS[cid]["name"]
        return jsonify(success=True, customer_id=cid, customer_name=DEMO_USERS[cid]["name"])
    return err("Invalid user persona", 400)


# ---------- Live Inventory Fetcher ----------
def fetch_live_inventory(rid=None):
    """Fetches real-time portion stock from the restaurant portal with graceful fallback."""
    try:
        url = f"{RESTAURANT_API_URL}/api/inventory"
        if rid:
            url += f"?restaurant_id={rid}"
        r = requests.get(url, timeout=2)
        if r.ok:
            return r.json().get("inventory", {})
    except Exception:
        pass
    fallback = {}
    for r_key, r_info in RESTAURANTS.items():
        fallback[r_key] = {}
        for dish in r_info["menu"].keys():
            fallback[r_key][dish] = {
                "available_qty": 20,
                "is_low_stock": False,
                "is_sold_out": False,
            }
    return fallback


# ---------- Pages ----------
@app.route("/")
def index():
    cid, _ = get_current_user()
    check_group_deals_refunds()
    wallet = get_wallet(cid)
    check_and_run_due_orders()
    return render_template("index.html", restaurants=RESTAURANTS, wallet=wallet)


@app.route("/menu/<rid>")
def menu(rid):
    cid, _ = get_current_user()
    r = RESTAURANTS.get(rid) or abort(404)
    wallet = get_wallet(cid)
    inv_data = fetch_live_inventory(rid).get(rid, {})
    return render_template("menu.html", rid=rid, r=r, wallet=wallet, inventory=inv_data)


@app.route("/orders")
def orders():
    cid, _ = get_current_user()
    rows = db().execute("SELECT * FROM orders WHERE customer_id=? ORDER BY created_at DESC", (cid,)).fetchall()
    wallet = get_wallet(cid)
    return render_template("orders.html", orders=rows, R=RESTAURANTS, wallet=wallet)


@app.route("/orders/<oid>")
def order_page(oid):
    cid, _ = get_current_user()
    o = get_order(oid) or abort(404)
    wallet = get_wallet(cid)
    return render_template("order.html", o=o, items=items_of(oid), r=RESTAURANTS[o["restaurant_id"]], wallet=wallet)


@app.route("/planner")
def planner_page():
    cid, _ = get_current_user()
    wallet = get_wallet(cid)
    check_and_run_due_orders()
    scheduled = db().execute("SELECT * FROM scheduled_orders WHERE customer_id=? ORDER BY scheduled_date ASC, scheduled_time ASC", (cid,)).fetchall()
    enriched = []
    for s in scheduled:
        raw_items = json.loads(s["items_json"]) if s["items_json"] else []
        r_menu = RESTAURANTS.get(s["restaurant_id"], {}).get("menu", {})
        items = []
        for it in raw_items:
            up = it.get("unit_price")
            if up is None:
                up = r_menu.get(it.get("item"), (0,))[0]
            items.append({**it, "unit_price": up})
        enriched.append({**dict(s), "items": items, "dish_items": items, "rname": RESTAURANTS.get(s["restaurant_id"], {}).get("name", s["restaurant_id"])})
    return render_template("planner.html", scheduled=enriched, restaurants=RESTAURANTS, restaurants_json=json.dumps(RESTAURANTS), wallet=wallet)


@app.route("/wallet")
def wallet_page():
    cid, _ = get_current_user()
    check_group_deals_refunds()
    wallet = get_wallet(cid)
    txs = db().execute("SELECT * FROM wallet_transactions WHERE customer_id=? ORDER BY created_at DESC", (cid,)).fetchall()
    return render_template("wallet.html", wallet=wallet, transactions=txs)


@app.route("/group-deals")
def group_deals_page():
    cid, _ = get_current_user()
    check_group_deals_refunds()
    wallet = get_wallet(cid)
    offers = []
    try:
        r = requests.get(f"{RESTAURANT_API_URL}/api/group-offers", timeout=3)
        if r.ok:
            offers = r.json().get("offers", [])
    except Exception:
        pass
    parts = db().execute("SELECT * FROM group_participations WHERE customer_id=?", (cid,)).fetchall()
    parts_map = {p["offer_id"]: dict(p) for p in parts}
    return render_template("group_deals.html", offers=offers, wallet=wallet, my_parts=parts_map)


@app.route("/charity")
def charity_page():
    cid, _ = get_current_user()
    wallet = get_wallet(cid)
    donations = []
    try:
        r = requests.get(f"{RESTAURANT_API_URL}/api/donations", timeout=3)
        if r.ok:
            donations = r.json().get("donations", [])
    except Exception:
        pass
    return render_template("charity.html", donations=donations, wallet=wallet)


@app.post("/api/donations/<did>/claim")
def api_forward_charity_claim(did):
    """Proxy claim request to restaurant portal so charities can claim from customer portal too."""
    data = request.get_json(silent=True) or {}
    try:
        resp = requests.post(f"{RESTAURANT_API_URL}/api/donations/{did}/claim", json=data, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return err(f"Restaurant charity service offline: {e}", 502)



# ---------- Core Order API (CSV Generation & Transmission) ----------
@app.post("/api/orders/create")
def create_order():
    cid, cname = get_current_user()
    data = request.get_json(silent=True) or {}
    rid = data.get("restaurant_id")
    r = RESTAURANTS.get(rid)
    pay_method = data.get("payment_method", "COD")
    try:
        items = [(i["item"], int(i["quantity"])) for i in data.get("items", []) if int(i["quantity"]) > 0]
    except (KeyError, ValueError, TypeError):
        return err("Invalid items", 400)
    if not r or not items or any(n not in r["menu"] for n, _ in items):
        return err("Invalid order", 400)

    # Calculate total
    total = sum(q * r["menu"][n][0] for n, q in items)

    # Check Wallet Payment if selected
    if pay_method == "WALLET":
        ok, msg = deduct_wallet(total, f"Food Order at {r['name']}", cid=cid)
        if not ok:
            return err(f"Wallet Payment Failed: {msg}. Please top up your wallet.", 400)

    oid = "ORD" + datetime.now().strftime("%m%d%H%M%S")
    fname, ts = f"order_{oid}.csv", now()

    # Generate CSV
    with open(os.path.join(OUT, fname), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "restaurant_id", "item", "quantity", "unit_price"])
        for n, q in items:
            w.writerow([oid, cid, rid, n, q, r["menu"][n][0]])

    db().execute(
        """INSERT INTO orders(order_id,customer_id,restaurant_id,input_file,payment_method,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)""",
        (oid, cid, rid, fname, pay_method, ts, ts),
    )
    db().executemany(
        "INSERT INTO order_items(order_id,item_name,quantity,unit_price) VALUES(?,?,?,?)",
        [(oid, n, q, r["menu"][n][0]) for n, q in items],
    )
    db().commit()
    return jsonify(success=True, order_id=oid, input_file=fname, total_amount=total)


@app.post("/api/orders/upload")
def upload_to_restaurant():
    """Send the generated CSV to the restaurant backend."""
    oid = (request.get_json(silent=True) or {}).get("order_id")
    o = get_order(oid)
    if not o:
        return err("Unknown order", 404)
    try:
        with open(os.path.join(OUT, o["input_file"]), "rb") as f:
            resp = requests.post(
                f"{RESTAURANT_API_URL}/api/orders/upload",
                files={"file": (o["input_file"], f, "text/csv")},
                data={"order_id": oid, "callback_url": request.host_url.rstrip("/")},
                headers={"X-API-Key": API_KEY},
                timeout=90,
            )
        body = resp.json()
    except Exception as e:
        return err(f"Restaurant unreachable: {e}", 502)
    if resp.ok and body.get("success"):
        db().execute("UPDATE orders SET stage='SENT', updated_at=? WHERE order_id=?", (now(), oid))
        db().commit()
    return jsonify(body), resp.status_code


@app.get("/api/orders/<oid>")
def api_order(oid):
    o = get_order(oid) or abort(404)
    return jsonify({**dict(o), "items": [dict(i) for i in items_of(oid)]})


def serve(oid, col, folder):
    o = get_order(oid)
    if not o or not o[col]:
        abort(404)
    return send_file(
        os.path.join(folder, o[col]),
        mimetype="text/csv",
        as_attachment=request.args.get("download") == "1",
        download_name=o[col],
    )


@app.get("/api/orders/<oid>/input")
def get_input(oid):
    return serve(oid, "input_file", OUT)


@app.get("/api/orders/<oid>/result")
def get_result(oid):
    return serve(oid, "result_file", IN)


@app.post("/api/orders/result")
def receive_result():
    """Called by the restaurant backend with the output CSV."""
    if request.headers.get("X-API-Key") != API_KEY:
        return err("Unauthorized", 401)
    f = request.files.get("file")
    if not f or not (f.filename or "").lower().endswith(".csv"):
        return err("A .csv file is required", 400)
    try:
        text = f.read().decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        need = {"order_id", "item", "quantity", "unit_price", "total_price", "status"}
        assert rows and need <= set(rows[0])
        assert all(r["status"].upper() in ("ACCEPTED", "REJECTED") for r in rows)
    except Exception:
        return err("Invalid CSV format", 400)
    oid = rows[0]["order_id"]
    order = get_order(oid)
    if not order:
        return err("Unknown order", 404)

    fname = f"order_{oid}_result.csv"
    with open(os.path.join(IN, fname), "w", newline="") as out:
        out.write(text)

    # Process item statuses
    rejected_refund = 0
    for r in rows:
        st = r["status"].upper()
        db().execute("UPDATE order_items SET status=? WHERE order_id=? AND item_name=?", (st, oid, r["item"]))
        if st == "REJECTED" and order["payment_method"] == "WALLET":
            rejected_refund += float(r.get("total_price", 0))

    # Auto-refund rejected items if customer paid by wallet!
    if rejected_refund > 0:
        credit_wallet(rejected_refund, f"Refund for unfulfilled items on #{oid}", cid=order["customer_id"])

    status = overall(r["status"].upper() for r in rows)
    db().execute("UPDATE orders SET result_file=?, stage='RESULT', status=?, updated_at=? WHERE order_id=?", (fname, status, now(), oid))
    db().commit()
    return jsonify(success=True, order_id=oid, message="Order result received")


# ---------- Feature 1: Multi-User Wallet API ----------
@app.get("/api/wallet")
def api_get_wallet():
    cid, _ = get_current_user()
    w = get_wallet(cid)
    txs = db().execute("SELECT * FROM wallet_transactions WHERE customer_id=? ORDER BY created_at DESC LIMIT 20", (cid,)).fetchall()
    return jsonify(success=True, balance=w["balance"], transactions=[dict(t) for t in txs])


@app.post("/api/wallet/topup")
def api_wallet_topup():
    cid, _ = get_current_user()
    data = request.get_json(silent=True) or {}
    try:
        amount = float(data.get("amount", 0))
        assert amount > 0
    except (ValueError, AssertionError):
        return err("Invalid amount", 400)
    ok, new_bal = credit_wallet(amount, "Wallet Top-Up (via Online Banking/Card)", cid=cid)
    return jsonify(success=True, balance=new_bal, message=f"Successfully added ₹{int(amount)} to wallet!")


# ---------- Feature 1: Scheduled Food Ordering & Meal Planning ----------
@app.post("/api/scheduled/create")
def api_create_scheduled_order():
    """Customers select multiple food items and specify quantities when planning a meal.
    Respective restaurant receives notification containing selected items, quantities, date & time,
    and reserves the scheduled food items to ensure availability."""
    cid, cname = get_current_user()
    data = request.get_json(silent=True) or {}
    rid = data.get("restaurant_id")
    meal_type = data.get("meal_type", "LUNCH")
    sched_date = data.get("scheduled_date")
    sched_time = data.get("scheduled_time", "12:30")
    items = data.get("items", [])

    r = RESTAURANTS.get(rid)
    if not r or not items or not sched_date:
        return err("Restaurant, items, and scheduled date are required", 400)

    # Validate items and calculate total amount
    valid_items = []
    total = 0.0
    for i in items:
        dish = i.get("item")
        qty = int(i.get("quantity", 0))
        if dish in r["menu"] and qty > 0:
            uprice = float(r["menu"][dish][0])
            valid_items.append({"item": dish, "quantity": qty, "unit_price": uprice})
            total += qty * uprice

    if not valid_items:
        return err("Please select at least one valid food item with quantity > 0", 400)

    sid = "SCH" + datetime.now().strftime("%m%d%H%M%S")
    ts = now()

    db().execute(
        """INSERT INTO scheduled_orders(id, customer_id, restaurant_id, meal_type, scheduled_date,
        scheduled_time, items_json, total_amount, status, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (sid, cid, rid, meal_type, sched_date, sched_time, json.dumps(valid_items), total, "SCHEDULED", ts),
    )
    db().commit()

    # Notify restaurant and reserve inventory portions
    cb_url = request.host_url.rstrip("/") if request else "http://127.0.0.1:5000"
    restaurant_notified = False
    try:
        resp = requests.post(
            f"{RESTAURANT_API_URL}/api/scheduled/notify",
            json={
                "scheduled_id": sid,
                "customer_id": cid,
                "customer_name": cname,
                "restaurant_id": rid,
                "meal_type": meal_type,
                "scheduled_date": sched_date,
                "scheduled_time": sched_time,
                "items": valid_items,
                "callback_url": cb_url,
            },
            headers={"X-API-Key": API_KEY},
            timeout=5,
        )
        if resp.ok and resp.json().get("success"):
            restaurant_notified = True
    except Exception as e:
        print(f"Warning: Could not notify restaurant immediately: {e}")

    add_notification(
        title="Meal Plan Scheduled & Reserved",
        message=f"Meal plan #{sid} scheduled for {sched_date} ({meal_type}) at {r['name']}. Portions reserved with kitchen.",
        ntype="SUCCESS",
        cid=cid,
    )

    return jsonify(
        success=True,
        scheduled_id=sid,
        restaurant_notified=restaurant_notified,
        message=f"Meal scheduled for {sched_date}! {len(valid_items)} item(s) reserved at {r['name']}.",
    )


@app.post("/api/scheduled/<sid>/cancel")
def api_cancel_scheduled(sid):
    sched = db().execute("SELECT * FROM scheduled_orders WHERE id=?", (sid,)).fetchone()
    if not sched:
        return err("Scheduled order not found", 404)
    if sched["status"] != "SCHEDULED":
        return err(f"Order is already {sched['status']}", 409)

    db().execute("UPDATE scheduled_orders SET status='CANCELLED' WHERE id=?", (sid,))
    db().commit()
    return jsonify(success=True, message="Scheduled meal cancelled successfully.")


@app.post("/api/scheduled/advance-notice")
def api_scheduled_advance_notice():
    """If the restaurant cannot prepare any scheduled food item on the selected date,
    the customer is notified in advance."""
    data = request.get_json(silent=True) or {}
    sid = data.get("scheduled_id")
    reason = data.get("reason", "Kitchen unable to prepare scheduled meal")
    st = data.get("status", "REJECTED_BY_RESTAURANT")

    s = db().execute("SELECT * FROM scheduled_orders WHERE id=?", (sid,)).fetchone()
    if not s:
        return err("Scheduled order not found", 404)

    db().execute(
        "UPDATE scheduled_orders SET status='REJECTED_BY_RESTAURANT', failure_reason=? WHERE id=?",
        (reason, sid),
    )
    db().commit()

    cid = s["customer_id"]
    rname = RESTAURANTS.get(s["restaurant_id"], {}).get("name", "Restaurant")
    add_notification(
        title=f"⚠️ Advance Notice from {rname}",
        message=f"{rname} cannot prepare your scheduled meal (#{sid}) on {s['scheduled_date']} ({s['meal_type']}). Reason: {reason}. No amount will be charged.",
        ntype="WARNING",
        cid=cid,
    )
    return jsonify(success=True, message="Advance cancellation notice received and customer notified")


def check_and_run_due_orders():
    """Automatically triggered on site visits: checks if any scheduled meals are due today and places them."""
    today = date.today().isoformat()
    now_time = datetime.now().strftime("%H:%M")
    due = db().execute(
        "SELECT * FROM scheduled_orders WHERE status='SCHEDULED' AND (scheduled_date < ? OR (scheduled_date = ? AND scheduled_time <= ?))",
        (today, today, now_time),
    ).fetchall()

    for s in due:
        sid = s["id"]
        cid = s["customer_id"]
        total = float(s["total_amount"])
        # Check wallet balance
        ok, msg = deduct_wallet(total, f"Scheduled Meal #{sid} Auto-Deduction", cid=cid)
        if not ok:
            db().execute("UPDATE scheduled_orders SET status='FAILED', failure_reason=? WHERE id=?", (msg, sid))
            db().commit()
            continue

        # Create the actual order
        items = json.loads(s["items_json"])
        rid = s["restaurant_id"]
        r = RESTAURANTS.get(rid)
        oid = "ORD" + datetime.now().strftime("%m%d%H%M%S")
        fname, ts = f"order_{oid}.csv", now()

        with open(os.path.join(OUT, fname), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["order_id", "customer_id", "restaurant_id", "item", "quantity", "unit_price"])
            for i in items:
                w.writerow([oid, cid, rid, i["item"], i["quantity"], r["menu"][i["item"]][0]])

        db().execute(
            """INSERT INTO orders(order_id,customer_id,restaurant_id,input_file,payment_method,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (oid, cid, rid, fname, "WALLET", ts, ts),
        )
        db().executemany(
            "INSERT INTO order_items(order_id,item_name,quantity,unit_price) VALUES(?,?,?,?)",
            [(oid, i["item"], i["quantity"], r["menu"][i["item"]][0]) for i in items],
        )
        db().execute("UPDATE scheduled_orders SET status='EXECUTED', created_order_id=? WHERE id=?", (oid, sid))
        db().commit()

        # Try automatic dispatch to restaurant
        try:
            with open(os.path.join(OUT, fname), "rb") as f:
                requests.post(
                    f"{RESTAURANT_API_URL}/api/orders/upload",
                    files={"file": (fname, f, "text/csv")},
                    data={"order_id": oid, "callback_url": "http://127.0.0.1:5000"},
                    headers={"X-API-Key": API_KEY},
                    timeout=10,
                )
            db().execute("UPDATE orders SET stage='SENT', updated_at=? WHERE order_id=?", (now(), oid))
            db().commit()
        except Exception:
            pass


@app.post("/api/scheduled/run-due")
def trigger_run_due():
    check_and_run_due_orders()
    return jsonify(success=True, message="Checked and executed due scheduled orders")


# ---------- Notifications API ----------
@app.get("/api/notifications")
def api_get_notifications():
    cid, _ = get_current_user()
    rows = db().execute("SELECT * FROM notifications WHERE customer_id=? ORDER BY created_at DESC LIMIT 30", (cid,)).fetchall()
    unread = sum(1 for r in rows if not r["is_read"])
    return jsonify(success=True, notifications=[dict(r) for r in rows], unread_count=unread)


@app.post("/api/notifications/clear")
def api_clear_notifications():
    cid, _ = get_current_user()
    db().execute("UPDATE notifications SET is_read=1 WHERE customer_id=?", (cid,))
    db().commit()
    return jsonify(success=True, message="All notifications marked as read")


# ---------- Feature 4: Customer Group Buying API ----------
@app.post("/api/group-deals/<gid>/join")
def api_join_group_deal(gid):
    cid, cname = get_current_user()
    # Fetch offer details from restaurant
    try:
        r = requests.get(f"{RESTAURANT_API_URL}/api/group-offers", timeout=3)
        if not r.ok:
            return err("Unable to connect to Restaurant Group Deals service", 502)
        offers = r.json().get("offers", [])
        offer = next((o for o in offers if o["id"] == gid), None)
        if not offer:
            return err("Group offer not found", 404)
    except Exception as e:
        return err(f"Restaurant service offline: {e}", 502)

    # Check if this user already joined
    existing = db().execute(
        "SELECT id FROM group_participations WHERE offer_id=? AND customer_id=? AND status='JOINED'",
        (gid, cid),
    ).fetchone()
    if existing:
        return err("You have already joined this group deal!", 409)

    price = float(offer["group_price"])
    # Deduct participation price from customer wallet
    ok, msg = deduct_wallet(price, f"Joined Group Deal for {offer['item_name']} (₹{int(price)})", cid=cid)
    if not ok:
        return err(f"Wallet balance insufficient (₹{int(price)} required). Please top up.", 400)

    # Record in group_participations
    db().execute(
        "INSERT INTO group_participations(offer_id, customer_id, item_name, amount_paid, status, joined_at) VALUES(?,?,?,?,?,?)",
        (gid, cid, offer["item_name"], price, "JOINED", now()),
    )
    db().commit()

    # Call restaurant join endpoint
    try:
        resp = requests.post(
            f"{RESTAURANT_API_URL}/api/group-offers/{gid}/join",
            json={"customer_id": cid, "customer_name": cname},
            timeout=5,
        )
        data = resp.json()
        if not resp.ok or not data.get("success"):
            # Refund wallet on join failure
            credit_wallet(price, f"Refund: Could not join group deal #{gid}", cid=cid)
            db().execute("DELETE FROM group_participations WHERE offer_id=? AND customer_id=?", (gid, cid))
            db().commit()
            return err(data.get("message", "Failed to join group deal"), resp.status_code)

        add_notification(
            title="Joined Group Deal",
            message=f"You joined the group deal for '{offer['item_name']}' at ₹{int(price)}. 100% wallet refund guaranteed if quota is not reached before deadline.",
            ntype="SUCCESS",
            cid=cid,
        )

        return jsonify(
            success=True,
            activated=data.get("activated", False),
            message=data.get("message", "Joined group deal successfully!"),
        )
    except Exception as e:
        credit_wallet(price, f"Refund: Connection error joining deal #{gid}", cid=cid)
        db().execute("DELETE FROM group_participations WHERE offer_id=? AND customer_id=?", (gid, cid))
        db().commit()
        return err(f"Network error: {e}", 502)


@app.post("/api/group-deals/deal-expired")
def api_deal_expired_webhook():
    check_group_deals_refunds()
    return jsonify(success=True, message="Group deal refunds processed successfully")


if __name__ == "__main__":
    app.run(port=5000, debug=True)

