import hmac, json, os, random, re, secrets, sqlite3, threading, time
import hashlib
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
load_dotenv()
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, date,timezone,timedelta
from pathlib import Path
import click
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, jsonify, abort, url_for, redirect, Response, session, flash

BASE = Path(__file__).parent
DB = BASE / "database.db"
DATAF = BASE / "data/questions.json"
UPLOADS = BASE / "static/uploads"
app = Flask(__name__)
app.json.ensure_ascii = False
app.config.update(SEND_FILE_MAX_AGE_DEFAULT=604800, MAX_CONTENT_LENGTH=2 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=8 * 3600,
                  SESSION_COOKIE_SECURE=os.environ.get("SITE_URL", "").startswith("https"))
SITE = "RRB ALP CBT-1 Practice"
NOINDEX_PATHS = ("/quiz", "/api", "/wrong", "/static", "/admin")
XROBOTS = ("/quiz", "/api", "/wrong", "/admin")
DIFFS = ("easy", "medium", "hard"); QTYPES = ("practice", "previous_paper", "important")

def db():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row; return c

@contextmanager
def tx():
    c = db()
    try:
        yield c; c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()

def now(): return datetime.now().isoformat(timespec="seconds")

def bump(c):
    c.execute("INSERT INTO meta(key,value) VALUES('qver','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
    c.execute("INSERT INTO meta(key,value) VALUES('qmod',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (date.today().isoformat(),))

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, qid TEXT, correct INTEGER, ts DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS wrong(qid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, score INTEGER, total INTEGER, ts DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS subjects(id TEXT PRIMARY KEY, name TEXT NOT NULL, ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS topics(id TEXT PRIMARY KEY, subject TEXT NOT NULL, name TEXT NOT NULL, concept TEXT DEFAULT '', formulas TEXT DEFAULT '[]', examples TEXT DEFAULT '[]', ord INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, date TEXT NOT NULL, shift TEXT NOT NULL, time TEXT DEFAULT '', source TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS questions(
 id TEXT PRIMARY KEY, subject TEXT NOT NULL, topic TEXT NOT NULL, subtopic TEXT NOT NULL DEFAULT '',
 question TEXT NOT NULL DEFAULT '', opt_a TEXT NOT NULL DEFAULT '', opt_b TEXT NOT NULL DEFAULT '', opt_c TEXT NOT NULL DEFAULT '', opt_d TEXT NOT NULL DEFAULT '',
 answer INTEGER NOT NULL CHECK(answer BETWEEN 0 AND 3), explanation TEXT NOT NULL DEFAULT '',
 difficulty TEXT NOT NULL DEFAULT 'medium' CHECK(difficulty IN ('easy','medium','hard')),
 qtype TEXT NOT NULL DEFAULT 'practice' CHECK(qtype IN ('practice','previous_paper','important')),
 paper_id TEXT, paper_date TEXT, shift TEXT, qno INTEGER, source TEXT, img TEXT, img_w INTEGER, img_h INTEGER,
 correct_answer TEXT, tag TEXT, pattern TEXT, pattern_papers INTEGER, pattern_size INTEGER,
 sort_key INTEGER NOT NULL DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_q ON questions(paper_id, qno) WHERE paper_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_q_topic ON questions(topic);
CREATE TABLE IF NOT EXISTS admins(username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, created_at TEXT);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    phone TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(student_id) REFERENCES students(id)
);

"""

def import_json(path, c):
    """Optional import of a questions.json-style file. Existing ids are skipped; content is copied unchanged."""
    d = json.loads(Path(path).read_text(encoding="utf-8")); sp = BASE / "data/img_sizes.json"
    sizes = json.loads(sp.read_text()) if sp.exists() else {}
    for i, s in enumerate(d.get("subjects", [])): c.execute("INSERT OR IGNORE INTO subjects(id,name,ord) VALUES(?,?,?)", (s["id"], s["name"], i))
    for i, t in enumerate(d.get("topics", [])):
        c.execute("INSERT OR IGNORE INTO topics(id,subject,name,concept,formulas,examples,ord) VALUES(?,?,?,?,?,?,?)",
                  (t["id"], t["subject"], t["name"], t.get("concept", ""), json.dumps(t.get("formulas", []), ensure_ascii=False), json.dumps(t.get("examples", []), ensure_ascii=False), i))
    for p in d.get("papers", []):
        c.execute("INSERT OR IGNORE INTO papers(id,date,shift,time,source) VALUES(?,?,?,?,?)", (p["id"], p["date"], p["shift"], p.get("time", ""), p.get("source", p.get("file", ""))))
    k = (c.execute("SELECT COALESCE(MAX(sort_key),-1) FROM questions").fetchone()[0]) + 1; n = 0; t0 = now()
    for q in d.get("questions", []):
        if c.execute("SELECT 1 FROM questions WHERE id=?", (q["id"],)).fetchone(): continue
        o = q["o"]; sz = sizes.get(q.get("img")) or [None, None]
        c.execute("""INSERT INTO questions(id,subject,topic,question,opt_a,opt_b,opt_c,opt_d,answer,explanation,qtype,paper_id,paper_date,shift,qno,source,img,img_w,img_h,
                     correct_answer,tag,pattern,pattern_papers,pattern_size,sort_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (q["id"], q["subject"], q["topic"], q["q"], o[0], o[1], o[2], o[3], q["a"], q.get("e", ""), "previous_paper" if q.get("paper") else "practice",
                   q.get("paper"), q.get("paper_date"), q.get("shift"), q.get("qno"), q.get("source"), q.get("img"), sz[0], sz[1],
                   q.get("correct_answer"), q.get("tag"), q.get("pattern"), q.get("pattern_papers"), q.get("pattern_size"), k, t0, t0)); k += 1; n += 1
    if n:
        bump(c)
        if DATAF.exists(): c.execute("UPDATE meta SET value=? WHERE key='qmod'", (datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat(),))
    return n

def init_db():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with tx() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM questions LIMIT 1").fetchone() and DATAF.exists(): import_json(DATAF, c)
        if not c.execute("SELECT 1 FROM meta WHERE key='secret'").fetchone(): c.execute("INSERT INTO meta VALUES('secret',?)", (secrets.token_hex(32),))
        pw = os.environ.get("ADMIN_PASSWORD")
        if pw and len(pw) >= 10 and not c.execute("SELECT 1 FROM admins").fetchone():
            c.execute("INSERT INTO admins VALUES(?,?,?)", (os.environ.get("ADMIN_USERNAME", "admin"), generate_password_hash(pw), now()))
        app.secret_key = os.environ.get("SECRET_KEY") or c.execute("SELECT value FROM meta WHERE key='secret'").fetchone()[0]

# ---------- in-memory read cache rebuilt from SQLite whenever the question version changes ----------
SUBJECTS = []; SUBJ = {}; TOPICS = []; TOPIC = {}; PLIST = []; PAPERS = {}; PSLUG = {}; ALLQ = []; QS = {}; PQ = []
PN = Counter(); ALLN = Counter(); SUBJ_PN = Counter(); LASTMOD = date.today().isoformat(); _ver = None; _lock = threading.Lock()

def ca_text(a, o): return "ABCD"[a] + (". " + o[a] if o[a] else "")

def row_to_q(r):
    o = [r["opt_a"], r["opt_b"], r["opt_c"], r["opt_d"]]
    d = dict(id=r["id"], subject=r["subject"], topic=r["topic"], q=r["question"], question=r["question"], o=o, options=o, a=r["answer"], e=r["explanation"],
             correct_answer=r["correct_answer"] or ca_text(r["answer"], o), qtype=r["qtype"], subtopic=r["subtopic"], difficulty=r["difficulty"],
             source=r["source"] or "", created_at=r["created_at"])
    if r["paper_id"]:
        d.update(paper=r["paper_id"], paper_date=r["paper_date"], shift=r["shift"], qno=r["qno"], tag=r["tag"] or "normal",
                 pattern=r["pattern"], pattern_papers=r["pattern_papers"] or 1, pattern_size=r["pattern_size"] or 1)
    if r["img"]: d.update(img=r["img"], img_w=r["img_w"], img_h=r["img_h"])
    return d

def refresh(force=False):
    global SUBJECTS, SUBJ, TOPICS, TOPIC, PLIST, PAPERS, PSLUG, ALLQ, QS, PQ, PN, ALLN, SUBJ_PN, LASTMOD, _ver
    with tx() as c:
        v = c.execute("SELECT value FROM meta WHERE key='qver'").fetchone(); v = v[0] if v else "0"
        if v == _ver and not force: return
        with _lock:
            subs = [dict(r) for r in c.execute("SELECT id,name FROM subjects ORDER BY ord")]
            tops = []
            for r in c.execute("SELECT * FROM topics ORDER BY ord"):
                t = dict(r); t["formulas"] = json.loads(t["formulas"] or "[]"); t["examples"] = json.loads(t["examples"] or "[]"); tops.append(t)
            allq = [row_to_q(r) for r in c.execute("SELECT * FROM questions ORDER BY sort_key, rowid")]
            cnt = Counter(q["paper"] for q in allq if q.get("paper"))
            pl = []
            for r in c.execute("SELECT * FROM papers"):
                if not cnt[r["id"]]: continue
                p = dict(r); d = datetime.strptime(p["date"], "%d/%m/%Y"); n = int(re.sub(r"\D", "", p["shift"]) or 0)
                p.update(slug=f"{d:%d-%m-%Y}-shift-{n}", long=f"{d.day} {d:%B %Y}", short=f"{d.day} {d:%b %Y}", iso=f"{d:%Y-%m-%d}", n=n, count=cnt[r["id"]],
                         verified=p["source"].lower().endswith(".pdf"), dt=d); pl.append(p)
            pl.sort(key=lambda p: (p["dt"], p["n"]))
            pq = [q for q in allq if q.get("paper")]
            m = c.execute("SELECT value FROM meta WHERE key='qmod'").fetchone()
            SUBJECTS, SUBJ = subs, {s["id"]: s for s in subs}; TOPICS, TOPIC = tops, {t["id"]: t for t in tops}
            PLIST, PAPERS, PSLUG = pl, {p["id"]: p for p in pl}, {p["slug"]: p for p in pl}
            ALLQ, QS, PQ = allq, {q["id"]: q for q in allq}, pq
            PN, ALLN, SUBJ_PN = Counter(q["topic"] for q in pq), Counter(q["topic"] for q in allq), Counter(q["subject"] for q in pq)
            LASTMOD = m[0] if m else date.today().isoformat(); _ver = v

def span():
    if not PLIST: return ""
    a, b = PLIST[0]["dt"], PLIST[-1]["dt"]
    if a == b: return f"{a.day} {a:%b %Y}"
    return f"{a.day}-{b.day} {b:%b %Y}" if (a.month, a.year) == (b.month, b.year) else f"{a.day} {a:%b %Y} to {b.day} {b:%b %Y}"

def indexable_topic(t): return ALLN[t] >= 3 and not t.startswith("misc-")
def topics_of(sid): return sorted([t for t in TOPICS if t["subject"] == sid and indexable_topic(t["id"])], key=lambda t: -PN[t["id"]])
def fit(*opts, n=62):
    return next((o for o in opts if len(o) <= n), opts[-1][:n].rsplit(" ", 1)[0])
def trunc(s, n=155): return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"
def base_url(): return os.environ.get("SITE_URL", request.url_root).rstrip("/")

def seo(title, desc, path, crumbs=None, og="website", ld=None, noindex=False):
    root = base_url(); items = []
    if crumbs and len(crumbs) > 1:
        items.append({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": n, "item": root + p} for i, (n, p) in enumerate(crumbs)]})
    for x in (ld or []):
        x = dict(x); x.setdefault("@context", "https://schema.org"); items.append(x)
    return dict(title=title, desc=trunc(desc), url=root + path, noindex=noindex, og=og, crumbs=crumbs, ld=items,
                image=root + url_for("static", filename="img/og-default.png"), site=SITE)

def webpage(path, name, desc, typ="WebPage"):
    return {"@type": typ, "name": name, "url": base_url() + path, "description": trunc(desc), "inLanguage": "en",
            "isPartOf": {"@type": "WebSite", "name": SITE, "url": base_url() + "/"}, "dateModified": LASTMOD}

def src(q):
    return f"{q['paper_date']} · {q['shift']} · Q.{q['qno']}" if q.get("paper") else "Practice question"

def paper_qs(pid=None):
    return [q for q in ALLQ if q.get("paper") and (pid is None or q["paper"] == pid)]

@app.context_processor
def inject():
    return {"subjects": SUBJECTS, "src": src, "T": TOPIC, "PN": PN, "plist": PLIST, "site": SITE, "npap": len(PLIST), "span": span(),
            "isize": lambda q: [q.get("img_w"), q.get("img_h")], "csrf_token": csrf_token}

def csrf_token():
    if "csrf" not in session: session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def send_reset_email(to_email, reset_url):
    username = os.environ.get("MAIL_USERNAME", "").strip()
    password = os.environ.get("MAIL_APP_PASSWORD", "").replace(" ", "")

    if not username or not password:
        raise RuntimeError("Gmail SMTP settings are missing.")

    msg = EmailMessage()
    msg["Subject"] = "Reset your RRB ALP password"
    msg["From"] = username
    msg["To"] = to_email
    msg.set_content(
        "We received a password reset request for your RRB ALP account.\n\n"
        f"Open this link within 15 minutes:\n{reset_url}\n\n"
        "If you did not request this, ignore this email."
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
        smtp.login(username, password)
        smtp.send_message(msg)


@app.before_request
def clean_urls():
    p = request.path

    if p.startswith("/static"):
        return

    refresh()

    public_paths = {
        "/login",
        "/register",
        "/robots.txt",
        "/sitemap.xml",
        "/googlee36b107012479386.html",
        "/forgot-password",
        "/reset-password",
    }

    # Admin pages use their own admin authentication.
    if p.startswith("/admin"):
        pass
    elif p not in public_paths and not session.get("student_id"):
        if p.startswith("/api/"):
            return jsonify({"error": "Login required"}), 401
        return redirect(url_for("student_login"))

    if request.method == "GET" and len(p) > 1 and p.endswith("/"):
        qs = request.query_string.decode()
        return redirect(
            p.rstrip("/") + ("?" + qs if qs else ""), 301
        )

    if request.method == "GET" and request.args and p != "/reset-password"and not p.startswith(NOINDEX_PATHS):
        return redirect(p, 301)

    if request.method == "POST" and (
        p.startswith("/admin") or p in ("/register", "/login","/forgot-password",
        "/reset-password", "/logout")
    ):
        tok = request.form.get("csrf", "")
        if not tok or not hmac.compare_digest(
            tok, session.get("csrf", "")
        ):
            abort(400)


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"

    if request.path.startswith(XROBOTS):
        r.headers["X-Robots-Tag"] = "noindex, follow"

    if request.path.startswith("/admin"):
        r.headers["Cache-Control"] = "no-store"
        r.headers["X-Frame-Options"] = "DENY"
        r.headers["Referrer-Policy"] = "same-origin"

    return r


init_db()
refresh(force=True)

HOME = [("Home", "/")]


@app.route("/googlee36b107012479386.html")
def google_site_verification():
    return Response(
        "google-site-verification: googlee36b107012479386.html",
        mimetype="text/html"
    )


@app.route("/register", methods=["GET", "POST"])
def student_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        if not all([name, email, phone, password]):
            flash("Please fill in all fields.", "error")
            return redirect(url_for("student_register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("student_register"))

        if len(name) > 100 or len(email) > 254 or len(phone) > 20:
            flash("Please check the length of your details.", "error")
            return redirect(url_for("student_register"))

        try:
            with tx() as c:
                c.execute(
                    """
                    INSERT INTO students
                    (name, email, phone, pw_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        email,
                        phone,
                        generate_password_hash(password),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("student_login"))

        except sqlite3.IntegrityError:
            flash("This email is already registered.", "error")
            return redirect(url_for("student_register"))

    desc = "Create an account to use RRB ALP CBT-1 practice questions."
    return render_template(
        "register.html",
        seo=seo("Student Registration", desc, "/register", noindex=True)
    )


@app.route("/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with db() as c:
            student = c.execute(
                """
                SELECT id, name, pw_hash
                FROM students
                WHERE email = ? COLLATE NOCASE
                """,
                (email,),
            ).fetchone()

        if student and check_password_hash(student["pw_hash"], password):
            session.clear()
            session["student_id"] = student["id"]
            session["student_name"] = student["name"]
            session["csrf"] = secrets.token_urlsafe(24)
            session.permanent = True

            with tx() as c:
                c.execute(
                    "UPDATE students SET last_login = ? WHERE id = ?",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        student["id"],
                    ),
                )

            flash("Login successful.", "success")
            return redirect(url_for("index"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("student_login"))

    desc = "Log in to your RRB ALP CBT-1 practice account."
    return render_template(
        "login.html",
        seo=seo("Student Login", desc, "/login", noindex=True)
    )


@app.route("/logout", methods=["POST"])
def student_logout():
    if not session.get("student_id"):
        return redirect(url_for("student_login"))

    tok = request.form.get("csrf", "")
    if not tok or not hmac.compare_digest(
        tok, session.get("csrf", "")
    ):
        abort(400)

    session.clear()
    return redirect(url_for("student_login"))



def send_reset_email(to_email, reset_url):
    import urllib.request
    import urllib.error

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is missing in environment.")

    payload = {
        "from": os.environ.get(
            "RESEND_FROM_EMAIL",
            "RRB ALP <onboarding@resend.dev>"
        ),
        "to": [to_email],
        "subject": "Reset your RRB ALP password",
        "text": (
            "We received a password reset request.\n\n"
            f"Reset your password using this link:\n{reset_url}\n\n"
            "This link expires in 15 minutes. "
            "If you did not request this, ignore this email."
        ),
    }
    
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "RRB-ALP-App/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
            if not result.get("id"):
                raise RuntimeError("Resend did not return an email ID.")
    except urllib.error.HTTPError as exc:
        error = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend API error {exc.code}: {error}") from exc



@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if email and len(email) <= 254:
            with db() as c:
                student = c.execute(
                    """
                    SELECT id, email
                    FROM students
                    WHERE email = ? COLLATE NOCASE
                    """,
                    (email,),
                ).fetchone()

            # Only registered email addresses receive reset emails.
            if student:
                raw_token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(
                    raw_token.encode()
                ).hexdigest()

                now = datetime.now(timezone.utc)
                expires = (
                    now + timedelta(minutes=15)
                ).isoformat()

                with tx() as c:
                    # Invalidate previous unused reset links.
                    c.execute(
                        """
                        UPDATE password_resets
                        SET used_at = ?
                        WHERE student_id = ?
                          AND used_at IS NULL
                        """,
                        (now.isoformat(), student["id"]),
                    )

                    c.execute(
                        """
                        INSERT INTO password_resets
                        (student_id, token_hash, expires_at,
                         used_at, created_at)
                        VALUES (?, ?, ?, NULL, ?)
                        """,
                        (
                            student["id"],
                            token_hash,
                            expires,
                            now.isoformat(),
                        ),
                    )

                reset_url = url_for(
                    "reset_password",
                    token=raw_token,
                    _external=True,
                )

                try:
                    send_reset_email(
                        student["email"],
                        reset_url,
                    )
                    flash(
                        "Password reset link sent to your email.",
                        "success",
                    )
                except Exception:
                    app.logger.exception(
                        "Password reset email delivery failed"
                    )
                    flash(
                        "Email could not be sent. Please try again later.",
                        "error",
                    )

        return redirect(url_for("student_login"))

    return render_template(
        "forgot_password.html",
        seo=seo(
            "Forgot Password",
            "Reset your RRB ALP account password.",
            "/forgot-password",
            noindex=True,
        ),
    )




@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token", "")
    if not token or len(token) > 200:
        flash("This reset link is invalid or expired.", "error")
        return redirect(url_for("student_login"))

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now_utc = datetime.now(timezone.utc)

    with db() as c:
        reset = c.execute(
            """SELECT id, student_id, expires_at, used_at
               FROM password_resets WHERE token_hash = ?""",
            (token_hash,),
        ).fetchone()

    valid = False
    if reset and not reset["used_at"]:
        try:
            expiry = datetime.fromisoformat(reset["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            valid = now_utc < expiry
        except (ValueError, TypeError):
            valid = False

    if not valid:
        flash("This reset link is invalid or expired.", "error")
        return redirect(url_for("student_login"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("reset_password", token=token))

        if password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("reset_password", token=token))

        # Re-check and consume the token atomically to prevent reuse.
        with tx() as c:
            current = c.execute(
                """SELECT student_id FROM password_resets
                   WHERE id = ? AND token_hash = ?
                   AND used_at IS NULL AND expires_at > ?""",
                (reset["id"], token_hash, now_utc.isoformat()),
            ).fetchone()

            if not current:
                flash("This reset link is invalid or expired.", "error")
                return redirect(url_for("student_login"))

            c.execute(
                "UPDATE students SET pw_hash = ? WHERE id = ?",
                (generate_password_hash(password), current["student_id"]),
            )
            c.execute(
                "UPDATE password_resets SET used_at = ? WHERE student_id = ? "
                "AND used_at IS NULL",
                (now_utc.isoformat(), current["student_id"]),
            )

        flash("Password reset successful. Please log in.", "success")
        return redirect(url_for("student_login"))

    return render_template(
        "reset_password.html",
        token=token,
        seo=seo("Set New Password", "Choose a new account password.",
                "/reset-password", noindex=True),
    )

@app.route("/")
def index():
    with tx() as c:
        att = c.execute("SELECT qid, correct FROM attempts").fetchall()
        wrong = c.execute("SELECT COUNT(*) FROM wrong").fetchone()[0]
        recent = c.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 5").fetchall()
    per = defaultdict(lambda: [0, 0])
    for a in att:
        if a["qid"] in QS: s = QS[a["qid"]]["subject"]; per[s][0] += a["correct"]; per[s][1] += 1
    total = len(att); right = sum(a["correct"] for a in att)
    stats = {"total": total, "acc": round(100 * right / total) if total else 0, "wrong": wrong}
    top = [(TOPIC[t], n) for t, n in PN.most_common() if indexable_topic(t)][:6]
    desc = (f"Practise {len(PQ)} questions from {len(PLIST)} RRB ALP CBT-1 shifts ({span()}), "
            "with answers, topic-wise MCQs and repeated-question patterns.")
    s = seo("RRB ALP CBT-1 Previous Papers & Practice Questions", desc, "/", HOME,
            ld=[{"@type": "WebSite", "name": SITE, "url": base_url() + "/", "inLanguage": "en", "description": trunc(desc)}])
    return render_template("index.html", stats=stats, per=per, recent=recent, seo=s, top=top, npq=len(PQ), nsub=SUBJ_PN)

@app.route("/subject/<sid>")
def subject(sid):
    if sid not in SUBJ: abort(404)
    sb = SUBJ[sid]; ts = topics_of(sid); n = SUBJ_PN[sid]
    path = f"/subject/{sid}"; nm = sb["name"]
    desc = f"{nm} in the RRB ALP CBT-1 papers ({span()}): {n} of {len(PQ)} questions across {len(ts)} topics, with answers and practice quizzes."
    sn = nm.split(" /")[0]
    s = seo(fit(f"{sn} for RRB ALP CBT-1 – Topics & Questions", f"{sn} – RRB ALP CBT-1 Topics"), desc, path, HOME + [(nm, path)], ld=[webpage(path, f"{nm} – RRB ALP CBT-1", desc, "CollectionPage")])
    return render_template("subject.html", s=sb, topics=ts, n=n, seo=s, npq=len(PQ))

@app.route("/topic/<tid>")
def topic(tid):
    if tid not in TOPIC: abort(404)
    t = TOPIC[tid]; sb = SUBJ[t["subject"]]; pq = [q for q in PQ if q["topic"] == tid]
    where = Counter(q["paper"] for q in pq)
    related = [x for x in topics_of(t["subject"]) if x["id"] != tid][:8]
    path = f"/topic/{tid}"
    desc = (f"{t['name']} for RRB ALP CBT-1: key rules plus {len(pq)} real questions from {len(where)} papers with answers, and a practice quiz."
            if pq else f"{t['name']} for RRB ALP CBT-1: key rules and practice questions.")
    ttl = f"{t['name']} – RRB ALP CBT-1 Questions & Practice"
    s = seo(fit(ttl, f"{t['name']} – RRB ALP CBT-1 Questions", f"{t['name']} – RRB ALP CBT-1"), desc, path, HOME + [(sb["name"], f"/subject/{sb['id']}"), (t["name"], path)], og="article",
            ld=[dict(webpage(path, t["name"], desc, "Article"), headline=t["name"], author={"@type": "Organization", "name": SITE}, about=sb["name"])],
            noindex=not indexable_topic(tid))
    return render_template("topic.html", t=t, s=sb, pq=pq[:8], npq=len(pq), where=[(PAPERS[p], n) for p, n in sorted(where.items())], related=related, seo=s)

@app.route("/papers")
def papers():
    desc = f"All {len(PLIST)} RRB ALP CBT-1 question papers ({span()}): open any shift to see every question with its answer."
    s = seo(f"RRB ALP CBT-1 Question Papers – {span()} Shifts", desc, "/papers", HOME + [("Papers", "/papers")],
            ld=[webpage("/papers", "RRB ALP CBT-1 question papers", desc, "CollectionPage")])
    return render_template("papers.html", papers=PLIST, seo=s)

@app.route("/papers/<slug>")
def paper(slug):
    p = PSLUG.get(slug)
    if not p: abort(404)
    qs = sorted(paper_qs(p["id"]), key=lambda q: q["qno"])
    tc = Counter(q["topic"] for q in qs); sc = Counter(q["subject"] for q in qs)
    i = PLIST.index(p); path = f"/papers/{slug}"; label = f"{p['short']} · Shift {p['n']}"
    desc = f"RRB ALP CBT-1 paper of {p['long']}, Shift {p['n']}{' (' + p['time'] + ')' if p['time'] else ''}: all {len(qs)} questions with answers, grouped by topic."
    quiz = {"@type": "Quiz", "name": f"RRB ALP CBT-1 {p['long']} Shift {p['n']}", "url": base_url() + path, **({"inLanguage": "hi"} if p["verified"] else {}),
            "about": {"@type": "Thing", "name": "RRB ALP CBT-1"},
            "hasPart": [{"@type": "Question", "name": q["q"], "eduQuestionType": "Multiple choice",
                         "acceptedAnswer": {"@type": "Answer", "text": q["o"][q["a"]]},
                         "suggestedAnswer": [{"@type": "Answer", "text": o} for j, o in enumerate(q["o"]) if j != q["a"]]}
                        for q in qs if not q.get("img") and all(q["o"])]}
    s = seo(f"RRB ALP CBT-1 Paper {p['short']} Shift {p['n']} – {len(qs)} Questions", desc, path,
            HOME + [("Papers", "/papers"), (label, path)], ld=[webpage(path, f"RRB ALP CBT-1 {p['long']} Shift {p['n']}", desc), quiz])
    return render_template("paper.html", p=p, qs=qs, tc=tc.most_common(), sc=sc, prev=PLIST[i - 1] if i else None,
                           nxt=PLIST[i + 1] if i + 1 < len(PLIST) else None, seo=s)

@app.route("/practice")
def practice():
    desc = "Topic-wise RRB ALP CBT-1 practice quizzes built from real previous-paper questions: reasoning, mathematics, science and more."
    s = seo("RRB ALP CBT-1 Practice MCQs – Topic-wise Quizzes", desc, "/practice", HOME + [("Practice", "/practice")],
            ld=[webpage("/practice", "RRB ALP CBT-1 practice quizzes", desc, "CollectionPage")])
    groups = [(sb, topics_of(sb["id"])) for sb in SUBJECTS]
    return render_template("practice.html", topics=TOPICS, groups=groups, seo=s)

@app.route("/practice/<tid>")
def practice_topic(tid):
    if tid not in TOPIC: abort(404)
    t = TOPIC[tid]; sb = SUBJ[t["subject"]]; path = f"/practice/{tid}"
    desc = f"Practice {t['name']} for RRB ALP CBT-1: 10 or 20 question quizzes drawn from {ALLN[tid]} questions, with instant score and answers."
    ttl = f"{t['name']} Practice Quiz – RRB ALP CBT-1 MCQs"
    s = seo(fit(ttl, f"{t['name']} Quiz – RRB ALP CBT-1 MCQs", f"{t['name']} Quiz – RRB ALP CBT-1"), desc, path,
            HOME + [("Practice", "/practice"), (t["name"], path)], ld=[webpage(path, f"{t['name']} practice quiz", desc)], noindex=not indexable_topic(tid))
    return render_template("practice_topic.html", t=t, s=sb, n=ALLN[tid], npq=PN[tid], seo=s)

@app.route("/quiz")
def quiz():
    title = "Practice"; a = request.args
    if a.get("mode") == "wrong": title = "Retry wrong questions"
    elif a.get("paper") in PAPERS:
        p = PAPERS[a["paper"]]; title = f"{p['date']} · {p['shift']}"
    elif a.get("pattern"): title = "Repeated pattern " + a["pattern"]
    elif a.get("tag"): title = a["tag"].replace("_", " ").title() + " questions"
    elif a.get("topic") in TOPIC: title = TOPIC[a["topic"]]["name"]
    elif a.get("subject") in SUBJ: title = SUBJ[a["subject"]]["name"]
    return render_template("quiz.html", title=title, qs=request.query_string.decode(),
                           seo=seo(title + " – Quiz", "Practice quiz", "/quiz", noindex=True))

@app.route("/important")
def important():
    tc = Counter(q["topic"] for q in PQ)
    ranked = [(TOPIC[t], n, len({q["paper"] for q in PQ if q["topic"] == t})) for t, n in tc.most_common()]
    pats = defaultdict(list)
    for q in PQ: pats[q.get("pattern") or q["id"]].append(q)
    def rows(tag):
        out = [dict(sample=v[0], size=len(v), papers=v[0]["pattern_papers"], topic=TOPIC[v[0]["topic"]]["name"],
                    where=sorted({f"{PAPERS[q['paper']]['date'][:5]} S{PAPERS[q['paper']]['shift'][-1]}" for q in v}))
               for v in pats.values() if v[0].get("tag") == tag and v[0].get("pattern")]
        return sorted(out, key=lambda r: (-r["papers"], -r["size"]))
    tags = Counter(q.get("tag", "normal") for q in PQ)
    desc = (f"Which RRB ALP CBT-1 topics and question patterns repeated across {len(PLIST)} shifts ({span()}): "
            f"{tags['high_frequency']} high-frequency and {tags['repeated']} repeated questions.")
    s = seo("Important & Repeated RRB ALP CBT-1 Topics", desc, "/important", HOME + [("Important topics", "/important")],
            ld=[webpage("/important", "Important and repeated RRB ALP CBT-1 topics", desc)])
    return render_template("important.html", ranked=ranked, hf=rows("high_frequency"), rep=rows("repeated"), tags=tags, npq=len(PQ), npap=len(PLIST), imp=[q for q in ALLQ if q["qtype"] == "important"][:30], seo=s)

@app.route("/wrong")
def wrong():
    with tx() as c:
        ids = [r[0] for r in c.execute("SELECT qid FROM wrong")]
    return render_template("wrong.html", qs=[QS[i] for i in ids if i in QS], topics=TOPIC,
                           seo=seo("Wrong questions – Retry", "Retry questions you got wrong", "/wrong", noindex=True))

@app.route("/robots.txt")
def robots():
    return Response(f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /quiz\nDisallow: /wrong\nDisallow: /admin\n\nSitemap: {base_url()}/sitemap.xml\n", mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap():
    paths = ["/", "/papers", "/important", "/practice"] + [f"/subject/{s['id']}" for s in SUBJECTS] + \
            [f"/papers/{p['slug']}" for p in PLIST] + \
            [f"/{k}/{t['id']}" for t in TOPICS if indexable_topic(t["id"]) for k in ("topic", "practice")]
    root = base_url()
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
          "".join(f"<url><loc>{root}{p}</loc><lastmod>{LASTMOD}</lastmod></url>\n" for p in paths) + "</urlset>\n"
    return Response(xml, mimetype="application/xml")


# ====================== ADMIN QUESTION MANAGER ======================
FAILS = defaultdict(list)

def admin_user():
    u = session.get("admin")
    if not u: return None
    with tx() as c: return u if c.execute("SELECT 1 FROM admins WHERE username=?", (u,)).fetchone() else None

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not admin_user(): return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))
        return f(*a, **k)
    return w

def aseo(title): return seo(title + " – Admin", "Admin", request.path, noindex=True)

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if admin_user(): return redirect(url_for("admin_home"))
    err = ""; ip = request.remote_addr or "?"
    if request.method == "POST":
        FAILS[ip] = [t for t in FAILS[ip] if time.time() - t < 900]
        if len(FAILS[ip]) >= 5: return render_template("admin/login.html", err="Too many attempts. Try again in 15 minutes.", seo=aseo("Login")), 429
        u, p = request.form.get("username", "").strip(), request.form.get("password", "")
        with tx() as c: row = c.execute("SELECT * FROM admins WHERE username=?", (u,)).fetchone()
        okpw = check_password_hash(row["pw_hash"] if row else generate_password_hash("x" * 12), p) and bool(row)
        if okpw:
            session.clear(); session["admin"] = u; session["csrf"] = secrets.token_urlsafe(24); session.permanent = True; FAILS.pop(ip, None)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/admin") and not nxt.startswith("//") else url_for("admin_home"))
        FAILS[ip].append(time.time()); err = "Invalid username or password."
    return render_template("admin/login.html", err=err, seo=aseo("Login")), (401 if err else 200)

@app.post("/admin/logout")
def admin_logout():
    session.clear(); return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_home():
    with tx() as c:
        total = c.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        types = {r[0]: r[1] for r in c.execute("SELECT qtype,COUNT(*) FROM questions GROUP BY qtype")}
        subj = {r[0]: r[1] for r in c.execute("SELECT subject,COUNT(*) FROM questions GROUP BY subject")}
        top = {r[0]: r[1] for r in c.execute("SELECT topic,COUNT(*) FROM questions GROUP BY topic")}
        recent = [row_to_q(r) for r in c.execute("SELECT * FROM questions ORDER BY created_at DESC, sort_key DESC LIMIT 10")]
    return render_template("admin/dashboard.html", total=total, types=types, subj=subj, top=top, recent=recent, seo=aseo("Dashboard"))

def like(x): return "%" + x.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

@app.route("/admin/questions")
@admin_required
def admin_questions():
    a = request.args; where = []; par = []
    if a.get("q", "").strip():
        l = like(a["q"].strip()); where.append("(" + " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in ("id", "question", "opt_a", "opt_b", "opt_c", "opt_d", "explanation", "subtopic")) + ")"); par += [l] * 8
    for k, col in (("subject", "subject"), ("topic", "topic"), ("qtype", "qtype"), ("difficulty", "difficulty")):
        if a.get(k): where.append(f"{col}=?"); par.append(a[k])
    w = (" WHERE " + " AND ".join(where)) if where else ""
    page = max(a.get("page", 1, type=int), 1); per = 25
    with tx() as c:
        total = c.execute("SELECT COUNT(*) FROM questions" + w, par).fetchone()[0]
        rows = [row_to_q(r) for r in c.execute("SELECT * FROM questions" + w + " ORDER BY created_at DESC, sort_key DESC LIMIT ? OFFSET ?", par + [per, (page - 1) * per])]
    from urllib.parse import urlencode
    qs = urlencode({k: v for k, v in a.items() if k != "page" and v})
    return render_template("admin/questions.html", rows=rows, total=total, page=page, pages=max(-(-total // per), 1), qs=qs, f=a, tmap=topic_map(), seo=aseo("Questions"))

def topic_map():
    m = defaultdict(list)
    for t in TOPICS: m[t["subject"]].append([t["id"], t["name"]])
    return m

def sniff(b):
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "png"
    if b[:3] == b"\xff\xd8\xff": return "jpg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "webp"

def dims(path):
    try:
        from PIL import Image
        with Image.open(path) as im: return list(im.size)
    except Exception: return [None, None]

FIELDS = ["subject", "topic", "subtopic", "question", "opt_a", "opt_b", "opt_c", "opt_d", "answer", "explanation", "difficulty", "qtype", "paper_date", "shift", "qno", "source"]

def parse_question(form, upload, ex=None):
    f = {k: (form.get(k) or "").strip() for k in FIELDS}; f["remove_img"] = bool(form.get("remove_img")); e = []; blob = ext = None
    if f["subject"] not in SUBJ: e.append("Choose a valid subject.")
    if f["topic"] not in TOPIC or TOPIC[f["topic"]]["subject"] != f["subject"]: e.append("Choose a topic that belongs to the selected subject.")
    if len(f["subtopic"]) > 120: e.append("Subtopic is too long (max 120).")
    if f["difficulty"] not in DIFFS: e.append("Choose a difficulty.")
    if f["qtype"] not in QTYPES: e.append("Choose a question type.")
    if f["answer"] not in list("ABCD"): e.append("Choose the correct answer (A-D).")
    if upload and upload.filename:
        blob = upload.read(1024 * 1024 + 1); ext = sniff(blob)
        if len(blob) > 1024 * 1024: e.append("Image must be 1 MB or smaller.")
        elif not ext: e.append("Image must be a PNG, JPEG or WebP file."); blob = None
    has_img = bool(blob) or bool(ex and ex.get("img") and not f["remove_img"])
    opts = [f[k] for k in ("opt_a", "opt_b", "opt_c", "opt_d")]
    if len(f["question"]) > 2000: e.append("Question is too long (max 2000 characters).")
    if not f["question"] and not has_img: e.append("Enter the question text (or attach an image).")
    elif f["question"] and len(f["question"]) < 5: e.append("Question text is too short.")
    if any(len(o) > 500 for o in opts): e.append("An option is too long (max 500 characters).")
    filled = [o for o in opts if o]
    if not has_img and len(filled) < 4: e.append("Fill in all four options A, B, C and D.")
    elif has_img and 0 < len(filled) < 4: e.append("With an image, fill in all four options or leave all four empty.")
    if len({o.lower() for o in filled}) < len(filled): e.append("Options must all be different.")
    if len(f["explanation"]) > 2000: e.append("Explanation is too long (max 2000 characters).")
    pid = None
    if f["qtype"] == "previous_paper":
        d = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try: d = datetime.strptime(f["paper_date"], fmt); break
            except ValueError: pass
        if not d: e.append("Previous-paper questions need a valid paper date.")
        if not re.fullmatch(r"[1-9]", re.sub(r"(?i)shift\s*", "", f["shift"])): e.append("Shift must be a number from 1 to 9.")
        if not (f["qno"].isdigit() and 1 <= int(f["qno"]) <= 500): e.append("Question number must be between 1 and 500.")
        if len(f["source"]) > 200: e.append("Source is too long (max 200).")
        if d and not e:
            sh = re.sub(r"(?i)shift\s*", "", f["shift"]); f["paper_date"] = f"{d:%d/%m/%Y}"; f["shift"] = f"Shift {sh}"; pid = f"{d:%Y%m%d}s{sh}"
            with tx() as c:
                dup = c.execute("SELECT id FROM questions WHERE paper_id=? AND qno=?", (pid, int(f["qno"]))).fetchone()
            if dup and (not ex or dup[0] != ex["id"]): e.append(f"Question {f['qno']} of that paper already exists ({dup[0]}).")
    else: f["paper_date"] = f["shift"] = f["qno"] = ""
    if f["question"] and filled and not e:
        with tx() as c:
            for r in c.execute("SELECT id,opt_a,opt_b,opt_c,opt_d FROM questions WHERE subject=? AND lower(question)=lower(?)", (f["subject"], f["question"])):
                if (not ex or r[0] != ex["id"]) and [x.lower() for x in r[1:]] == [o.lower() for o in opts]: e.append(f"This question already exists ({r[0]}).")
    return f, e, blob, ext, pid

def save_question(f, blob, ext, pid, ex=None):
    img, iw, ih = (ex.get("img"), ex.get("img_w"), ex.get("img_h")) if ex else (None, None, None); old = img
    if blob:
        name = f"uploads/{secrets.token_hex(8)}.{ext}"; (BASE / "static" / name).write_bytes(blob); img = name; iw, ih = dims(BASE / "static" / name)
    elif f["remove_img"]: img = iw = ih = None
    o = [f["opt_a"], f["opt_b"], f["opt_c"], f["opt_d"]]; a = "ABCD".index(f["answer"]); paper = f["qtype"] == "previous_paper"
    row = dict(subject=f["subject"], topic=f["topic"], subtopic=f["subtopic"], question=f["question"], opt_a=o[0], opt_b=o[1], opt_c=o[2], opt_d=o[3], answer=a,
               explanation=f["explanation"], difficulty=f["difficulty"], qtype=f["qtype"], paper_id=pid if paper else None, paper_date=f["paper_date"] if paper else None,
               shift=f["shift"] if paper else None, qno=int(f["qno"]) if paper else None, source=f["source"] or ("Admin entry" if paper else None),
               img=img, img_w=iw, img_h=ih, correct_answer=ca_text(a, o), updated_at=now())
    if ex and ex["qtype"] == "previous_paper" and not paper: row.update(tag=None, pattern=None, pattern_papers=None, pattern_size=None)
    with tx() as c:
        if paper and not c.execute("SELECT 1 FROM papers WHERE id=?", (pid,)).fetchone():
            c.execute("INSERT INTO papers(id,date,shift,time,source) VALUES(?,?,?,?,?)", (pid, f["paper_date"], f["shift"], "", ""))
        if ex:
            c.execute("UPDATE questions SET " + ",".join(f"{k}=?" for k in row) + " WHERE id=?", list(row.values()) + [ex["id"]]); qid = ex["id"]
        else:
            qid = f"p{pid}q{row['qno']:02d}" if paper else "a" + secrets.token_hex(5)
            row.update(id=qid, created_at=now(), sort_key=c.execute("SELECT COALESCE(MAX(sort_key),0)+1 FROM questions").fetchone()[0], tag="normal" if paper else None)
            c.execute("INSERT INTO questions(" + ",".join(row) + ") VALUES(" + ",".join("?" * len(row)) + ")", list(row.values()))
        bump(c)
    if old and old != img and old.startswith("uploads/"): remove_upload(old)
    refresh(force=True); return qid

def remove_upload(name):
    with tx() as c: used = c.execute("SELECT 1 FROM questions WHERE img=?", (name,)).fetchone()
    p = BASE / "static" / name
    if not used and name.startswith("uploads/") and p.exists(): p.unlink()

def form_ctx(f, errs, ex=None, title="Add question"):
    return dict(f=f, errs=errs, ex=ex, tmap=topic_map(), diffs=DIFFS, qtypes=QTYPES, title=title, seo=aseo(title))

@app.route("/admin/questions/new", methods=["GET", "POST"])
@admin_required
def admin_new():
    f = {"difficulty": "medium", "qtype": "practice", "answer": ""}
    if request.method == "POST":
        f, errs, blob, ext, pid = parse_question(request.form, request.files.get("image"))
        if not errs:
            qid = save_question(f, blob, ext, pid); flash(f"Question {qid} saved.", "ok"); return redirect(url_for("admin_preview", qid=qid))
        return render_template("admin/form.html", **form_ctx(f, errs)), 422
    return render_template("admin/form.html", **form_ctx(f, []))

def q_to_form(q):
    d = dict(subject=q["subject"], topic=q["topic"], subtopic=q["subtopic"], question=q["q"], opt_a=q["o"][0], opt_b=q["o"][1], opt_c=q["o"][2], opt_d=q["o"][3],
             answer="ABCD"[q["a"]], explanation=q["e"], difficulty=q["difficulty"], qtype=q["qtype"], source=q["source"])
    if q.get("paper"):
        d.update(paper_date=datetime.strptime(q["paper_date"], "%d/%m/%Y").strftime("%Y-%m-%d"), shift=re.sub(r"\D", "", q["shift"]), qno=str(q["qno"]))
    return d

@app.route("/admin/questions/<qid>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit(qid):
    ex = QS.get(qid) or abort(404)
    if request.method == "POST":
        f, errs, blob, ext, pid = parse_question(request.form, request.files.get("image"), ex)
        if not errs:
            save_question(f, blob, ext, pid, ex); flash(f"Question {qid} updated.", "ok"); return redirect(url_for("admin_preview", qid=qid))
        return render_template("admin/form.html", **form_ctx(f, errs, ex, "Edit question")), 422
    return render_template("admin/form.html", **form_ctx(q_to_form(ex), [], ex, "Edit question"))

@app.route("/admin/questions/<qid>")
@admin_required
def admin_preview(qid):
    q = QS.get(qid) or abort(404)
    return render_template("admin/preview.html", q=q, seo=aseo("Preview " + qid))

@app.post("/admin/questions/<qid>/delete")
@admin_required
def admin_delete(qid):
    q = QS.get(qid) or abort(404)
    with tx() as c:
        c.execute("DELETE FROM questions WHERE id=?", (qid,)); c.execute("DELETE FROM wrong WHERE qid=?", (qid,)); bump(c)
    if q.get("img"): remove_upload(q["img"])
    refresh(force=True); flash(f"Question {qid} deleted.", "ok"); return redirect(url_for("admin_questions"))

@app.cli.command("create-admin")
@click.option("--username", default="admin", show_default=True)
@click.password_option()
def create_admin_cmd(username, password):
    """Create or reset an admin account (password is stored hashed)."""
    if len(password) < 10: raise click.ClickException("Password must be at least 10 characters.")
    with tx() as c: c.execute("INSERT OR REPLACE INTO admins VALUES(?,?,?)", (username, generate_password_hash(password), now()))
    click.echo(f"Admin '{username}' saved.")

@app.cli.command("import-json")
@click.argument("path", default=str(DATAF))
def import_json_cmd(path):
    """Import questions from a questions.json-style file (existing ids are skipped)."""
    with tx() as c: n = import_json(path, c)
    click.echo(f"Imported {n} new questions.")

@app.errorhandler(400)
def bad_request(e): return ("Bad request (missing or invalid form token). Reload the page and try again.", 400)

@app.errorhandler(413)
def too_big(e): return ("Upload too large (max 1 MB image).", 413)

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html", seo=seo("Page not found", "This page does not exist.", request.path, noindex=True)), 404

@app.get("/api/questions")
def api_questions():
    a = request.args; qs = ALLQ
    if a.get("mode") == "wrong":
        with tx() as c: ids = {r[0] for r in c.execute("SELECT qid FROM wrong")}
        qs = [q for q in qs if q["id"] in ids]
    elif a.get("paper"): qs = paper_qs(a["paper"])
    elif a.get("pattern"): qs = [q for q in paper_qs() if q.get("pattern") == a["pattern"]]
    elif a.get("tag"): qs = [q for q in paper_qs() if q.get("tag") == a["tag"]]
    elif a.get("topic"): qs = [q for q in qs if q["topic"] == a["topic"]]
    elif a.get("subject"): qs = [q for q in qs if q["subject"] == a["subject"]]
    qs = list(qs)
    if a.get("src") == "papers": qs = [q for q in qs if q.get("paper")]
    if not a.get("paper"): random.shuffle(qs)
    n = a.get("n", type=int)
    if n: qs = qs[:n]
    return jsonify([{"id": q["id"], "q": "" if q.get("img") else q["q"], "o": list("ABCD") if q.get("img") else q["o"],
                     "img": url_for("static", filename=q["img"]) if q.get("img") else None,
                     "src": src(q), "topic": TOPIC[q["topic"]]["name"]} for q in qs])

@app.post("/api/submit")
def submit():
    d = request.get_json(force=True); ans = d.get("answers", {}); res = {}; score = 0
    with tx() as c:
        for qid in d.get("ids", []):
            q = QS.get(qid)
            if not q: continue
            ok = ans.get(qid) == q["a"]; score += ok
            res[qid] = {"a": q["a"], "e": q.get("e", ""), "ok": ok}
            c.execute("INSERT INTO attempts(qid, correct) VALUES(?,?)", (qid, int(ok)))
            if ok: c.execute("DELETE FROM wrong WHERE qid=?", (qid,))
            else: c.execute("INSERT OR IGNORE INTO wrong(qid) VALUES(?)", (qid,))
        c.execute("INSERT INTO sessions(label, score, total) VALUES(?,?,?)", (d.get("label", "Practice"), score, len(res)))
    return jsonify({"score": score, "total": len(res), "results": res})

if __name__ == "__main__":
    app.run(debug=True)
