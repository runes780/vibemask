"""
Vault storage for mapping data.
Stores mappings outside the project directory with encryption.
"""

import sqlite3
import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class MappingRecord:
    """A single entity mapping record."""
    entity_id: str
    project_id: str
    entity_type: str
    original_text: str
    masked_text: str
    created_at: str
    last_seen_at: str
    source: str
    confidence: float


@dataclass
class Session:
    """A masking session for tracking operations."""
    session_id: str
    project_id: str
    created_at: str
    status: str  # "pending" | "ai_completed" | "restored" | "failed"
    input_files: List[str]
    output_files: List[str]
    mappings: Dict[str, str]  # masked -> original
    stats: Dict[str, int]  # entity type counts


def get_vibemask_home() -> Path:
    """Get VibeMask home directory (outside project)."""
    if os.name == 'nt':
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home()
    
    return base / ".vibemask"


def get_project_fingerprint(project_path: str) -> str:
    """Generate unique fingerprint for a project."""
    # Normalize path
    normalized = os.path.abspath(project_path)
    
    # Add user info for uniqueness
    user = os.environ.get("USER", os.environ.get("USERNAME", "default"))
    
    # Create hash
    content = f"{normalized}:{user}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_vault_path(project_path: str) -> Path:
    """Get vault path for a specific project."""
    fingerprint = get_project_fingerprint(project_path)
    return get_vibemask_home() / "projects" / fingerprint / "vault.sqlite"


