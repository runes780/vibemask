"""
Presidio Engine Integration for VibeMask.
Combines: Rules (L1) + Chinese spaCy NER (L2) + Ollama Judge (L3)
"""

from typing import List, Dict, Optional, Any
from dataclasses import dataclass

# Presidio imports
try:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult as PresidioResult
    from presidio_analyzer import Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    PRESIDIO_AVAILABLE = True
except ImportError:
    PRESIDIO_AVAILABLE = False


@dataclass  
class VibeMaskResult:
    """Extended recognition result with VibeMask metadata."""
    entity_type: str
    start: int
    end: int
    score: float
    text: str
    source: str  # "regex", "spacy", "ollama"
    
    def to_presidio(self) -> "PresidioResult":
        """Convert to Presidio RecognizerResult."""
        if PRESIDIO_AVAILABLE:
            return PresidioResult(
                entity_type=self.entity_type,
                start=self.start,
                end=self.end,
                score=self.score
            )
        return None


class ChinesePhoneRecognizer(PatternRecognizer if PRESIDIO_AVAILABLE else object):
    """Chinese phone number recognizer."""
    
    def __init__(self):
        if not PRESIDIO_AVAILABLE:
            return
        
        patterns = [
            Pattern(
                name="chinese_phone",
                regex=r"1[3-9]\d{9}",
                score=0.9
            ),
            Pattern(
                name="chinese_phone_sep",
                regex=r"1[3-9]\d[- ]?\d{4}[- ]?\d{4}",
                score=0.85
            ),
        ]
        
        super().__init__(
            supported_entity="PHONE_NUMBER",
            patterns=patterns,
            name="ChinesePhoneRecognizer",
            supported_language="zh"
        )


class ChineseIDRecognizer(PatternRecognizer if PRESIDIO_AVAILABLE else object):
    """Chinese ID card number recognizer."""
    
    def __init__(self):
        if not PRESIDIO_AVAILABLE:
            return
        
        patterns = [
            Pattern(
                name="chinese_id_18",
                regex=r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
                score=0.95
            ),
            Pattern(
                name="chinese_id_15",
                regex=r"[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}",
                score=0.85
            ),
        ]
        
        super().__init__(
            supported_entity="CN_ID_CARD",
            patterns=patterns,
            name="ChineseIDRecognizer",
            supported_language="zh"
        )


class ChineseNameRecognizer(PatternRecognizer if PRESIDIO_AVAILABLE else object):
    """Chinese name recognizer using common surname patterns."""
    
    SURNAMES = "王李张刘陈杨黄赵周吴徐孙马胡朱郭何罗高林郑梁谢唐许冯曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆范汪廖石金韦贾夏傅方邹熊孟秦白江阎薛尹段雷侯龙史陶黎贺毛郝顾龚邵邱钱戴严"
    
    def __init__(self):
        if not PRESIDIO_AVAILABLE:
            return
        
        # Build pattern for common Chinese names (2-4 chars starting with surname)
        surname_pattern = f"[{self.SURNAMES}]"
        patterns = [
            Pattern(
                name="chinese_name_2",
                regex=f"{surname_pattern}[\\u4e00-\\u9fa5]",
                score=0.4  # Lower score, needs context
            ),
            Pattern(
                name="chinese_name_3",
                regex=f"{surname_pattern}[\\u4e00-\\u9fa5]{{2}}",
                score=0.5
            ),
            Pattern(
                name="chinese_name_4",
                regex=f"{surname_pattern}[\\u4e00-\\u9fa5]{{3}}",
                score=0.4
            ),
        ]
        
        super().__init__(
            supported_entity="PERSON",
            patterns=patterns,
            name="ChineseNameRecognizer",
            supported_language="zh",
            context=["姓名", "名字", "联系人", "先生", "女士", "同学", "老师"]
        )


