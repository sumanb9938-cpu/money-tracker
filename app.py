from flask import Flask, render_template, request, redirect, session, Response, g
import mysql.connector
import os
import csv
import io

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "secret123")

# ---------------- MYSQL DATABASE CONFIG ---------------- #
MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '9938asdf9938')
MYSQL_DB = os.getenv('MYSQL_DB', 'money_tracker')

def get_db():
    if 'db' not in g:
        g.db = mysql.connector.connect(
            host=MYSQL_HOST,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DB
        )
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None and db.is_connected():
        db.close()

# ---------------- INITIALIZE DATABASE TABLES ---------------- #
def init_db():
    # Connect to MySQL server to ensure database exists
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD
    )
    cursor = conn.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DB}")
    conn.commit()
    cursor.close()
    conn.close()

    # Connect to specific database to ensure tables exist
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB
    )
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(255),
        password VARCHAR(255),
        is_admin INT DEFAULT 0,
        email VARCHAR(255)
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
        status VARCHAR(50) DEFAULT 'pending'
    )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

init_db()


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

        return redirect('/login')

    return render_template('register.html')


# ---------------- HOME ---------------- #
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM money_records WHERE user_id=%s", (user_id,))
    records = cursor.fetchall()

    cursor.execute("""
    SELECT SUM(amount) FROM money_records 
    WHERE type='given' AND status='pending' AND user_id=%s
    """, (user_id,))
    res_claim = cursor.fetchone()
    to_claim = (res_claim[0] if res_claim and res_claim[0] is not None else 0)

    cursor.execute("""
    SELECT SUM(amount) FROM money_records 
    WHERE type='received' AND status='pending' AND user_id=%s
    """, (user_id,))
    res_pay = cursor.fetchone()
    to_pay = (res_pay[0] if res_pay and res_pay[0] is not None else 0)

    cursor.execute("""
    SELECT name,
    SUM(CASE WHEN type='given' THEN amount ELSE 0 END),
    SUM(CASE WHEN type='received' THEN amount ELSE 0 END)
    FROM money_records
    WHERE user_id=%s AND status='pending'
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
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    name = request.form['name']
    amount = request.form['amount']
    type_ = request.form['type']
    date = request.form['date']
    reason = request.form['reason']

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM money_records")
    count_row = cursor.fetchone()
    serial_no = (count_row[0] if count_row else 0) + 1

    cursor.execute("""
    INSERT INTO money_records 
    (serial_no, name, amount, type, date_taken, reason, user_id, status)
    VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
    """, (serial_no, name, amount, type_, date, reason, user_id))

    db.commit()
    return redirect('/')


# ---------------- EDIT ---------------- #
@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit(id):
    if 'user_id' not in session:
        return redirect('/login')

    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        name = request.form['name']
        amount = request.form['amount']
        type_ = request.form['type']
        date = request.form['date']
        reason = request.form['reason']

        cursor.execute("""
        UPDATE money_records
        SET name=%s, amount=%s, type=%s, date_taken=%s, reason=%s
        WHERE id=%s AND user_id=%s
        """, (name, amount, type_, date, reason, id, session['user_id']))

        db.commit()
        return redirect('/')

    cursor.execute("SELECT * FROM money_records WHERE id=%s AND user_id=%s", (id, session['user_id']))
    record = cursor.fetchone()

    return render_template("edit.html", r=record)


# ---------------- DELETE ---------------- #
@app.route('/delete/<int:id>')
def delete(id):
    if 'user_id' not in session:
        return redirect('/login')

    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM money_records WHERE id=%s AND user_id=%s", (id, session['user_id']))
    db.commit()
    return redirect('/')


# ---------------- MARK PAID ---------------- #
@app.route('/mark_paid/<int:id>')
def mark_paid(id):
    if 'user_id' not in session:
        return redirect('/login')

    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE money_records SET status='paid' WHERE id=%s AND user_id=%s", (id, session['user_id']))
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
        cursor.execute(f"UPDATE money_records SET status='paid' WHERE id IN ({placeholders}) AND user_id=%s", tuple(ids) + (session['user_id'],))
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
        cursor.execute(f"DELETE FROM money_records WHERE id IN ({placeholders}) AND user_id=%s", tuple(ids) + (session['user_id'],))
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
    cursor.execute("SELECT * FROM money_records WHERE user_id=%s ORDER BY date_taken DESC", (user_id,))
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
    db = get_db()
    cursor = db.cursor()

    # Fetch all records for this specific person
    cursor.execute("SELECT * FROM money_records WHERE user_id=%s AND name=%s ORDER BY date_taken DESC", (user_id, name))
    records = cursor.fetchall()

    # Get specific aggregations for this person
    cursor.execute("""
    SELECT 
    SUM(CASE WHEN type='given' THEN amount ELSE 0 END),
    SUM(CASE WHEN type='received' THEN amount ELSE 0 END)
    FROM money_records
    WHERE user_id=%s AND name=%s AND status='pending'
    """, (user_id, name))
    
    res = cursor.fetchone()
    total_claim = (res[0] if res and res[0] is not None else 0)
    total_pay = (res[1] if res and res[1] is not None else 0)
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