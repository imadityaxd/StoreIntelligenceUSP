from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "docs" / "Computer_Vision_Tools_OpenCV_YOLO_Tracking_Guide.pdf"


BLUE = colors.HexColor("#2E74B5")
DARK_BLUE = colors.HexColor("#1F4D78")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#5E6A78")
LIGHT_BLUE = colors.HexColor("#E8EEF5")
LIGHT_GRAY = colors.HexColor("#F4F6F9")
BORDER = colors.HexColor("#B7C3D0")


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "GuideTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=28,
            textColor=BLUE,
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "GuideSubtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            textColor=MUTED,
            spaceAfter=16,
        ),
        "h1": ParagraphStyle(
            "GuideH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=BLUE,
            spaceBefore=14,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "GuideH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=17,
            textColor=BLUE,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "GuideBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "GuideSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.8,
            leading=12,
            textColor=INK,
        ),
        "table_header": ParagraphStyle(
            "GuideTableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=DARK_BLUE,
        ),
        "callout_title": ParagraphStyle(
            "GuideCalloutTitle",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=DARK_BLUE,
            spaceAfter=3,
        ),
    }


S = styles()


def para(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def bullet_list(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(para(item), leftIndent=12) for item in items],
        bulletType="bullet",
        start="circle",
        leftIndent=18,
        bulletFontName="Helvetica",
        bulletFontSize=7,
        bulletOffsetY=1,
    )


