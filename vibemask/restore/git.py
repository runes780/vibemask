"""
Git diff restoration for files modified by AI tools.
"""

import subprocess
from pathlib import Path
from typing import List, Dict, Set, Tuple
from ..core.replacer import restore_text


# File extensions considered as text files
TEXT_EXTENSIONS: Set[str] = {
    '.md', '.txt', '.log', '.json', '.yaml', '.yml',
    '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs',
    '.sql', '.html', '.css', '.xml', '.csv', '.ini', '.cfg',
    '.sh', '.bash', '.zsh', '.toml', '.env', '.conf',
}

# Maximum file size to process (5MB)
MAX_FILE_SIZE = 5 * 1024 * 1024


def get_changed_files(repo_path: Path) -> List[Path]:
    """
    Get list of files changed in the git working tree.
    
    Args:
        repo_path: Path to git repository
        
    Returns:
        List of changed file paths
    """
    try:
        result = subprocess.run(
            ['git', 'diff', '--name-only'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        if result.returncode != 0:
            return []
        
        files = []
        for line in result.stdout.strip().split('\n'):
            if line:
                files.append(repo_path / line)
        
        return files
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def get_staged_files(repo_path: Path) -> List[Path]:
    """Get list of staged (but not committed) files."""
    try:
        result = subprocess.run(
            ['git', 'diff', '--staged', '--name-only'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        if result.returncode != 0:
            return []
        
        files = []
        for line in result.stdout.strip().split('\n'):
            if line:
                files.append(repo_path / line)
        
        return files
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def is_text_file(file_path: Path) -> bool:
    """Check if file is a text file based on extension."""
    return file_path.suffix.lower() in TEXT_EXTENSIONS


def is_safe_to_process(file_path: Path) -> Tuple[bool, str]:
    """
    Check if file is safe to process.
    
    Returns:
        Tuple of (is_safe, reason)
    """
    if not file_path.exists():
        return False, "file_not_found"
    
    if not file_path.is_file():
        return False, "not_a_file"
    
    if not is_text_file(file_path):
        return False, "not_text_file"
    
    try:
        size = file_path.stat().st_size
        if size > MAX_FILE_SIZE:
            return False, f"file_too_large:{size}"
    except OSError:
        return False, "cannot_stat"
    
    return True, "ok"


def restore_file(
    file_path: Path,
    mappings: Dict[str, str],
    dry_run: bool = False
) -> Dict:
    """
    Restore a single file by replacing masked tokens with originals.
    
    Args:
        file_path: Path to file
        mappings: Dict of {masked -> original}
        dry_run: If True, don't write changes
        
    Returns:
        Dict with restoration results
    """
    result = {
        "file": str(file_path),
        "success": False,
        "changes": 0,
        "error": None,
    }
    
    # Safety check
    safe, reason = is_safe_to_process(file_path)
    if not safe:
        result["error"] = reason
        return result
    
    try:
        # Read current content
        content = file_path.read_text(encoding='utf-8')
        
        # Count potential matches
        matches = 0
        for masked in mappings.keys():
            matches += content.count(masked)
        
        if matches == 0:
            result["success"] = True
            result["changes"] = 0
            return result
        
        # Restore content
        restored = restore_text(content, mappings)
        
        if restored != content:
            result["changes"] = matches
            
            if not dry_run:
                file_path.write_text(restored, encoding='utf-8')
            
            result["success"] = True
        else:
            result["success"] = True
            result["changes"] = 0
        
        return result
        
    except UnicodeDecodeError:
        result["error"] = "unicode_error"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def restore_changed_files(
    repo_path: Path,
    mappings: Dict[str, str],
    dry_run: bool = False,
    include_staged: bool = False
) -> List[Dict]:
    """
    Restore all changed files in a git repository.
    
    Args:
        repo_path: Path to git repository
        mappings: Dict of {masked -> original}
        dry_run: If True, don't write changes
        include_staged: If True, also process staged files
        
    Returns:
        List of restoration results for each file
    """
    results = []
    
    # Get changed files
    files = get_changed_files(repo_path)
    
    if include_staged:
        staged = get_staged_files(repo_path)
        files = list(set(files + staged))
    
    for file_path in files:
        result = restore_file(file_path, mappings, dry_run)
        results.append(result)
    
    return results


def summarize_restoration(results: List[Dict]) -> Dict:
    """Summarize restoration results."""
    total = len(results)
    success = sum(1 for r in results if r["success"])
    failed = total - success
    total_changes = sum(r["changes"] for r in results)
    
    errors = [r for r in results if r.get("error")]
    
    return {
        "total_files": total,
        "successful": success,
        "failed": failed,
        "total_changes": total_changes,
        "errors": errors,
    }
