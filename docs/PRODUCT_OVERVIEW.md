# Product Overview

## What It Is

Vivid Store AI - CCTV Retail Systems is a focused CCTV analytics product.

It turns retail CCTV footage into:

- visible person detection
- camera-local person IDs
- live store metrics
- zone, queue, funnel, and heatmap signals when calibration supports them
- session-scoped reports

Core product promise:

> CCTV footage -> tracked people -> live store intelligence -> downloadable
> session report.

---

## Who It Is For

This product is intended for:

- retail store operators who need to understand what is happening in-store
- store managers reviewing queue, zone, and physical person movement
- analysts who need session-level CCTV evidence and exported metrics
- technical reviewers validating an end-to-end CCTV intelligence workflow

The first user should not need to understand event schemas, model internals, or
database details to understand the dashboard output.

---

## Problem It Solves

Retail CCTV often contains useful operational signals, but raw video is hard to
review manually.

Vivid Store AI helps answer:

- How many people were detected in this CCTV run?
- Which people moved through the store?
- Did people enter key zones?
- Did people reach the billing queue?
- Was queue activity detected?
- Which zones showed the strongest activity?
- Is the output reliable enough to review?
- Can this run be exported as a report?

---

## How It Works

1. A user selects a saved CCTV source or uploads a new CCTV video.
2. The dashboard creates a new isolated session.
3. YOLO detects people in sampled video frames.
4. A physical-person validation layer suppresses weak, reflection-like, or
   physically implausible tracks.
5. The tracker assigns camera-local person IDs to countable tracks.
6. The pipeline writes event data and overlay data.
7. The dashboard draws boxes and IDs over the video.
8. The analytics API computes session-scoped metrics.
9. The dashboard shows live metrics, events, funnel, heatmap, alerts, and data reliability.
10. The session can be reopened from Session History.
11. The session can be exported as Markdown, CSV, or JSON.

---

## Inputs

Minimum input:

- one CCTV video

With only a video, the product can provide:

- person detection
- bounding boxes
- person IDs
- session report

Stronger input:

- store ID
- camera ID
- camera role
- matching layout/calibration
- optional POS data

With stronger input, the product can provide better:

- zone events
- queue events
- funnel signals
- heatmap signals
- conversion signal

---

## Outputs

Dashboard outputs:

- video with person boxes and IDs
- current-session valid people count
- current-session event count
- queue signal
- conversion signal
- person journey funnel
- zone heatmap
- event feed
- data reliability status
- session history

Report outputs:

- Markdown report
- CSV report
- JSON report

Reports are scoped to one session. A new CCTV run should not silently merge with
old runs.

---

## Reliability Rules

The product should remain honest about uncertainty.

- If only detection is possible, show detection-focused output.
- If calibration is missing, do not fake zone or queue intelligence.
- If POS is missing, do not present conversion as confirmed.
- If confidence is low, mark the signal as lower reliability.
- If a person-like box is likely a reflection or unstable false track, show it
  as suspect and exclude it from metrics.
- If a new video is uploaded, create a new session.

---

## Success Criteria

The product is working when:

- a CCTV source can be selected or uploaded
- a new session is created
- people are detected
- boxes and IDs appear on video
- supported metrics update for that session
- reports export successfully
- Session History can reopen a completed run
- automated tests pass
