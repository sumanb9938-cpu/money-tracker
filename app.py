from flask import Flask, render_template, request, redirect, session, Response, g, url_for
from authlib.integrations.flask_client import OAuth
import mysql.connector
import os
import csv
import io
import secrets
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "secret123")

# Allow HTTP for local testing with Authlib
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# ---------------- GOOGLE OAUTH CONFIG ---------------- #
GOOGLE_CLIENT_ID = (os.getenv('GOOGLE_CLIENT_ID') or '').strip().strip('"').strip("'")
GOOGLE_CLIENT_SECRET = (os.getenv('GOOGLE_CLIENT_SECRET') or '').strip().strip('"').strip("'")

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

import datetime
import secrets

def generate_unique_public_id(cursor):
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    while True:
        code = ''.join(secrets.choice(alphabet) for _ in range(6))
        public_id = f"MT-{code}"
        cursor.execute("SELECT id FROM users WHERE public_user_id = %s", (public_id,))
        if not cursor.fetchone():
            return public_id


def migrate_existing_users_public_ids(db, cursor):
    try:
        cursor.execute("SELECT id FROM users WHERE public_user_id IS NULL OR public_user_id = ''")
        unassigned_users = cursor.fetchall()
        for (uid,) in unassigned_users:
            new_pid = generate_unique_public_id(cursor)
            cursor.execute("UPDATE users SET public_user_id = %s WHERE id = %s", (new_pid, uid))
        db.commit()
    except Exception as e:
        print(f"Warning: Public User ID migration note: {e}")


# ---------------- DATABASE CONFIG (POSTGRESQL / MYSQL DUAL) ---------------- #
def clean_db_url(url):
    if not url:
        return url
    url = url.strip().strip('"').strip("'")
    while url and url[-1] in ('"', "'"):
        url = url[:-1]
    url = url.replace('sslmode="require"', 'sslmode=require')
    url = url.replace("sslmode='require'", 'sslmode=require')
    url = url.replace('sslmode=require"', 'sslmode=require')
    url = url.replace("sslmode=require'", 'sslmode=require')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url

RAW_DB_URL = os.getenv('DATABASE_URL')
DATABASE_URL = clean_db_url(RAW_DB_URL)

def connect_postgres(url):
    import psycopg2
    try:
        return psycopg2.connect(url, connect_timeout=5)
    except Exception as e:
        if "pg-2880f184-sumanb9938-feb8.l.aivencloud.com" in url:
            ip_url = url.replace("pg-2880f184-sumanb9938-feb8.l.aivencloud.com", "168.144.148.237")
            try:
                return psycopg2.connect(ip_url, connect_timeout=5)
            except Exception:
                pass
        raise e

