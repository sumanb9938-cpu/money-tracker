from flask import Flask, render_template, request, redirect, session, Response, g, url_for
from functools import wraps
from authlib.integrations.flask_client import OAuth
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import mysql.connector
import os
import csv
import io
import secrets
import datetime
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
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

def ensure_default_admin(conn, cursor):
    try:
        cursor.execute("SELECT id FROM users WHERE role = 'admin' OR is_admin = 1 LIMIT 1")
        if not cursor.fetchone():
            adm_username = os.getenv('ADMIN_USERNAME', 'admin')
            adm_password = os.getenv('ADMIN_PASSWORD', 'Admin@12345')
            adm_email = os.getenv('ADMIN_EMAIL', 'admin@ledgerpro.com')
            hashed_pw = generate_password_hash(adm_password)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d")
            
            cursor.execute("""
            INSERT INTO users (username, password, role, is_admin, is_active, email, full_name, created_at)
            VALUES (%s, %s, 'admin', 1, 1, %s, 'System Administrator', %s)
            """, (adm_username, hashed_pw, adm_email, now_str))
            conn.commit()
            print(f"Default Administrator account initialized successfully: username='{adm_username}'")
    except Exception as e:
        print("Admin initialization note:", e)


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
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL,
                type VARCHAR(50) NOT NULL,
                message TEXT NOT NULL,
                reference_id INT NULL,
                is_read INT DEFAULT 0,
                created_at VARCHAR(50)
            );
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS settlement_requests (
                id SERIAL PRIMARY KEY,
                record_id INT NULL,
                sender_id INT NOT NULL,
                receiver_id INT NOT NULL,
                amount DOUBLE PRECISION,
                proof_image TEXT,
                note TEXT,
                status VARCHAR(50) DEFAULT 'pending',
                created_at VARCHAR(50)
            );
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS announcements (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                message TEXT NOT NULL,
                status VARCHAR(50) DEFAULT 'published',
                created_at VARCHAR(50),
                created_by INT
            );
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS support_tickets (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL,
                record_id INT NULL,
                issue_type VARCHAR(100),
                subject VARCHAR(255) NOT NULL,
                message TEXT NOT NULL,
                status VARCHAR(50) DEFAULT 'open',
                admin_response TEXT,
                created_at VARCHAR(50),
                updated_at VARCHAR(50)
            );
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id SERIAL PRIMARY KEY,
                admin_id INT NOT NULL,
                action VARCHAR(100) NOT NULL,
                target_type VARCHAR(50),
                target_id INT NULL,
                details TEXT,
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
                ("created_at", "VARCHAR(50)"),
                ("role", "VARCHAR(50) DEFAULT 'user'"),
                ("is_active", "INT DEFAULT 1")
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
            ensure_default_admin(conn, cursor)

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
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                type VARCHAR(50) NOT NULL,
                message TEXT NOT NULL,
                reference_id INT NULL,
                is_read INT DEFAULT 0,
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
def create_notification(cursor, target_user_id, type_, message, reference_id=None):
    if not target_user_id:
        return
    try:
        created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute("""
        INSERT INTO notifications (user_id, type, message, reference_id, is_read, created_at)
        VALUES (%s, %s, %s, %s, 0, %s)
        """, (target_user_id, type_, message, reference_id, created_at))
    except Exception as e:
        print(f"Warning: Failed to create notification: {e}")
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
           OR LOWER(public_user_id) = LOWER(%s)
           OR LOWER(full_name) = LOWER(%s)) 
          AND id != %s 
        LIMIT 1
        """,
        (name_clean, name_clean, name_clean, name_clean, current_user_id)
    )
    res = cursor.fetchone()
    return res[0] if res else None


def auto_link_records_for_user(db, cursor, user_id, username=None, email=None, public_user_id=None, full_name=None):
    if not user_id:
        return
    try:
        conditions = []
        params = []
        if username:
            conditions.append("LOWER(name) = LOWER(%s)")
            params.append(username)
        if email:
            conditions.append("LOWER(name) = LOWER(%s)")
            params.append(email)
        if public_user_id:
            conditions.append("LOWER(name) = LOWER(%s)")
            params.append(public_user_id)
        if full_name:
            conditions.append("LOWER(name) = LOWER(%s)")
            params.append(full_name)

        if conditions:
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
           u_creator.username AS creator_username, u_creator.full_name AS creator_fullname,
           u_cp.username AS counterparty_username, u_cp.full_name AS counterparty_fullname
    FROM money_records r
    LEFT JOIN users u_creator ON r.user_id = u_creator.id
    LEFT JOIN users u_cp ON r.counterparty_user_id = u_cp.id
    WHERE r.user_id = %s OR r.counterparty_user_id = %s
    ORDER BY r.id DESC
    """, (current_user_id, current_user_id))
    
    rows = cursor.fetchall()
    
    # Query pending settlement requests
    cursor.execute("""
    SELECT record_id, id, amount, proof_image, note, status, sender_id, receiver_id, created_at
    FROM settlement_requests
    WHERE status = 'pending' AND (sender_id = %s OR receiver_id = %s)
    """, (current_user_id, current_user_id))
    sr_rows = cursor.fetchall()
    pending_sr_map = {}
    for sr in sr_rows:
        if sr[0]:
            pending_sr_map[sr[0]] = {
                "id": sr[1],
                "amount": sr[2],
                "proof_image": sr[3] or "",
                "note": sr[4] or "",
                "status": sr[5],
                "sender_id": sr[6],
                "receiver_id": sr[7],
                "created_at": sr[8] or ""
            }

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
        creator_fname = row[11]
        cp_uname = row[12]
        cp_fname = row[13]

        is_creator = (current_user_id == creator_id)

        creator_display = creator_fname or creator_uname or "User"
        cp_display = cp_fname or cp_uname or raw_name

        if is_creator:
            display_name = cp_display
            effective_type = raw_type
            other_user_id = cp_id
        else:
            display_name = creator_display
            effective_type = 'received' if raw_type == 'given' else 'given'
            other_user_id = creator_id

        amt_str = f"{amount:g}" if isinstance(amount, float) and amount.is_integer() else f"{amount}"
        if effective_type == 'given':
            perspective_text = f"You will get ₹{amt_str} from {display_name}"
        else:
            perspective_text = f"You need to pay ₹{amt_str} to {display_name}"

        pending_sr = pending_sr_map.get(rec_id)

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
            other_user_id,    # 10 (target user ID for counterparty)
            is_creator,       # 11
            raw_type,         # 12
            raw_name,         # 13
            pending_sr        # 14
        ))
        
    return formatted





