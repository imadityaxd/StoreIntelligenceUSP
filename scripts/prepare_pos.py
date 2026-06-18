#prepare_pos.py
import csv, json
from pathlib import Path
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

src = Path('D:/code/purplletech/Brigade_Bangalore_10_April_26 (1)bc6219c.csv')
rows = list(csv.DictReader(src.read_text(encoding='utf-8-sig').splitlines()))

invoices = {}
for row in rows:
    inv = row['invoice_number'].strip()
    if not inv:
        continue
    if inv not in invoices:
        date_str = row['order_date'].strip()
        time_str = row['order_time'].strip()
        dt_ist = datetime.strptime(date_str + ' ' + time_str, '%d-%m-%Y %H:%M:%S').replace(tzinfo=IST)
        dt_utc = dt_ist.astimezone(timezone.utc)
        invoices[inv] = {
            'transaction_id': inv,
            'store_id': 'STORE_BLR_002',
            'timestamp': dt_utc.isoformat(),
            'basket_value_inr': 0.0,
            'metadata': {
                'order_id': row['order_id'].strip(),
                'customer_number': row['customer_number'].strip(),
                'salesperson_id': row['salesperson_id'].strip(),
            }
        }
    try:
        invoices[inv]['basket_value_inr'] += float(row['NMV'] or 0)
    except:
        pass

txns = list(invoices.values())
out = Path('data/pos_transactions.json')
out.write_text(json.dumps(txns, indent=2), encoding='utf-8')
print('Prepared ' + str(len(txns)) + ' transactions')
for t in sorted(txns, key=lambda x: x['timestamp'])[-3:]:
    print('  ' + t['transaction_id'] + ' @ ' + t['timestamp'] + ' = INR ' + str(round(t['basket_value_inr'], 2)))