if DATABASE_URL:
    import psycopg2

    def get_db():
        if 'db' not in g:
            g.db = connect_postgres(DATABASE_URL)
        return g.db

    @app.teardown_appcontext
    def close_db(exception=None):
        db = g.pop('db', None)
        if db is not None:
            db.close()

    def init_db():
        try:
            conn = connect_postgres(DATABASE_URL)
            cursor = conn.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255),
                password VARCHAR(255),
                is_admin INT DEFAULT 0,
                email VARCHAR(255),
                full_name VARCHAR(255),
                phone VARCHAR(50),
                public_user_id VARCHAR(50),
                avatar_url TEXT,
                qr_code_data TEXT,
                created_at VARCHAR(50)
            );
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS money_records (
                id SERIAL PRIMARY KEY,
                serial_no INT,
                name VARCHAR(255),
                amount DOUBLE PRECISION,
                type VARCHAR(50),
                date_taken VARCHAR(50),
                reason TEXT,
                user_id INT,
                counterparty_user_id INT NULL,
                status VARCHAR(50) DEFAULT 'pending'
            );
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS connections (
                id SERIAL PRIMARY KEY,
                requester_id INT NOT NULL,
                receiver_id INT NOT NULL,
                status VARCHAR(50) DEFAULT 'pending',
                created_at VARCHAR(50)
            );
            ''')
            try:
                cursor.execute("ALTER TABLE money_records ADD COLUMN IF NOT EXISTS counterparty_user_id INT;")
            except Exception as e:
                print(f"Warning: PostgreSQL migration error: {e}")

            user_cols = [
                ("full_name", "VARCHAR(255)"),
                ("phone", "VARCHAR(50)"),
                ("public_user_id", "VARCHAR(50)"),
                ("avatar_url", "TEXT"),
                ("qr_code_data", "TEXT"),
                ("created_at", "VARCHAR(50)")
            ]
            for col_name, col_type in user_cols:
                try:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                except Exception as e:
                    print(f"Warning: PostgreSQL user column {col_name} migration error: {e}")

            try:
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_user_id ON users(public_user_id);")
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_connections_req_rec ON connections(requester_id, receiver_id);")
            except Exception as e:
                pass

            migrate_existing_users_public_ids(conn, cursor)

            conn.commit()
            cursor.close()
            conn.close()
            print("PostgreSQL Database initialized successfully.")
        except Exception as e:
            print(f"Warning: Could not initialize PostgreSQL database: {e}")


else:
    import mysql.connector
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '9938asdf9938')
    MYSQL_DB = os.getenv('MYSQL_DB', 'money_tracker')
    MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))

    def get_db():
        if 'db' not in g:
            g.db = mysql.connector.connect(
                host=MYSQL_HOST,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                database=MYSQL_DB,
                port=MYSQL_PORT
            )
        return g.db

    @app.teardown_appcontext
    def close_db(exception=None):
        db = g.pop('db', None)
        if db is not None and db.is_connected():
            db.close()

    def init_db():
        try:
            conn = mysql.connector.connect(
                host=MYSQL_HOST,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                port=MYSQL_PORT
            )
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DB}")
            conn.commit()
            cursor.close()
            conn.close()

            conn = mysql.connector.connect(
                host=MYSQL_HOST,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                database=MYSQL_DB,
                port=MYSQL_PORT
            )
            cursor = conn.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(255),
                password VARCHAR(255),
                is_admin INT DEFAULT 0,
                email VARCHAR(255),
                full_name VARCHAR(255),
                phone VARCHAR(50),
                public_user_id VARCHAR(50),
                avatar_url TEXT,
                qr_code_data TEXT,
                created_at VARCHAR(50)
            )
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS money_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                serial_no INT,
                name VARCHAR(255),
                amount DOUBLE,
                type VARCHAR(50),
                date_taken VARCHAR(50),
                reason TEXT,
                user_id INT,
                counterparty_user_id INT NULL,
                status VARCHAR(50) DEFAULT 'pending'
            )
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS connections (
                id INT AUTO_INCREMENT PRIMARY KEY,
                requester_id INT NOT NULL,
                receiver_id INT NOT NULL,
                status VARCHAR(50) DEFAULT 'pending',
                created_at VARCHAR(50)
            )
            ''')
            try:
                cursor.execute("""
                    SELECT COUNT(*) FROM information_schema.columns 
                    WHERE table_schema = DATABASE() AND table_name = 'money_records' AND column_name = 'counterparty_user_id'
                """)
                if cursor.fetchone()[0] == 0:
                    cursor.execute("ALTER TABLE money_records ADD COLUMN counterparty_user_id INT NULL")
            except Exception as e:
                print(f"Warning: MySQL migration error: {e}")

            user_cols = [
                ("full_name", "VARCHAR(255)"),
                ("phone", "VARCHAR(50)"),
                ("public_user_id", "VARCHAR(50)"),
                ("avatar_url", "TEXT"),
                ("qr_code_data", "TEXT"),
                ("created_at", "VARCHAR(50)")
            ]
            for col_name, col_type in user_cols:
                try:
                    cursor.execute(f"""
                        SELECT COUNT(*) FROM information_schema.columns 
                        WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = '{col_name}'
                    """)
                    if cursor.fetchone()[0] == 0:
                        cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type} NULL")
                except Exception as e:
                    print(f"Warning: MySQL user column {col_name} migration error: {e}")

            migrate_existing_users_public_ids(conn, cursor)

            conn.commit()
            cursor.close()
            conn.close()
            print("MySQL Database initialized successfully.")
        except Exception as e:
            print(f"Warning: Could not initialize MySQL database: {e}")

init_db()


