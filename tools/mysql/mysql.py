# 连接mysql数据库，执行查询操作

import mysql.connector
from agents import function_tool
@function_tool
def connect_mysql(host: str, port: int, user: str, password: str, database: str):
    conn = mysql.connector.connect(host=host, port=port, user=user, password=password, database=database)
    return conn

@function_tool
def query_mysql(conn: mysql.connector.connection.MySQLConnection, query: str):
    cursor = conn.cursor()
    cursor.execute(query)
    return cursor.fetchall()