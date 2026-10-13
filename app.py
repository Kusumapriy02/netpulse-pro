import csv, io, os, random, re, sqlite3
from datetime import date, timedelta
from functools import wraps
from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join("/tmp" if os.environ.get("VERCEL") else BASE, "broadband.db")  # Vercel only allows writing to /tmp
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

PANELS = {"customer": "customer", "staff": "staff", "admin": "admin"}
HINTS = {"customer": ("NP1001", "welcome123"), "staff": ("staff1", "staff123"), "admin": ("admin", "admin123")}


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    random.seed(7)
    if os.path.exists(DB):
        os.remove(DB)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE plans(id INTEGER PRIMARY KEY, name TEXT, speed TEXT, price INTEGER, validity_days INTEGER);
    CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, connection_id TEXT, phone TEXT, plan_id INTEGER,
                           expiry_date TEXT, followed_up INTEGER DEFAULT 0);
    CREATE TABLE transactions(id INTEGER PRIMARY KEY, ref TEXT, customer_id INTEGER, plan_id INTEGER,
                              amount INTEGER, status TEXT, created_at TEXT);
    CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT,
                       display_name TEXT, customer_id INTEGER);
    """)
    plans = [(1, "Basic", "50 Mbps", 399, 30), (2, "Standard", "100 Mbps", 699, 30), (3, "Premium", "300 Mbps", 999, 30)]
    c.executemany("INSERT INTO plans VALUES(?,?,?,?,?)", plans)
    today = date.today()
    names = ["Anitha Rao", "Ravi Kumar", "Sneha Reddy", "Kiran Babu", "Priya Nair", "Suresh Varma"]
    offsets = [3, 6, 14, 20, 9, 25]
    for i, n in enumerate(names, 1):
        conn = f"NP{1000 + i}"
        c.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,0)",
                  (i, n, conn, f"98480{10000 + i * 111}", (i % 3) + 1, (today + timedelta(days=offsets[i - 1])).isoformat()))
        c.execute("INSERT INTO users(username,password_hash,role,display_name,customer_id) VALUES(?,?,?,?,?)",
                  (conn, generate_password_hash("welcome123"), "customer", n, i))
    c.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,?,?)",
              ("staff1", generate_password_hash("staff123"), "staff", "Support Desk"))
    c.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,?,?)",
              ("admin", generate_password_hash("admin123"), "admin", "Administrator"))
    price = {p[0]: p[3] for p in plans}
    for k in range(30):
        cid = random.randint(1, 6)
        pid = (cid % 3) + 1
        d = today - timedelta(days=random.randint(1, 150))
        c.execute("INSERT INTO transactions(ref,customer_id,plan_id,amount,status,created_at) VALUES(?,?,?,?,?,?)",
                  (f"TXN{100200 + k}", cid, pid, price[pid], "success" if random.random() > 0.2 else "failed", d.isoformat()))
    c.commit()
    c.close()


def role_required(role, api=False):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if session.get("role") != role:
                if api:
                    abort(401)
                flash("Sign in to continue.", "warning")
                return redirect(url_for("login", role=role))
            return f(*a, **k)
        return w
    return deco


def new_ref():
    n = db().execute("SELECT COUNT(*) n FROM transactions").fetchone()["n"]
    return f"TXN{100200 + n}"


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/customer/login", defaults={"role": "customer"}, methods=["GET", "POST"])
@app.route("/staff/login", defaults={"role": "staff"}, methods=["GET", "POST"])
@app.route("/admin/login", defaults={"role": "admin"}, methods=["GET", "POST"])
def login(role):
    if role not in PANELS:
        abort(404)
    if session.get("role") == role:
        return redirect(url_for(PANELS[role]))
    if request.method == "POST":
        ident = request.form.get("username", "").strip()
        u = db().execute("""SELECT * FROM users WHERE role=? AND (username=? OR (role='customer' AND
                            customer_id=(SELECT id FROM customers WHERE phone=?)))""", (role, ident, ident)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear()
            session.update(user_id=u["id"], role=role, name=u["display_name"], customer_id=u["customer_id"])
            return redirect(url_for(PANELS[role]))
        flash("Wrong username or password. Check both and try again.", "danger")
    return render_template(f"login_{role}.html", hint=HINTS[role])


@app.route("/customer/register", methods=["GET", "POST"])
def register():
    if session.get("role") == "customer":
        return redirect(url_for("customer"))
    d = db()
    plans = d.execute("SELECT * FROM plans").fetchall()
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        phone = re.sub(r"\D", "", f.get("phone", ""))
        pw = f.get("password", "")
        plan = d.execute("SELECT * FROM plans WHERE id=?", (f.get("plan_id", type=int),)).fetchone()
        err = None
        if len(name) < 2:
            err = "Enter your full name."
        elif len(phone) != 10:
            err = "Enter a 10-digit phone number."
        elif len(pw) < 6:
            err = "Password must be at least 6 characters."
        elif pw != f.get("confirm", ""):
            err = "Passwords do not match."
        elif not plan:
            err = "Choose a plan."
        elif d.execute("SELECT 1 FROM customers WHERE phone=?", (phone,)).fetchone():
            err = "This phone number is already registered. Sign in instead."
        if err:
            flash(err, "danger")
        else:
            exp = (date.today() + timedelta(days=plan["validity_days"])).isoformat()
            cid = d.execute("INSERT INTO customers(name,connection_id,phone,plan_id,expiry_date,followed_up) VALUES(?,?,?,?,?,0)",
                            (name, "TMP", phone, plan["id"], exp)).lastrowid
            conn = f"NP{1000 + cid}"
            d.execute("UPDATE customers SET connection_id=? WHERE id=?", (conn, cid))
            d.execute("INSERT INTO users(username,password_hash,role,display_name,customer_id) VALUES(?,?,?,?,?)",
                      (conn, generate_password_hash(pw), "customer", name, cid))
            d.execute("INSERT INTO transactions(ref,customer_id,plan_id,amount,status,created_at) VALUES(?,?,?,?,?,?)",
                      (new_ref(), cid, plan["id"], plan["price"], "success", date.today().isoformat()))
            d.commit()
            session.clear()
            session.update(user_id=d.execute("SELECT id FROM users WHERE username=?", (conn,)).fetchone()["id"],
                           role="customer", name=name, customer_id=cid)
            flash(f"Welcome, {name}! Your connection ID is {conn}. Use it or your phone number to sign in next time.", "success")
            return redirect(url_for("customer"))
    return render_template("register.html", plans=plans, form=request.form)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You are signed out.", "success")
    return redirect(url_for("home"))


@app.route("/customer")
@role_required("customer")
def customer():
    d = db()
    cid = session["customer_id"]
    cur = d.execute("""SELECT c.*, p.name plan_name, p.speed, p.price FROM customers c
                       JOIN plans p ON p.id=c.plan_id WHERE c.id=?""", (cid,)).fetchone()
    plans = d.execute("SELECT * FROM plans").fetchall()
    txns = d.execute("""SELECT t.*, p.name plan_name FROM transactions t JOIN plans p ON p.id=t.plan_id
                        WHERE customer_id=? ORDER BY created_at DESC, id DESC LIMIT 10""", (cid,)).fetchall()
    days_left = (date.fromisoformat(cur["expiry_date"]) - date.today()).days
    return render_template("customer.html", cur=cur, plans=plans, txns=txns, days_left=days_left)


@app.route("/recharge", methods=["POST"])
@role_required("customer")
def recharge():
    d = db()
    cid = session["customer_id"]
    plan = d.execute("SELECT * FROM plans WHERE id=?", (request.form.get("plan_id", type=int),)).fetchone()
    cust = d.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not plan:
        flash("Choose a plan to recharge.", "danger")
        return redirect(url_for("customer"))
    ok = request.form.get("simulate_fail") != "on"
    ref = new_ref()
    d.execute("INSERT INTO transactions(ref,customer_id,plan_id,amount,status,created_at) VALUES(?,?,?,?,?,?)",
              (ref, cid, plan["id"], plan["price"], "success" if ok else "failed", date.today().isoformat()))
    if ok:
        start = max(date.today(), date.fromisoformat(cust["expiry_date"]))
        new_exp = (start + timedelta(days=plan["validity_days"])).isoformat()
        d.execute("UPDATE customers SET plan_id=?, expiry_date=?, followed_up=0 WHERE id=?", (plan["id"], new_exp, cid))
        flash(f"Payment received (ref {ref}). {plan['name']} is active until {new_exp}.", "success")
    else:
        flash(f"Payment failed (ref {ref}). Your plan was not renewed. Try again.", "danger")
    d.commit()
    return redirect(url_for("customer"))


@app.route("/staff")
@role_required("staff")
def staff():
    limit = (date.today() + timedelta(days=7)).isoformat()
    rows = db().execute("""SELECT c.*, p.name plan_name FROM customers c JOIN plans p ON p.id=c.plan_id
                           WHERE c.expiry_date <= ? ORDER BY c.expiry_date""", (limit,)).fetchall()
    items = [dict(r, days_left=(date.fromisoformat(r["expiry_date"]) - date.today()).days) for r in rows]
    return render_template("staff.html", items=items)


@app.route("/followup/<int:cid>", methods=["POST"])
@role_required("staff")
def followup(cid):
    db().execute("UPDATE customers SET followed_up=1 WHERE id=?", (cid,))
    db().commit()
    flash("Marked as followed up.", "success")
    return redirect(url_for("staff"))


@app.route("/admin")
@role_required("admin")
def admin():
    recent = db().execute("""SELECT t.*, c.name, p.name plan_name FROM transactions t
                             JOIN customers c ON c.id=t.customer_id JOIN plans p ON p.id=t.plan_id
                             ORDER BY created_at DESC, t.id DESC LIMIT 8""").fetchall()
    return render_template("admin.html", recent=recent)


@app.route("/admin/export.csv")
@role_required("admin")
def export():
    rows = db().execute("""SELECT t.ref, t.created_at, c.connection_id, c.name, p.name plan, t.amount, t.status
                           FROM transactions t JOIN customers c ON c.id=t.customer_id JOIN plans p ON p.id=t.plan_id
                           ORDER BY t.created_at DESC""").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ref", "date", "connection", "customer", "plan", "amount", "status"])
    w.writerows([tuple(r) for r in rows])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=transactions.csv"})


@app.route("/api/summary")
@role_required("admin", api=True)
def api_summary():
    d = db()
    ok = d.execute("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE status='success'").fetchone()["s"]
    n = d.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
    failed = d.execute("SELECT COUNT(DISTINCT customer_id) n FROM transactions WHERE status='failed'").fetchone()["n"]
    rec = d.execute("""SELECT COUNT(DISTINCT f.customer_id) n FROM transactions f JOIN transactions s
                       ON s.customer_id=f.customer_id AND s.status='success' AND s.created_at>=f.created_at
                       WHERE f.status='failed'""").fetchone()["n"]
    limit = (date.today() + timedelta(days=7)).isoformat()
    near = d.execute("SELECT COUNT(*) n FROM customers WHERE expiry_date<=?", (limit,)).fetchone()["n"]
    return jsonify(total_revenue=ok, customers=n, recovery_rate=round(100 * rec / failed) if failed else 100, near_expiry=near)


@app.route("/api/revenue_trend")
@role_required("admin", api=True)
def api_revenue_trend():
    rows = db().execute("""SELECT substr(created_at,1,7) m, SUM(amount) s FROM transactions
                           WHERE status='success' GROUP BY m ORDER BY m""").fetchall()
    return jsonify(labels=[r["m"] for r in rows], values=[r["s"] for r in rows])


@app.route("/api/plan_distribution")
@role_required("admin", api=True)
def api_plan_distribution():
    rows = db().execute("""SELECT p.name, COUNT(c.id) n FROM plans p LEFT JOIN customers c ON c.plan_id=p.id
                           GROUP BY p.id ORDER BY p.id""").fetchall()
    return jsonify(labels=[r["name"] for r in rows], values=[r["n"] for r in rows])


@app.route("/api/renewal_rate")
@role_required("admin", api=True)
def api_renewal_rate():
    r = db().execute("SELECT SUM(status='success') ok, SUM(status='failed') bad FROM transactions").fetchone()
    return jsonify(labels=["Renewed", "Lapsed"], values=[r["ok"] or 0, r["bad"] or 0])


if not os.path.exists(DB):
    init_db()  # first run (also when started by a server like gunicorn)

if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        init_db()  # start over with fresh demo data
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
