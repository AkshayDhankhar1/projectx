"""
tracker.py — Visitor Session Management and Re-Identification

This module manages visitor "sessions" — tracking each person from
when they enter the store until they exit. It handles:

1. SESSION CREATION: When ByteTrack detects a new person, we create
   a VisitorSession with a unique visitor_id like "VIS_a1b2c3"

2. ZONE TRACKING: As the person moves between zones, we update their
   session with zone enter/exit events and dwell times

3. STAFF DETECTION: We classify people as staff or customer based on:
   - Color analysis: staff wear all-black uniforms
   - Dwell pattern: staff are visible for most of the clip

4. RE-IDENTIFICATION: When someone exits and a similar person enters
   soon after, we check if it's the same person re-entering:
   - Compare color histograms (appearance similarity)
   - If cosine similarity > threshold → REENTRY (same visitor_id)
   - Otherwise → new visitor session

Key data structures:
- VisitorSession: tracks one person's journey through the store
- SessionManager: manages all active and recent sessions
"""

import hashlib
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VisitorSession:
    """Tracks one person's journey through the store.
    
    From ENTRY to EXIT, this object accumulates all data about
    the visitor: which zones they visited, how long they stayed,
    and their appearance features for re-identification.
    """
    visitor_id: str           # Unique ID like "VIS_a1b2c3"
    track_id: int             # ByteTrack's internal tracking ID
    entry_time: str           # ISO timestamp when they entered
    is_staff: bool = False    # True if classified as store staff
    is_active: bool = True    # False after EXIT event
    
    # Zone tracking
    current_zone: Optional[str] = None     # Which zone they're currently in
    zone_enter_time: Optional[str] = None  # When they entered current zone
    zones_visited: list = field(default_factory=list)  # History of zones
    
    # Dwell time tracking (zone_id → total ms)
    dwell_times: dict = field(default_factory=dict)
    
    # Event ordering
    session_seq: int = 0  # Incremented for each event in this session
    
    # Appearance features for re-identification
    # This is a color histogram of the person's clothing
    appearance_histogram: Optional[np.ndarray] = None
    
    # Detection confidence scores (to compute average)
    confidence_scores: list = field(default_factory=list)
    
    # Frame count — how many frames this person appears in
    frame_count: int = 0
    
    def next_seq(self) -> int:
        """Get the next event sequence number for this visitor."""
        self.session_seq += 1
        return self.session_seq
    
    def avg_confidence(self) -> float:
        """Average detection confidence across all frames."""
        if not self.confidence_scores:
            return 0.0
        return sum(self.confidence_scores) / len(self.confidence_scores)


def generate_visitor_id(track_id: int, timestamp: str) -> str:
    """Generate a unique visitor ID from track ID and timestamp.
    
    Format: VIS_<6-char hex hash>
    The hash ensures uniqueness while keeping IDs short and readable.
    
    Example: VIS_c8a2f1
    """
    raw = f"{track_id}_{timestamp}"
    hash_hex = hashlib.md5(raw.encode()).hexdigest()[:6]
    return f"VIS_{hash_hex}"


