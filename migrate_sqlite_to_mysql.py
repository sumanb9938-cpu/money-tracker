import sqlite3
import mysql.connector
import os

MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '9938asdf9938')
MYSQL_DB = os.getenv('MYSQL_DB', 'money_tracker')

def migrate():
    # 1. Connect to MySQL server and ensure DB exists
    mconn = mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD
    )
    mcursor = mconn.cursor()
    mcursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DB}")
    mcursor.execute(f"USE {MYSQL_DB}")
    
    # Drop existing tables to ensure clean migration schema
    mcursor.execute("DROP TABLE IF EXISTS money_records")
    mcursor.execute("DROP TABLE IF EXISTS users")
    
    # 2. Create tables in MySQL
    mcursor.execute('''
    CREATE TABLE users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(255),
        password VARCHAR(255),
        is_admin INT DEFAULT 0,
        email VARCHAR(255)
    )
    ''')
    
    mcursor.execute('''
    CREATE TABLE money_records (
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
    mconn.commit()

    # 3. Read data from SQLite database.db
    sqlite_path = 'database.db'
    if not os.path.exists(sqlite_path):
        print("No database.db found for migration.")
        return

    sconn = sqlite3.connect(sqlite_path)
    scursor = sconn.cursor()

    # Migrate users
    scursor.execute("SELECT * FROM users")
    s_users = scursor.fetchall()

    for u in s_users:
        if len(u) == 5:
            mcursor.execute("INSERT INTO users (id, username, password, is_admin, email) VALUES (%s, %s, %s, %s, %s)", u)
        elif len(u) == 3:
            mcursor.execute("INSERT INTO users (id, username, password) VALUES (%s, %s, %s)", u)

    # Migrate records
    scursor.execute("SELECT * FROM money_records")
    s_records = scursor.fetchall()

    for r in s_records:
        mcursor.execute("""
        INSERT INTO money_records 
        (id, serial_no, name, amount, type, date_taken, reason, user_id, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, r)

    mconn.commit()
    print("Migration from SQLite to MySQL completed successfully!")
    sconn.close()
    mconn.close()

if __name__ == '__main__':
    migrate()