class VibeMaskPresidioEngine:
    """
    VibeMask Presidio-based detection engine.
    
    Architecture:
    - L1: Pattern/Regex recognizers (high confidence)
    - L2: spaCy Chinese NER (medium confidence)
    - L3: Ollama judge for verification (optional)
    """
    
    def __init__(
        self,
        languages: List[str] = None,
        use_spacy: bool = True,
        use_ollama: bool = False,
        ollama_model: str = "qwen2.5:0.5b",
        ollama_url: str = "http://localhost:11434"
    ):
        if not PRESIDIO_AVAILABLE:
            raise ImportError(
                "Presidio is not installed. "
                "Install with: pip install presidio-analyzer presidio-anonymizer"
            )
        
        self.languages = languages or ["zh", "en", "ja", "es"]
        self.use_spacy = use_spacy
        self.use_ollama = use_ollama
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url
        
        self._analyzer = None
        self._anonymizer = None
        self._setup_engine()
    
    def _setup_engine(self):
        """Initialize the Presidio analyzer with multi-language support."""
        # Available spaCy models for each language
        SPACY_MODELS = {
            "zh": "zh_core_web_sm",
            "en": "en_core_web_sm",
            "ja": "ja_core_news_sm",
            "es": "es_core_news_sm",
        }
        
        # Configure NLP engine with available languages
        if self.use_spacy:
            available_models = []
            available_languages = []
            
            for lang in self.languages:
                if lang in SPACY_MODELS:
                    try:
                        import spacy
                        spacy.load(SPACY_MODELS[lang])
                        available_models.append({
                            "lang_code": lang, 
                            "model_name": SPACY_MODELS[lang]
                        })
                        available_languages.append(lang)
                    except OSError:
                        # Model not installed, skip
                        pass
            
            if available_models:
                try:
                    nlp_config = {
                        "nlp_engine_name": "spacy",
                        "models": available_models
                    }
                    provider = NlpEngineProvider(nlp_configuration=nlp_config)
                    nlp_engine = provider.create_engine()
                    self.languages = available_languages
                except Exception:
                    nlp_engine = None
                    self.use_spacy = False
            else:
                nlp_engine = None
                self.use_spacy = False
        else:
            nlp_engine = None
        
        # Create analyzer
        if nlp_engine:
            self._analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine,
                supported_languages=self.languages
            )
        else:
            self._analyzer = AnalyzerEngine(
                supported_languages=["en"]
            )
        
        # Add Chinese pattern recognizers
        self._analyzer.registry.add_recognizer(ChinesePhoneRecognizer())
        self._analyzer.registry.add_recognizer(ChineseIDRecognizer())
        self._analyzer.registry.add_recognizer(ChineseNameRecognizer())
        
        # Create anonymizer
        self._anonymizer = AnonymizerEngine()
    
    def analyze(
        self,
        text: str,
        language: str = "zh",
        entities: List[str] = None
    ) -> List[VibeMaskResult]:
        """
        Analyze text for PII entities.
        
        Args:
            text: Text to analyze
            language: Language code ("zh" or "en")
            entities: Optional list of entity types to detect
            
        Returns:
            List of VibeMaskResult with detected entities
        """
        # Get results from Presidio
        presidio_results = self._analyzer.analyze(
            text=text,
            language=language,
            entities=entities
        )
        
        # Convert to VibeMask results
        results = []
        for r in presidio_results:
            result = VibeMaskResult(
                entity_type=r.entity_type,
                start=r.start,
                end=r.end,
                score=r.score,
                text=text[r.start:r.end],
                source="presidio"
            )
            results.append(result)
        
        # L3: Ollama verification (optional)
        if self.use_ollama and results:
            results = self._verify_with_ollama(results, text)
        
        return results
    
    def _verify_with_ollama(
        self,
        candidates: List[VibeMaskResult],
        context: str
    ) -> List[VibeMaskResult]:
        """Use Ollama to verify/filter candidates."""
        try:
            import httpx
            
            # Build verification prompt
            entities_text = ", ".join([f'"{c.text}"' for c in candidates[:10]])
            prompt = f"""判断以下文本中标记的内容是否确实是敏感信息（如人名、电话、身份证等）。
            
文本片段："{context[:500]}"

待验证实体：{entities_text}

对于每个实体，回复 Y（是敏感信息）或 N（不是）。格式：实体:Y/N，逗号分隔。"""

            response = httpx.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False
                },
                trust_env=False,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result_text = response.json().get("response", "")
                # Parse response and filter candidates
                verified = []
                for candidate in candidates:
                    # Simple check: if entity text appears with :Y or :y
                    if f"{candidate.text}:Y" in result_text or f"{candidate.text}:y" in result_text:
                        candidate.source = "ollama_verified"
                        candidate.score = min(candidate.score + 0.1, 1.0)
                        verified.append(candidate)
                    elif f"{candidate.text}:N" in result_text or f"{candidate.text}:n" in result_text:
                        # Marked as not PII, skip
                        pass
                    else:
                        # No explicit judgment, keep with original score
                        verified.append(candidate)
                return verified
        except Exception as e:
            # Ollama failed, return original candidates
            pass
        
        return candidates
    
    def anonymize(
        self,
        text: str,
        results: List[VibeMaskResult] = None,
        operators: Dict[str, dict] = None
    ) -> tuple:
        """
        Anonymize text based on analysis results.
        
        Args:
            text: Text to anonymize
            results: Optional pre-computed results (will analyze if None)
            operators: Optional operator configs for each entity type
            
        Returns:
            Tuple of (anonymized_text, mapping_dict)
        """
        if results is None:
            results = self.analyze(text)
        
        # Convert to Presidio format
        presidio_results = [r.to_presidio() for r in results if r.to_presidio()]
        
        # Default operators - use Chinese pseudo names
        if operators is None:
            operators = {
                "DEFAULT": OperatorConfig("replace", {"new_value": "<MASKED>"}),
                "PERSON": OperatorConfig("replace", {"new_value": "<人名>"}),
                "PHONE_NUMBER": OperatorConfig("mask", {"chars_to_mask": 5, "masking_char": "*"}),
            }
        
        # Anonymize
        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=presidio_results,
            operators=operators
        )
        
        # Build mapping for restoration
        mapping = {}
        for r in results:
            original = r.text
            # Find in anonymized text what it became
            # This is approximate - actual mapping would need tracking
            mapping[original] = r.entity_type
        
        return anonymized.text, mapping
    
    def get_supported_entities(self) -> List[str]:
        """Get list of supported entity types."""
        return self._analyzer.get_supported_entities()


# ============================================================
# Convenience functions
# ============================================================

_engine: Optional[VibeMaskPresidioEngine] = None


def get_engine(
    use_ollama: bool = False,
    ollama_model: str = "qwen2.5:0.5b"
) -> VibeMaskPresidioEngine:
    """Get or create the global Presidio engine."""
    global _engine
    if _engine is None:
        _engine = VibeMaskPresidioEngine(
            use_ollama=use_ollama,
            ollama_model=ollama_model
        )
    return _engine


def analyze_text(
    text: str,
    language: str = "zh",
    use_ollama: bool = False
) -> List[VibeMaskResult]:
    """Analyze text for PII using the global engine."""
    engine = get_engine(use_ollama=use_ollama)
    return engine.analyze(text, language=language)


def is_presidio_available() -> bool:
    """Check if Presidio is available."""
    return PRESIDIO_AVAILABLE
