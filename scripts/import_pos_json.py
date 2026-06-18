#import_pos_json.py
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path('.')))
from app.database import StoreDatabase

db = StoreDatabase(Path('data/store_intelligence.sqlite3'))
db.initialize()

txns = json.loads(Path('data/pos_transactions.json').read_text())
db.upsert_pos_transactions(txns)
print('Imported ' + str(len(txns)) + ' POS transactions')

# Verify
pos = db.fetch_pos_transactions('STORE_BLR_002')
print('Fetched back: ' + str(len(pos)) + ' transactions from DB')