class SessionManager:
    """Manages all visitor sessions — active, exited, and re-entries.
    
    This is the central brain of the tracking system. It:
    - Creates new sessions when a person is first detected
    - Updates sessions as people move between zones
    - Detects re-entries by comparing appearances
    - Classifies staff based on appearance and dwell patterns
    """

    def __init__(self, reentry_window_minutes: int = 5, 
                 similarity_threshold: float = 0.7):
        # Active sessions (track_id → VisitorSession)
        self.active_sessions: dict[int, VisitorSession] = {}
        
        # Recently exited sessions (for re-entry detection)
        # Kept for reentry_window_minutes after exit
        self.recent_exits: list[VisitorSession] = []
        
        # Re-entry matching parameters
        self.reentry_window_minutes = reentry_window_minutes
        self.similarity_threshold = similarity_threshold
        
        # Total frame count (for staff detection percentage)
        self.total_frames: int = 0

    def create_session(self, track_id: int, timestamp: str, 
                       confidence: float) -> VisitorSession:
        """Create a new visitor session for a newly detected person.
        
        Called when ByteTrack assigns a new track_id that we haven't
        seen before.
        """
        visitor_id = generate_visitor_id(track_id, timestamp)
        session = VisitorSession(
            visitor_id=visitor_id,
            track_id=track_id,
            entry_time=timestamp,
        )
        session.confidence_scores.append(confidence)
        self.active_sessions[track_id] = session
        return session

    def get_session(self, track_id: int) -> Optional[VisitorSession]:
        """Get the session for a given ByteTrack track_id."""
        return self.active_sessions.get(track_id)

    def end_session(self, track_id: int) -> Optional[VisitorSession]:
        """End a session when a visitor exits. Move to recent_exits."""
        session = self.active_sessions.pop(track_id, None)
        if session:
            session.is_active = False
            self.recent_exits.append(session)
        return session

    def check_reentry(self, histogram: np.ndarray, 
                      timestamp: str) -> Optional[VisitorSession]:
        """Check if a new detection matches a recently exited visitor.
        
        Re-identification logic:
        1. Compare the new person's color histogram against each
           recently exited session's histogram
        2. Use cosine similarity: sim = dot(a,b) / (|a| × |b|)
           - 1.0 = identical appearance
           - 0.0 = completely different
        3. If sim > threshold (0.7) → same person, return their session
        
        This catches the common retail scenario: customer steps outside
        to take a phone call and comes back in 2 minutes later.
        """
        if histogram is None or len(self.recent_exits) == 0:
            return None

        best_match = None
        best_similarity = 0.0

        for session in self.recent_exits:
            if session.appearance_histogram is None:
                continue
            
            # Compute cosine similarity between histograms
            similarity = self._cosine_similarity(
                histogram, session.appearance_histogram
            )
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = session

        if best_similarity >= self.similarity_threshold and best_match:
            # Remove from recent exits (they're re-entering)
            self.recent_exits.remove(best_match)
            # Reactivate the session
            best_match.is_active = True
            self.active_sessions[best_match.track_id] = best_match
            return best_match

        return None

    def classify_staff(self, session: VisitorSession) -> bool:
        """Classify whether a visitor session belongs to store staff.
        
        Staff detection heuristics (combined scoring):
        
        1. APPEARANCE: Staff wear all-black uniforms.
           We check if the color histogram has high values in the
           dark/low-brightness range (HSV Value channel < 50).
        
        2. DWELL PATTERN: Staff are present for most of the video.
           If a person appears in >60% of total frames → likely staff.
        
        Both heuristics must agree for a staff classification,
        reducing false positives.
        """
        is_dark_clothing = False
        is_persistent = False

        # Check 1: Dark clothing (histogram analysis)
        if session.appearance_histogram is not None:
            hist = session.appearance_histogram
            # If more than 50% of the histogram is in dark bins → dark clothing
            if len(hist) > 0:
                dark_bins = hist[:len(hist)//4]  # Bottom quarter = darkest
                dark_ratio = np.sum(dark_bins) / (np.sum(hist) + 1e-6)
                is_dark_clothing = dark_ratio > 0.5

        # Check 2: Persistent presence (>60% of frames)
        if self.total_frames > 0:
            presence_ratio = session.frame_count / self.total_frames
            is_persistent = presence_ratio > 0.6

        # Both must agree for staff classification
        return is_dark_clothing and is_persistent

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors.
        
        Cosine similarity measures the angle between two vectors:
        - 1.0 = vectors point in the same direction (identical)
        - 0.0 = vectors are perpendicular (unrelated)
        - -1.0 = vectors point in opposite directions
        
        Formula: cos(θ) = (a · b) / (|a| × |b|)
        
        We use this to compare color histograms: if two people
        wear similar clothes, their histograms will be similar.
        """
        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return float(dot_product / (norm_a * norm_b))

    def extract_appearance_histogram(self, frame: np.ndarray, 
                                      bbox: tuple) -> np.ndarray:
        """Extract a color histogram from a person's bounding box.
        
        Process:
        1. Crop the frame to the bounding box area
        2. Take only the upper half (torso — more distinctive than legs)
        3. Convert from BGR to HSV color space
           - H (Hue): the color itself (red, blue, green, etc.)
           - S (Saturation): how vivid the color is
           - V (Value): brightness (dark to light)
        4. Compute a histogram of the H and S channels
        5. Normalize to unit length (for cosine similarity)
        
        HSV is better than RGB for this because it separates color
        from brightness. This makes matching robust to lighting changes.
        """
        import cv2
        
        x1, y1, x2, y2 = map(int, bbox)
        
        # Ensure coordinates are within frame bounds
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        if x2 <= x1 or y2 <= y1:
            return np.zeros(32)
        
        # Crop to bounding box
        crop = frame[y1:y2, x1:x2]
        
        # Take upper half (torso region — more distinctive)
        mid_y = crop.shape[0] // 2
        torso = crop[:mid_y, :]
        
        if torso.size == 0:
            return np.zeros(32)
        
        # Convert BGR → HSV
        hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
        
        # Compute histogram on Hue (16 bins) and Saturation (16 bins)
        hist = cv2.calcHist(
            [hsv], [0, 1], None,
            [16, 16],  # 16 bins for H, 16 for S
            [0, 180, 0, 256]  # H range: 0-180, S range: 0-256
        )
        
        # Flatten to 1D vector (16×16 = 256 values)
        hist = hist.flatten()
        
        # Normalize to unit length for cosine similarity
        norm = np.linalg.norm(hist)
        if norm > 0:
            hist = hist / norm
        
        return hist