# ---------------- HELPER FUNCTIONS ---------------- #
def get_connection_between(cursor, u1, u2):
    cursor.execute("""
    SELECT id, requester_id, receiver_id, status, created_at 
    FROM connections 
    WHERE (requester_id = %s AND receiver_id = %s) 
       OR (requester_id = %s AND receiver_id = %s)
    """, (u1, u2, u2, u1))
    return cursor.fetchone()


def get_pending_connection_requests(cursor, user_id):
    cursor.execute("""
    SELECT c.id, c.requester_id, c.created_at, u.username, u.full_name, u.public_user_id, u.avatar_url
    FROM connections c
    JOIN users u ON c.requester_id = u.id
    WHERE c.receiver_id = %s AND c.status = 'pending'
    ORDER BY c.id DESC
    """, (user_id,))
    rows = cursor.fetchall()
    return [{
        "id": r[0],
        "requester_id": r[1],
        "created_at": r[2],
        "username": r[3],
        "name": r[4] or r[3] or "User",
        "public_user_id": r[5],
        "avatar_url": r[6] or ""
    } for r in rows]


def get_accepted_connected_users(cursor, user_id):
    cursor.execute("""
    SELECT u.id, u.username, u.full_name, u.public_user_id, u.avatar_url
    FROM connections c
    JOIN users u ON (CASE WHEN c.requester_id = %s THEN c.receiver_id ELSE c.requester_id END) = u.id
    WHERE (c.requester_id = %s OR c.receiver_id = %s) AND c.status = 'accepted'
    ORDER BY u.full_name ASC
    """, (user_id, user_id, user_id))
    rows = cursor.fetchall()
    return [{
        "id": r[0],
        "username": r[1],
        "name": r[2] or r[1] or "User",
        "public_user_id": r[3],
        "avatar_url": r[4] or ""
    } for r in rows]


def get_user_profile(db, cursor, user_id):
    cursor.execute("""
    SELECT id, username, email, full_name, phone, public_user_id, avatar_url, qr_code_data, created_at
    FROM users WHERE id = %s
    """, (user_id,))
    u = cursor.fetchone()
    if not u:
        return None
    
    uid, username, email, full_name, phone, public_id, avatar_url, qr_data, created_at = u
    
    dirty = False
    if not public_id:
        public_id = generate_unique_public_id(cursor)
        cursor.execute("UPDATE users SET public_user_id = %s WHERE id = %s", (public_id, uid))
        dirty = True
        
    if not created_at:
        created_at = datetime.datetime.now().strftime("%Y-%m-%d")
        cursor.execute("UPDATE users SET created_at = %s WHERE id = %s", (created_at, uid))
        dirty = True
        
    if dirty:
        db.commit()

    return {
        "id": uid,
        "username": username or "",
        "email": email or "",
        "full_name": full_name or username or "User",
        "phone": phone or "",
        "public_user_id": public_id,
        "avatar_url": avatar_url or "",
        "qr_code_data": qr_data or "",
        "created_at": created_at or ""
    }


def find_counterparty_id(cursor, name, current_user_id):
    if not name:
        return None
    name_clean = name.strip()
    cursor.execute(
        """
        SELECT id FROM users 
        WHERE (LOWER(username) = LOWER(%s) 
           OR LOWER(email) = LOWER(%s) 
           OR LOWER(public_user_id) = LOWER(%s)) 
          AND id != %s 
        LIMIT 1
        """,
        (name_clean, name_clean, name_clean, current_user_id)
    )
    res = cursor.fetchone()
    return res[0] if res else None


def auto_link_records_for_user(db, cursor, user_id, username, email, public_user_id=None):
    if not user_id:
        return
    try:
        conditions = ["LOWER(name) = LOWER(%s)"]
        params = [username]
        if email:
            conditions.append("LOWER(name) = LOWER(%s)")
            params.append(email)
        if public_user_id:
            conditions.append("LOWER(name) = LOWER(%s)")
            params.append(public_user_id)

        where_clause = " OR ".join(conditions)
        cursor.execute(f"""
        UPDATE money_records 
        SET counterparty_user_id = %s 
        WHERE counterparty_user_id IS NULL AND user_id != %s 
        AND ({where_clause})
        """, (user_id, user_id) + tuple(params))
        db.commit()
    except Exception as e:
        print("Auto-link error:", e)



