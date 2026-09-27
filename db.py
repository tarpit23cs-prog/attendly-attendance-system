"""
db.py
-----
A very simple helper for connecting to MySQL.

We create a brand-new connection every time get_db_connection()
is called. This is the simplest possible approach for a
beginner-friendly project (no connection pooling, no ORM).
"""

import mysql.connector
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME


def get_db_connection():
    """
    Opens and returns a new connection to the MySQL database.
    Call conn.close() when you are done with it.
    """
    conn = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )
    return conn
