"""
Vault storage for mapping data.
Stores mappings outside the project directory with encryption.
"""

import sqlite3
import hashlib
import json
import os
import secrets
import uuid
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, replace

from .crypto import (
    CIPHERTEXT_PREFIX,
    LEGACY_CIPHERTEXT_PREFIX,
    VaultCipher,
    VaultKeyMissingError,
    parse_envelope,
)
from .integrity import (
    VaultIntegrityError,
    VaultTamperError,
    append_integrity_checkpoint,
    compare_with_trusted_state,
    validate_database,
)
from .keyring_policy import NativeKeyringStore, SecureKeyStore, resolve_encryption_key
from .locking import VaultFileLock, assert_no_stale_sidecars
from .recovery import (
    RecoveryIdentityError,
    RecoveryPayload,
    RecoveryReplaceRequired,
    load_recovery_bundle,
    write_recovery_bundle,
)


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
        self._batch_conn: sqlite3.Connection | None = None
        self._batch_failed = False
        self._batch_owner: int | None = None
        
        # Ensure directory exists
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = VaultFileLock(Path(f"{self.vault_path}.lock"))
        self._ensure_private_permissions()
        
        # Initialize database
        self._init_db()
        self.active_key_version = self._read_active_key_version()
        encrypted_data_exists = self._contains_encrypted_data()
        key = resolve_encryption_key(
            self.key_store,
            self.project_id,
            self.active_key_version,
            encrypted_data_exists=encrypted_data_exists,
        )
        self._cipher = VaultCipher(
            key, self.project_id, key_version=self.active_key_version
        )
        self._migrate_sensitive_fields()
        self._initialize_integrity()
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
    
    def _connect(self) -> sqlite3.Connection:
        """Open SQLite with the vault's required durability and privacy settings."""
        conn = sqlite3.connect(self.vault_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("PRAGMA journal_mode = DELETE")
        return conn

    def _init_db(self):
        with self._lock:
            assert_no_stale_sidecars(self.vault_path)
            self._init_db_unlocked()

    def _init_db_unlocked(self):
        """Initialize database schema."""
        conn = self._connect()
        
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

        conn.execute('''
            CREATE TABLE IF NOT EXISTS vault_audit (
                epoch INTEGER PRIMARY KEY,
                previous_head TEXT NOT NULL,
                manifest TEXT NOT NULL,
                operation TEXT NOT NULL,
                head TEXT NOT NULL
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
        conn = self._connect()
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

    def _read_active_key_version(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'active_key_version'"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return 1
        try:
            version = int(row[0])
        except (TypeError, ValueError) as exc:
            raise VaultTamperError("The vault active key version is invalid.") from exc
        if version < 1 or str(version) != row[0]:
            raise VaultTamperError("The vault active key version is invalid.")
        return version

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

    def _migration_required(self, conn: sqlite3.Connection) -> bool:
        version = conn.execute(
            "SELECT value FROM vault_meta WHERE key = ?", ("encryption_version",)
        ).fetchone()
        if version is None or version[0] != str(ENCRYPTION_VERSION):
            return True
        prefix = f"{CIPHERTEXT_PREFIX}{self.active_key_version}:%"
        digest_prefix = f"vmhmac:v2:{self.active_key_version}:%"
        mapping = conn.execute(
            """
            SELECT 1 FROM mappings WHERE original_text NOT LIKE ?
               OR original_digest IS NULL OR original_digest NOT LIKE ?
            LIMIT 1
            """,
            (prefix, digest_prefix),
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
        with self._lock:
            assert_no_stale_sidecars(self.vault_path)
            self._migrate_sensitive_fields_unlocked()

    def _migrate_sensitive_fields_unlocked(self) -> None:
        """Encrypt every legacy sensitive value in one idempotent transaction."""
        conn = self._connect()
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
                    envelope = parse_envelope(original_text)
                    plaintext = self._cipher.decrypt(original_text, context)
                    if envelope.version != 2:
                        original_text = self._cipher.encrypt(plaintext, context)
                        migrated_plaintext = True
                else:
                    plaintext = original_text
                    original_text = self._cipher.encrypt(plaintext, context)
                    migrated_plaintext = True
                digest_prefix = f"vmhmac:v2:{self.active_key_version}:"
                if not original_digest or not original_digest.startswith(digest_prefix):
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
                        envelope = parse_envelope(value)
                        plaintext = self._cipher.decrypt(
                            value, self._session_context(session_id, field)
                        )
                        if envelope.version == 2:
                            encrypted_values[field] = value
                        else:
                            encrypted_values[field] = self._cipher.encrypt(
                                plaintext, self._session_context(session_id, field)
                            )
                            migrated_plaintext = True
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
            vacuum = self._connect()
            try:
                vacuum.execute("PRAGMA secure_delete = ON")
                vacuum.execute("VACUUM")
            finally:
                vacuum.close()

    def _initialize_integrity(self) -> None:
        """Bootstrap or verify the keyring-anchored logical audit history."""
        with self._lock:
            assert_no_stale_sidecars(self.vault_path)
            conn = self._connect()
            try:
                audit_count = conn.execute("SELECT COUNT(*) FROM vault_audit").fetchone()[0]
                trusted = self.key_store.get_trusted_state(self.project_id)
                integrity_key = self.key_store.get_integrity_key(self.project_id)
                if audit_count:
                    if integrity_key is None:
                        raise VaultKeyMissingError(
                            "This vault has an audit history, but its integrity key is missing."
                        )
                    if trusted is None:
                        raise VaultIntegrityError(
                            "This audited vault is missing its keyring trusted state."
                        )
                    self._integrity_key = integrity_key
                    current = validate_database(conn, integrity_key)
                    resolved = compare_with_trusted_state(conn, current, trusted)
                    if resolved != trusted:
                        self.key_store.set_trusted_state(self.project_id, resolved)
                    self.active_key_version = resolved.active_key_version
                    return

                if trusted is not None:
                    raise VaultIntegrityError(
                        "The vault audit history is missing but trusted state already exists."
                    )
                if integrity_key is None:
                    integrity_key = secrets.token_bytes(32)
                    self.key_store.set_integrity_key(self.project_id, integrity_key)
                self._integrity_key = integrity_key
                conn.execute("BEGIN IMMEDIATE")
                database_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT OR REPLACE INTO vault_meta(key, value) VALUES ('database_id', ?)",
                    (database_id,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO vault_meta(key, value) "
                    "VALUES ('active_key_version', ?)",
                    (str(self.active_key_version),),
                )
                state = append_integrity_checkpoint(conn, integrity_key, "bootstrap")
                conn.commit()
                self.key_store.set_trusted_state(self.project_id, state)
                self.active_key_version = state.active_key_version
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _verify_connection(self, conn: sqlite3.Connection):
        trusted = self.key_store.get_trusted_state(self.project_id)
        if trusted is None:
            raise VaultIntegrityError("This audited vault is missing its keyring trusted state.")
        current = validate_database(conn, self._integrity_key)
        resolved = compare_with_trusted_state(conn, current, trusted)
        if resolved != trusted:
            self.key_store.set_trusted_state(self.project_id, resolved)
        return resolved

    def verify(self) -> Dict:
        """Verify authenticated contents and the rollback anchor without decrypting values."""
        with self._lock:
            conn = self._connect()
            try:
                state = self._verify_connection(conn)
                return {
                    "verified": True,
                    "database_id": state.database_id,
                    "active_key_version": state.active_key_version,
                    "epoch": state.epoch,
                    "manifest": state.manifest,
                    "chain_head": state.chain_head,
                }
            finally:
                conn.close()

    def _verified_write(self, operation: str, callback):
        """Commit one mutation and its audit checkpoint atomically."""
        if self._batch_conn is not None:
            if self._batch_owner != threading.get_ident():
                raise RuntimeError("A vault batch cannot be shared across threads.")
            return callback(self._batch_conn)
        with self._lock:
            assert_no_stale_sidecars(self.vault_path)
            conn = self._connect()
            state = None
            try:
                trusted = self._verify_connection(conn)
                conn.execute("BEGIN IMMEDIATE")
                result = callback(conn)
                state = append_integrity_checkpoint(
                    conn,
                    self._integrity_key,
                    operation,
                    recovery_key_version=trusted.recovery_key_version,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            self.key_store.set_trusted_state(self.project_id, state)
            return result

    @contextmanager
    def batch(self, operation: str = "batch"):
        """Group all enclosed writes into one SQLite commit and keyring checkpoint."""
        owner = threading.get_ident()
        if self._batch_conn is not None:
            if self._batch_owner != owner:
                raise RuntimeError("A vault batch cannot be shared across threads.")
            try:
                yield self
            except Exception:
                self._batch_failed = True
                raise
            return

        with self._lock:
            assert_no_stale_sidecars(self.vault_path)
            conn = self._connect()
            state = None
            committed = False
            try:
                trusted = self._verify_connection(conn)
                conn.execute("BEGIN IMMEDIATE")
                self._batch_conn = conn
                self._batch_owner = owner
                self._batch_failed = False
                yield self
                if self._batch_failed:
                    raise RuntimeError("The vault batch was marked failed by a nested operation.")
                state = append_integrity_checkpoint(
                    conn,
                    self._integrity_key,
                    operation,
                    recovery_key_version=trusted.recovery_key_version,
                )
                conn.commit()
                committed = True
            except Exception:
                if not committed:
                    conn.rollback()
                raise
            finally:
                self._batch_conn = None
                self._batch_owner = None
                self._batch_failed = False
                conn.close()
            self.key_store.set_trusted_state(self.project_id, state)
    
    def get_or_create_mapping(
        self,
        original: str,
        entity_type: str,
        masked: str,
        source: str = "unknown",
        confidence: float = 1.0
    ) -> str:
        return self._verified_write(
            "upsert-mapping",
            lambda conn: self._get_or_create_mapping_unlocked(
                conn, original, entity_type, masked, source, confidence
            ),
        )

    def _get_or_create_mapping_unlocked(
        self,
        conn: sqlite3.Connection,
        original: str,
        entity_type: str,
        masked: str,
        source: str = "unknown",
        confidence: float = 1.0,
    ) -> str:
        """
        Get existing mapping or create new one.
        Ensures stable mappings within a project.
        """
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
                raise RuntimeError("Vault mapping digest collision detected.")
            # Update last_seen_at
            cursor.execute('''
                UPDATE mappings SET last_seen_at = ? WHERE entity_id = ?
            ''', (datetime.now().isoformat(), row[1]))
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
        
        return candidate_masked
    
    def get_mapping_by_masked(self, masked: str) -> Optional[str]:
        """Get original text for a masked value."""
        self.verify()
        conn = self._connect()
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
        self.verify()
        conn = self._connect()
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
        return self._verified_write(
            "create-session",
            lambda conn: self._create_session_unlocked(
                conn, input_files, mappings, stats, output_files, status
            ),
        )

    def _create_session_unlocked(
        self,
        conn: sqlite3.Connection,
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
        
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get session by ID."""
        self.verify()
        conn = self._connect()
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
        self.verify()
        conn = self._connect()
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
        self.verify()
        # Normalize path
        abs_path = str(Path(file_path).absolute())
        
        conn = self._connect()
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
        self._verified_write(
            "update-session-status",
            lambda conn: self._update_session_status_unlocked(
                conn, session_id, status, output_files
            ),
        )

    def _update_session_status_unlocked(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        status: str,
        output_files: Optional[List[str]] = None,
    ):
        """Update session status."""
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
        
    
    def get_stats(self) -> Dict:
        """Get vault statistics."""
        self.verify()
        conn = self._connect()
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

    def security_status(self) -> Dict:
        """Return security posture metadata without decrypting or exposing vault values."""
        result = self.verify()
        result.update(
            {
                "encryption": self._cipher.algorithm,
                "key_provider": self.key_store.name,
                "encryption_version": ENCRYPTION_VERSION,
                "recovery_current": (
                    self.key_store.get_trusted_state(self.project_id).recovery_key_version
                    == result["active_key_version"]
                ),
            }
        )
        return result

    def _reencrypt_sensitive_rows(
        self,
        conn: sqlite3.Connection,
        old_version: int,
        new_cipher: VaultCipher,
    ) -> None:
        mapping_rows = conn.execute(
            "SELECT entity_id, original_text FROM mappings ORDER BY entity_id"
        ).fetchall()
        for entity_id, encrypted in mapping_rows:
            envelope = parse_envelope(encrypted)
            if envelope.key_version != old_version:
                raise VaultIntegrityError(
                    "A mapping uses an unexpected encryption key version."
                )
            context = self._mapping_context(entity_id)
            plaintext = self._cipher.decrypt(encrypted, context)
            conn.execute(
                "UPDATE mappings SET original_text = ?, original_digest = ? "
                "WHERE entity_id = ?",
                (
                    new_cipher.encrypt(plaintext, context),
                    new_cipher.lookup_digest(plaintext, "mapping-original"),
                    entity_id,
                ),
            )

        session_rows = conn.execute(
            "SELECT session_id, input_files, output_files, mappings "
            "FROM sessions ORDER BY session_id"
        ).fetchall()
        for session_id, input_files, output_files, mappings in session_rows:
            encrypted_fields = []
            for field, encrypted in zip(
                ("input_files", "output_files", "mappings"),
                (input_files, output_files, mappings),
            ):
                envelope = parse_envelope(encrypted)
                if envelope.key_version != old_version:
                    raise VaultIntegrityError(
                        "A session uses an unexpected encryption key version."
                    )
                context = self._session_context(session_id, field)
                plaintext = self._cipher.decrypt(encrypted, context)
                encrypted_fields.append(new_cipher.encrypt(plaintext, context))
            conn.execute(
                "UPDATE sessions SET input_files = ?, output_files = ?, mappings = ? "
                "WHERE session_id = ?",
                (*encrypted_fields, session_id),
            )

    def _verify_decryptable_fields(
        self, conn: sqlite3.Connection, cipher: VaultCipher
    ) -> None:
        for entity_id, encrypted in conn.execute(
            "SELECT entity_id, original_text FROM mappings"
        ):
            cipher.decrypt(encrypted, self._mapping_context(entity_id))
        for row in conn.execute(
            "SELECT session_id, input_files, output_files, mappings FROM sessions"
        ):
            session_id = row[0]
            for field, encrypted in zip(
                ("input_files", "output_files", "mappings"), row[1:]
            ):
                decoded = cipher.decrypt(
                    encrypted, self._session_context(session_id, field)
                )
                json.loads(decoded)

    def rotate_key(self) -> int:
        """Rotate every encrypted field transactionally and retire the old key last."""
        if self._batch_conn is not None:
            raise RuntimeError("Vault key rotation cannot run inside a write batch.")
        self.verify()
        old_version = self.active_key_version
        new_version = old_version + 1
        new_key = secrets.token_bytes(32)
        new_cipher = VaultCipher(new_key, self.project_id, key_version=new_version)
        self.key_store.set_encryption_key(self.project_id, new_version, new_key)
        committed = False
        state = None
        try:
            with self._lock:
                assert_no_stale_sidecars(self.vault_path)
                conn = self._connect()
                try:
                    trusted = self._verify_connection(conn)
                    if trusted.active_key_version != old_version:
                        raise VaultIntegrityError(
                            "The vault active key changed before rotation acquired the lock."
                        )
                    conn.execute("BEGIN IMMEDIATE")
                    self._reencrypt_sensitive_rows(conn, old_version, new_cipher)
                    conn.execute(
                        "INSERT OR REPLACE INTO vault_meta(key, value) "
                        "VALUES ('active_key_version', ?)",
                        (str(new_version),),
                    )
                    state = append_integrity_checkpoint(
                        conn,
                        self._integrity_key,
                        "rotate-key",
                        recovery_key_version=trusted.recovery_key_version,
                    )
                    conn.commit()
                    committed = True
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            self.key_store.set_trusted_state(self.project_id, state)
        except Exception:
            if not committed:
                self.key_store.delete_encryption_key(self.project_id, new_version)
            raise

        self.active_key_version = new_version
        self._cipher = new_cipher
        self.verify()
        with self._lock:
            conn = self._connect()
            try:
                self._verify_decryptable_fields(conn, new_cipher)
                conn.execute("VACUUM")
            finally:
                conn.close()
        self.key_store.delete_encryption_key(self.project_id, old_version)
        return new_version

    def backup_key(
        self,
        output: Path,
        passphrase: str,
        *,
        overwrite: bool = False,
    ) -> None:
        """Create a durable authenticated recovery bundle for the current key version."""
        with self._lock:
            conn = self._connect()
            try:
                state = self._verify_connection(conn)
            finally:
                conn.close()
            encryption_key = self.key_store.get_encryption_key(
                self.project_id, state.active_key_version
            )
            integrity_key = self.key_store.get_integrity_key(self.project_id)
            if encryption_key is None or integrity_key is None:
                raise VaultKeyMissingError("Vault recovery material is incomplete.")
            exported_state = replace(
                state, recovery_key_version=state.active_key_version
            )
            payload = RecoveryPayload(
                project_id=self.project_id,
                database_id=state.database_id,
                key_version=state.active_key_version,
                encryption_key=encryption_key,
                integrity_key=integrity_key,
                trusted_state=exported_state,
            )
            write_recovery_bundle(
                Path(output), payload, passphrase, overwrite=overwrite
            )
            self.key_store.set_trusted_state(self.project_id, exported_state)

    @classmethod
    def restore_key_for_project(
        cls,
        project_path: str,
        bundle: Path,
        passphrase: str,
        *,
        key_store: Optional[SecureKeyStore] = None,
        replace: bool = False,
    ) -> "VaultStorage":
        """Restore keyring material, then require a complete vault verification."""
        store = key_store or default_key_store()
        payload = load_recovery_bundle(Path(bundle), passphrase)
        project_id = get_project_fingerprint(project_path)
        if payload.project_id != project_id:
            raise RecoveryIdentityError(
                "The recovery bundle belongs to a different VibeMask project."
            )
        vault_path = get_vault_path(project_path)
        if not vault_path.exists():
            raise RecoveryIdentityError("The target vault database does not exist.")

        lock = VaultFileLock(Path(f"{vault_path}.lock"))
        with lock:
            assert_no_stale_sidecars(vault_path)
            conn = sqlite3.connect(vault_path, timeout=5.0)
            try:
                metadata = dict(
                    conn.execute(
                        "SELECT key, value FROM vault_meta "
                        "WHERE key IN ('database_id', 'active_key_version')"
                    ).fetchall()
                )
            finally:
                conn.close()
            if (
                metadata.get("database_id") != payload.database_id
                or metadata.get("active_key_version") != str(payload.key_version)
            ):
                raise RecoveryIdentityError(
                    "The recovery bundle does not match the target vault database."
                )

            previous_key = store.get_encryption_key(project_id, payload.key_version)
            previous_integrity = store.get_integrity_key(project_id)
            previous_state = store.get_trusted_state(project_id)
            differences = (
                previous_key not in (None, payload.encryption_key)
                or previous_integrity not in (None, payload.integrity_key)
                or previous_state not in (None, payload.trusted_state)
            )
            if differences and not replace:
                raise RecoveryReplaceRequired(
                    "Different vault keyring material exists; explicit replacement is required."
                )

            store.set_encryption_key(project_id, payload.key_version, payload.encryption_key)
            store.set_integrity_key(project_id, payload.integrity_key)
            store.set_trusted_state(project_id, payload.trusted_state)

        try:
            restored = cls(project_path, key_store=store)
            restored.verify()
            return restored
        except Exception:
            with lock:
                if previous_key is None:
                    store.delete_encryption_key(project_id, payload.key_version)
                else:
                    store.set_encryption_key(project_id, payload.key_version, previous_key)
                if previous_integrity is None:
                    store.delete_integrity_key(project_id)
                else:
                    store.set_integrity_key(project_id, previous_integrity)
                if previous_state is None:
                    store.delete_trusted_state(project_id)
                else:
                    store.set_trusted_state(project_id, previous_state)
            raise