def get_user_perspective_records(cursor, current_user_id):
    cursor.execute("""
    SELECT r.id, r.serial_no, r.name, r.amount, r.type, r.date_taken, r.reason, r.user_id, r.status, r.counterparty_user_id,
           u_creator.username AS creator_username,
           u_cp.username AS counterparty_username
    FROM money_records r
    LEFT JOIN users u_creator ON r.user_id = u_creator.id
    LEFT JOIN users u_cp ON r.counterparty_user_id = u_cp.id
    WHERE r.user_id = %s OR r.counterparty_user_id = %s
    ORDER BY r.id DESC
    """, (current_user_id, current_user_id))
    
    rows = cursor.fetchall()
    formatted = []
    
    for row in rows:
        rec_id = row[0]
        serial_no = row[1]
        raw_name = row[2]
        amount = row[3]
        raw_type = row[4]
        date_taken = row[5]
        reason = row[6]
        creator_id = row[7]
        status = row[8]
        cp_id = row[9]
        creator_uname = row[10]
        cp_uname = row[11]

        is_creator = (current_user_id == creator_id)

        if is_creator:
            display_name = cp_uname if cp_uname else raw_name
            effective_type = raw_type
        else:
            display_name = creator_uname if creator_uname else "User"
            effective_type = 'received' if raw_type == 'given' else 'given'

        amt_str = f"{amount:g}" if isinstance(amount, float) and amount.is_integer() else f"{amount}"
        if effective_type == 'given':
            perspective_text = f"You will get ₹{amt_str} from {display_name}"
        else:
            perspective_text = f"You need to pay ₹{amt_str} to {display_name}"

        formatted.append((
            rec_id,           # 0
            serial_no,        # 1
            display_name,     # 2
            amount,           # 3
            effective_type,   # 4
            date_taken,       # 5
            reason,           # 6
            creator_id,       # 7
            status,           # 8
            perspective_text, # 9
            cp_id,            # 10
            is_creator,       # 11
            raw_type,         # 12
            raw_name          # 13
        ))
        
    return formatted





# ---------------- LOGIN ---------------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s",
                       (username, password))
        user = cursor.fetchone()

        if user:
            session['user_id'] = user[0]
            return redirect('/')
        else:
            return render_template('login.html', error="Invalid username or password")

    return render_template('login.html')


# ---------------- REGISTER ---------------- #
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        cursor = db.cursor()
        cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)",
                       (username, password))
        db.commit()

        cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
        new_u = cursor.fetchone()
        if new_u:
            auto_link_records_for_user(db, cursor, new_u[0], username, None)

        return redirect('/login')

    return render_template('register.html')


# ---------------- GOOGLE OAUTH ROUTES ---------------- #
@app.route('/login/google')
def login_google():
    redirect_uri = url_for('google_callback', _external=True)
    print("Initiating Google OAuth with redirect_uri:", redirect_uri)
    return google.authorize_redirect(redirect_uri)


@app.route('/login/google/callback')
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            user_info = google.get('https://www.googleapis.com/oauth2/v3/userinfo').json()

        email = user_info.get('email')
        if not email:
            return render_template('login.html', error="Failed to retrieve email from Google.")

        db = get_db()
        cursor = db.cursor()

        cursor.execute("SELECT id, username, email FROM users WHERE email=%s OR username=%s", (email, email))
        user = cursor.fetchone()

        if user:
            session['user_id'] = user[0]
            auto_link_records_for_user(db, cursor, user[0], user[1], user[2])
        else:
            random_password = secrets.token_hex(16)
            cursor.execute("INSERT INTO users (username, password, email) VALUES (%s, %s, %s)",
                           (email, random_password, email))
            db.commit()

            cursor.execute("SELECT id, username, email FROM users WHERE email=%s OR username=%s", (email, email))
            new_user = cursor.fetchone()
            if new_user:
                session['user_id'] = new_user[0]
                auto_link_records_for_user(db, cursor, new_user[0], new_user[1], new_user[2])

        return redirect('/')
    except Exception as e:
        print("Google Login Error:", e)
        return render_template('login.html', error="Google authentication failed. Please try again.")


