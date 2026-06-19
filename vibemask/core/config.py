"""
VibeMask Configuration System.
Supports .vibemask.yaml config files.
"""

from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from pydantic import BaseModel
import json


class DetectionConfig(BaseModel):
    """Detection layer configuration."""
    # L1: Regex (always enabled)
    regex_enabled: bool = True
    
    # L2: Heuristic
    heuristic_enabled: bool = True
    heuristic_min_confidence: float = 0.5
    
    # L3: Model judge (optional)
    model_enabled: bool = False
    model_backend: str = "ollama"  # "ollama" or "onnx"
    model_name: str = "qwen2.5:0.5b"
    model_threshold: float = 0.7
    
    # Entity filters
    enabled_entities: List[str] = ["PERSON", "PHONE", "EMAIL", "IDCN", "URL"]


class MaskingConfig(BaseModel):
    """Masking strategy configuration."""
    # Default operator
    default_operator: str = "chinese_pseudo"  # or "replace", "mask", "encrypt"
    
    # Per-entity operator overrides
    entity_operators: Dict[str, str] = {}
    
    # Chinese pseudo name settings
    preserve_length: bool = True
    use_tiangan_dizhi: bool = True


class StorageConfig(BaseModel):
    """Storage and vault configuration."""
    # Vault location
    vault_path: str = "~/.vibemask"
    
    # Encryption
    # Retained for config compatibility; vault encryption is always enforced.
    encrypt_mappings: bool = True
    use_keychain: bool = False
    
    # Session retention
    session_retention_days: int = 30


class VibeMaskConfig(BaseModel):
    """Main configuration model."""
    version: str = "2.0"
    
    detection: DetectionConfig = DetectionConfig()
    masking: MaskingConfig = MaskingConfig()
    storage: StorageConfig = StorageConfig()
    
    # File processing
    ooxml_mode: str = "auto"  # "auto", "lossless", "legacy"
    skip_patterns: List[str] = ["*.min.js", "*.min.css", "node_modules/*"]


# ============================================================
# Config Loading
# ============================================================

CONFIG_FILENAMES = [".vibemask.yaml", ".vibemask.yml", ".vibemask.json", "vibemask.config.json"]


def find_config_file(start_path: Path = None) -> Optional[Path]:
    """
    Find config file by walking up directory tree.
    
    Args:
        start_path: Starting directory (default: cwd)
        
    Returns:
        Path to config file if found, None otherwise
    """
    if start_path is None:
        start_path = Path.cwd()
    
    current = Path(start_path).resolve()
    
    while current != current.parent:
        for filename in CONFIG_FILENAMES:
            config_path = current / filename
            if config_path.exists():
                return config_path
        current = current.parent
    
    return None


def load_config(config_path: Optional[Path] = None) -> VibeMaskConfig:
    """
    Load configuration from file or use defaults.
    
    Args:
        config_path: Optional explicit config path
        
    Returns:
        VibeMaskConfig instance
    """
    if config_path is None:
        config_path = find_config_file()
    
    if config_path is None:
        return VibeMaskConfig()
    
    config_path = Path(config_path)
    
    if config_path.suffix in {'.yaml', '.yml'}:
        try:
            import yaml
            with open(config_path) as f:
                data = yaml.safe_load(f)
        except ImportError:
            # Fall back to JSON-like subset
            with open(config_path) as f:
                data = json.load(f)
    else:
        with open(config_path) as f:
            data = json.load(f)
    
    return VibeMaskConfig(**data)


def save_config(config: VibeMaskConfig, path: Path) -> None:
    """Save configuration to file."""
    path = Path(path)
    
    data = config.model_dump()
    
    if path.suffix in {'.yaml', '.yml'}:
        try:
            import yaml
            with open(path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        except ImportError:
            # Fall back to JSON
            with open(path, 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    else:
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================
# Default Config Template
# ============================================================

DEFAULT_CONFIG_YAML = """# VibeMask Configuration
version: "2.0"

detection:
  # L1: Regex patterns (always enabled)
  regex_enabled: true
  
  # L2: Heuristic rules (Chinese names, etc.)
  heuristic_enabled: true
  heuristic_min_confidence: 0.5
  
  # L3: Model judge (optional)
  model_enabled: false
  model_backend: ollama  # or "onnx"
  model_name: qwen2.5:0.5b
  model_threshold: 0.7
  
  # Entities to detect
  enabled_entities:
    - PERSON
    - PHONE
    - EMAIL
    - IDCN
    - URL

masking:
  # Default masking operator
  default_operator: chinese_pseudo  # or "replace", "mask", "encrypt"
  
  # Per-entity overrides
  entity_operators:
    PHONE: mask      # 138****5678
    EMAIL: replace   # <EMAIL>
  
  # Chinese pseudo name settings
  preserve_length: true
  use_tiangan_dizhi: true

storage:
  vault_path: ~/.vibemask
  encrypt_mappings: true  # always enforced; cannot downgrade to plaintext
  session_retention_days: 30

# File processing
ooxml_mode: auto  # "auto", "lossless", "legacy"
skip_patterns:
  - "*.min.js"
  - "*.min.css"
  - "node_modules/*"
"""


def create_default_config(path: Path = None) -> Path:
    """Create a default config file."""
    if path is None:
        path = Path.cwd() / ".vibemask.yaml"
    
    with open(path, 'w') as f:
        f.write(DEFAULT_CONFIG_YAML)
    
    return path
