from flask import Flask, render_template, request, redirect, session, Response
import sqlite3
import os
import csv
import io

app = Flask(__name__)
app.secret_key = "secret123"

# ---------------- DATABASE ---------------- #
# Using sqlite3 instead of psycopg2 for easy local testing
conn = sqlite3.connect('database.db', check_same_thread=False)
cursor = conn.cursor()

# ---------------- CREATE TABLES ---------------- #
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS money_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

        cursor.execute("SELECT * FROM users WHERE username=? AND password=?",
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

        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                       (username, password))
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

    cursor.execute("""
    SELECT SUM(amount) FROM money_records 
    WHERE type='given' AND status='pending' AND user_id=?
    """, (user_id,))
    to_claim = cursor.fetchone()[0] or 0

    cursor.execute("""
    SELECT SUM(amount) FROM money_records 
    WHERE type='received' AND status='pending' AND user_id=?
    """, (user_id,))
    to_pay = cursor.fetchone()[0] or 0

    cursor.execute("""
    SELECT name,
    SUM(CASE WHEN type='given' THEN amount ELSE 0 END),
    SUM(CASE WHEN type='received' THEN amount ELSE 0 END)
    FROM money_records
    WHERE user_id=? AND status='pending'
    GROUP BY name
    """, (user_id,))
    people = cursor.fetchall()

    return render_template("index.html",
                           records=records,
                           to_claim=to_claim,
                           to_pay=to_pay,
                           people=people)


# ---------------- ADD ---------------- #
@app.route('/add', methods=['POST'])
def add():
    user_id = session['user_id']

    name = request.form['name']
    amount = request.form['amount']
    type_ = request.form['type']
    date = request.form['date']
    reason = request.form['reason']

    cursor.execute("SELECT COUNT(*) FROM money_records")
    count_row = cursor.fetchone()
    serial_no = (count_row[0] if count_row else 0) + 1

    cursor.execute("""
    INSERT INTO money_records 
    (serial_no, name, amount, type, date_taken, reason, user_id, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (serial_no, name, amount, type_, date, reason, user_id))

    conn.commit()
    return redirect('/')


# ---------------- EDIT ---------------- #
@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit(id):
    if request.method == 'POST':
        name = request.form['name']
        amount = request.form['amount']
        type_ = request.form['type']
        date = request.form['date']
        reason = request.form['reason']

        cursor.execute("""
        UPDATE money_records
        SET name=?, amount=?, type=?, date_taken=?, reason=?
        WHERE id=?
        """, (name, amount, type_, date, reason, id))

        conn.commit()
        return redirect('/')

    cursor.execute("SELECT * FROM money_records WHERE id=?", (id,))
    record = cursor.fetchone()

    return render_template("edit.html", r=record)


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


# ---------------- BULK ACTIONS ---------------- #
@app.route('/bulk_settle', methods=['POST'])
def bulk_settle():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
    
    data = request.get_json()
    ids = data.get('ids', [])
    if ids:
        # Securely parameterize the IN clause
        placeholders = ','.join(['?'] * len(ids))
        cursor.execute(f"UPDATE money_records SET status='paid' WHERE id IN ({placeholders}) AND user_id=?", tuple(ids) + (session['user_id'],))
        conn.commit()
    return {"status": "success"}

@app.route('/bulk_delete', methods=['POST'])
def bulk_delete():
    if 'user_id' not in session:
        return {"status": "error", "message": "Unauthorized"}, 401
        
    data = request.get_json()
    ids = data.get('ids', [])
    if ids:
        placeholders = ','.join(['?'] * len(ids))
        cursor.execute(f"DELETE FROM money_records WHERE id IN ({placeholders}) AND user_id=?", tuple(ids) + (session['user_id'],))
        conn.commit()
    return {"status": "success"}


# ---------------- EXPORT CSV ---------------- #
@app.route('/export/csv')
def export_csv():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    
    cursor.execute("SELECT * FROM money_records WHERE user_id=? ORDER BY date_taken DESC", (user_id,))
    records = cursor.fetchall()
    
    si = io.StringIO()
    cw = csv.writer(si)
    
    cw.writerow(['ID', 'Date', 'Name', 'Detail', 'Type', 'Status', 'Amount'])
    
    for r in records:
        type_str = "Given" if r[4] == 'given' else "Received"
        status_str = "Settled" if r[8] == 'paid' else "Pending"
        cw.writerow([r[0], r[5], r[2], r[6], type_str, status_str, f"{r[3]:.2f}"])
        
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

    # Fetch all records for this specific person
    cursor.execute("SELECT * FROM money_records WHERE user_id=? AND name=? ORDER BY date_taken DESC", (user_id, name))
    records = cursor.fetchall()

    # Get specific aggregations for this person
    cursor.execute("""
    SELECT 
    SUM(CASE WHEN type='given' THEN amount ELSE 0 END),
    SUM(CASE WHEN type='received' THEN amount ELSE 0 END)
    FROM money_records
    WHERE user_id=? AND name=? AND status='pending'
    """, (user_id, name))
    
    res = cursor.fetchone()
    total_claim = res[0] or 0
    total_pay = res[1] or 0
    balance = total_claim - total_pay

    return render_template("person.html",
                           name=name,
                           records=records,
                           total_claim=total_claim,
                           total_pay=total_pay,
                           balance=balance)


# ---------------- LOGOUT ---------------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


if __name__ == "__main__":
    app.run(debug=True, port=5000)