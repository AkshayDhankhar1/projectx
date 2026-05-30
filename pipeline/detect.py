"""
detect.py — Main Detection Pipeline

This is the core of Part A (30 points). It processes CCTV video clips
and produces structured behavioural events.

HIGH-LEVEL FLOW:
================
For each video clip:
  1. Open video file with OpenCV
  2. Load YOLOv8n model (nano — smallest, fastest, CPU-friendly)
  3. Initialize ByteTrack tracker
  4. For every Nth frame (skip frames for CPU performance):
     a. Run YOLO detection → bounding boxes for all persons
     b. Feed detections into ByteTrack → persistent track IDs
     c. For each tracked person:
        - Extract appearance (color histogram)
        - Check zone membership (which zone polygon contains the person)
        - Classify as staff or customer
        - Emit appropriate events (ENTRY, EXIT, ZONE_ENTER, DWELL, etc.)
  5. After all frames processed, emit EXIT events for remaining sessions

CONCEPTS YOU NEED TO KNOW:
==========================
- Bounding box: [x1, y1, x2, y2] rectangle around a detected person
- Confidence: 0-1 probability that the detection is correct
- Track ID: persistent integer ID assigned by ByteTrack to each person
- Centroid: center point of bounding box = ((x1+x2)/2, (y1+y2)/2)
- Frame skipping: processing every Nth frame to reduce CPU load
- IoU: Intersection over Union — overlap metric between two boxes

EDGE CASES HANDLED:
===================
- Group entry: ByteTrack naturally separates individuals in groups
- Partial occlusion: ByteTrack's low-conf matching keeps tracks alive
- Empty periods: no detections → no events → API handles gracefully
- Staff: identified by dark clothing + high frame presence
- Re-entry: appearance histogram comparison against recent exits
"""

import os
import sys
import json
import argparse
import cv2
import numpy as np
from datetime import datetime, timezone, timedelta

# Add parent directory to path so we can import from pipeline/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.emit import EventEmitter
from pipeline.tracker import SessionManager, VisitorSession


def load_store_layout(layout_path: str) -> dict:
    """Load store layout configuration from JSON file.
    
    Returns the layout dict which contains camera mappings,
    zone definitions, and entry line coordinates.
    """
    with open(layout_path, "r") as f:
        data = json.load(f)
    # Return the first (and only) store config
    store_id = list(data["stores"].keys())[0]
    return store_id, data["stores"][store_id]


def get_camera_config(layout: dict, video_filename: str) -> tuple:
    """Find which camera ID and zones correspond to a video file.
    
    Matches the video filename (e.g., "CAM 3.mp4") against the
    camera configs in store_layout.json.
    
    Returns: (camera_id, camera_config) or (None, None)
    """
    for cam_id, cam_config in layout["cameras"].items():
        if cam_config["file"] == video_filename:
            return cam_id, cam_config
    return None, None


def get_zone_for_point(zones: dict, x_norm: float, y_norm: float,
                       camera_id: str) -> str | None:
    """Determine which zone a point (normalized 0-1) falls into.
    
    Uses a simple "point in polygon" check. Normalized coordinates
    mean (0,0) = top-left corner, (1,1) = bottom-right corner.
    This makes zone definitions resolution-independent.
    
    Algorithm: Ray casting — count how many times a ray from the
    point crosses polygon edges. Odd count = inside, even = outside.
    """
    for zone_id, zone_config in zones.items():
        if zone_config.get("camera") != camera_id:
            continue
        polygon = zone_config.get("polygon_normalized", [])
        if not polygon:
            continue
        if point_in_polygon(x_norm, y_norm, polygon):
            return zone_id
    return None


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """Check if a point (x, y) is inside a polygon.
    
    Uses the ray casting algorithm:
    - Cast a ray from the point to the right
    - Count how many polygon edges the ray crosses
    - Odd number of crossings = point is inside
    - Even number = point is outside
    
    This works for any simple (non-self-intersecting) polygon.
    """
    n = len(polygon)
    inside = False
    j = n - 1
    
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        
        # Check if ray crosses this edge
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    
    return inside


def calculate_timestamp(video_start_time: datetime, frame_number: int,
                        fps: float) -> str:
    """Calculate the real-world timestamp for a given frame.
    
    Formula: timestamp = video_start_time + (frame_number / fps)
    
    Example: If video starts at 20:10:00 and frame 150 at 30fps:
    timestamp = 20:10:00 + (150/30) = 20:10:05
    """
    seconds_offset = frame_number / fps
    timestamp = video_start_time + timedelta(seconds=seconds_offset)
    return timestamp.isoformat()