class VaultStorage:
    """
    SQLite-based storage for entity mappings.
    
    Features:
    - Stored outside project directory
    - Stable mappings (same entity -> same mask)
    - Session tracking for restoration
    """
    
    def __init__(self, project_path: str):
        self.project_path = project_path
        self.project_id = get_project_fingerprint(project_path)
        self.vault_path = get_vault_path(project_path)
        
        # Ensure directory exists
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.vault_path)
        
        # Mappings table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS mappings (
                entity_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                original_text TEXT NOT NULL,
                masked_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source TEXT,
                confidence REAL
            )
        ''')
        
        # Sessions table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                input_files TEXT,
                output_files TEXT,
                mappings TEXT,
                stats TEXT
            )
        ''')
        
        # Indexes
        conn.execute('CREATE INDEX IF NOT EXISTS idx_mappings_project ON mappings(project_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_mappings_original ON mappings(original_text, entity_type)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)')
        
        conn.commit()
        conn.close()
    
    def get_or_create_mapping(
        self,
        original: str,
        entity_type: str,
        masked: str,
        source: str = "unknown",
        confidence: float = 1.0
    ) -> str:
        """
        Get existing mapping or create new one.
        Ensures stable mappings within a project.
        """
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        # Check for existing mapping
        cursor.execute('''
            SELECT masked_text, entity_id FROM mappings
            WHERE project_id = ? AND original_text = ? AND entity_type = ?
        ''', (self.project_id, original, entity_type))
        
        row = cursor.fetchone()
        
        if row:
            # Update last_seen_at
            cursor.execute('''
                UPDATE mappings SET last_seen_at = ? WHERE entity_id = ?
            ''', (datetime.now().isoformat(), row[1]))
            conn.commit()
            conn.close()
            return row[0]
        
        def tweak_mask(value: str, attempt: int) -> str:
            """Make a same-length variant of a masked value to avoid collisions."""
            if not value:
                return value

            chars = list(value)

            # Prefer tweaking digits while keeping separators intact (phones, IDs, counters).
            digit_positions = [i for i, ch in enumerate(chars) if ch.isdigit()]
            if digit_positions:
                k = min(4, len(digit_positions))
                suffix = f"{attempt:0{k}d}"[-k:]
                for j in range(k):
                    chars[digit_positions[-k + j]] = suffix[j]
                return "".join(chars)

            # CJK-friendly tweak for names/addresses.
            cjk_positions = [i for i, ch in enumerate(chars) if "\u4e00" <= ch <= "\u9fff"]
            if cjk_positions:
                pool = list("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥")
                k = min(2, len(cjk_positions))
                for j in range(k):
                    chars[cjk_positions[-1 - j]] = pool[(attempt + j) % len(pool)]
                return "".join(chars)

            # Generic: overwrite last chars with base36 counter.
            alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

            def to_base36(n: int) -> str:
                if n == 0:
                    return "0"
                out = []
                while n > 0:
                    n, r = divmod(n, 36)
                    out.append(alphabet[r])
                return "".join(reversed(out))

            token = to_base36(attempt)
            k = min(len(chars), len(token))
            chars[-k:] = list(token[-k:])
            return "".join(chars)

        # Create new mapping
        entity_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # Ensure masked_text is unique within project to guarantee lossless restoration.
        candidate_masked = masked
        attempt = 1
        while True:
            cursor.execute(
                '''
                SELECT 1 FROM mappings
                WHERE project_id = ? AND masked_text = ?
                LIMIT 1
                ''',
                (self.project_id, candidate_masked),
            )
            if cursor.fetchone() is None:
                break
            attempt += 1
            candidate_masked = tweak_mask(masked, attempt)

        cursor.execute('''
            INSERT INTO mappings 
            (entity_id, project_id, entity_type, original_text, masked_text, 
             created_at, last_seen_at, source, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (entity_id, self.project_id, entity_type, original, candidate_masked, 
              now, now, source, confidence))
        
        conn.commit()
        conn.close()
        return candidate_masked
    
    def get_mapping_by_masked(self, masked: str) -> Optional[str]:
        """Get original text for a masked value."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT original_text FROM mappings
            WHERE project_id = ? AND masked_text = ?
        ''', (self.project_id, masked))
        
        row = cursor.fetchone()
        conn.close()
        
        return row[0] if row else None
    
    def get_all_mappings(self) -> Dict[str, str]:
        """Get all mappings for the project {masked -> original}."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT masked_text, original_text FROM mappings
            WHERE project_id = ?
        ''', (self.project_id,))
        
        mappings = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        
        return mappings
    
    # ========== Session Management ==========
    
    def create_session(
        self,
        input_files: List[str],
        mappings: Dict[str, str],
        stats: Dict[str, int],
        output_files: Optional[List[str]] = None,
        status: str = "pending",
    ) -> str:
        """Create a new masking session."""
        session_id = str(uuid.uuid4())[:8]  # Short ID for display
        now = datetime.now().isoformat()
        if output_files is None:
            output_files = []
        
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO sessions
            (session_id, project_id, created_at, status, input_files, output_files, mappings, stats)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id,
            self.project_id,
            now,
            status,
            json.dumps(input_files),
            json.dumps(output_files),
            json.dumps(mappings),
            json.dumps(stats)
        ))
        
        conn.commit()
        conn.close()
        
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get session by ID."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT session_id, project_id, created_at, status, 
                   input_files, output_files, mappings, stats
            FROM sessions WHERE session_id = ?
        ''', (session_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return Session(
            session_id=row[0],
            project_id=row[1],
            created_at=row[2],
            status=row[3],
            input_files=json.loads(row[4]),
            output_files=json.loads(row[5]),
            mappings=json.loads(row[6]),
            stats=json.loads(row[7])
        )
    
    def list_sessions(self, limit: int = 50) -> List[Session]:
        """List recent sessions."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT session_id, project_id, created_at, status,
                   input_files, output_files, mappings, stats
            FROM sessions
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (self.project_id, limit))
        
        sessions = []
        for row in cursor.fetchall():
            sessions.append(Session(
                session_id=row[0],
                project_id=row[1],
                created_at=row[2],
                status=row[3],
                input_files=json.loads(row[4]),
                output_files=json.loads(row[5]),
                mappings=json.loads(row[6]),
                stats=json.loads(row[7])
            ))
        
        conn.close()
        return sessions
    
    def find_latest_session_for_file(self, file_path: str) -> Optional[Session]:
        """Find the latest session that processed the given file."""
        # Normalize path
        abs_path = str(Path(file_path).absolute())
        
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        # Search recent sessions
        cursor.execute('''
            SELECT session_id, project_id, created_at, status,
                   input_files, output_files, mappings, stats
            FROM sessions
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT 100
        ''', (self.project_id,))
        
        for row in cursor.fetchall():
            input_files = json.loads(row[4])
            output_files = json.loads(row[5])
            # Check if file matches (handling potential relative/absolute mismatches)
            all_files = list(input_files) + list(output_files)
            for f in all_files:
                if str(Path(f).absolute()) == abs_path:
                    conn.close()
                    return Session(
                        session_id=row[0],
                        project_id=row[1],
                        created_at=row[2],
                        status=row[3],
                        input_files=input_files,
                        output_files=output_files,
                        mappings=json.loads(row[6]),
                        stats=json.loads(row[7])
                    )
        
        conn.close()
        return None
    
    def update_session_status(
        self,
        session_id: str,
        status: str,
        output_files: Optional[List[str]] = None
    ):
        """Update session status."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        if output_files is not None:
            cursor.execute('''
                UPDATE sessions SET status = ?, output_files = ?
                WHERE session_id = ?
            ''', (status, json.dumps(output_files), session_id))
        else:
            cursor.execute('''
                UPDATE sessions SET status = ? WHERE session_id = ?
            ''', (status, session_id))
        
        conn.commit()
        conn.close()
    
    def get_stats(self) -> Dict:
        """Get vault statistics."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        # Count mappings by type
        cursor.execute('''
            SELECT entity_type, COUNT(*) FROM mappings
            WHERE project_id = ?
            GROUP BY entity_type
        ''', (self.project_id,))
        
        type_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Count sessions by status
        cursor.execute('''
            SELECT status, COUNT(*) FROM sessions
            WHERE project_id = ?
            GROUP BY status
        ''', (self.project_id,))
        
        session_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        conn.close()
        
        return {
            "vault_path": str(self.vault_path),
            "project_id": self.project_id,
            "mappings_by_type": type_counts,
            "sessions_by_status": session_counts,
            "total_mappings": sum(type_counts.values()),
            "total_sessions": sum(session_counts.values()),
        }
