import csv, io, os, sqlite3
from datetime import datetime
import requests
from flask import Flask, abort, g, jsonify, render_template, request, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", BASE)
RESTAURANT_API_URL = os.environ.get("RESTAURANT_API_URL", "http://127.0.0.1:5001").rstrip("/")
API_KEY = os.environ.get("API_KEY", "demo-key")
CUSTOMER_ID = "C001"
OUT, IN = (os.path.join(DATA, d) for d in ("generated_orders", "received_results"))
DB = os.path.join(DATA, "customer.db")
for d in (OUT, IN):
    os.makedirs(d, exist_ok=True)

RESTAURANTS = {
    "R001": {"name": "Royal Restaurant", "menu": {
        "Chicken Biryani": (180, "Basmati rice slow-cooked with spiced chicken"),
        "Paneer Rice": (150, "Fried rice with paneer and vegetables"),
        "Coke": (40, "Chilled 300 ml bottle")}},
    "R002": {"name": "South Indian Restaurant", "menu": {
        "Masala Dosa": (90, "Crisp dosa with potato masala"),
        "Idli Sambar": (60, "Three idlis with sambar and chutney"),
        "Filter Coffee": (30, "Fresh decoction coffee")}},
    "R003": {"name": "Fast Food Corner", "menu": {
        "Veg Burger": (99, "Veg patty, lettuce, cheese"),
        "French Fries": (70, "Salted, with ketchup"),
        "Cold Coffee": (80, "Blended with ice cream")}},
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 1_000_000


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


with sqlite3.connect(DB) as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, customer_id TEXT, restaurant_id TEXT,
        input_file TEXT, result_file TEXT, stage TEXT DEFAULT 'CREATED', status TEXT DEFAULT 'PENDING',
        created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT,
        item_name TEXT, quantity INT, unit_price REAL, status TEXT DEFAULT 'PENDING');""")


def err(msg, code):
    return jsonify(success=False, message=msg), code


def overall(statuses):
    s = set(statuses)
    if not s or "PENDING" in s:
        return "PENDING"
    if s == {"ACCEPTED"}:
        return "ACCEPTED"
    if s == {"REJECTED"}:
        return "REJECTED"
    return "PARTIALLY ACCEPTED"


def get_order(oid):
    return db().execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()


def items_of(oid):
    return db().execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()


# ---------- pages ----------
@app.route("/")
def index():
    return render_template("index.html", restaurants=RESTAURANTS)


@app.route("/menu/<rid>")
def menu(rid):
    return render_template("menu.html", rid=rid, r=RESTAURANTS.get(rid) or abort(404))


@app.route("/orders")
def orders():
    rows = db().execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return render_template("orders.html", orders=rows, R=RESTAURANTS)


@app.route("/orders/<oid>")
def order_page(oid):
    o = get_order(oid) or abort(404)
    return render_template("order.html", o=o, items=items_of(oid), r=RESTAURANTS[o["restaurant_id"]])


# ---------- API ----------
@app.post("/api/orders/create")
def create_order():
    data = request.get_json(silent=True) or {}
    r = RESTAURANTS.get(data.get("restaurant_id"))
    try:
        items = [(i["item"], int(i["quantity"])) for i in data.get("items", []) if int(i["quantity"]) > 0]
    except (KeyError, ValueError, TypeError):
        return err("Invalid items", 400)
    if not r or not items or any(n not in r["menu"] for n, _ in items):
        return err("Invalid order", 400)
    oid = "ORD" + datetime.now().strftime("%m%d%H%M%S")
    fname, now = f"order_{oid}.csv", datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(OUT, fname), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "restaurant_id", "item", "quantity", "unit_price"])
        for n, q in items:
            w.writerow([oid, CUSTOMER_ID, data["restaurant_id"], n, q, r["menu"][n][0]])
    db().execute("INSERT INTO orders(order_id,customer_id,restaurant_id,input_file,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                 (oid, CUSTOMER_ID, data["restaurant_id"], fname, now, now))
    db().executemany("INSERT INTO order_items(order_id,item_name,quantity,unit_price) VALUES(?,?,?,?)",
                     [(oid, n, q, r["menu"][n][0]) for n, q in items])
    db().commit()
    return jsonify(success=True, order_id=oid, input_file=fname)


@app.post("/api/orders/upload")
def upload_to_restaurant():
    """Send the generated CSV to the restaurant backend."""
    oid = (request.get_json(silent=True) or {}).get("order_id")
    o = get_order(oid)
    if not o:
        return err("Unknown order", 404)
    try:
        with open(os.path.join(OUT, o["input_file"]), "rb") as f:
            resp = requests.post(f"{RESTAURANT_API_URL}/api/orders/upload",
                                 files={"file": (o["input_file"], f, "text/csv")},
                                 data={"order_id": oid, "callback_url": request.host_url.rstrip("/")},
                                 headers={"X-API-Key": API_KEY}, timeout=90)
        body = resp.json()
    except Exception as e:  # unreachable / bad response
        return err(f"Restaurant unreachable: {e}", 502)
    if resp.ok and body.get("success"):
        db().execute("UPDATE orders SET stage='SENT', updated_at=? WHERE order_id=?",
                     (datetime.now().isoformat(timespec="seconds"), oid))
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
    return send_file(os.path.join(folder, o[col]), mimetype="text/csv",
                     as_attachment=request.args.get("download") == "1", download_name=o[col])


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
    if not get_order(oid):
        return err("Unknown order", 404)
    fname = f"order_{oid}_result.csv"
    with open(os.path.join(IN, fname), "w", newline="") as out:
        out.write(text)
    for r in rows:
        db().execute("UPDATE order_items SET status=? WHERE order_id=? AND item_name=?",
                     (r["status"].upper(), oid, r["item"]))
    status = overall(r["status"].upper() for r in rows)
    db().execute("UPDATE orders SET result_file=?, stage='RESULT', status=?, updated_at=? WHERE order_id=?",
                 (fname, status, datetime.now().isoformat(timespec="seconds"), oid))
    db().commit()
    return jsonify(success=True, order_id=oid, message="Order result received")


if __name__ == "__main__":
    app.run(port=5000, debug=True)
