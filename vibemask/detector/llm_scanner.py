from typing import List, Dict, Tuple, Optional, Any, Generator
import json
import time
import httpx
from dataclasses import dataclass
import re

@dataclass
class AnonymizedResult:
    text: str
    mappings: Dict[str, str]  # original -> replacement
    pii_types: Dict[str, str] # original -> type

class LLMConfig:
    # Remote high-performance server
    REMOTE_URL = "http://100.84.147.32:11434"
    REMOTE_MODEL = "qwen3:30b-a3b-instruct-2507-q4_K_M"
    
    # Local server (Fallback)
    LOCAL_URL = "http://localhost:11434"
    LOCAL_MODEL = "qwen3:4b-instruct"  # Default local model
    
    TIMEOUT = 180  # Longer timeout for generation
    CONNECT_TIMEOUT = 2.0  # Quick fail for connection

    # For long documents, chunk input to avoid context overflow and huge latency.
    # This is in characters (rough proxy for tokens), and is intentionally conservative.
    MAX_CHUNK_CHARS = 1800
    
class LLMScanner:
    """
    LLM-based PII scanner and rewriter.
    Uses Qwen to identify and rewrite sensitive information in one pass.
    Auto-selects backend (Remote 30B -> Local 4B).
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        max_chunk_chars: Optional[int] = None,
    ):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.max_chunk_chars = max_chunk_chars
        self._ollama_available = False
        self._model_available = False
        self._available_models: List[str] = []
        self._setup_backend()
        
    def _setup_backend(self):
        """Determine which backend to use with availability checking."""
        # Set defaults
        if not self.base_url:
            self.base_url = LLMConfig.LOCAL_URL
        if not self.model:
            self.model = LLMConfig.LOCAL_MODEL
        if not self.timeout:
            self.timeout = LLMConfig.TIMEOUT
        if not self.connect_timeout:
            self.connect_timeout = LLMConfig.CONNECT_TIMEOUT
        if not self.max_chunk_chars:
            self.max_chunk_chars = LLMConfig.MAX_CHUNK_CHARS

        # Check Ollama availability
        self._check_ollama_availability()
        
        if self._ollama_available:
            if self._model_available:
                print(f"[LLMScanner] ✓ Ollama ready ({self.model}) @ {self.base_url}")
            else:
                print(f"[LLMScanner] ⚠ Ollama running but model '{self.model}' not found")
                self._suggest_model_pull()
        else:
            print(f"[LLMScanner] ✗ Ollama not available @ {self.base_url}")
            print(f"[LLMScanner] → To use LLM detection, start Ollama: ollama serve")
    
    def _check_ollama_availability(self) -> None:
        """Check if Ollama service is running and model is available."""
        try:
            with httpx.Client(timeout=self.connect_timeout, trust_env=False) as client:
                # Check if Ollama API is reachable
                res = client.get(f"{self.base_url}/api/tags")
                if res.status_code != 200:
                    self._ollama_available = False
                    return
                
                self._ollama_available = True
                
                # Parse available models
                data = res.json()
                models = data.get("models", [])
                self._available_models = [m.get("name", "") for m in models]
                
                # Check if target model exists (handle version suffixes)
                target_base = self.model.split(":")[0] if ":" in self.model else self.model
                for m in self._available_models:
                    m_base = m.split(":")[0] if ":" in m else m
                    if m == self.model or m_base == target_base or self.model in m:
                        self._model_available = True
                        # Update to exact model name if we matched a variant
                        if m != self.model and self.model in m:
                            self.model = m
                        break
                        
        except httpx.ConnectError:
            self._ollama_available = False
        except httpx.TimeoutException:
            self._ollama_available = False
        except Exception as e:
            print(f"[LLMScanner] Warning: Ollama check failed: {e}")
            self._ollama_available = False
    
    def _suggest_model_pull(self) -> None:
        """Suggest model pull command if model not found."""
        print(f"[LLMScanner] → To install the model, run: ollama pull {self.model}")
        if self._available_models:
            print(f"[LLMScanner] → Available models: {', '.join(self._available_models[:5])}")
            # Look for similar models
            similar = [m for m in self._available_models if "qwen" in m.lower()]
            if similar:
                print(f"[LLMScanner] → Similar Qwen models available: {', '.join(similar[:3])}")
    
    def is_available(self) -> bool:
        """Check if LLM scanner is ready to use."""
        return self._ollama_available and self._model_available
    
    def get_status(self) -> Dict[str, Any]:
        """Get detailed status of the LLM scanner."""
        return {
            "ollama_available": self._ollama_available,
            "model_available": self._model_available,
            "base_url": self.base_url,
            "model": self.model,
            "available_models": self._available_models,
        }
    
    def ensure_available(self, fallback_to_error: bool = True) -> bool:
        """
        Ensure Ollama is available before scanning.
        
        Args:
            fallback_to_error: If True, raises error when unavailable.
                              If False, returns False silently.
        
        Returns:
            True if available, False otherwise.
            
        Raises:
            RuntimeError: If not available and fallback_to_error is True.
        """
        if self.is_available():
            return True
        
        # Re-check in case Ollama was just started
        self._check_ollama_availability()
        if self.is_available():
            print(f"[LLMScanner] ✓ Ollama now available ({self.model})")
            return True
        
        if fallback_to_error:
            if not self._ollama_available:
                raise RuntimeError(
                    f"Ollama is not running at {self.base_url}. "
                    f"Start it with: ollama serve"
                )
            else:
                raise RuntimeError(
                    f"Model '{self.model}' not found in Ollama. "
                    f"Install it with: ollama pull {self.model}\n"
                    f"Available models: {', '.join(self._available_models[:5])}"
                )
        return False
            
    def _check_connection(self, url: str, timeout: float) -> bool:
        """Check if LLM server is reachable (legacy method)."""
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                res = client.get(f"{url}/api/tags")
                return res.status_code == 200
        except:
            return False

    def _httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(timeout=self.timeout, connect=self.connect_timeout)

    def _chunk_text(self, text: str) -> List[str]:
        """
        Split long text into newline-preserving chunks.

        This keeps chunk boundaries stable for mapping extraction, and avoids
        exceeding the model context window.
        """
        if len(text) <= self.max_chunk_chars:
            return [text]

        # Prefer splitting on existing newlines.
        lines = text.splitlines(keepends=True)
        chunks: List[str] = []
        buf: List[str] = []
        buf_len = 0

        def flush():
            nonlocal buf, buf_len
            if buf:
                chunks.append("".join(buf))
                buf = []
                buf_len = 0

        for line in lines:
            if buf and buf_len + len(line) > self.max_chunk_chars:
                flush()
            buf.append(line)
            buf_len += len(line)

        flush()
        return chunks

    def _detect_entities_single(self, text: str, stream: bool) -> Dict[str, str]:
        """Single-shot LLM entity detection for one chunk."""
        prompt = self._build_prompt(text)

        if stream:
            response_text = self._stream_request(prompt)
        else:
            response_text = self._sync_request(prompt)

        parsed = self._parse_response(response_text)
        return dict(parsed.pii_types)
        
    def scan_and_rewrite(self, text: str, stream: bool = True) -> AnonymizedResult:
        """
        Scan text for PII and rewrite it using LLM.
        
        Args:
            text: Input text to anonymize
            stream: Whether to use streaming API (better for long texts)
            
        Returns:
            AnonymizedResult containing rewritten text and mappings
        """
        # Chunk for long documents to avoid context overflow and huge latency.
        chunks = self._chunk_text(text)

        merged_types: Dict[str, str] = {}
        for chunk in chunks:
            try:
                types = self._detect_entities_single(chunk, stream=stream)
            except Exception as e:
                print(f"LLM Request failed on {self.base_url}: {e}")
                raise

            for orig, t in types.items():
                merged_types.setdefault(orig, t)

        # Build deterministic, reversible replacements locally (avoid relying on LLM rewrite).
        mappings: Dict[str, str] = {}
        try:
            from ..masker.placeholder import PlaceholderGenerator
        except Exception:
            PlaceholderGenerator = None

        generator = PlaceholderGenerator() if PlaceholderGenerator else None
        for original, pii_type in merged_types.items():
            if generator:
                core_type = self._map_pii_type_to_entity_type(pii_type)
                mappings[original] = generator.generate(original, core_type)

        anonymized_text = text
        for orig, repl in sorted(mappings.items(), key=lambda kv: len(kv[0]), reverse=True):
            anonymized_text = anonymized_text.replace(orig, repl)

        return AnonymizedResult(text=anonymized_text, mappings=mappings, pii_types=merged_types)

    def _build_prompt(self, text: str) -> str:
        return f"""/no_think
