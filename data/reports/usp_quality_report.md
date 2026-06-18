# USP Event Quality Report
- USP quality score: 90/100
- Total events: 292
- Configured cameras covered: 4/4

## Score Breakdown
- schema_validity: 20
- configured_camera_coverage: 15
- entry_exit_balance: 15
- event_catalogue_coverage: 15
- queue_session_sanity: 15
- pos_matching_signal: 2
- inference_risk_control: 3
- staff_signal: 5

## Quality Rates
- entry_exit_balance: 1.0
- low_confidence_rate: 0.1815
- inferred_event_rate: 0.1712
- pos_match_rate: 0.0417

## Event Counts
- BILLING_QUEUE_ABANDON: 21
- BILLING_QUEUE_JOIN: 24
- ENTRY: 40
- EXIT: 40
- REENTRY: 1
- ZONE_DWELL: 13
- ZONE_ENTER: 99
- ZONE_EXIT: 54

## Warnings
- CAM_4 is not configured in the active layout, so that feed is not scored.
- POS match rate is low; clock sync and billing-zone calibration need review.
- Inferred-event share is high; collect/review ground truth before final USP claims.

## Recommendations
- Add a human review/annotation loop for false positives and missed visitors.
- Replace centroid association with ByteTrack or BoT-SORT for crowded/occluded scenes.
- Calibrate every camera from sampled frames and keep zone/layout versions.
- Track observed-only metrics separately from LOW-confidence inferred metrics.
