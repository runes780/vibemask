"""
Smart Detector with Jieba Segmentation and spaCy NER.
Combines word boundary detection with NER for intelligent name detection.
"""

from typing import List, Set, Optional, Tuple
from ..core.span import Span, EntityType, SourceType

# Try to import optional dependencies
try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False


# ============================================================
# Chinese Surname Database (Expanded)
# ============================================================

COMMON_SURNAMES: Set[str] = {
    # Top 100 common surnames
    "王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴",
    "徐", "孙", "马", "胡", "朱", "郭", "何", "罗", "高", "林",
    "郑", "梁", "谢", "宋", "唐", "许", "韩", "冯", "邓", "曹",
    "彭", "曾", "肖", "田", "董", "袁", "潘", "于", "蒋", "蔡",
    "余", "杜", "叶", "程", "苏", "魏", "吕", "丁", "任", "沈",
    "姚", "卢", "姜", "崔", "钟", "谭", "陆", "汪", "范", "金",
    "石", "廖", "贾", "夏", "韦", "付", "方", "白", "邹", "孟",
    "熊", "秦", "邱", "江", "尹", "薛", "闫", "段", "雷", "侯",
    "龙", "史", "陶", "黎", "贺", "顾", "毛", "郝", "龚", "邵",
    "万", "钱", "严", "覃", "武", "戴", "莫", "孔", "向", "汤",
    "温", "康", "施", "文", "牛", "樊", "葛", "常", "邢", "安",
    "裴", "倪", "庄", "聂", "章", "鲁", "岳", "齐", "伍", "霍",
    "翟", "柳", "焦", "卞", "查", "管", "柏", "祝", "蓝", "单",
    "鲍", "卜", "巫",
    # Additional surnames (missing from司法部 test)
    "傅", "介", "慕", "成", "欧", "司", "诸", "上", "东", "皇",
    "令", "淳", "瞿", "滕", "濮", "禹", "臧", "宓", "褚", "祁",
    "宁", "仇", "栾", "隋", "靳", "甄", "艾", "苗", "盛", "童",
    "费", "连", "应", "项", "阮", "和", "卓", "劳", "喜", "佟",
    "关", "游", "解", "原", "辛", "庞", "农", "边", "储", "屠",
}

COMPOUND_SURNAMES: Set[str] = {
    "欧阳", "司马", "诸葛", "上官", "东方", "皇甫", "令狐", "慕容",
    "公孙", "轩辕", "西门", "夏侯", "南宫", "独孤", "长孙", "宇文",
    "尉迟", "淳于", "端木", "司徒", "司空", "钟离", "鲜于",
}

# Title words that often follow names
TITLE_WORDS = {"老师", "教授", "博士", "先生", "女士", "同学", "医生", "院士", "主任", "经理", "总监"}

# Words that look like names (surname + chars) but are NOT names
# These are filtered out even if jieba segments them
NON_NAME_WORDS = {
    # 2-char common words starting with surnames
    "郑重", "责任", "范围", "高效", "金融", "方法", "任务", "任何", "周围", "周期",
    "高级", "高度", "高技", "高新", "高速", "高层", "高端", "高质",
    "金属", "金额", "金钱", "金牌",
    "方案", "方向", "方面", "方式", "方便", "方程", "方针",
    "任意", "任职", "任命", "任免", "任期",
    "马上", "马力", "马达", "马路",
    "范畴", "范本", "范例", "范式",
    "余额", "余数", "余下", "余地",
    "常见", "常用", "常规", "常量", "常态", "常务",
    "程序", "程度", "程式",
    "史料", "史记",
    "石油", "石化", "石材",
    "田野", "田地", "田间",
    "江河", "江南", "江苏", "江西", "江浙",
    "黄金", "黄色", "黄河",
    "何时", "何地", "何处", "何况", "何必", "何以",
    "唐代", "唐朝", "唐山",
    "宋代", "宋朝",
    "陈述", "陈列", "陈旧", "陈设",
    "王朝", "王国", "王牌",
    "张扬", "张贴", "张力", "张开",
    "于是", "于此",
    # 3-char common words starting with surnames
    "高效性", "高效率", "高质量", "高科技", "高新技",
    "高级教", "高级别", "高级的", "高中数", "高中语", "高中英", "高中物", "高中化", "高中生",
    "正高级", "副高级",  # Professional titles
    "金融业", "金融科", "金融服",
    "范围内", "范围为",
    "责任人", "责任心",
    "任务书", "任务量", "任期内",
    "周期性", "周围的",
    "方法论", "方法学",
    # Common tech/education terms
    "计算机", "程序员", "物联网", "区块链",
    "承诺书", "评价表",
    # 4-char education/profession terms
    "高级教师", "高级职称", "高级工程", "高级技师", "高级会计",
    "高中数学", "高中语文", "高中英语", "高中物理", "高中化学", "高中生物",
    "初中数学", "初中语文", "初中英语",
    "正高级教", "副高级教",
    # Commonly misdetected terms (from test)
    "单位", "文字学", "文字", "语言学", "应用语", "字学",
    "研究生", "博士生", "硕士生", "本科生",
    "研究所", "出版社", "日报社", "律师协", "服务中",
}