def verify_user_account_password(cursor, user_id, provided_password):
    if not provided_password:
        return False
    cursor.execute("SELECT password FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return False
    stored_password = row[0]
    if stored_password.startswith('pbkdf2:') or stored_password.startswith('scrypt:') or stored_password.startswith('argon2:'):
        return check_password_hash(stored_password, provided_password)
    try:
        if check_password_hash(stored_password, provided_password):
            return True
    except Exception:
        pass
    return stored_password == provided_password


# ---------------- LOGIN ---------------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form['username'].strip()
        password = request.form['password']

        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            SELECT id, password, role, is_admin, is_active, username 
            FROM users 
            WHERE LOWER(username)=LOWER(%s) OR LOWER(email)=LOWER(%s) OR LOWER(public_user_id)=LOWER(%s)
        """, (login_input, login_input, login_input))
        candidates = cursor.fetchall()

        for user in candidates:
            if verify_user_account_password(cursor, user[0], password):
                is_active = 1 if user[4] is None or user[4] == 1 else 0
                if is_active == 0:
                    return render_template('login.html', error="Your account has been deactivated by an administrator. Please contact support.")

                session['user_id'] = user[0]
                session['username'] = user[5] or login_input
                role = user[2] or ('admin' if user[3] == 1 else 'user')
                session['role'] = role

                if role == 'admin':
                    return redirect('/admin')
                return redirect('/')

        return render_template('login.html', error="Invalid username or password")

    return render_template('login.html')


# ---------------- REGISTER ---------------- #
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        hashed_pw = generate_password_hash(password)

        db = get_db()
        cursor = db.cursor()
        cursor.execute("INSERT INTO users (username, password, role, is_active) VALUES (%s, %s, 'user', 1)",
                       (username, hashed_pw))
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

        cursor.execute("SELECT id, username, email, role, is_admin, is_active FROM users WHERE email=%s OR username=%s", (email, email))
        user = cursor.fetchone()

        if user:
            is_active = 1 if user[5] is None or user[5] == 1 else 0
            if is_active == 0:
                return render_template('login.html', error="Your account has been deactivated by an administrator. Please contact support.")
            session['user_id'] = user[0]
            session['username'] = user[1]
            role = user[3] or ('admin' if user[4] == 1 else 'user')
            session['role'] = role
            auto_link_records_for_user(db, cursor, user[0], user[1], user[2])
        else:
            random_password = secrets.token_hex(16)
            cursor.execute("INSERT INTO users (username, password, email, role, is_active) VALUES (%s, %s, %s, 'user', 1)",
                           (email, random_password, email))
            db.commit()

            cursor.execute("SELECT id, username, email, role, is_admin FROM users WHERE email=%s OR username=%s", (email, email))
            new_user = cursor.fetchone()
            if new_user:
                session['user_id'] = new_user[0]
                session['username'] = new_user[1]
                session['role'] = new_user[3] or 'user'
                auto_link_records_for_user(db, cursor, new_user[0], new_user[1], new_user[2])

        if session.get('role') == 'admin':
            return redirect('/admin')
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

    cursor.execute("SELECT username, email, public_user_id, full_name FROM users WHERE id=%s", (user_id,))
    curr_u = cursor.fetchone()
    if curr_u:
        auto_link_records_for_user(db, cursor, user_id, curr_u[0], curr_u[1], curr_u[2] if len(curr_u) > 2 else None, curr_u[3] if len(curr_u) > 3 else None)

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

    if counterparty_id:
        cursor.execute("SELECT username, full_name FROM users WHERE id=%s", (user_id,))
        creator_row = cursor.fetchone()
        creator_name = (creator_row[1] or creator_row[0] or "A user") if creator_row else "A user"
        amt_str = f"{amount:g}" if isinstance(amount, float) and amount.is_integer() else f"{amount}"
        create_notification(
            cursor, 
            counterparty_id, 
            'transaction_created', 
            f"{creator_name} created a connected transaction of ₹{amt_str}.", 
            serial_no
        )

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

        cursor.execute("SELECT user_id, counterparty_user_id, type FROM money_records WHERE id=%s AND (user_id=%s OR counterparty_user_id=%s)", (id, user_id, user_id))
        existing = cursor.fetchone()

        if not existing:
            return redirect(url_for('index', error="Access Denied: You are not authorized to edit this transaction."))

        creator_id, cp_id_val, raw_type = existing[0], existing[1], existing[2]
        
        # Deny edit access if current user is debtor (the person giving back money)
        is_debtor = False
        if user_id == creator_id:
            is_debtor = (raw_type in ('got', 'received'))
        elif user_id == cp_id_val:
            is_debtor = (raw_type == 'given')

        if is_debtor:
            return redirect(url_for('index', error="Access Denied: As the person giving back money (debtor), you cannot edit this transaction record."))

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

        target_notif_user = counterparty_id if user_id == creator_id else creator_id
        if target_notif_user:
            cursor.execute("SELECT username, full_name FROM users WHERE id=%s", (user_id,))
            editor_row = cursor.fetchone()
            editor_name = (editor_row[1] or editor_row[0] or "A user") if editor_row else "A user"
            amt_str = f"{amount:g}" if isinstance(amount, float) and amount.is_integer() else f"{amount}"
            create_notification(
                cursor,
                target_notif_user,
                'transaction_edited',
                f"{editor_name} updated a connected transaction of ₹{amt_str}.",
                id
            )

        db.commit()
        return redirect(url_for('index', success="Transaction updated successfully."))

    # GET: fetch record from user perspective
    records = get_user_perspective_records(cursor, user_id)
    target_rec = None
    for r in records:
        if r[0] == id:
            target_rec = r
            break

    if not target_rec:
        return redirect(url_for('index', error="Access Denied: You are not authorized to view or edit this transaction."))

    # r[4] is effective_type for current_user. If effective_type == 'received', user is debtor!
    if target_rec[4] == 'received':
        return redirect(url_for('index', error="Access Denied: As the person giving back money (debtor), you cannot edit this transaction record."))

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
    if cursor.rowcount == 0:
        return redirect(url_for('index', error="Access Denied: You are not authorized to delete this transaction."))
    return redirect(url_for('index', success="Transaction deleted successfully."))


# ---------------- MARK PAID ---------------- #
@app.route('/mark_paid/<int:id>')
def mark_paid(id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT user_id, counterparty_user_id, amount, type FROM money_records WHERE id=%s AND (user_id=%s OR counterparty_user_id=%s)", (id, user_id, user_id))
    rec = cursor.fetchone()
    if not rec:
        return redirect(url_for('index', error="Access Denied: Transaction not found or unauthorized access."))

    u1, u2, amount, raw_type = rec[0], rec[1], rec[2], rec[3]

    # Check if current_user is debtor (needs to pay)
    is_debtor = False
    if user_id == u1:
        is_debtor = (raw_type in ('got', 'received'))
    elif user_id == u2:
        is_debtor = (raw_type == 'given')

    if is_debtor:
        return redirect(url_for('settlements', error="As a debtor, you cannot directly settle transactions. Please submit a settlement request with payment proof for verification."))

    target_notif_user = u2 if user_id == u1 else u1
    if target_notif_user:
        cursor.execute("SELECT username, full_name FROM users WHERE id=%s", (user_id,))
        actor_row = cursor.fetchone()
        actor_name = (actor_row[1] or actor_row[0] or "A user") if actor_row else "A user"
        amt_str = f"{amount:g}" if isinstance(amount, float) and amount.is_integer() else f"{amount}"
        create_notification(
            cursor,
            target_notif_user,
            'transaction_paid',
            f"{actor_name} marked a transaction of ₹{amt_str} as paid.",
            id
        )

    cursor.execute("UPDATE money_records SET status='paid' WHERE id=%s AND (user_id=%s OR counterparty_user_id=%s)", (id, user_id, user_id))
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

        # Only allow settling records where current_user is creditor
        placeholders = ','.join(['%s'] * len(ids))
        cursor.execute(f"""
            SELECT id, user_id, counterparty_user_id, type FROM money_records
            WHERE id IN ({placeholders}) AND (user_id = %s OR counterparty_user_id = %s)
        """, tuple(ids) + (session['user_id'], session['user_id']))
        rows = cursor.fetchall()
        allowed_ids = []
        for r in rows:
            rec_id, u1, u2, r_type = r[0], r[1], r[2], r[3]
            is_debtor = False
            if session['user_id'] == u1:
                is_debtor = (r_type in ('got', 'received'))
            elif session['user_id'] == u2:
                is_debtor = (r_type == 'given')
            if not is_debtor:
                allowed_ids.append(rec_id)

        if allowed_ids:
            p_allowed = ','.join(['%s'] * len(allowed_ids))
            cursor.execute(f"UPDATE money_records SET status='paid' WHERE id IN ({p_allowed})", tuple(allowed_ids))
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

    cursor.execute("SELECT username, full_name FROM users WHERE id=%s", (current_user_id,))
    sender_row = cursor.fetchone()
    sender_name = (sender_row[1] or sender_row[0] or "A user") if sender_row else "A user"
    create_notification(
        cursor,
        target_user_id,
        'connection_request',
        f"{sender_name} sent you a connection request.",
        target_user_id
    )

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

    requester_id = conn_row[1]
    new_status = 'accepted' if action == 'accept' else 'rejected'
    cursor.execute("UPDATE connections SET status = %s WHERE id = %s", (new_status, connection_id))

    if action == 'accept':
        cursor.execute("SELECT username, full_name FROM users WHERE id=%s", (current_user_id,))
        rec_row = cursor.fetchone()
        rec_name = (rec_row[1] or rec_row[0] or "A user") if rec_row else "A user"
        create_notification(
            cursor,
            requester_id,
            'connection_accepted',
            f"{rec_name} accepted your connection request.",
            connection_id
        )

    db.commit()

    return {"status": "success", "message": f"Connection {new_status}!"}


# ---------------- NOTIFICATIONS API ---------------- #
@app.route('/api/notifications', methods=['GET'])
def get_notifications_api():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("""
    SELECT id, type, message, reference_id, is_read, created_at
    FROM notifications
    WHERE user_id = %s
    ORDER BY id DESC
    LIMIT 20
    """, (user_id,))
    rows = cursor.fetchall()
    
    notifs = [{
        "id": r[0],
        "type": r[1],
        "message": r[2],
        "reference_id": r[3],
        "is_read": r[4],
        "created_at": r[5]
    } for r in rows]
    
    unread_count = sum(1 for n in notifs if n["is_read"] == 0)
    
    return {
        "status": "success",
        "unread_count": unread_count,
        "notifications": notifs
    }


@app.route('/api/notifications/mark_read', methods=['POST'])
def mark_notifications_read():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s", (user_id,))
    db.commit()
    
    return {"status": "success"}



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

    error = request.args.get('error')
    success = request.args.get('success')
    open_qr_modal = request.args.get('open_qr_modal') == 'true'

    return render_template("profile.html", user=user_prof, error=error, success=success, open_qr_modal=open_qr_modal)


@app.route('/api/profile/qr/verify-password', methods=['POST'])
def verify_qr_password_api():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401

    data = request.get_json(silent=True) or request.form
    password = (data.get('password') or '').strip()

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    if verify_user_account_password(cursor, user_id, password):
        return {"status": "success", "message": "Password verified successfully."}
    else:
        return {"status": "error", "message": "Incorrect password. Verification failed."}, 400


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
    confirm_password = request.form.get('confirm_password', '').strip()

    db = get_db()
    cursor = db.cursor()

    # Check existing user profile data
    existing_prof = get_user_profile(db, cursor, user_id)
    existing_qr = existing_prof['qr_code_data'] if existing_prof else ''

    qr_file = request.files.get('qr_file')

    # If QR code is being changed, require password confirmation
    if (qr_file and qr_file.filename) or (qr_code_data and qr_code_data != existing_qr):
        if not verify_user_account_password(cursor, user_id, confirm_password):
            return render_template(
                "profile.html",
                user=existing_prof,
                error="Account password confirmation required to update Payment QR code.",
                open_edit_modal=True
            )

    if qr_file and qr_file.filename:
        ext = os.path.splitext(qr_file.filename)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']:
            upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'qr_codes')
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"qr_user_{user_id}_{secrets.token_hex(4)}{ext}"
            filepath = os.path.join(upload_dir, filename)
            qr_file.save(filepath)
            qr_code_data = f"/static/uploads/qr_codes/{filename}"

    # Validate phone number uniqueness if provided
    if phone:
        cursor.execute("SELECT id FROM users WHERE phone = %s AND id != %s", (phone, user_id))
        existing_phone_user = cursor.fetchone()
        if existing_phone_user:
            user_prof = get_user_profile(db, cursor, user_id)
            if user_prof:
                user_prof['full_name'] = full_name
                user_prof['phone'] = phone
                user_prof['email'] = email
                user_prof['avatar_url'] = avatar_url
                user_prof['qr_code_data'] = qr_code_data
            return render_template(
                "profile.html",
                user=user_prof,
                error="This phone number is already registered to another account.",
                open_edit_modal=True
            )

    cursor.execute("""
    UPDATE users
    SET full_name = %s, phone = %s, email = %s, avatar_url = %s, qr_code_data = %s
    WHERE id = %s
    """, (full_name, phone, email, avatar_url, qr_code_data, user_id))

    db.commit()

    auto_link_records_for_user(db, cursor, user_id, email=email, full_name=full_name)

    return redirect(url_for('profile', success="Profile updated successfully!"))


@app.route('/profile/qr/upload', methods=['POST'])
def upload_qr_code():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    confirm_password = request.form.get('confirm_password', '').strip()

    db = get_db()
    cursor = db.cursor()

    # Enforce password confirmation for QR code modifications
    if not verify_user_account_password(cursor, user_id, confirm_password):
        user_prof = get_user_profile(db, cursor, user_id)
        return render_template(
            "profile.html",
            user=user_prof,
            error="Incorrect account password. QR code security verification failed.",
            open_qr_modal=True
        )

    remove_qr = request.form.get('remove_qr')
    if remove_qr == 'true':
        cursor.execute("UPDATE users SET qr_code_data = '' WHERE id = %s", (user_id,))
        db.commit()
        return redirect(url_for('profile', success="Payment QR code removed."))

    qr_file = request.files.get('qr_file')
    qr_text = request.form.get('qr_text', '').strip()

    if qr_file and qr_file.filename:
        ext = os.path.splitext(qr_file.filename)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']:
            upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'qr_codes')
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"qr_user_{user_id}_{secrets.token_hex(4)}{ext}"
            filepath = os.path.join(upload_dir, filename)
            qr_file.save(filepath)
            qr_code_url = f"/static/uploads/qr_codes/{filename}"

            cursor.execute("UPDATE users SET qr_code_data = %s WHERE id = %s", (qr_code_url, user_id))
            db.commit()
            return redirect(url_for('profile', success="Payment QR code uploaded successfully!", open_qr_modal='true'))
        else:
            user_prof = get_user_profile(db, cursor, user_id)
            return render_template(
                "profile.html",
                user=user_prof,
                error="Invalid file type. Please upload a PNG, JPG, JPEG, WEBP, or SVG image.",
                open_qr_modal=True
            )

    elif qr_text:
        cursor.execute("UPDATE users SET qr_code_data = %s WHERE id = %s", (qr_text, user_id))
        db.commit()
        return redirect(url_for('profile', success="Payment QR code updated successfully!", open_qr_modal='true'))

    # ---------------- SETTLEMENT & VERIFICATION SYSTEM ---------------- #
def create_notification(cursor, user_id, notif_type, message, reference_id=None):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("""
    INSERT INTO notifications (user_id, type, message, reference_id, is_read, created_at)
    VALUES (%s, %s, %s, %s, 0, %s)
    """, (user_id, notif_type, message, reference_id, now_str))


def get_settlement_requests_for_user(cursor, user_id):
    cursor.execute("""
    SELECT sr.id, sr.record_id, sr.sender_id, sr.receiver_id, sr.amount, sr.proof_image, sr.note, sr.status, sr.created_at,
           u_sender.username, u_sender.full_name, u_sender.public_user_id, u_sender.avatar_url
    FROM settlement_requests sr
    JOIN users u_sender ON sr.sender_id = u_sender.id
    WHERE sr.receiver_id = %s
    ORDER BY sr.id DESC
    """, (user_id,))
    received_rows = cursor.fetchall()
    
    received_requests = [{
        "id": r[0],
        "record_id": r[1],
        "sender_id": r[2],
        "receiver_id": r[3],
        "amount": r[4],
        "proof_image": r[5] or "",
        "note": r[6] or "",
        "status": r[7],
        "created_at": r[8] or "",
        "sender_name": r[10] or r[9] or "User",
        "sender_username": r[9] or "",
        "sender_public_id": r[11] or "",
        "sender_avatar": r[12] or ""
    } for r in received_rows]

    cursor.execute("""
    SELECT sr.id, sr.record_id, sr.sender_id, sr.receiver_id, sr.amount, sr.proof_image, sr.note, sr.status, sr.created_at,
           u_rec.username, u_rec.full_name, u_rec.public_user_id, u_rec.avatar_url
    FROM settlement_requests sr
    JOIN users u_rec ON sr.receiver_id = u_rec.id
    WHERE sr.sender_id = %s
    ORDER BY sr.id DESC
    """, (user_id,))
    sent_rows = cursor.fetchall()

    sent_requests = [{
        "id": r[0],
        "record_id": r[1],
        "sender_id": r[2],
        "receiver_id": r[3],
        "amount": r[4],
        "proof_image": r[5] or "",
        "note": r[6] or "",
        "status": r[7],
        "created_at": r[8] or "",
        "receiver_name": r[10] or r[9] or "User",
        "receiver_username": r[9] or "",
        "receiver_public_id": r[11] or "",
        "receiver_avatar": r[12] or ""
    } for r in sent_rows]

    return {
        "received": received_requests,
        "sent": sent_requests
    }


def get_pending_settlement_count(cursor, user_id):
    cursor.execute("SELECT COUNT(*) FROM settlement_requests WHERE receiver_id = %s AND status = 'pending'", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


@app.route('/settlements')
def settlements():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    settlement_data = get_settlement_requests_for_user(cursor, user_id)
    pending_count = get_pending_settlement_count(cursor, user_id)
    connected_friends = get_accepted_connected_users(cursor, user_id)
    records = get_user_perspective_records(cursor, user_id)

    error = request.args.get('error')
    success = request.args.get('success')

    return render_template(
        "settlements.html",
        received_requests=settlement_data['received'],
        sent_requests=settlement_data['sent'],
        pending_count=pending_count,
        connected_friends=connected_friends,
        records=records,
        error=error,
        success=success
    )


@app.route('/settlements/request', methods=['POST'])
def request_settlement():
    if 'user_id' not in session:
        return redirect('/login')

    sender_id = session['user_id']
    receiver_id = request.form.get('receiver_id')
    receiver_identifier = request.form.get('receiver_identifier', '').strip()
    record_id = request.form.get('record_id')
    amount_str = request.form.get('amount', '0').strip()
    note = request.form.get('note', '').strip()

    try:
        amount = float(amount_str)
    except ValueError:
        amount = 0.0

    if amount <= 0:
        return redirect(url_for('settlements', error="Please enter a valid positive settlement amount."))

    db = get_db()
    cursor = db.cursor()

    rec_id_val = int(record_id) if record_id and record_id.isdigit() else None

    # Fail-safe: if record_id is provided, derive target creditor (receiver_id) directly from money_records
    if rec_id_val:
        cursor.execute("SELECT user_id, counterparty_user_id FROM money_records WHERE id = %s AND (user_id = %s OR counterparty_user_id = %s)", (rec_id_val, sender_id, sender_id))
        rec_info = cursor.fetchone()
        if rec_info:
            c_uid, cp_uid = rec_info[0], rec_info[1]
            record_target_id = cp_uid if sender_id == c_uid else c_uid
            if record_target_id and record_target_id != sender_id:
                receiver_id = record_target_id

    if not receiver_id and receiver_identifier:
        cursor.execute(
            """
            SELECT id FROM users
            WHERE (LOWER(public_user_id) = LOWER(%s)
               OR LOWER(username) = LOWER(%s)
               OR LOWER(email) = LOWER(%s)
               OR LOWER(full_name) = LOWER(%s)
               OR phone = %s)
              AND id != %s
            LIMIT 1
            """,
            (receiver_identifier, receiver_identifier, receiver_identifier, receiver_identifier, receiver_identifier, sender_id)
        )
        r_row = cursor.fetchone()
        if r_row:
            receiver_id = r_row[0]

    if not receiver_id:
        return redirect(url_for('settlements', error="Target user not found. The other party must have a registered account on LedgerPro to receive settlement requests."))

    receiver_id = int(receiver_id)
    if receiver_id == sender_id:
        return redirect(url_for('settlements', error="You cannot send a settlement request to yourself."))

    # Upload proof image file if present
    proof_image_path = ""
    proof_file = request.files.get('proof_file')
    if proof_file and proof_file.filename:
        ext = os.path.splitext(proof_file.filename)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']:
            upload_dir = os.path.join(app.root_path, 'static', 'uploads', 'proofs')
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"proof_user_{sender_id}_{secrets.token_hex(4)}{ext}"
            filepath = os.path.join(upload_dir, filename)
            proof_file.save(filepath)
            proof_image_path = f"/static/uploads/proofs/{filename}"

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    rec_id_val = int(record_id) if record_id and record_id.isdigit() else None

    if rec_id_val:
        cursor.execute("SELECT id FROM money_records WHERE id = %s AND (user_id = %s OR counterparty_user_id = %s)", (rec_id_val, sender_id, sender_id))
        if not cursor.fetchone():
            return redirect(url_for('settlements', error="Access Denied: You are not authorized for this transaction record."))

    cursor.execute("""
    INSERT INTO settlement_requests (record_id, sender_id, receiver_id, amount, proof_image, note, status, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
    """, (rec_id_val, sender_id, receiver_id, amount, proof_image_path, note, now_str))

    if rec_id_val:
        cursor.execute("UPDATE money_records SET status = 'verification_pending' WHERE id = %s AND (user_id = %s OR counterparty_user_id = %s)", (rec_id_val, sender_id, sender_id))

    db.commit()

    cursor.execute("SELECT id FROM settlement_requests WHERE sender_id = %s ORDER BY id DESC LIMIT 1", (sender_id,))
    req_row = cursor.fetchone()
    req_id = req_row[0] if req_row else None

    cursor.execute("SELECT username, full_name FROM users WHERE id = %s", (sender_id,))
    s_row = cursor.fetchone()
    sender_name = (s_row[1] or s_row[0] or "A user") if s_row else "A user"

    amt_formatted = f"{amount:g}" if amount.is_integer() else f"{amount:.2f}"
    create_notification(
        cursor,
        receiver_id,
        'settlement_request',
        f"{sender_name} submitted a settlement request of ₹{amt_formatted} for verification.",
        req_id
    )
    db.commit()

    redirect_to = request.form.get('redirect_to')
    if redirect_to:
        sep = '&' if '?' in redirect_to else '?'
        return redirect(f"{redirect_to}{sep}success=Settlement+request+sent+for+verification!")

    return redirect(url_for('settlements', success="Settlement request sent for verification!"))


@app.route('/settlements/verify/<int:request_id>', methods=['POST'])
def verify_settlement(request_id):
    if 'user_id' not in session:
        return redirect('/login')

    receiver_id = session['user_id']
    action = request.form.get('action', '').strip().lower()

    if action not in ('approve', 'reject'):
        return redirect(url_for('settlements', error="Invalid verification action."))

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
    SELECT id, record_id, sender_id, receiver_id, amount, status
    FROM settlement_requests
    WHERE id = %s AND receiver_id = %s AND status = 'pending'
    """, (request_id, receiver_id))
    req = cursor.fetchone()

    if not req:
        return redirect(url_for('settlements', error="Access Denied: Settlement request not found or unauthorized access."))

    s_id = req[2]
    amount = req[4]
    rec_id = req[1]

    cursor.execute("SELECT username, full_name FROM users WHERE id = %s", (receiver_id,))
    rec_row = cursor.fetchone()
    receiver_name = (rec_row[1] or rec_row[0] or "A user") if rec_row else "A user"
    amt_formatted = f"{amount:g}" if amount.is_integer() else f"{amount:.2f}"

    if action == 'approve':
        cursor.execute("UPDATE settlement_requests SET status = 'approved' WHERE id = %s AND receiver_id = %s", (request_id, receiver_id))
        if rec_id:
            cursor.execute("UPDATE money_records SET status = 'paid' WHERE id = %s AND (user_id = %s OR counterparty_user_id = %s)", (rec_id, receiver_id, receiver_id))
        
        create_notification(
            cursor,
            s_id,
            'settlement_approved',
            f"{receiver_name} verified and approved your settlement request of ₹{amt_formatted}!",
            request_id
        )
        db.commit()
        return redirect(url_for('settlements', success="Settlement verified and approved!"))
    else:
        cursor.execute("UPDATE settlement_requests SET status = 'rejected' WHERE id = %s AND receiver_id = %s", (request_id, receiver_id))
        if rec_id:
            cursor.execute("UPDATE money_records SET status = 'pending' WHERE id = %s AND (user_id = %s OR counterparty_user_id = %s)", (rec_id, receiver_id, receiver_id))

        create_notification(
            cursor,
            s_id,
            'settlement_rejected',
            f"{receiver_name} rejected your settlement request of ₹{amt_formatted}.",
            request_id
        )
        db.commit()
        return redirect(url_for('settlements', success="Settlement request rejected."))


@app.route('/api/settlements/pending-count', methods=['GET'])
def get_pending_settlement_count_api():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()
    count = get_pending_settlement_count(cursor, user_id)
    return {"status": "success", "count": count}


@app.context_processor
def inject_approvals_context():
    if 'user_id' in session:
        try:
            db = get_db()
            cursor = db.cursor()
            user_id = session['user_id']
            p_settle = get_pending_settlement_count(cursor, user_id)
            p_conn = len(get_pending_connection_requests(cursor, user_id))
            return {
                'nav_pending_settlements': p_settle,
                'nav_pending_connections': p_conn,
                'nav_total_approvals': p_settle + p_conn
            }
        except Exception:
            pass
    return {
        'nav_pending_settlements': 0,
        'nav_pending_connections': 0,
        'nav_total_approvals': 0
    }


@app.route('/approvals')
def approvals():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    settlement_data = get_settlement_requests_for_user(cursor, user_id)
    pending_settlements = [s for s in settlement_data['received'] if s['status'] == 'pending']
    history_settlements = [s for s in settlement_data['received'] if s['status'] != 'pending']

    pending_connections = get_pending_connection_requests(cursor, user_id)

    cursor.execute("""
    SELECT c.id, c.requester_id, c.status, c.created_at, u.username, u.full_name, u.public_user_id, u.avatar_url
    FROM connections c
    JOIN users u ON c.requester_id = u.id
    WHERE c.receiver_id = %s AND c.status != 'pending'
    ORDER BY c.id DESC LIMIT 10
    """, (user_id,))
    history_connections_rows = cursor.fetchall()
    history_connections = [{
        "id": r[0],
        "requester_id": r[1],
        "status": r[2],
        "created_at": r[3],
        "username": r[4],
        "name": r[5] or r[4] or "User",
        "public_user_id": r[6],
        "avatar_url": r[7] or ""
    } for r in history_connections_rows]

    error = request.args.get('error')
    success = request.args.get('success')

    return render_template(
        "approvals.html",
        pending_settlements=pending_settlements,
        pending_connections=pending_connections,
        history_settlements=history_settlements,
        history_connections=history_connections,
        total_pending=len(pending_settlements) + len(pending_connections),
        error=error,
        success=success
    )


# ==============================================================================
# ADMIN SYSTEM & MANAGEMENT CONTROLLERS (FEATURES 1 - 20)
# ==============================================================================

def log_admin_action(cursor, admin_id, action, target_type=None, target_id=None, details=None):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at)
    VALUES (%s, %s, %s, %s, %s, %s)
    """, (admin_id, action, target_type, target_id, details, now_str))


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        user_id = session['user_id']
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT role, is_admin, is_active FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if not row or (row[2] is not None and row[2] == 0):
            session.clear()
            return redirect('/login')
        role, is_adm = row[0], row[1]
        if role != 'admin' and is_adm != 1:
            return redirect(url_for('index', error="Access Denied: Administrator privileges required."))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM money_records")
    total_tx = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM money_records WHERE status = 'pending'")
    pending_tx = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM money_records WHERE status = 'paid'")
    paid_tx = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM money_records WHERE status = 'verification_pending'")
    verif_pending_tx = cursor.fetchone()[0]
    
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM money_records")
    total_money = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM money_records WHERE status = 'paid'")
    settled_money = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM money_records WHERE status = 'pending'")
    pending_money = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM connections WHERE status = 'accepted'")
    active_conns = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM connections WHERE status = 'pending'")
    pending_conns = cursor.fetchone()[0]

    cursor.execute("""
    SELECT u.id, u.username, u.full_name, u.public_user_id,
           COUNT(r.id) AS tx_count
    FROM users u
    LEFT JOIN money_records r ON u.id = r.user_id OR u.id = r.counterparty_user_id
    GROUP BY u.id, u.username, u.full_name, u.public_user_id
    ORDER BY tx_count DESC LIMIT 5
    """)
    top_rows = cursor.fetchall()
    top_users = []
    for tr in top_rows:
        cursor.execute("SELECT COUNT(*) FROM connections WHERE (requester_id = %s OR receiver_id = %s) AND status = 'accepted'", (tr[0], tr[0]))
        c_count = cursor.fetchone()[0]
        top_users.append({
            "id": tr[0],
            "username": tr[1],
            "full_name": tr[2],
            "public_user_id": tr[3],
            "tx_count": tr[4],
            "conn_count": c_count
        })

    stats = {
        "total_users": total_users,
        "total_transactions": total_tx,
        "pending_transactions": pending_tx,
        "paid_transactions": paid_tx,
        "verif_pending_transactions": verif_pending_tx,
        "total_money_tracked": total_money,
        "settled_money": settled_money,
        "pending_money": pending_money,
        "active_connections": active_conns,
        "pending_connections": pending_conns
    }

    return render_template("admin/dashboard.html", active_page="dashboard", stats=stats, top_users=top_users)


@app.route('/admin/users')
@admin_required
def admin_users():
    db = get_db()
    cursor = db.cursor()
    
    search = request.args.get('search', '').strip()
    role_f = request.args.get('role', '').strip()
    status_f = request.args.get('status', '').strip()

    query = "SELECT id, username, email, full_name, phone, public_user_id, role, is_active, created_at FROM users WHERE 1=1"
    params = []

    if search:
        query += " AND (LOWER(username) LIKE %s OR LOWER(full_name) LIKE %s OR LOWER(email) LIKE %s OR LOWER(phone) LIKE %s OR LOWER(public_user_id) LIKE %s)"
        s_term = f"%{search.lower()}%"
        params.extend([s_term, s_term, s_term, s_term, s_term])

    if role_f:
        query += " AND role = %s"
        params.append(role_f)

    if status_f == 'active':
        query += " AND (is_active IS NULL OR is_active = 1)"
    elif status_f == 'inactive':
        query += " AND is_active = 0"

    query += " ORDER BY id DESC"
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    users_list = [{
        "id": r[0],
        "username": r[1],
        "email": r[2] or "",
        "full_name": r[3] or "",
        "phone": r[4] or "",
        "public_user_id": r[5] or "",
        "role": r[6] or "user",
        "is_active": 1 if r[7] is None or r[7] == 1 else 0,
        "created_at": r[8] or ""
    } for r in rows]

    return render_template(
        "admin/users.html",
        active_page="users",
        users=users_list,
        search_query=search,
        filter_role=role_f,
        filter_status=status_f
    )


@app.route('/admin/users/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    db = get_db()
    cursor = db.cursor()

    user_prof = get_user_profile(db, cursor, user_id)
    if not user_prof:
        return redirect(url_for('admin_users', error="User account not found."))

    records = get_user_perspective_records(cursor, user_id)
    total_tx = len(records)
    pending_tx = sum(1 for r in records if r[8] == 'pending' or r[8] == 'verification_pending')
    paid_tx = sum(1 for r in records if r[8] == 'paid')

    user_stats = {
        "total_tx": total_tx,
        "pending_tx": pending_tx,
        "paid_tx": paid_tx
    }

    log_admin_action(cursor, session['user_id'], "Viewed User Details", "user", user_id, f"Viewed user @{user_prof['username']}")
    db.commit()

    return render_template(
        "admin/user_detail.html",
        active_page="users",
        target_user=user_prof,
        user_records=records,
        user_stats=user_stats
    )


@app.route('/admin/users/<int:user_id>/toggle-status', methods=['POST'])
@admin_required
def admin_toggle_user_status(user_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT username, is_active, role FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    if not row:
        return redirect(url_for('admin_users', error="User not found."))

    uname, curr_active, role = row[0], (1 if row[1] is None or row[1] == 1 else 0), row[2]
    new_active = 0 if curr_active == 1 else 1

    if role == 'admin' and new_active == 0:
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND (is_active IS NULL OR is_active = 1)")
        admin_count = cursor.fetchone()[0]
        if admin_count <= 1:
            return redirect(url_for('admin_users', error="Cannot deactivate the last remaining active admin account!"))

    cursor.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_active, user_id))
    action_text = "Deactivated Account" if new_active == 0 else "Reactivated Account"
    log_admin_action(cursor, session['user_id'], action_text, "user", user_id, f"{action_text} for @{uname}")
    db.commit()

    return redirect(url_for('admin_users', success=f"Account status updated for @{uname}."))


@app.route('/admin/users/role', methods=['POST'])
@admin_required
def admin_change_user_role():
    target_id = request.form.get('user_id')
    new_role = request.form.get('role', 'user').strip().lower()

    if new_role not in ('user', 'admin'):
        return redirect(url_for('admin_users', error="Invalid role specified."))

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT username, role, is_admin FROM users WHERE id = %s", (target_id,))
    row = cursor.fetchone()
    if not row:
        return redirect(url_for('admin_users', error="User not found."))

    uname, curr_role, is_adm = row[0], row[1], row[2]

    if curr_role == 'admin' and new_role == 'user':
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND (is_active IS NULL OR is_active = 1)")
        admin_count = cursor.fetchone()[0]
        if admin_count <= 1:
            return redirect(url_for('admin_users', error="Cannot demote the last remaining admin account!"))

    is_adm_val = 1 if new_role == 'admin' else 0
    cursor.execute("UPDATE users SET role = %s, is_admin = %s WHERE id = %s", (new_role, is_adm_val, target_id))
    log_admin_action(cursor, session['user_id'], "Changed User Role", "user", target_id, f"Changed role for @{uname} from {curr_role} to {new_role}")
    db.commit()

    return redirect(url_for('admin_users', success=f"Role updated to {new_role.upper()} for @{uname}."))


@app.route('/admin/transactions')
@admin_required
def admin_transactions():
    db = get_db()
    cursor = db.cursor()

    search = request.args.get('search', '').strip()
    status_f = request.args.get('status', '').strip()

    query = """
    SELECT r.id, r.serial_no, r.name, r.amount, r.type, r.date_taken, r.reason, r.status,
           u_creator.username AS creator_username, u_creator.full_name AS creator_fullname,
           u_cp.username AS cp_username, u_cp.full_name AS cp_fullname
    FROM money_records r
    LEFT JOIN users u_creator ON r.user_id = u_creator.id
    LEFT JOIN users u_cp ON r.counterparty_user_id = u_cp.id
    WHERE 1=1
    """
    params = []

    if search:
        query += " AND (LOWER(r.name) LIKE %s OR LOWER(r.reason) LIKE %s OR LOWER(u_creator.username) LIKE %s OR LOWER(u_cp.username) LIKE %s)"
        s_term = f"%{search.lower()}%"
        params.extend([s_term, s_term, s_term, s_term])

    if status_f:
        query += " AND r.status = %s"
        params.append(status_f)

    query += " ORDER BY r.id DESC LIMIT 100"
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    tx_list = [{
        "id": r[0],
        "serial_no": r[1],
        "name": r[2],
        "amount": r[3],
        "type": r[4],
        "date_taken": r[5] or "",
        "reason": r[6] or "",
        "status": r[7] or "pending",
        "creator_username": r[8] or "Unknown",
        "creator_name": r[9] or r[8] or "User",
        "cp_username": r[10] or "",
        "counterparty_name": r[11] or r[10] or r[2]
    } for r in rows]

    return render_template(
        "admin/transactions.html",
        active_page="transactions",
        transactions=tx_list,
        search_query=search,
        filter_status=status_f
    )


@app.route('/admin/connections')
@admin_required
def admin_connections():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
    SELECT c.id, c.requester_id, c.receiver_id, c.status, c.created_at,
           u_req.username, u_req.full_name, u_req.public_user_id,
           u_rec.username, u_rec.full_name, u_rec.public_user_id
    FROM connections c
    JOIN users u_req ON c.requester_id = u_req.id
    JOIN users u_rec ON c.receiver_id = u_rec.id
    ORDER BY c.id DESC
    """)
    rows = cursor.fetchall()

    conns = [{
        "id": r[0],
        "requester_id": r[1],
        "receiver_id": r[2],
        "status": r[3],
        "created_at": r[4] or "",
        "req_username": r[5],
        "req_name": r[6] or r[5],
        "req_public_id": r[7],
        "rec_username": r[8],
        "rec_name": r[9] or r[8],
        "rec_public_id": r[10]
    } for r in rows]

    accepted_cnt = sum(1 for c in conns if c['status'] == 'accepted')
    pending_cnt = sum(1 for c in conns if c['status'] == 'pending')

    return render_template(
        "admin/connections.html",
        active_page="connections",
        connections=conns,
        stats={"accepted": accepted_cnt, "pending": pending_cnt}
    )