def table(data: list[list[str]], widths: list[float]) -> Table:
    rows = []
    for ridx, row in enumerate(data):
        style = "table_header" if ridx == 0 else "small"
        rows.append([para(cell, style) for cell in row])
    t = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BLUE),
                ("GRID", (0, 0), (-1, -1), 0.45, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


def four_ws(rows: list[tuple[str, str]]) -> Table:
    return table([["Question", "Answer"], *[list(row) for row in rows]], [1.1, 5.1])


def callout(title: str, body: str) -> Table:
    t = Table(
        [[para(title, "callout_title")], [para(body, "small")]],
        colWidths=[6.2 * inch],
        hAlign="LEFT",
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
                ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#D5DCE5")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(inch, 0.45 * inch, "Vivid Store AI reference guide")
    canvas.drawRightString(7.5 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_story() -> list:
    story: list = [
        para("Computer Vision Building Blocks", "title"),
        para("OpenCV, YOLOv8 / Ultralytics, ByteTrack, and BoT-SORT explained for Vivid Store AI", "subtitle"),
        callout(
            "Purpose of this guide",
            "This document explains the three main computer vision layers in the CCTV analytics pipeline. "
            "It answers the four W's for each tool: what it is, why it matters, where it is used, and when it runs.",
        ),
        Spacer(1, 10),
        para("1. The Simple Pipeline", "h1"),
        para(
            "In your project, these tools form a pipeline: OpenCV reads CCTV frames, YOLOv8 finds people in those frames, "
            "ByteTrack or BoT-SORT links detections across time, and the application turns stable tracks into overlays and metrics."
        ),
        table(
            [
                ["Stage", "Tool", "Main job", "Output"],
                ["1", "OpenCV", "Open video or camera stream and extract frames.", "Image frames with width, height, FPS, and timestamp context."],
                ["2", "YOLOv8 / Ultralytics", "Detect people in each sampled frame.", "Person bounding boxes with confidence scores."],
                ["3", "ByteTrack / BoT-SORT", "Connect boxes across frames into identities.", "Track IDs that stay stable while a person remains visible."],
                ["4", "Vivid Store AI pipeline", "Validate physical people, map zones, generate events, and draw overlays.", "Green countable people, suspect boxes, events, metrics, and reports."],
            ],
            [0.45, 1.45, 2.55, 1.75],
        ),
        Spacer(1, 8),
        callout(
            "Memory hook",
            "OpenCV handles frames. YOLO handles detection. ByteTrack and BoT-SORT handle identity over time. The app handles business meaning.",
        ),
        PageBreak(),
    ]

    sections = [
        (
            "2. OpenCV",
            [
                ("What?", "OpenCV is a computer vision library used to read, write, transform, and analyze images and video frames. In this project, it is the frame and pixel handling layer."),
                ("Why?", "YOLO cannot work directly on a video file as a business object. It needs image frames. OpenCV opens the CCTV clip or stream, reads frames, reports FPS and resolution, and gives the detector actual pixel arrays."),
                ("Where?", "OpenCV is used in the detection pipeline, video inspection, frame extraction, camera calibration helpers, and backend-side overlay or reference frame generation."),
                ("When?", "It runs before YOLO for frame extraction, during processing for frame sampling and geometry, and after detection when frames or overlays need to be saved, inspected, or exported."),
            ],
            [
                "Frame: one still image from the video. A 30 FPS video has about 30 frames per second.",
                "FPS: frames per second. This controls timestamp math and how often the system samples the video.",
                "Resolution: width and height of the frame. Detection boxes use these coordinates.",
                "Color format: OpenCV usually reads images as BGR, while many AI/image tools think in RGB.",
                "VideoCapture: the OpenCV object that opens a file, webcam, or stream and reads frames one by one.",
            ],
            "OpenCV is the video and pixel plumbing that feeds the AI model clean frames.",
        ),
        (
            "3. YOLOv8 / Ultralytics",
            [
                ("What?", "YOLOv8 is an object detection model. Ultralytics is the Python package/framework commonly used to run YOLOv8 models. It returns detected objects, including person boxes, class labels, and confidence scores."),
                ("Why?", "The dashboard needs to know where people are in each frame. YOLOv8 provides person bounding boxes more reliably than simple motion detection, especially when shelves, lighting, and background are complex."),
                ("Where?", "YOLOv8 runs inside the detection pipeline after OpenCV extracts a sampled frame. Its output is passed to the tracker and becomes the basis for dashboard boxes."),
                ("When?", "It runs on each sampled frame during a CCTV analysis or live stream session. Sampling is controlled by process FPS to balance speed and accuracy."),
            ],
            [
                "Object detection: finding where an object is, not just saying what is in the image.",
                "Bounding box: the rectangle around a detected person, usually x1, y1, x2, y2.",
                "Class: the object category. This project mainly uses the person class.",
                "Confidence: the model's score for how likely the box is a real object of that class.",
                "IoU and NMS: overlap logic used to remove duplicate boxes and support matching.",
            ],
            "YOLOv8 is the model that says: there is a person-shaped object at this rectangle in this frame.",
        ),
        (
            "4. ByteTrack and BoT-SORT",
            [
                ("What?", "ByteTrack and BoT-SORT are multi-object tracking algorithms. They connect YOLO detections across frames and assign track IDs so one person can keep the same identity while visible."),
                ("Why?", "Without tracking, the system would see the same person as a new detection in every frame. Tracking prevents repeated counting and makes movement, dwell time, queue behavior, and events possible."),
                ("Where?", "They run after YOLO detection and before event generation. Tracker output feeds overlay labels, physical-person validation, zone mapping, and metrics."),
                ("When?", "They run continuously during analysis. Every sampled frame updates existing tracks, creates new tracks, or marks missing tracks as temporarily lost or ended."),
            ],
            [
                "Track ID: a temporary identity assigned to a detected person inside one camera run.",
                "Association: deciding which new box belongs to which existing track.",
                "Motion prediction: estimating where a track should appear in the next frame.",
                "Lost track: a person was visible before but is missing now because of occlusion or detection failure.",
                "Re-identification: using appearance features to reconnect an identity after a short disappearance.",
            ],
            "Tracking turns many frame-by-frame detections into one continuous person path.",
        ),
    ]

    for title, ws_rows, bullets, one_line in sections:
        story.extend([para(title, "h1"), four_ws(ws_rows), Spacer(1, 8), para("Core Ideas", "h2"), bullet_list(bullets), Spacer(1, 8), callout("In one line", one_line), Spacer(1, 10)])

    story.extend(
        [
            para("5. ByteTrack vs BoT-SORT", "h1"),
            table(
                [
                    ["Tracker", "Strength", "Best use", "Tradeoff"],
                    ["ByteTrack", "Uses high-confidence and low-confidence detections to keep tracks alive.", "Good general-purpose tracking when people are visible and movement is moderate.", "Can still switch IDs when occlusion is heavy or camera views are complex."],
                    ["BoT-SORT", "Combines motion, box matching, and stronger association logic; can be better in crowded scenes.", "Billing counters, dense zones, and camera views with partial occlusions.", "Usually heavier and may need more tuning than simpler tracking."],
                ],
                [1.0, 2.0, 2.0, 1.2],
            ),
            para("6. How They Work Together In Vivid Store AI", "h1"),
            bullet_list(
                [
                    "OpenCV opens the CCTV file or stream and extracts frames.",
                    "YOLOv8 detects people in each sampled frame.",
                    "ByteTrack or BoT-SORT links person boxes across frames into track IDs.",
                    "Physical-person validation checks whether a track is stable and plausible enough to count.",
                    "The zone system maps each valid person's footpoint to store zones.",
                    "The event emitter creates entries, zone visits, queue joins, exits, and dwell events when supported.",
                    "The dashboard draws boxes, IDs, metrics, event feed, funnel, heatmap, and reports.",
                ]
            ),
            table(
                [
                    ["Problem you see", "Likely layer", "What to tune or inspect"],
                    ["No boxes on people", "YOLO/OpenCV", "Check video readability, lighting, YOLO confidence, image size, and process FPS."],
                    ["Boxes appear but IDs change", "Tracker", "Try Auto profile, ByteTrack, BoT-SORT, or higher process FPS."],
                    ["Mirror/reflection gets counted", "Validation layer", "Review suspect boxes, confidence, geometry, and physical-person validation strictness."],
                    ["Boxes work but zones are empty", "Layout/calibration", "Check camera role, store layout polygons, and zone mapping."],
                    ["Metrics are zero after a run", "Events/API/session", "Check whether valid tracks generated events and whether dashboard is scoped to the current session."],
                ],
                [1.65, 1.2, 3.35],
            ),
            para("7. Glossary", "h1"),
            table(
                [
                    ["Term", "Meaning"],
                    ["Frame", "A single image from a video."],
                    ["Detection", "A model output saying an object exists at a bounding box."],
                    ["Bounding box", "The rectangle around a detected person."],
                    ["Track", "A sequence of detections linked as the same person over time."],
                    ["Track ID", "The temporary identity assigned to one tracked person."],
                    ["Confidence", "The model's score for a detection."],
                    ["IoU", "A measure of how much two boxes overlap."],
                    ["Occlusion", "When a person is partly or fully hidden by another object or person."],
                    ["Dwell", "How long a person stays in a zone."],
                    ["Footpoint", "The bottom-center point of a box, often used to estimate where a person stands on the floor."],
                ],
                [1.35, 4.85],
            ),
        ]
    )
    return story


def build() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=LETTER,
        rightMargin=0.85 * inch,
        leftMargin=0.85 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.7 * inch,
        title="Computer Vision Tools Guide",
        author="Vivid Store AI",
    )
    doc.build(build_story(), onFirstPage=footer, onLaterPages=footer)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    build()
