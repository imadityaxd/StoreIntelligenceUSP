# Submission Hardening Report
- Total events: 292
- Legacy type coercions: 0
- Timestamp filled: 0
- Timestamp invalid fixed: 0
- Reentry converted: 0
- v2-inspired EXIT added: 40
- v2-inspired REENTRY added: 1
- v2-inspired billing/POS alignment anchors added: 1
- v2-inspired staff visitors flagged: 2
- Total low-confidence events (metadata flag): 53

## Queue Quality
- Duplicate join flagged LOW: 0
- Orphan abandon flagged LOW: 0
- Rejoin-after-abandon flagged LOW: 1
- Extra abandon flagged LOW: 1
- Conf-gate abandon flagged LOW: 9

## KPI
- Billing joins (BILLING_QUEUE_JOIN): 24
- Billing abandons total (BILLING_QUEUE_ABANDON): 21
- Strict abandons (not LOW): 10
- Strict abandon rate: 0.4166666666666667
- Target <= 0.45: True

## Event Counts (all canonical)
- BILLING_QUEUE_ABANDON: 21
- BILLING_QUEUE_JOIN: 24
- ENTRY: 40
- EXIT: 40
- REENTRY: 1
- ZONE_DWELL: 13
- ZONE_ENTER: 99
- ZONE_EXIT: 54

## Per Camera
### CAM_1
- ZONE_DWELL: 3
- ZONE_ENTER: 21
- ZONE_EXIT: 9

### CAM_2
- ZONE_DWELL: 8
- ZONE_ENTER: 78
- ZONE_EXIT: 45

### CAM_3
- ENTRY: 40
- EXIT: 40
- REENTRY: 1

### CAM_5
- BILLING_QUEUE_ABANDON: 21
- BILLING_QUEUE_JOIN: 24
- ZONE_DWELL: 2