@app.route('/admin/connections/<int:conn_id>/remove', methods=['POST'])
@admin_required
def admin_remove_connection(conn_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM connections WHERE id = %s", (conn_id,))
    log_admin_action(cursor, session['user_id'], "Removed Connection Record", "connection", conn_id, f"Removed connection #{conn_id}")
    db.commit()
    return redirect(url_for('admin_connections', success="Connection record removed successfully."))


@app.route('/admin/reports')
@admin_required
def admin_reports():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT COUNT(*) FROM money_records WHERE status = 'paid'")
    settled_cnt = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM money_records WHERE status = 'pending'")
    pending_cnt = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM money_records")
    total_amt = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM money_records WHERE status = 'paid'")
    settled_amt = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM money_records WHERE status = 'pending'")
    pending_amt = cursor.fetchone()[0]

    report_data = {
        "growth_labels": ["Week 1", "Week 2", "Week 3", "Current Week"],
        "growth_values": [5, 12, 28, 45],
        "settled_tx_count": settled_cnt,
        "pending_tx_count": pending_cnt,
        "total_amount": total_amt,
        "settled_amount": settled_amt,
        "pending_amount": pending_amt
    }

    return render_template("admin/reports.html", active_page="reports", report_data=report_data)


@app.route('/admin/announcements')
@admin_required
def admin_announcements():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, title, message, status, created_at FROM announcements ORDER BY id DESC")
    rows = cursor.fetchall()
    anns = [{
        "id": r[0],
        "title": r[1],
        "message": r[2],
        "status": r[3],
        "created_at": r[4] or ""
    } for r in rows]

    return render_template("admin/announcements.html", active_page="announcements", announcements=anns)


@app.route('/admin/announcements/create', methods=['POST'])
@admin_required
def admin_create_announcement():
    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()
    publish_now = request.form.get('publish_now') == 'true'

    if not title or not message:
        return redirect(url_for('admin_announcements', error="Title and message are required."))

    db = get_db()
    cursor = db.cursor()
    status = 'published' if publish_now else 'draft'
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    cursor.execute("""
    INSERT INTO announcements (title, message, status, created_at, created_by)
    VALUES (%s, %s, %s, %s, %s)
    """, (title, message, status, now_str, session['user_id']))

    db.commit()

    if status == 'published':
        cursor.execute("SELECT id FROM users WHERE is_active IS NULL OR is_active = 1")
        all_users = cursor.fetchall()
        for u in all_users:
            create_notification(cursor, u[0], 'announcement', f"📢 {title}: {message[:60]}...", None)
        db.commit()

    log_admin_action(cursor, session['user_id'], "Created Announcement", "announcement", None, f"Created announcement '{title}' ({status})")
    db.commit()

    return redirect(url_for('admin_announcements', success="Announcement created successfully!"))


@app.route('/admin/announcements/<int:ann_id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_announcement(ann_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT title, status FROM announcements WHERE id = %s", (ann_id,))
    row = cursor.fetchone()
    if row:
        new_status = 'draft' if row[1] == 'published' else 'published'
        cursor.execute("UPDATE announcements SET status = %s WHERE id = %s", (new_status, ann_id))
        
        if new_status == 'published':
            cursor.execute("SELECT id FROM users WHERE is_active IS NULL OR is_active = 1")
            all_users = cursor.fetchall()
            for u in all_users:
                create_notification(cursor, u[0], 'announcement', f"📢 {row[0]}", ann_id)
        
        log_admin_action(cursor, session['user_id'], "Toggled Announcement Status", "announcement", ann_id, f"Set announcement #{ann_id} status to {new_status}")
        db.commit()

    return redirect(url_for('admin_announcements', success="Announcement status updated."))


@app.route('/admin/announcements/<int:ann_id>/delete', methods=['POST'])
@admin_required
def admin_delete_announcement(ann_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM announcements WHERE id = %s", (ann_id,))
    log_admin_action(cursor, session['user_id'], "Deleted Announcement", "announcement", ann_id, f"Deleted announcement #{ann_id}")
    db.commit()
    return redirect(url_for('admin_announcements', success="Announcement deleted."))


@app.route('/admin/support')
@admin_required
def admin_support():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
    SELECT t.id, t.user_id, t.record_id, t.issue_type, t.subject, t.message, t.status, t.admin_response, t.created_at,
           u.username, u.full_name
    FROM support_tickets t
    JOIN users u ON t.user_id = u.id
    ORDER BY t.id DESC
    """)
    rows = cursor.fetchall()
    tickets = [{
        "id": r[0],
        "user_id": r[1],
        "record_id": r[2],
        "issue_type": r[3] or "General",
        "subject": r[4],
        "message": r[5],
        "status": r[6] or "open",
        "admin_response": r[7] or "",
        "created_at": r[8] or "",
        "username": r[9],
        "user_name": r[10] or r[9]
    } for r in rows]

    return render_template("admin/support.html", active_page="support", tickets=tickets)


@app.route('/admin/support/update', methods=['POST'])
@admin_required
def admin_update_support():
    ticket_id = request.form.get('ticket_id')
    status = request.form.get('status', 'open').strip().lower()
    admin_response = request.form.get('admin_response', '').strip()

    db = get_db()
    cursor = db.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    cursor.execute("""
    UPDATE support_tickets
    SET status = %s, admin_response = %s, updated_at = %s
    WHERE id = %s
    """, (status, admin_response, now_str, ticket_id))

    cursor.execute("SELECT user_id, subject FROM support_tickets WHERE id = %s", (ticket_id,))
    t_row = cursor.fetchone()
    if t_row:
        create_notification(cursor, t_row[0], 'support_ticket', f"🛠 Support Ticket Update (#{ticket_id}): {status.upper()} - {admin_response[:50]}", ticket_id)

    log_admin_action(cursor, session['user_id'], "Updated Support Ticket", "support_ticket", ticket_id, f"Updated ticket #{ticket_id} to status {status}")
    db.commit()

    return redirect(url_for('admin_support', success="Support ticket updated successfully."))


@app.route('/admin/audit_logs')
@admin_required
def admin_audit_logs():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
    SELECT l.id, l.admin_id, l.action, l.target_type, l.target_id, l.details, l.created_at,
           u.username
    FROM admin_audit_logs l
    JOIN users u ON l.admin_id = u.id
    ORDER BY l.id DESC LIMIT 200
    """)
    rows = cursor.fetchall()
    logs = [{
        "id": r[0],
        "admin_id": r[1],
        "action": r[2],
        "target_type": r[3] or "",
        "target_id": r[4],
        "details": r[5] or "",
        "created_at": r[6] or "",
        "admin_username": r[7]
    } for r in rows]

    return render_template("admin/audit_logs.html", active_page="audit_logs", audit_logs=logs)


@app.route('/support/submit', methods=['POST'])
def submit_support_ticket():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    issue_type = request.form.get('issue_type', 'General')
    subject = request.form.get('subject', '').strip()
    message = request.form.get('message', '').strip()
    record_id = request.form.get('record_id')

    if not subject or not message:
        return redirect(url_for('index', error="Subject and message are required to submit a report."))

    rec_id_val = int(record_id) if record_id and record_id.isdigit() else None
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO support_tickets (user_id, record_id, issue_type, subject, message, status, created_at)
    VALUES (%s, %s, %s, %s, %s, 'open', %s)
    """, (user_id, rec_id_val, issue_type, subject, message, now_str))

    db.commit()
    return redirect(url_for('index', success="Your report/problem ticket has been submitted to support!"))


# ---------------- LOGOUT ---------------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


if __name__ == "__main__":
    app.run(debug=True, port=5000)