任务：从文本中识别个人信息（PII）实体，输出严格 JSON。

允许的 type 枚举（必须使用其一）：
PERSON, PHONE, EMAIL, IDCN, ADDRESS, DATE, HEALTH, TRACK, ACCOUNT, PASSWORD, URL, OTHER

输出要求：
1) 只输出 JSON（不要解释/不要 Markdown 代码块）
2) 格式：
{{"entities":[{{"text":"张三","type":"PERSON"}},{{"text":"13812345678","type":"PHONE"}}]}}
3) text 必须是原文子串（完全一致，包含空格/分隔符）
4) entities 去重（同一 text 只输出一次）
5) 不要把公司/学校/项目/学科/机构等当作 PII

文本：
{text}
"""

    def _sync_request(self, prompt: str) -> str:
        with httpx.Client(timeout=self._httpx_timeout(), trust_env=False) as client:
            response = client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.0,
                        "num_ctx": 4096,
                        "num_predict": 512,
                    }
                }
            )
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
            return response.json().get("response", "")

    def _stream_request(self, prompt: str) -> str:
        """Stream request to reduce TTFB (Time To First Byte) perception."""
        full_response = []
        with httpx.Client(timeout=self._httpx_timeout(), trust_env=False) as client:
            with client.stream(
                "POST", 
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": True,  # Enable streaming
                    "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 512}
                }
            ) as response:
                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")
                
                for line in response.iter_lines():
                    if not line: continue
                    try:
                        data = json.loads(line)
                        chunk = data.get("response", "")
                        full_response.append(chunk)
                        # In a real GUI app, we would yield chunk here
                        if data.get("done"): break
                    except:
                        pass
                        
        return "".join(full_response)

    def _parse_response(self, response_text: str) -> AnonymizedResult:
        """Parse detection-only JSON output from LLM (with backward-compatible fallback)."""
        detected = self._parse_json_entities(response_text)
        if detected is not None:
            return detected
        
        # Split into main sections
        parts = re.split(r"===脱敏文本===", response_text)
        if len(parts) < 2:
            # Fallback: try to guess or return raw
            return AnonymizedResult(text=response_text, mappings={}, pii_types={})
            
        content = parts[1]
        
        # Split mappings
        text_and_map = re.split(r"===映射表===", content)
        anonymized_text = text_and_map[0].strip()
        
        mappings = {}
        pii_types = {}
        
        if len(text_and_map) > 1:
            mapping_section = text_and_map[1].strip()
            
            # Track used replacements to ensure uniqueness (required for restoration)
            used_replacements = set()
            
            for line in mapping_section.split('\n'):
                line = line.strip()
                if not line or "->" not in line: continue
                
                try:
                    pii_type = "UNKNOWN"
                    # Parse: Original -> Replacement | Type
                    if "|" in line:
                        mapping_part, type_part = line.split("|", 1)
                        orig, repl = mapping_part.split("->", 1)
                        pii_type = type_part.strip()
                    else:
                        orig, repl = line.split("->", 1)
                    
                    orig = orig.strip()
                    repl = repl.strip()
                    
                    # Uniquify replacement if needed
                    base_repl = repl
                    counter = 2
                    while repl in used_replacements:
                        # Conflict! Append number or modify
                        # For generated names like "赵甲", "赵甲2" is acceptable fallback
                        # But better if we could ask LLM for unique names.
                        # For now, safe fallback:
                        repl = f"{base_repl}{counter}"
                        counter += 1
                    
                    used_replacements.add(repl)
                    
                    if pii_type:
                        pii_types[orig] = pii_type
                    mappings[orig] = repl
                except:
                    continue
                    
        return AnonymizedResult(
            text=anonymized_text,
            mappings=mappings,
            pii_types=pii_types
        )

    def _parse_json_entities(self, response_text: str) -> Optional[AnonymizedResult]:
        """Parse strict JSON output: {\"entities\": [{\"text\":...,\"type\":...}, ...]}."""
        try:
            # Extract first JSON object in the response.
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None

            payload = response_text[start : end + 1].strip()
            data = json.loads(payload)
            entities = data.get("entities")
            if not isinstance(entities, list):
                return None

            pii_types: Dict[str, str] = {}
            # No replacements here; caller builds them.
            for item in entities:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", ""))
                t = str(item.get("type", "")).strip().upper()
                if not text.strip() or not t:
                    continue
                pii_types[text] = t

            return AnonymizedResult(text="", mappings={}, pii_types=pii_types)
        except Exception:
            return None

    def _map_pii_type_to_entity_type(self, pii_type: str):
        """Map LLM PII types to internal EntityType enum."""
        from ..core.span import EntityType as CoreEntityType

        t = (pii_type or "").strip().upper()
        mapping = {
            "PERSON": CoreEntityType.PERSON,
            "NAME": CoreEntityType.PERSON,
            "PHONE": CoreEntityType.PHONE,
            "PHONE_NUMBER": CoreEntityType.PHONE,
            "EMAIL": CoreEntityType.EMAIL,
            "EMAIL_ADDRESS": CoreEntityType.EMAIL,
            "ID": CoreEntityType.IDCN,
            "IDCN": CoreEntityType.IDCN,
            "CN_ID_CARD": CoreEntityType.IDCN,
            "ADDRESS": CoreEntityType.ADDRESS,
            "LOCATION": CoreEntityType.ADDRESS,
            "DATE": CoreEntityType.DATE_TIME,
            "DATE_TIME": CoreEntityType.DATE_TIME,
            "URL": CoreEntityType.URL,
        }
        return mapping.get(t, CoreEntityType.UNKNOWN)

# Global instance
_scanner: Optional[LLMScanner] = None

def get_llm_scanner() -> LLMScanner:
    global _scanner
    if _scanner is None:
        _scanner = LLMScanner()
    return _scanner
