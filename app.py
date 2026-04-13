from flask import Flask, render_template, request, redirect, session


app = Flask(__name__)
app.secret_key = "secret123"

# ---------------- DATABASE ---------------- #
import psycopg2
import os

DATABASE_URL = "your_render_db_url_here"

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()


# Create tables


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

conn.commit()

# ---------------- LOGIN ---------------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
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

        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
        conn.commit()

        return redirect('/login')

    return render_template('register.html')


# ---------------- HOME ---------------- #
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    cursor.execute("SELECT * FROM money_records WHERE user_id=?", (user_id,))
    records = cursor.fetchall()

    cursor.execute("SELECT SUM(amount) FROM money_records WHERE type='given' AND status='pending' AND user_id=?", (user_id,))
    to_claim = cursor.fetchone()[0] or 0

    cursor.execute("SELECT SUM(amount) FROM money_records WHERE type='received' AND status='pending' AND user_id=?", (user_id,))
    to_pay = cursor.fetchone()[0] or 0

    return render_template('index.html', records=records, to_claim=to_claim, to_pay=to_pay)


# ---------------- ADD ---------------- #
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
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (serial_no, name, amount, type_, date, reason, user_id))

    conn.commit()
    return redirect('/')


# ---------------- DELETE ---------------- #
@app.route('/delete/<int:id>')
def delete(id):
    cursor.execute("DELETE FROM money_records WHERE id=?", (id,))
    conn.commit()
    return redirect('/')


# ---------------- MARK PAID ---------------- #
@app.route('/mark_paid/<int:id>')
def mark_paid(id):
    cursor.execute("UPDATE money_records SET status='paid' WHERE id=?", (id,))
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