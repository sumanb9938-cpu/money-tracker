from flask import Flask, render_template, request, redirect, session, flash, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, case
import os

app = Flask(__name__)
app.secret_key = "secret123"

# ---------------- DATABASE CONFIG ---------------- #
# Using a new database file for the new schema
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database_v2.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------------- MODELS ---------------- #
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

class MoneyRecord(db.Model):
    __tablename__ = 'money_records'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    serial_no = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    paid_amount = db.Column(db.Float, default=0.0)
    type = db.Column(db.String(50), nullable=False) # 'given' or 'received'
    date_taken = db.Column(db.String(50), nullable=False)
    reason = db.Column(db.String(300), nullable=True)
    user_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), default='pending') # 'pending', 'paid'

with app.app_context():
    db.create_all()

# ---------------- LOGIN ---------------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            flash("Welcome back!", "success")
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

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return render_template('register.html', error="Username already exists")

        new_user = User(
            username=username,
            password_hash=generate_password_hash(password)
        )
        db.session.add(new_user)
        db.session.commit()
        
        flash("Account created! Please sign in.", "success")
        return redirect('/login')

    return render_template('register.html')


# ---------------- HOME ---------------- #
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    records = MoneyRecord.query.filter_by(user_id=user_id).all()

    # To claim = sum(amount - paid_amount) where type=given
    to_claim = db.session.query(func.sum(MoneyRecord.amount - MoneyRecord.paid_amount)).filter_by(
        type='given', status='pending', user_id=user_id
    ).scalar() or 0

    to_pay = db.session.query(func.sum(MoneyRecord.amount - MoneyRecord.paid_amount)).filter_by(
        type='received', status='pending', user_id=user_id
    ).scalar() or 0

    people = db.session.query(
        MoneyRecord.name,
        func.sum(case((MoneyRecord.type == 'given', MoneyRecord.amount - MoneyRecord.paid_amount), else_=0)),
        func.sum(case((MoneyRecord.type == 'received', MoneyRecord.amount - MoneyRecord.paid_amount), else_=0))
    ).filter_by(user_id=user_id, status='pending').group_by(MoneyRecord.name).all()

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
    amount = float(request.form['amount'])
    type_ = request.form['type']
    date = request.form['date']
    reason = request.form['reason']

    serial_no = MoneyRecord.query.count() + 1

    new_record = MoneyRecord(
        serial_no=serial_no,
        name=name,
        amount=amount,
        type=type_,
        date_taken=date,
        reason=reason,
        user_id=user_id
    )

    db.session.add(new_record)
    db.session.commit()
    
    flash("Record added successfully!", "success")
    return redirect(request.referrer or '/')


# ---------------- EDIT ---------------- #
@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit(id):
    if 'user_id' not in session:
        return redirect('/login')
        
    record = MoneyRecord.query.filter_by(id=id, user_id=session['user_id']).first()
    if not record:
        flash("Record not found or permission denied.", "error")
        return redirect('/')

    if request.method == 'POST':
        record.name = request.form['name']
        record.amount = float(request.form['amount'])
        record.type = request.form['type']
        record.date_taken = request.form['date']
        record.reason = request.form['reason']

        db.session.commit()
        flash("Record updated successfully!", "success")
        return redirect('/')

    return render_template("edit.html", r=record)


# ---------------- DELETE ---------------- #
@app.route('/delete/<int:id>')
def delete(id):
    if 'user_id' not in session:
        return redirect('/login')

    record = MoneyRecord.query.filter_by(id=id, user_id=session['user_id']).first()
    if record:
        db.session.delete(record)
        db.session.commit()
        flash("Record deleted safely.", "success")
    else:
        flash("Permission denied.", "error")
        
    return redirect(request.referrer or '/')


# ---------------- MARK PAID ---------------- #
@app.route('/mark_paid/<int:id>')
def mark_paid(id):
    if 'user_id' not in session:
        return redirect('/login')

    record = MoneyRecord.query.filter_by(id=id, user_id=session['user_id']).first()
    if record:
        record.status = 'paid'
        record.paid_amount = record.amount
        db.session.commit()
        flash("Record marked as completely paid!", "success")
    
    return redirect(request.referrer or '/')

# ---------------- PARTIAL PAY ---------------- #
@app.route('/partial_pay/<int:id>', methods=['POST'])
def partial_pay(id):
    if 'user_id' not in session:
        return redirect('/login')

    record = MoneyRecord.query.filter_by(id=id, user_id=session['user_id']).first()
    if record:
        pay_amount = float(request.form.get('pay_amount', 0))
        if pay_amount > 0:
            record.paid_amount += pay_amount
            if record.paid_amount >= record.amount:
                record.status = 'paid'
                record.paid_amount = record.amount
                flash("Record fully paid!", "success")
            else:
                flash(f"Partial payment of ₹{pay_amount} recorded!", "success")
            db.session.commit()
            
    return redirect(request.referrer or '/')


# ---------------- PERSON DETAIL ---------------- #
@app.route('/person/<string:name>')
def person_detail(name):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    records = MoneyRecord.query.filter_by(user_id=user_id, name=name).order_by(MoneyRecord.date_taken.desc()).all()

    total_claim = db.session.query(func.sum(MoneyRecord.amount - MoneyRecord.paid_amount)).filter_by(
        user_id=user_id, name=name, type='given', status='pending'
    ).scalar() or 0

    total_pay = db.session.query(func.sum(MoneyRecord.amount - MoneyRecord.paid_amount)).filter_by(
        user_id=user_id, name=name, type='received', status='pending'
    ).scalar() or 0

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
    flash("You have been logged out.", "success")
    return redirect('/login')


if __name__ == "__main__":
    app.run(debug=True, port=5000)