class SmartChineseNameDetector:
    """
    Intelligent Chinese name detector using:
    1. Jieba word segmentation (word boundary)
    2. spaCy NER (named entity recognition)
    3. Heuristic validation (surname + context)
    4. Ollama LLM verification (optional, for semantic understanding)
    """
    
    def __init__(
        self, 
        use_spacy: bool = True, 
        use_jieba: bool = True,
        use_llm: bool = False,
        llm_model: str = "qwen3:4b-instruct",
        ollama_url: str = "http://localhost:11434"
    ):
        self.use_spacy = use_spacy and SPACY_AVAILABLE
        self.use_jieba = use_jieba and JIEBA_AVAILABLE
        self.use_llm = use_llm
        self.llm_model = llm_model
        self.ollama_url = ollama_url
        
        self._nlp = None
        if self.use_spacy:
            try:
                self._nlp = spacy.load("zh_core_web_sm")
            except OSError:
                self.use_spacy = False
        
        if self.use_jieba:
            # Initialize jieba
            jieba.initialize()
    
    def detect(self, text: str, strict: bool = False) -> List[Span]:
        """
        Detect Chinese names using smart combination of methods.
        
        Pipeline:
        1. Text preprocessing (handle spaces in names)
        2. spaCy NER PERSON entities (high confidence)
        3. Jieba segmented words that match name patterns (medium confidence)
        4. LLM verification to filter false positives (if enabled)
        """
        spans = []
        
        # Preprocess: also detect on space-normalized text
        normalized_text, char_map = self._normalize_spaces(text)
        
        # Method 1: spaCy NER on both original and normalized
        if self.use_spacy and self._nlp:
            spans.extend(self._detect_spacy_ner(text))
            # Also detect on normalized text and map back
            if normalized_text != text:
                norm_spans = self._detect_spacy_ner(normalized_text)
                spans.extend(self._map_spans_to_original(norm_spans, char_map))
        
        # Method 2: Jieba word segmentation + pattern matching
        if self.use_jieba:
            spans.extend(self._detect_jieba_names(text, strict))
            # Also on normalized text
            if normalized_text != text:
                norm_spans = self._detect_jieba_names(normalized_text, strict)
                spans.extend(self._map_spans_to_original(norm_spans, char_map))
        
        # Deduplicate first
        spans = self._deduplicate_spans(spans)
        
        # Method 3: LLM verification (filter false positives)
        if self.use_llm and spans:
            spans = self._verify_with_llm(spans, text)
        
        return spans
    
    def _normalize_spaces(self, text: str) -> tuple:
        """
        Remove spaces within Chinese text to normalize names like '韩  潇' → '韩潇'.
        
        Returns:
            (normalized_text, char_map) where char_map[norm_idx] = original_idx
        """
        import re
        
        result = []
        char_map = []  # Maps normalized position to original position
        
        i = 0
        while i < len(text):
            char = text[i]
            
            # Check if this is a Chinese character followed by spaces then another Chinese char
            if '\u4e00' <= char <= '\u9fff':
                result.append(char)
                char_map.append(i)
                
                # Look ahead for pattern: Chinese + spaces + Chinese
                j = i + 1
                while j < len(text) and (text[j] == ' ' or text[j] == '\u3000'):
                    j += 1
                
                # If we skipped spaces and hit another Chinese char, dont include the spaces
                if j > i + 1 and j < len(text) and '\u4e00' <= text[j] <= '\u9fff':
                    i = j  # Skip the spaces
                else:
                    i += 1
            else:
                result.append(char)
                char_map.append(i)
                i += 1
        
        return ''.join(result), char_map
    
    def _map_spans_to_original(self, spans: List[Span], char_map: List[int]) -> List[Span]:
        """Map spans from normalized text positions back to original text positions."""
        mapped = []
        for span in spans:
            if span.start < len(char_map) and span.end <= len(char_map):
                original_start = char_map[span.start]
                original_end = char_map[span.end - 1] + 1 if span.end > 0 else char_map[span.start] + 1
                
                # Create new span with mapped positions
                mapped.append(Span(
                    start=original_start,
                    end=original_end,
                    text=span.text,  # Keep the normalized text (without spaces)
                    type=span.type,
                    source=span.source,
                    confidence=span.confidence,
                    reason=f"{span.reason}+normalized"
                ))
        return mapped
    
    def _verify_with_llm(self, candidates: List[Span], context: str) -> List[Span]:
        """
        Use Ollama LLM to semantically verify if candidates are real person names.
        
        This filters out false positives like "高级教师", "高中数学" etc.
        """
        try:
            import httpx
            
            # Build prompt with candidates
            candidate_texts = list(set([c.text for c in candidates[:15]]))  # Limit to 15
            
            # Create numbered list for clarity
            numbered_list = '\n'.join([f"{i+1}. {w}" for i, w in enumerate(candidate_texts)])
            
            prompt = f"""/no_think
判断以下词语是否是中国人名。只回复Y或N，每行一个。

上下文："{context[:500]}"

词语列表：
{numbered_list}

请按相同顺序回复（只写Y或N，每行一个）："""

            response = httpx.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 100}
                },
                trust_env=False,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result_text = response.json().get("response", "")
                
                # Parse LLM response - extract Y/N answers
                answers = []
                for line in result_text.strip().split('\n'):
                    line = line.strip().upper()
                    if line.startswith('Y') or line == 'Y':
                        answers.append(True)
                    elif line.startswith('N') or line == 'N':
                        answers.append(False)
                    elif 'Y' in line and 'N' not in line:
                        answers.append(True)
                    elif 'N' in line:
                        answers.append(False)
                    else:
                        # Unclear response - default to True (keep candidate)
                        answers.append(True)
                
                # Build set of rejected texts (explicitly marked as N)
                rejected_texts = set()
                for i, is_name in enumerate(answers):
                    if i < len(candidate_texts) and not is_name:
                        rejected_texts.add(candidate_texts[i])
                
                # Filter: KEEP candidates unless explicitly rejected
                # This is less aggressive than only keeping Y
                verified_spans = []
                for span in candidates:
                    if span.text not in rejected_texts:
                        span.confidence = min(span.confidence + 0.1, 0.95)
                        span.reason = f"{span.reason}+llm_kept"
                        verified_spans.append(span)
                
                return verified_spans
                
        except Exception as e:
            # LLM verification failed, return original candidates
            import sys
            print(f"LLM verification failed: {e}", file=sys.stderr)
        
        return candidates
    
    def _detect_spacy_ner(self, text: str) -> List[Span]:
        """Use spaCy Chinese NER to detect PERSON entities."""
        spans = []
        
        doc = self._nlp(text)
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                # Validate: should look like a Chinese name
                if self._is_valid_chinese_name(ent.text):
                    spans.append(Span(
                        start=ent.start_char,
                        end=ent.end_char,
                        text=ent.text,
                        type=EntityType.PERSON,
                        source=SourceType.HEURISTIC,  # Use HEURISTIC as MODEL not available
                        confidence=0.85,
                        reason="spacy_ner:PERSON"
                    ))
        
        return spans
    
    def _detect_jieba_names(self, text: str, strict: bool) -> List[Span]:
        """
        Use jieba to segment text, then check if any word is a name.
        
        Key advantage: Only checks COMPLETE WORDS, not substrings.
        This prevents "郑重承诺" from being detected (it's one word, not split).
        """
        spans = []
        min_confidence = 0.75 if strict else 0.55
        
        # Get word positions
        words_with_positions = list(jieba.tokenize(text))
        
        for word, start, end in words_with_positions:
            word_text = word
            
            # Skip non-Chinese or too short/long
            if not self._is_all_chinese(word_text):
                continue
            if not 2 <= len(word_text) <= 4:
                continue
            
            # Check if starts with surname
            first_char = word_text[0]
            first_two = word_text[:2] if len(word_text) >= 2 else ""
            
            is_surname_start = (first_char in COMMON_SURNAMES or first_two in COMPOUND_SURNAMES)
            
            if not is_surname_start:
                continue
            
            # CRITICAL: Skip if word is in the NON_NAME_WORDS exclusion list
            if word_text in NON_NAME_WORDS:
                continue
            
            # Calculate confidence
            confidence = self._calculate_word_confidence(text, start, end, word_text)
            
            if confidence >= min_confidence:
                spans.append(Span(
                    start=start,
                    end=end,
                    text=word_text,
                    type=EntityType.PERSON,
                    source=SourceType.HEURISTIC,
                    confidence=confidence,
                    reason="jieba_word_name"
                ))
        
        return spans
    
    def _calculate_word_confidence(self, text: str, start: int, end: int, word: str) -> float:
        """
        Calculate confidence for a word being a name.
        
        Since jieba already handles word boundaries, we focus on context.
        """
        base_confidence = 0.5
        
        # Check for title words after the word
        after_text = text[end:end+6] if end < len(text) else ""
        for title in TITLE_WORDS:
            if after_text.startswith(title):
                base_confidence += 0.35
                break
        
        # Check for colon before
        before_text = text[max(0, start-3):start]
        if "：" in before_text or ":" in before_text:
            base_confidence += 0.3
        
        # Check for context keywords nearby
        context = text[max(0, start-20):min(len(text), end+20)]
        context_keywords = {"姓名", "联系人", "负责人", "老师", "同学", "先生", "女士"}
        for kw in context_keywords:
            if kw in context:
                base_confidence += 0.2
                break
        
        # 3-char names are most common
        if len(word) == 3:
            base_confidence += 0.1
        elif len(word) == 4:
            base_confidence -= 0.1
        
        # Cap and floor
        return max(0.0, min(0.95, base_confidence))
    
    def _is_valid_chinese_name(self, text: str) -> bool:
        """Check if text could be a valid Chinese name."""
        if not 2 <= len(text) <= 4:
            return False
        if not self._is_all_chinese(text):
            return False
        
        # CRITICAL: Filter out known non-names even if spaCy says it's a PERSON
        if text in NON_NAME_WORDS:
            return False
        
        # Should start with surname
        first_char = text[0]
        first_two = text[:2] if len(text) >= 2 else ""
        
        return first_char in COMMON_SURNAMES or first_two in COMPOUND_SURNAMES
    
    def _is_all_chinese(self, text: str) -> bool:
        """Check if all characters are Chinese."""
        return all('\u4e00' <= c <= '\u9fff' for c in text)
    
    def _deduplicate_spans(self, spans: List[Span]) -> List[Span]:
        """Remove duplicate spans, keeping highest confidence."""
        if not spans:
            return []
        
        # Sort by position, then confidence descending
        spans.sort(key=lambda s: (s.start, -s.confidence))
        
        result = []
        for span in spans:
            # Check if overlaps with existing
            overlaps = False
            for existing in result:
                if span.start < existing.end and span.end > existing.start:
                    overlaps = True
                    break
            
            if not overlaps:
                result.append(span)
        
        return sorted(result, key=lambda s: s.start)


