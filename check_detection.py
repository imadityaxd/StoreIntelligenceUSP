import json
from pathlib import Path
from collections import Counter, defaultdict

lines = [json.loads(l) for l in Path('data/events_phase8_submission.jsonl').read_text().splitlines() if l.strip()]

print('=== DETECTION QUALITY SUMMARY ===')
print(f'Total events: {len(lines)}')
print()

per_cam = defaultdict(Counter)
for e in lines:
    per_cam[e['camera_id']][e['event_type']] += 1

for cam in sorted(per_cam):
    print(cam + ':')
    for et, cnt in sorted(per_cam[cam].items()):
        print('  ' + et.ljust(30) + str(cnt))
    print()

confs = [e['confidence'] for e in lines]
buckets = Counter()
for c in confs:
    if c >= 0.8: buckets['HIGH (>=0.8)'] += 1
    elif c >= 0.6: buckets['MED (0.6-0.8)'] += 1
    elif c >= 0.45: buckets['LOW-MED (0.45-0.6)'] += 1
    else: buckets['LOW (<0.45)'] += 1
print('Confidence distribution:')
for k,v in sorted(buckets.items()):
    print('  ' + k.ljust(20) + str(v))
print()

entries  = sum(1 for e in lines if e['event_type'] == 'ENTRY')
exits    = sum(1 for e in lines if e['event_type'] == 'EXIT')
reentries= sum(1 for e in lines if e['event_type'] == 'REENTRY')
cam4     = sum(1 for e in lines if e['camera_id'] == 'CAM_4')
staff    = sum(1 for e in lines if e['is_staff'])
low_conf = sum(1 for e in lines if isinstance(e.get('metadata'),dict) and e['metadata'].get('data_confidence_flag')=='LOW')

print('Key detection metrics:')
print('  ENTRY events    : ' + str(entries))
print('  EXIT events     : ' + str(exits))
print('  REENTRY events  : ' + str(reentries) + '  <- 0 is a known weakness')
print('  CAM_4 events    : ' + str(cam4) + '  <- 0 is a known weakness')
print('  Low-conf events : ' + str(low_conf) + ' (flagged in metadata, schema valid)')
print('  Staff events    : ' + str(staff))
print()

entry_times = [e['timestamp'] for e in lines if e['event_type']=='ENTRY']
ts_counts = Counter(entry_times)
simultaneous = {ts: cnt for ts, cnt in ts_counts.items() if cnt > 1}
print('Group handling:')
print('  Simultaneous ENTRY same timestamp: ' + str(len(simultaneous)))
print('  Unique ENTRY visitor_ids: ' + str(len(set(e['visitor_id'] for e in lines if e['event_type']=='ENTRY'))))