# ---------------- HOME ---------------- #
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT username, email, public_user_id FROM users WHERE id=%s", (user_id,))
    curr_u = cursor.fetchone()
    if curr_u:
        auto_link_records_for_user(db, cursor, user_id, curr_u[0], curr_u[1], curr_u[2] if len(curr_u) > 2 else None)

    cursor.execute("SELECT username, email, public_user_id FROM users WHERE id != %s", (user_id,))
    registered_users_rows = cursor.fetchall()
    registered_users = list(set(
        [u[0] for u in registered_users_rows if u[0]] + 
        [u[1] for u in registered_users_rows if u[1]] +
        [u[2] for u in registered_users_rows if u[2]]
    ))

    pending_requests = get_pending_connection_requests(cursor, user_id)
    connected_friends = get_accepted_connected_users(cursor, user_id)

    records = get_user_perspective_records(cursor, user_id)

    to_claim = sum(r[3] for r in records if r[4] == 'given' and r[8] == 'pending')
    to_pay = sum(r[3] for r in records if r[4] == 'received' and r[8] == 'pending')

    people_dict = {}
    for r in records:
        if r[8] == 'pending':
            p_name = r[2]
            if p_name not in people_dict:
                people_dict[p_name] = {'given': 0.0, 'received': 0.0}
            if r[4] == 'given':
                people_dict[p_name]['given'] += r[3]
            elif r[4] == 'received':
                people_dict[p_name]['received'] += r[3]

    people = [(p_name, data['given'], data['received']) for p_name, data in people_dict.items()]

    return render_template("index.html",
                           records=records,
                           to_claim=to_claim,
                           to_pay=to_pay,
                           people=people,
                           registered_users=registered_users,
                           pending_requests=pending_requests,
                           connected_friends=connected_friends)


# ---------------- ADD ---------------- #
@app.route('/add', methods=['POST'])
def add():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    name = request.form['name'].strip()
    amount = float(request.form['amount'])
    type_ = request.form['type']
    date = request.form['date']
    reason = request.form['reason']

    db = get_db()
    cursor = db.cursor()

    counterparty_id = find_counterparty_id(cursor, name, user_id)

    cursor.execute("SELECT COUNT(*) FROM money_records")
    count_row = cursor.fetchone()
    serial_no = (count_row[0] if count_row else 0) + 1

    cursor.execute("""
    INSERT INTO money_records 
    (serial_no, name, amount, type, date_taken, reason, user_id, counterparty_user_id, status)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
    """, (serial_no, name, amount, type_, date, reason, user_id, counterparty_id))

    db.commit()
    return redirect('/')