def process_video(
    video_path: str,
    store_id: str,
    camera_id: str,
    camera_config: dict,
    layout: dict,
    emitter: EventEmitter,
    video_start_time: datetime,
    frame_skip: int = 5,
    confidence_threshold: float = 0.3,
):
    """Process one video clip and emit detection events.
    
    This is the core processing loop. For each frame:
    1. Run YOLO to detect people
    2. Track them with ByteTrack
    3. Determine zones and emit events
    
    Args:
        video_path: path to the MP4 file
        store_id: store identifier (e.g., "STORE_PRP_001")
        camera_id: camera identifier (e.g., "CAM_ENTRY_01")
        camera_config: camera settings from store_layout.json
        layout: full store layout config
        emitter: EventEmitter instance for writing events
        video_start_time: when the video recording started
        frame_skip: process every Nth frame (5 = ~6fps from 30fps)
        confidence_threshold: minimum confidence to keep a detection
    """
    # Import YOLO and supervision here — they're heavy imports
    from ultralytics import YOLO
    import supervision as sv

    print(f"\n{'='*60}")
    print(f"Processing: {os.path.basename(video_path)}")
    print(f"Camera: {camera_id} ({camera_config.get('type', 'unknown')})")
    print(f"{'='*60}")

    # ----------------------------------------------------------
    # Step 1: Load YOLOv8n model
    # ----------------------------------------------------------
    # YOLOv8n = "nano" version, only 3.2MB, optimized for speed
    # Downloads automatically on first run
    model = YOLO("yolov8n.pt")

    # ----------------------------------------------------------
    # Step 2: Open video file
    # ----------------------------------------------------------
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"Resolution: {frame_width}x{frame_height}")
    print(f"FPS: {fps:.1f}, Frames: {total_frames}")
    print(f"Processing every {frame_skip}th frame (effective {fps/frame_skip:.1f} fps)")

    # ----------------------------------------------------------
    # Step 3: Initialize ByteTrack tracker
    # ----------------------------------------------------------
    # ByteTrack parameters:
    # - track_activation_threshold: min confidence to START a new track
    # - lost_track_buffer: frames to keep a "lost" track before deleting
    # - minimum_matching_threshold: IoU threshold for matching detections
    # - frame_rate: video frame rate (affects motion prediction)
    tracker = sv.ByteTrack(
        track_activation_threshold=0.25,
        lost_track_buffer=30,  # Keep lost tracks for ~1s at 30fps
        minimum_matching_threshold=0.8,
        frame_rate=int(fps),
    )

    # ----------------------------------------------------------
    # Step 4: Initialize session manager
    # ----------------------------------------------------------
    session_mgr = SessionManager(
        reentry_window_minutes=5,
        similarity_threshold=0.7,
    )

    # ----------------------------------------------------------
    # Step 5: Process frames
    # ----------------------------------------------------------
    frame_number = 0
    processed_count = 0
    cam_type = camera_config.get("type", "floor")

    # Determine if this is an entry camera (for ENTRY/EXIT events)
    is_entry_cam = cam_type == "entry_exit"
    is_billing_cam = cam_type == "billing"

    # Get entry line coordinates (for entry camera)
    entry_line = layout.get("entry_line", {})

    # Track billing zone occupancy for queue depth
    billing_occupants = set()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_number += 1

        # Skip frames for performance (process every Nth frame)
        if frame_number % frame_skip != 0:
            continue

        processed_count += 1
        session_mgr.total_frames = processed_count

        # Calculate the real timestamp for this frame
        timestamp = calculate_timestamp(video_start_time, frame_number, fps)

        # ----------------------------------------------------------
        # Run YOLO detection
        # ----------------------------------------------------------
        # classes=[0] filters for "person" only (COCO class 0)
        # imgsz=640: resize to 640px for inference (faster than 1080p)
        # verbose=False: suppress YOLO output for cleaner logs
        results = model(frame, classes=[0], imgsz=640, verbose=False)[0]

        # Convert YOLO results to Supervision Detections format
        detections = sv.Detections.from_ultralytics(results)

        # Filter by confidence threshold
        if len(detections) > 0:
            mask = detections.confidence >= confidence_threshold
            detections = detections[mask]

        # ----------------------------------------------------------
        # Run ByteTrack tracking
        # ----------------------------------------------------------
        # ByteTrack assigns persistent track_ids to each detection.
        # Same person in consecutive frames → same track_id.
        if len(detections) > 0:
            detections = tracker.update_with_detections(detections)

        # ----------------------------------------------------------
        # Process each tracked person
        # ----------------------------------------------------------
        if detections.tracker_id is None:
            continue

        for i in range(len(detections)):
            track_id = int(detections.tracker_id[i])
            bbox = detections.xyxy[i]  # [x1, y1, x2, y2]
            confidence = float(detections.confidence[i])

            # Calculate centroid (center of bounding box)
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2

            # Normalize to 0-1 range (resolution-independent)
            cx_norm = cx / frame_width
            cy_norm = cy / frame_height

            # Get or create session for this track
            session = session_mgr.get_session(track_id)

            if session is None:
                # New person detected!

                # Check for re-entry first
                histogram = session_mgr.extract_appearance_histogram(
                    frame, tuple(bbox)
                )
                reentry_session = session_mgr.check_reentry(
                    histogram, timestamp
                )

                if reentry_session:
                    # This person was here before — REENTRY
                    session = reentry_session
                    session.track_id = track_id
                    session_mgr.active_sessions[track_id] = session
                    session.appearance_histogram = histogram
                    emitter.emit_reentry(
                        camera_id=camera_id,
                        visitor_id=session.visitor_id,
                        timestamp=timestamp,
                        confidence=confidence,
                        is_staff=session.is_staff,
                        session_seq=session.next_seq(),
                    )
                else:
                    # Truly new person — create session
                    session = session_mgr.create_session(
                        track_id, timestamp, confidence
                    )
                    session.appearance_histogram = histogram

                    # Emit ENTRY event (on entry camera only)
                    if is_entry_cam:
                        emitter.emit_entry(
                            camera_id=camera_id,
                            visitor_id=session.visitor_id,
                            timestamp=timestamp,
                            confidence=confidence,
                            session_seq=session.next_seq(),
                        )
                    elif is_billing_cam:
                        # Person appeared at billing counter
                        billing_occupants.add(session.visitor_id)
                        emitter.emit_billing_queue_join(
                            camera_id=camera_id,
                            visitor_id=session.visitor_id,
                            timestamp=timestamp,
                            queue_depth=len(billing_occupants),
                            confidence=confidence,
                            session_seq=session.next_seq(),
                        )
                    else:
                        # Appeared on floor camera — emit as entry too
                        emitter.emit_entry(
                            camera_id=camera_id,
                            visitor_id=session.visitor_id,
                            timestamp=timestamp,
                            confidence=confidence,
                            session_seq=session.next_seq(),
                        )
            else:
                # Existing tracked person — update their session
                session.frame_count += 1
                session.confidence_scores.append(confidence)

                # Update appearance histogram periodically
                if session.frame_count % 10 == 0:
                    histogram = session_mgr.extract_appearance_histogram(
                        frame, tuple(bbox)
                    )
                    if histogram is not None:
                        session.appearance_histogram = histogram

            # ----------------------------------------------------------
            # Zone detection (for floor cameras)
            # ----------------------------------------------------------
            if cam_type in ("floor", "billing"):
                zone = get_zone_for_point(
                    layout.get("zones", {}),
                    cx_norm, cy_norm,
                    camera_id,
                )

                if zone and zone != session.current_zone:
                    # Person entered a new zone
                    if session.current_zone:
                        # First, emit exit from previous zone
                        emitter.emit_zone_exit(
                            camera_id=camera_id,
                            visitor_id=session.visitor_id,
                            timestamp=timestamp,
                            zone_id=session.current_zone,
                            confidence=confidence,
                            is_staff=session.is_staff,
                            session_seq=session.next_seq(),
                        )

                    # Record zone entry
                    session.current_zone = zone
                    session.zone_enter_time = timestamp
                    if zone not in session.zones_visited:
                        session.zones_visited.append(zone)

                    emitter.emit_zone_enter(
                        camera_id=camera_id,
                        visitor_id=session.visitor_id,
                        timestamp=timestamp,
                        zone_id=zone,
                        confidence=confidence,
                        is_staff=session.is_staff,
                        sku_zone=zone,
                        session_seq=session.next_seq(),
                    )

                elif zone and zone == session.current_zone:
                    # Same zone — check for dwell (30+ seconds)
                    if session.zone_enter_time:
                        try:
                            enter_dt = datetime.fromisoformat(session.zone_enter_time)
                            current_dt = datetime.fromisoformat(timestamp)
                            dwell_seconds = (current_dt - enter_dt).total_seconds()
                            dwell_ms = int(dwell_seconds * 1000)

                            # Emit ZONE_DWELL every 30 seconds of continuous dwell
                            if dwell_ms >= 30000 and dwell_ms % 30000 < (frame_skip * 1000 / fps + 500):
                                emitter.emit_zone_dwell(
                                    camera_id=camera_id,
                                    visitor_id=session.visitor_id,
                                    timestamp=timestamp,
                                    zone_id=zone,
                                    dwell_ms=dwell_ms,
                                    confidence=confidence,
                                    is_staff=session.is_staff,
                                    sku_zone=zone,
                                    session_seq=session.next_seq(),
                                )

                                # Track dwell time
                                session.dwell_times[zone] = dwell_ms
                        except (ValueError, TypeError):
                            pass

        # Progress indicator
        if processed_count % 50 == 0:
            pct = (frame_number / total_frames) * 100
            print(f"  Progress: {pct:.0f}% ({processed_count} frames processed)")

    # ----------------------------------------------------------
    # Post-processing: classify staff and emit EXIT events
    # ----------------------------------------------------------
    for track_id, session in list(session_mgr.active_sessions.items()):
        # Classify staff based on accumulated appearance data
        session.is_staff = session_mgr.classify_staff(session)

        # Emit EXIT for remaining active sessions
        final_timestamp = calculate_timestamp(
            video_start_time, frame_number, fps
        )
        emitter.emit_exit(
            camera_id=camera_id,
            visitor_id=session.visitor_id,
            timestamp=final_timestamp,
            confidence=session.avg_confidence(),
            is_staff=session.is_staff,
            session_seq=session.next_seq(),
        )

    cap.release()

    print(f"  Completed: {processed_count} frames processed")
    print(f"  Sessions: {len(session_mgr.active_sessions) + len(session_mgr.recent_exits)} total")
    print(f"  Events emitted: {emitter.get_event_count()}")


