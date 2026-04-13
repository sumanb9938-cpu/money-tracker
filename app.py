from flask import Flask, render_template, request, redirect, session
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"

# ---------------- DATABASE ---------------- #
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# ---------------- CREATE TABLES ---------------- #
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT,
    password TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS money_records (
    id SERIAL PRIMARY KEY,
    serial_no INT,
    name TEXT,
    amount REAL,
    type TEXT,
    date_taken TEXT,
    reason TEXT,
    user_id INT,
    status TEXT DEFAULT 'pending'
)
''')

conn.commit()


# ---------------- LOGIN ---------------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor.execute(
            "SELECT * FROM users WHERE username=%s AND password=%s",
            (username, password)
        )
        user = cursor.fetchone()

        if user:
            session['user_id'] = user[0]
            return redirect('/')
        else:
            return "Invalid Login"

    return render_template('login.html')


# ---------------- REGISTER ---------------- #
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s)",
            (username, password)
        )
        conn.commit()

        return redirect('/login')

    return render_template('register.html')


# ---------------- HOME (DASHBOARD) ---------------- #
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    # ALL RECORDS
    cursor.execute("SELECT * FROM money_records WHERE user_id=%s", (user_id,))
    records = cursor.fetchall()

    # TO CLAIM
    cursor.execute("""
    SELECT SUM(amount) FROM money_records 
    WHERE type='given' AND status='pending' AND user_id=%s
    """, (user_id,))
    to_claim = cursor.fetchone()[0] or 0

    # TO PAY
    cursor.execute("""
    SELECT SUM(amount) FROM money_records 
    WHERE type='received' AND status='pending' AND user_id=%s
    """, (user_id,))
    to_pay = cursor.fetchone()[0] or 0

    # PEOPLE SUMMARY (Splitwise logic)
    cursor.execute("""
    SELECT name,
    SUM(CASE WHEN type='given' THEN amount ELSE 0 END) as given_total,
    SUM(CASE WHEN type='received' THEN amount ELSE 0 END) as received_total
    FROM money_records
    WHERE user_id=%s AND status='pending'
    GROUP BY name
    """, (user_id,))

    people = cursor.fetchall()

    return render_template(
        "index.html",
        records=records,
        to_claim=to_claim,
        to_pay=to_pay,
        people=people
    )


# ---------------- ADD RECORD ---------------- #
@app.route('/add', methods=['POST'])
def add():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    name = request.form['name']
    amount = request.form['amount']
    type_ = request.form['type']
    date = request.form['date']
    reason = request.form['reason']

    cursor.execute("SELECT COUNT(*) FROM money_records")
    serial_no = cursor.fetchone()[0] + 1

    cursor.execute("""
        INSERT INTO money_records 
        (serial_no, name, amount, type, date_taken, reason, user_id, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
    """, (serial_no, name, amount, type_, date, reason, user_id))

    conn.commit()
    return redirect('/')


# ---------------- DELETE ---------------- #
@app.route('/delete/<int:id>')
def delete(id):
    cursor.execute("DELETE FROM money_records WHERE id=%s", (id,))
    conn.commit()
    return redirect('/')


# ---------------- MARK AS PAID ---------------- #
@app.route('/mark_paid/<int:id>')
def mark_paid(id):
    cursor.execute("UPDATE money_records SET status='paid' WHERE id=%s", (id,))
    conn.commit()
    return redirect('/')


# ---------------- LOGOUT ---------------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ---------------- RUN ---------------- #
if __name__ == "__main__":
    app.run(debug=True)