# ---------------- EDIT ---------------- #
@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit(id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        name = request.form['name'].strip()
        amount = float(request.form['amount'])
        type_ = request.form['type']
        date = request.form['date']
        reason = request.form['reason']

        cursor.execute("SELECT user_id, counterparty_user_id FROM money_records WHERE id=%s AND (user_id=%s OR counterparty_user_id=%s)", (id, user_id, user_id))
        existing = cursor.fetchone()

        if existing:
            creator_id = existing[0]
            # If editor is counterparty, flip submitted type back to creator's perspective for DB storage
            if user_id == existing[1] and user_id != existing[0]:
                stored_type = 'received' if type_ == 'given' else 'given'
            else:
                stored_type = type_

            counterparty_id = find_counterparty_id(cursor, name, creator_id)

            cursor.execute("""
            UPDATE money_records
            SET name=%s, amount=%s, type=%s, date_taken=%s, reason=%s, counterparty_user_id=%s
            WHERE id=%s AND (user_id=%s OR counterparty_user_id=%s)
            """, (name, amount, stored_type, date, reason, counterparty_id, id, user_id, user_id))

            db.commit()
        return redirect('/')

    # GET: fetch record from user perspective
    records = get_user_perspective_records(cursor, user_id)
    target_rec = None
    for r in records:
        if r[0] == id:
            target_rec = r
            break

    if not target_rec:
        return redirect('/')

    return render_template("edit.html", r=target_rec)


# ---------------- DELETE ---------------- #
@app.route('/delete/<int:id>')
def delete(id):
    if 'user_id' not in session:
        return redirect('/login')

    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM money_records WHERE id=%s AND (user_id=%s OR counterparty_user_id=%s)", (id, session['user_id'], session['user_id']))
    db.commit()
    return redirect('/')


# ---------------- MARK PAID ---------------- #
@app.route('/mark_paid/<int:id>')
def mark_paid(id):
    if 'user_id' not in session:
        return redirect('/login')

    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE money_records SET status='paid' WHERE id=%s AND (user_id=%s OR counterparty_user_id=%s)", (id, session['user_id'], session['user_id']))
    db.commit()
    return redirect('/')


# ---------------- BULK ACTIONS ---------------- #
@app.route('/bulk_settle', methods=['POST'])
def bulk_settle():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    data = request.get_json()
    ids = data.get('ids', [])
    if ids:
        db = get_db()
        cursor = db.cursor()
        placeholders = ','.join(['%s'] * len(ids))
        cursor.execute(f"UPDATE money_records SET status='paid' WHERE id IN ({placeholders}) AND (user_id=%s OR counterparty_user_id=%s)", tuple(ids) + (session['user_id'], session['user_id']))
        db.commit()
    return {"status": "success"}

@app.route('/bulk_delete', methods=['POST'])
def bulk_delete():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
        
    data = request.get_json()
    ids = data.get('ids', [])
    if ids:
        db = get_db()
        cursor = db.cursor()
        placeholders = ','.join(['%s'] * len(ids))
        cursor.execute(f"DELETE FROM money_records WHERE id IN ({placeholders}) AND (user_id=%s OR counterparty_user_id=%s)", tuple(ids) + (session['user_id'], session['user_id']))
        db.commit()
    return {"status": "success"}


# ---------------- EXPORT CSV ---------------- #
@app.route('/export/csv')
def export_csv():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()
    records = get_user_perspective_records(cursor, user_id)
    
    si = io.StringIO()
    cw = csv.writer(si)
    
    cw.writerow(['ID', 'Date', 'Name', 'Detail', 'Type', 'Status', 'Amount', 'Perspective Note'])
    
    for r in records:
        type_str = "Given (To Claim)" if r[4] == 'given' else "Received (To Pay)"
        status_str = "Settled" if r[8] == 'paid' else "Pending"
        cw.writerow([r[0], r[5], r[2], r[6], type_str, status_str, f"{r[3]:.2f}", r[9]])
        
    output = si.getvalue()
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=transaction_history.csv"}
    )


# ---------------- PERSON DETAIL ---------------- #
@app.route('/person/<string:name>')
def person_detail(name):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    all_records = get_user_perspective_records(cursor, user_id)
    name_clean = name.strip().lower()

    person_records = [
        r for r in all_records 
        if r[2].strip().lower() == name_clean or r[13].strip().lower() == name_clean
    ]

    total_claim = sum(r[3] for r in person_records if r[4] == 'given' and r[8] == 'pending')
    total_pay = sum(r[3] for r in person_records if r[4] == 'received' and r[8] == 'pending')
    balance = total_claim - total_pay

    return render_template("person.html",
                           name=name,
                           records=person_records,
                           total_claim=total_claim,
                           total_pay=total_pay,
                           balance=balance)