def main():
    """Main entry point for the detection pipeline.
    
    Usage:
        python pipeline/detect.py --clips-dir ./Resources/ \\
            --layout ./data/store_layout.json \\
            --output ./data/events.jsonl
    """
    parser = argparse.ArgumentParser(
        description="Store Intelligence Detection Pipeline"
    )
    parser.add_argument(
        "--clips-dir", required=True,
        help="Directory containing CCTV video clips (MP4 files)"
    )
    parser.add_argument(
        "--layout", required=True,
        help="Path to store_layout.json"
    )
    parser.add_argument(
        "--output", required=True,
        help="Output path for events JSONL file"
    )
    parser.add_argument(
        "--frame-skip", type=int, default=5,
        help="Process every Nth frame (default: 5, effective ~6fps)"
    )
    parser.add_argument(
        "--confidence", type=float, default=0.3,
        help="Minimum detection confidence threshold (default: 0.3)"
    )
    args = parser.parse_args()

    # Load store layout
    store_id, layout = load_store_layout(args.layout)
    print(f"Store: {store_id} ({layout.get('store_name', 'Unknown')})")

    # Initialize event emitter
    # Clear existing output file
    if os.path.exists(args.output):
        os.remove(args.output)
    emitter = EventEmitter(args.output, store_id)

    # Video start time — derived from the video file timestamps
    # All our clips are from 10/04/2026 around 20:10 UTC
    video_start_time = datetime(2026, 4, 10, 20, 10, 0, tzinfo=timezone.utc)

    # Process each video clip
    clips_dir = args.clips_dir
    video_files = sorted([
        f for f in os.listdir(clips_dir)
        if f.endswith(".mp4")
    ])

    print(f"\nFound {len(video_files)} video clips")

    for video_file in video_files:
        video_path = os.path.join(clips_dir, video_file)
        camera_id, camera_config = get_camera_config(layout, video_file)

        if camera_id is None:
            print(f"\nSkipping {video_file} — not mapped in store layout")
            continue

        # Skip storage camera (no customer traffic)
        if camera_config.get("type") == "storage":
            print(f"\nSkipping {video_file} — storage camera (no customers)")
            continue

        process_video(
            video_path=video_path,
            store_id=store_id,
            camera_id=camera_id,
            camera_config=camera_config,
            layout=layout,
            emitter=emitter,
            video_start_time=video_start_time,
            frame_skip=args.frame_skip,
            confidence_threshold=args.confidence,
        )

    emitter.close()

    print(f"\n{'='*60}")
    print(f"PIPELINE COMPLETE")
    print(f"Total events: {emitter.get_event_count()}")
    print(f"Output: {args.output}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
