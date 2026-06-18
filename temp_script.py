
import sqlite3, pathlib
p = pathlib.Path('data/dashboard_live.sqlite3')
print('exists=', p.exists())
conn = sqlite3.connect(p)
print('tables=', [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])
print('sessions=', conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
print('events=', conn.execute('SELECT COUNT(*) FROM events').fetchone()[0])
print('pos=', conn.execute('SELECT COUNT(*) FROM pos_transactions').fetchone()[0])