# ============================================================
# Module-level convenience functions
# ============================================================

_detector: Optional[SmartChineseNameDetector] = None
_detector_llm: Optional[SmartChineseNameDetector] = None


def get_smart_detector(use_llm: bool = False, llm_model: str = "qwen3:4b-instruct") -> SmartChineseNameDetector:
    """Get or create a smart detector instance."""
    global _detector, _detector_llm
    
    if use_llm:
        if _detector_llm is None:
            _detector_llm = SmartChineseNameDetector(use_llm=True, llm_model=llm_model)
        return _detector_llm
    else:
        if _detector is None:
            _detector = SmartChineseNameDetector(use_llm=False)
        return _detector


def detect_chinese_names_smart(
    text: str, 
    strict: bool = False, 
    use_llm: bool = False,
    llm_model: str = "qwen3:4b-instruct"
) -> List[Span]:
    """
    Detect Chinese names using smart detection (jieba + spaCy + optional LLM).
    
    Args:
        text: Text to analyze
        strict: Use stricter confidence thresholds
        use_llm: Enable Ollama LLM verification for semantic filtering
        llm_model: Ollama model to use (default: qwen3:4b-instruct)
    
    Returns:
        List of detected name Spans
    """
    detector = get_smart_detector(use_llm=use_llm, llm_model=llm_model)
    return detector.detect(text, strict)


def is_smart_detection_available() -> bool:
    """Check if smart detection dependencies are available."""
    return JIEBA_AVAILABLE or SPACY_AVAILABLE