# ---------------- SEARCH USER ---------------- #
@app.route('/search_user', methods=['GET', 'POST'])
def search_user():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    current_user_id = session['user_id']
    
    if request.method == 'POST':
        data = request.get_json() or {}
        public_id = data.get('public_id', '').strip()
    else:
        public_id = request.args.get('public_id', '').strip()

    if not public_id:
        return {"status": "error", "message": "Please enter a Public User ID."}, 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
    SELECT id, username, full_name, public_user_id, avatar_url, qr_code_data
    FROM users
    WHERE (LOWER(public_user_id) = LOWER(%s) OR LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s)) AND id != %s
    """, (public_id, public_id, public_id, current_user_id))

    u = cursor.fetchone()
    if not u:
        return {"status": "not_found", "message": "No user found with this User ID."}

    uid, username, full_name, pub_id, avatar_url, qr_data = u

    conn_row = get_connection_between(cursor, current_user_id, uid)
    conn_status = 'none'
    if conn_row:
        c_id, req_id, rec_id, c_st, c_time = conn_row
        if c_st == 'accepted':
            conn_status = 'accepted'
        elif c_st == 'pending':
            if req_id == current_user_id:
                conn_status = 'pending_sent'
            else:
                conn_status = 'pending_received'
        elif c_st == 'blocked':
            conn_status = 'blocked'

    return {
        "status": "success",
        "user": {
            "id": uid,
            "username": username or "",
            "name": full_name or username or "User",
            "public_user_id": pub_id or "",
            "avatar_url": avatar_url or "",
            "has_qr": bool(qr_data),
            "connection_status": conn_status
        }
    }


# ---------------- CONNECTIONS SYSTEM ---------------- #
@app.route('/send_connection_request', methods=['POST'])
def send_connection_request():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    current_user_id = session['user_id']
    data = request.get_json() or {}
    target_public_id = data.get('target_public_id', '').strip()
    target_user_id = data.get('target_user_id')

    db = get_db()
    cursor = db.cursor()

    if not target_user_id and target_public_id:
        cursor.execute("SELECT id FROM users WHERE (LOWER(public_user_id) = LOWER(%s) OR LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s))", (target_public_id, target_public_id, target_public_id))
        row = cursor.fetchone()
        if row:
            target_user_id = row[0]

    if not target_user_id:
        return {"status": "error", "message": "Invalid target user."}, 400

    if target_user_id == current_user_id:
        return {"status": "error", "message": "Connecting with yourself is not allowed."}, 400

    conn_row = get_connection_between(cursor, current_user_id, target_user_id)
    if conn_row:
        c_id, req_id, rec_id, status, c_time = conn_row
        if status == 'accepted':
            return {"status": "info", "message": "You are already connected with this user."}
        elif status == 'pending':
            if req_id == current_user_id:
                return {"status": "info", "message": "Connection request already pending."}
            else:
                return {"status": "info", "message": "This user has already sent you a connection request! Check your notifications."}
        elif status == 'blocked':
            return {"status": "error", "message": "Unable to connect with this user."}, 400
        elif status == 'rejected':
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            cursor.execute("""
            UPDATE connections
            SET requester_id = %s, receiver_id = %s, status = 'pending', created_at = %s
            WHERE id = %s
            """, (current_user_id, target_user_id, now_str, c_id))
            db.commit()
            return {"status": "success", "message": "Connection request sent!"}

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("""
    INSERT INTO connections (requester_id, receiver_id, status, created_at)
    VALUES (%s, %s, 'pending', %s)
    """, (current_user_id, target_user_id, now_str))
    db.commit()

    return {"status": "success", "message": "Connection request sent!"}


@app.route('/respond_connection', methods=['POST'])
def respond_connection():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    current_user_id = session['user_id']
    data = request.get_json() or {}
    connection_id = data.get('connection_id')
    action = data.get('action', '').strip().lower()

    if not connection_id or action not in ('accept', 'reject'):
        return {"status": "error", "message": "Invalid arguments."}, 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, requester_id, receiver_id FROM connections WHERE id = %s AND receiver_id = %s AND status = 'pending'", (connection_id, current_user_id))
    conn_row = cursor.fetchone()
    if not conn_row:
        return {"status": "error", "message": "Connection request not found or already processed."}, 404

    new_status = 'accepted' if action == 'accept' else 'rejected'
    cursor.execute("UPDATE connections SET status = %s WHERE id = %s", (new_status, connection_id))
    db.commit()

    return {"status": "success", "message": f"Connection {new_status}!"}



# ---------------- MY CONNECTIONS PAGE ---------------- #
@app.route('/connections')
def my_connections():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    connected_users = get_accepted_connected_users(cursor, user_id)
    return render_template("connections.html", connections=connected_users)


# ---------------- PROFILE ---------------- #
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    user_prof = get_user_profile(db, cursor, user_id)
    if not user_prof:
        session.clear()
        return redirect('/login')

    return render_template("profile.html", user=user_prof)


@app.route('/profile/edit', methods=['POST'])
def edit_profile():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    full_name = request.form.get('full_name', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    avatar_url = request.form.get('avatar_url', '').strip()
    qr_code_data = request.form.get('qr_code_data', '').strip()

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
    UPDATE users
    SET full_name = %s, phone = %s, email = %s, avatar_url = %s, qr_code_data = %s
    WHERE id = %s
    """, (full_name, phone, email, avatar_url, qr_code_data, user_id))

    db.commit()
    return redirect('/profile')


# ---------------- LOGOUT ---------------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


if __name__ == "__main__":
    app.run(debug=True, port=5000)