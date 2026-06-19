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
from dataclasses import dataclass

from .crypto import (
    CIPHERTEXT_PREFIX,
    LEGACY_CIPHERTEXT_PREFIX,
    VaultCipher,
)
from .keyring_policy import NativeKeyringStore, SecureKeyStore, resolve_encryption_key


ENCRYPTION_VERSION = 2


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


def default_key_store() -> SecureKeyStore:
    """Return the production key provider; tests replace this factory."""
    return NativeKeyringStore()


class VaultStorage:
    """
    SQLite-based storage for entity mappings.
    
    Features:
    - Stored outside project directory
    - Stable mappings (same entity -> same mask)
    - Session tracking for restoration
    """
    
    def __init__(self, project_path: str, key_store: Optional[SecureKeyStore] = None):
        self.project_path = project_path
        self.project_id = get_project_fingerprint(project_path)
        self.vault_path = get_vault_path(project_path)
        self.key_store = key_store or default_key_store()
        self.key_provider = self.key_store
        
        # Ensure directory exists
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_private_permissions()
        
        # Initialize database
        self._init_db()
        encrypted_data_exists = self._contains_encrypted_data()
        key = resolve_encryption_key(
            self.key_store,
            self.project_id,
            1,
            encrypted_data_exists=encrypted_data_exists,
        )
        self._cipher = VaultCipher(key, self.project_id, key_version=1)
        self._migrate_sensitive_fields()
        self._ensure_private_permissions()

    def _ensure_private_permissions(self):
        """Keep vault directories/files readable only by the current user."""
        if os.name == "nt":
            return

        paths = [
            get_vibemask_home(),
            get_vibemask_home() / "projects",
            self.vault_path.parent,
        ]
        for path in paths:
            if path.exists():
                try:
                    path.chmod(0o700)
                except OSError:
                    pass

        if self.vault_path.exists():
            try:
                self.vault_path.chmod(0o600)
            except OSError:
                pass
    
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
                original_digest TEXT,
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

        conn.execute('''
            CREATE TABLE IF NOT EXISTS vault_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

        columns = {row[1] for row in conn.execute("PRAGMA table_info(mappings)")}
        if "original_digest" not in columns:
            conn.execute("ALTER TABLE mappings ADD COLUMN original_digest TEXT")
        
        # Indexes
        conn.execute('CREATE INDEX IF NOT EXISTS idx_mappings_project ON mappings(project_id)')
        conn.execute('DROP INDEX IF EXISTS idx_mappings_original')
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_mappings_digest '
            'ON mappings(project_id, original_digest, entity_type)'
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_mappings_masked ON mappings(project_id, masked_text)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)')
        
        conn.commit()
        conn.close()

    def _contains_encrypted_data(self) -> bool:
        """Check only ciphertext markers; never load or expose stored plaintext."""
        conn = sqlite3.connect(self.vault_path)
        try:
            prefixes = (f"{LEGACY_CIPHERTEXT_PREFIX}%", f"{CIPHERTEXT_PREFIX}%")
            mapping = conn.execute(
                "SELECT 1 FROM mappings WHERE original_text LIKE ? OR original_text LIKE ? LIMIT 1",
                prefixes,
            ).fetchone()
            session = conn.execute(
                """
                SELECT 1 FROM sessions
                WHERE input_files LIKE ? OR input_files LIKE ?
                   OR output_files LIKE ? OR output_files LIKE ?
                   OR mappings LIKE ? OR mappings LIKE ?
                LIMIT 1
                """,
                prefixes * 3,
            ).fetchone()
            return mapping is not None or session is not None
        finally:
            conn.close()

    @staticmethod
    def _mapping_context(entity_id: str) -> str:
        return f"mappings:{entity_id}:original_text"

    @staticmethod
    def _session_context(session_id: str, field: str) -> str:
        return f"sessions:{session_id}:{field}"

    def _encrypt_json(self, value, session_id: str, field: str) -> str:
        encoded = json.dumps(value, ensure_ascii=False)
        return self._cipher.encrypt(encoded, self._session_context(session_id, field))

    def _decrypt_json(self, value: str, session_id: str, field: str, default):
        if value is None:
            return default
        decoded = self._cipher.decrypt(value, self._session_context(session_id, field))
        return json.loads(decoded)

    @staticmethod
    def _migration_required(conn: sqlite3.Connection) -> bool:
        version = conn.execute(
            "SELECT value FROM vault_meta WHERE key = ?", ("encryption_version",)
        ).fetchone()
        if version is None or version[0] != str(ENCRYPTION_VERSION):
            return True
        prefix = f"{CIPHERTEXT_PREFIX}1:%"
        mapping = conn.execute(
            """
            SELECT 1 FROM mappings WHERE original_text NOT LIKE ?
               OR original_digest IS NULL OR original_digest NOT LIKE 'vmhmac:v2:1:%'
            LIMIT 1
            """,
            (prefix,),
        ).fetchone()
        session = conn.execute(
            """
            SELECT 1 FROM sessions
            WHERE input_files IS NULL OR input_files NOT LIKE ?
               OR output_files IS NULL OR output_files NOT LIKE ?
               OR mappings IS NULL OR mappings NOT LIKE ?
            LIMIT 1
            """,
            (prefix, prefix, prefix),
        ).fetchone()
        return mapping is not None or session is not None

    def _migrate_sensitive_fields(self) -> None:
        """Encrypt every legacy sensitive value in one idempotent transaction."""
        conn = sqlite3.connect(self.vault_path)
        migrated_plaintext = False
        try:
            if not self._migration_required(conn):
                return
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("BEGIN IMMEDIATE")
            mapping_rows = conn.execute(
                "SELECT entity_id, original_text, original_digest FROM mappings"
            ).fetchall()
            for entity_id, original_text, original_digest in mapping_rows:
                context = self._mapping_context(entity_id)
                if self._cipher.is_encrypted(original_text):
                    plaintext = self._cipher.decrypt(original_text, context)
                else:
                    plaintext = original_text
                    original_text = self._cipher.encrypt(plaintext, context)
                    migrated_plaintext = True
                if not original_digest or not original_digest.startswith("vmhmac:v2:1:"):
                    original_digest = self._cipher.lookup_digest(plaintext, "mapping-original")
                conn.execute(
                    "UPDATE mappings SET original_text = ?, original_digest = ? WHERE entity_id = ?",
                    (original_text, original_digest, entity_id),
                )

            session_rows = conn.execute(
                "SELECT session_id, input_files, output_files, mappings FROM sessions"
            ).fetchall()
            for session_id, input_files, output_files, mappings in session_rows:
                values = {
                    "input_files": input_files if input_files is not None else "[]",
                    "output_files": output_files if output_files is not None else "[]",
                    "mappings": mappings if mappings is not None else "{}",
                }
                encrypted_values = {}
                for field, value in values.items():
                    if self._cipher.is_encrypted(value):
                        self._cipher.decrypt(value, self._session_context(session_id, field))
                        encrypted_values[field] = value
                    else:
                        encrypted_values[field] = self._cipher.encrypt(
                            value, self._session_context(session_id, field)
                        )
                        migrated_plaintext = True
                conn.execute(
                    """
                    UPDATE sessions
                    SET input_files = ?, output_files = ?, mappings = ?
                    WHERE session_id = ?
                    """,
                    (
                        encrypted_values["input_files"],
                        encrypted_values["output_files"],
                        encrypted_values["mappings"],
                        session_id,
                    ),
                )

            conn.execute(
                "INSERT OR REPLACE INTO vault_meta(key, value) VALUES (?, ?)",
                ("encryption_version", str(ENCRYPTION_VERSION)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if migrated_plaintext:
            vacuum = sqlite3.connect(self.vault_path)
            try:
                vacuum.execute("PRAGMA secure_delete = ON")
                vacuum.execute("VACUUM")
            finally:
                vacuum.close()
    
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

        original_digest = self._cipher.lookup_digest(original, "mapping-original")
        # Check for existing mapping
        cursor.execute('''
            SELECT masked_text, entity_id, original_text FROM mappings
            WHERE project_id = ? AND original_digest = ? AND entity_type = ?
        ''', (self.project_id, original_digest, entity_type))
        
        row = cursor.fetchone()
        
        if row:
            stored_original = self._cipher.decrypt(row[2], self._mapping_context(row[1]))
            if stored_original != original:
                conn.close()
                raise RuntimeError("Vault mapping digest collision detected.")
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
                pool = list(
                    "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
                    "天地玄黄宇宙洪荒日月星辰春夏秋冬东南西北"
                    "壹贰叁肆伍陆柒捌玖零"
                )
                n = attempt
                for pos in reversed(cjk_positions):
                    chars[pos] = pool[n % len(pool)]
                    n //= len(pool)

                # For very collision-heavy legacy vaults, keep expanding within
                # the CJK block instead of cycling through the small friendly pool.
                if n:
                    for j, pos in enumerate(reversed(cjk_positions), start=1):
                        chars[pos] = chr(0x4E00 + ((attempt // j) % 20_902))
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
        encrypted_original = self._cipher.encrypt(original, self._mapping_context(entity_id))

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
            (entity_id, project_id, entity_type, original_text, original_digest,
             masked_text, created_at, last_seen_at, source, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (entity_id, self.project_id, entity_type, encrypted_original, original_digest,
              candidate_masked, now, now, source, confidence))
        
        conn.commit()
        conn.close()
        return candidate_masked
    
    def get_mapping_by_masked(self, masked: str) -> Optional[str]:
        """Get original text for a masked value."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT entity_id, original_text FROM mappings
            WHERE project_id = ? AND masked_text = ?
        ''', (self.project_id, masked))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        return self._cipher.decrypt(row[1], self._mapping_context(row[0]))
    
    def get_all_mappings(self) -> Dict[str, str]:
        """Get all mappings for the project {masked -> original}."""
        conn = sqlite3.connect(self.vault_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT entity_id, masked_text, original_text FROM mappings
            WHERE project_id = ?
        ''', (self.project_id,))
        
        mappings = {
            row[1]: self._cipher.decrypt(row[2], self._mapping_context(row[0]))
            for row in cursor.fetchall()
        }
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
            self._encrypt_json(input_files, session_id, "input_files"),
            self._encrypt_json(output_files, session_id, "output_files"),
            self._encrypt_json(mappings, session_id, "mappings"),
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
            input_files=self._decrypt_json(row[4], row[0], "input_files", []),
            output_files=self._decrypt_json(row[5], row[0], "output_files", []),
            mappings=self._decrypt_json(row[6], row[0], "mappings", {}),
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
                input_files=self._decrypt_json(row[4], row[0], "input_files", []),
                output_files=self._decrypt_json(row[5], row[0], "output_files", []),
                mappings=self._decrypt_json(row[6], row[0], "mappings", {}),
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
            input_files = self._decrypt_json(row[4], row[0], "input_files", [])
            output_files = self._decrypt_json(row[5], row[0], "output_files", [])
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
                        mappings=self._decrypt_json(row[6], row[0], "mappings", {}),
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
            ''', (
                status,
                self._encrypt_json(output_files, session_id, "output_files"),
                session_id,
            ))
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

        version_row = cursor.execute(
            "SELECT value FROM vault_meta WHERE key = ?", ("encryption_version",)
        ).fetchone()
        
        conn.close()
        
        return {
            "vault_path": str(self.vault_path),
            "project_id": self.project_id,
            "mappings_by_type": type_counts,
            "sessions_by_status": session_counts,
            "total_mappings": sum(type_counts.values()),
            "total_sessions": sum(session_counts.values()),
            "encryption": self._cipher.algorithm,
            "key_provider": self.key_provider.name,
            "encryption_version": int(version_row[0]) if version_row else 0,
        }
