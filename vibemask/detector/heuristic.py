"""
Heuristic detector for Chinese names.
L2 detection layer - generates candidates for LLM judgment.
"""

import re
from typing import List, Set
from ..core.span import Span, EntityType, SourceType


# ============================================================
# Chinese Surname Database
# ============================================================

# Top 300+ most common Chinese surnames (extended coverage)
COMMON_SURNAMES: Set[str] = {
    # Top 100 (covers ~85% of population)
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
    # Additional common surnames (101-200)
    "温", "康", "施", "文", "牛", "樊", "葛", "常", "邢", "安",
    "裴", "倪", "庄", "聂", "章", "鲁", "岳", "齐", "伍", "霍",
    "翟", "柳", "焦", "卞", "查", "管", "柏", "祝", "蓝", "单",
    "解", "童", "应", "路", "穆", "靳", "涂", "詹", "殷", "纪",
    "舒", "祁", "柯", "米", "梅", "尚", "边", "盛", "甄", "耿",
    "宁", "苗", "凌", "栗", "贝", "庞", "项", "颜", "欧", "阮",
    "辛", "兰", "符", "游", "连", "关", "荣", "虞", "乔", "滕",
    "褚", "喻", "郁", "容", "牟", "鲍", "卜", "臧", "仇", "练",
    # Additional less common but valid surnames (201-300+)
    "晏", "储", "席", "卓", "成", "池", "仲", "鞠", "芮", "禹",
    "班", "邬", "寇", "简", "阳", "巫", "束", "扈", "蒯", "甘",
    "房", "缪", "沙", "车", "巩", "隋", "楚", "伊", "刁", "翁",
    "菅", "娄", "麦", "荆", "郜", "来", "房", "党", "植", "原",
    "惠", "暴", "冀", "隆", "封", "都", "邝", "茹", "养", "燕",
    "艾", "鄢", "宗", "包", "全", "申", "俞", "佟", "言", "花",
}

# Compound surnames (2 characters)
COMPOUND_SURNAMES: Set[str] = {
    "欧阳", "司马", "诸葛", "上官", "东方", "皇甫", "令狐", "慕容",
    "夏侯", "公孙", "宇文", "尉迟", "长孙", "轩辕", "南宫", "端木",
    "独孤", "百里", "呼延", "太史", "公冶", "司徒", "东郭", "羊舌",
}

# Context trigger words (increase confidence when nearby)
TRIGGER_WORDS: List[str] = [
    "老师", "同学", "家长", "联系人", "收件人", "抄送", "致电",
    "微信", "姓名", "学生", "负责人", "经理", "主任", "先生", "女士",
    "医生", "护士", "教授", "博士", "院长", "处长", "局长", "书记",
    "员工", "职员", "专员", "顾问", "总监", "董事", "会计", "律师",
]

# Colon patterns for structured data
COLON_PATTERNS = [
    r'(?:姓名|联系人|负责人|老师|家长|学生|员工|收件人)[：:]\s*',
    r'(?:name|Name|NAME)[：:]\s*',
]


# URL pattern to exclude from name detection
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')

def detect_chinese_names(text: str, strict: bool = False, exclude_ranges: List[tuple] = None) -> List[Span]:
    """
    Detect potential Chinese names using heuristics.
    
    This is L2 detection - generates candidates that should be
    verified by LLM or user confirmation.
    
    Args:
        text: Input text to scan
        strict: If True, only return high-confidence candidates
        exclude_ranges: List of (start, end) tuples to exclude from detection
        
    Returns:
        List of candidate Span objects
    """
    candidates = []
    
    # Build exclusion zones (URLs should not be parsed for names)
    excluded = set()
    if exclude_ranges:
        for start, end in exclude_ranges:
            for i in range(start, end):
                excluded.add(i)
    
    # Auto-detect URLs and exclude them
    for match in URL_PATTERN.finditer(text):
        for i in range(match.start(), match.end()):
            excluded.add(i)
    
    # Strategy 1: Colon structure detection
    candidates.extend(_detect_colon_names(text))
    
    # Strategy 2: Surname + name length heuristic
    candidates.extend(_detect_surname_based(text, strict, excluded))
    
    # Strategy 3: Name list pattern (multiple names per line)
    candidates.extend(_detect_name_list(text))
    
    # Remove duplicates
    seen = set()
    unique = []
    for c in candidates:
        key = (c.start, c.end, c.text)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    
    return unique


def _detect_colon_names(text: str) -> List[Span]:
    """Detect names after colon patterns like '姓名：张三'."""
    candidates = []
    
    for pattern in COLON_PATTERNS:
        full_pattern = pattern + r'([^\s,，。；;:：\n]{2,4})'
        for match in re.finditer(full_pattern, text):
            name = match.group(1)
            name_start = match.end() - len(name)
            
            if _is_likely_name(name):
                candidates.append(Span(
                    start=name_start,
                    end=match.end(),
                    text=name,
                    type=EntityType.PERSON,
                    source=SourceType.HEURISTIC,
                    confidence=0.85,
                    reason="colon_structure",
                ))
    
    return candidates


def _detect_surname_based(text: str, strict: bool, excluded: set = None) -> List[Span]:
    """Detect names starting with common surnames."""
    candidates = []
    # Balanced threshold: 0.55 allows real names with trigger words,
    # while expanded non_names list blocks false positives
    min_confidence = 0.70 if strict else 0.55
    if excluded is None:
        excluded = set()
    
    # Check compound surnames first (longer match)
    for surname in COMPOUND_SURNAMES:
        for match in re.finditer(re.escape(surname), text):
            start = match.start()
            # Try 3-4 character names (surname + 1-2 given name chars)
            for name_len in [3, 4]:
                end = start + name_len
                if end <= len(text):
                    candidate = text[start:end]
                    if _is_likely_name(candidate):
                        conf = _calculate_name_confidence(text, start, end, candidate)
                        if conf >= min_confidence:
                            candidates.append(Span(
                                start=start,
                                end=end,
                                text=candidate,
                                type=EntityType.PERSON,
                                source=SourceType.HEURISTIC,
                                confidence=conf,
                                reason="compound_surname",
                            ))
    
    # Check single-character surnames
    for i, char in enumerate(text):
        # Skip if position is in excluded range (e.g., URL)
        if i in excluded:
            continue
            
        if char in COMMON_SURNAMES:
            # Skip if this is part of a longer word
            if i > 0 and text[i-1:i+1] in COMPOUND_SURNAMES:
                continue
            
            # Try 2-4 character names (prefer longer matches)
            for name_len in [3, 2, 4]:  # Prefer 3-char names (most common)
                end = i + name_len
                if end <= len(text):
                    # Skip if any position in range is excluded
                    if any(p in excluded for p in range(i, end)):
                        continue
                    candidate = text[i:end]
                    if _is_likely_name(candidate):
                        conf = _calculate_name_confidence(text, i, end, candidate)
                        if conf >= min_confidence:
                            candidates.append(Span(
                                start=i,
                                end=end,
                                text=candidate,
                                type=EntityType.PERSON,
                                source=SourceType.HEURISTIC,
                                confidence=conf,
                                reason="single_surname",
                            ))
    
    return candidates


def _detect_name_list(text: str) -> List[Span]:
    """Detect name lists (multiple short names per line or comma-separated)."""
    candidates = []
    
    # Pattern: comma or space separated 2-3 char names
    lines = text.split('\n')
    
    for line_start, line in _iter_lines_with_offset(text):
        # Check if line looks like a name list
        parts = re.split(r'[,，、\s]+', line.strip())
        
        if len(parts) >= 3:
            # Could be a name list if most parts are 2-4 chars
            name_like = [p for p in parts if 2 <= len(p) <= 4 and _is_likely_name(p)]
            
            if len(name_like) / len(parts) > 0.6:
                # This looks like a name list
                for name in name_like:
                    idx = line.find(name)
                    if idx >= 0:
                        start = line_start + idx
                        candidates.append(Span(
                            start=start,
                            end=start + len(name),
                            text=name,
                            type=EntityType.PERSON,
                            source=SourceType.HEURISTIC,
                            confidence=0.75,
                            reason="name_list",
                        ))
    
    return candidates


def _iter_lines_with_offset(text: str):
    """Iterate lines with their starting offset."""
    offset = 0
    for line in text.split('\n'):
        yield offset, line
        offset += len(line) + 1


# ============================================================
# Organization/Company Suffixes - NOT names
# ============================================================
ORG_SUFFIXES = {
    "公司", "集团", "有限", "股份", "企业", "工厂", "研究院", "研究所",
    "科技", "技术", "网络", "电子", "电气", "机械", "材料", "化工",
    "医药", "医疗", "生物", "制药", "食品", "服装", "纺织", "建筑",
    "工程", "设计", "咨询", "投资", "贸易", "物流", "运输", "通信",
    "信息", "软件", "系统", "智能", "环保", "能源", "新能源", "汽车",
    "设备", "仪器", "仪表", "检测", "制造", "加工", "印刷", "包装",
}

ORG_PREFIXES = {
    "杭州", "浙江", "上海", "北京", "深圳", "广州", "南京", "苏州",
    "宁波", "温州", "嘉兴", "绍兴", "金华", "台州", "湖州", "衢州",
    "丽水", "舟山", "中国", "国际", "亚洲", "东方", "西方", "南方", "北方",
}

# Additional checks for context
ORG_CONTEXT_WORDS = {
    "企业", "公司", "集团", "有限", "股份", "责任", "技术中心", "研发中心",
    "总部", "分公司", "子公司", "办事处", "工作室",
}


def _is_likely_name(candidate: str) -> bool:
    """Check if a string is likely a Chinese name (not organization)."""
    # Length check
    if not 2 <= len(candidate) <= 4:
        return False
    
    # Must be all Chinese characters
    if not all('\u4e00' <= c <= '\u9fff' for c in candidate):
        return False
    
    # First char should be a surname
    if candidate[0] not in COMMON_SURNAMES:
        # Check compound surname
        if len(candidate) >= 3 and candidate[:2] not in COMPOUND_SURNAMES:
            return False
    
    # CRITICAL: Check if it contains organization suffixes
    for suffix in ORG_SUFFIXES:
        if suffix in candidate:
            return False
    
    # Check if ends with typical organization characters
    org_end_chars = {"公", "司", "限", "份", "厂", "院", "所", "区", "市", "省", "县", "镇", "村"}
    if candidate[-1] in org_end_chars:
        return False
    
    # Avoid common non-name words - COMPREHENSIVE list to prevent false positives
    non_names = {
        # ========== 2-char words starting with common surnames ==========
        # 郑 (zheng) - very common surname
        "郑重", "郑州",
        # 任 (ren) - common surname  
        "任务", "任何", "任意", "任职", "任命", "任用", "任期", "任免",
        # 范 (fan) - common surname
        "范围", "范畴", "范本", "范例", "范式",
        # 高 (gao) - very common surname
        "高效", "高层", "高端", "高度", "高级", "高性", "高技", "高新", "高质", "高速",
        # 金 (jin) - common surname
        "金融", "金属", "金额", "金价", "金钱", "金牌",
        # 方 (fang) - common surname
        "方法", "方案", "方向", "方面", "方式", "方程", "方便", "方针",
        # 马 (ma) - very common surname  
        "马上", "马力", "马达", "马路",
        # 于 (yu) - common surname
        "于是", "于此",
        # 余 (yu) - common surname
        "余额", "余数", "余下", "余地",
        # 常 (chang) - common surname
        "常见", "常用", "常规", "常量", "常态", "常务",
        # 程 (cheng) - common surname
        "程序", "程度", "程式",
        # 史 (shi) - common surname
        "史料", "史记",
        # 石 (shi) - common surname
        "石油", "石化", "石材",
        # 田 (tian) - common surname
        "田野", "田地", "田间",
        # 江 (jiang) - common surname
        "江河", "江南", "江苏", "江西", "江浙",
        # 黄 (huang) - very common surname
        "黄金", "黄色", "黄河",
        # 周 (zhou) - very common surname
        "周围", "周期", "周年", "周边", "周末", "周全", "周到",
        # 吴 (wu) - common surname
        "吴越",
        # 郭 (guo) - common surname
        # 何 (he) - common surname
        "何时", "何地", "何处", "何况", "何必", "何以",
        # 曹 (cao) - common surname
        # 蔡 (cai) - common surname  
        "蔡司",
        # 唐 (tang) - common surname
        "唐代", "唐朝", "唐山",
        # 宋 (song) - common surname
        "宋代", "宋朝",
        # 陈 (chen) - very common surname
        "陈述", "陈列", "陈旧", "陈设",
        # 杨 (yang) - very common surname
        "杨树",
        # 刘 (liu) - very common surname
        # 赵 (zhao) - common surname
        # 孙 (sun) - common surname
        "孙子",
        # 李 (li) - most common surname
        "李子",
        # 王 (wang) - most common surname
        "王朝", "王国", "王牌",
        # 张 (zhang) - very common surname
        "张扬", "张贴", "张力", "张开",
        
        # ========== Cities and places ==========
        "中国", "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "武汉",
        "上城", "下城", "拱墅", "西湖", "滨江", "余杭", "萧山", "临安",
        "富阳", "桐庐", "淳安", "建德", "钱塘", "临平", "浙江", "江苏",
        
        # ========== Company/org/formal terms ==========
        "公司", "集团", "有限", "科技", "网络", "互联", "股份", "企业", "工厂", "研究",
        "评价", "评估", "评审", "评定", "认定", "认证",
        "材料", "资料", "证明", "报告", "申请", "审批", "批准", "承诺",
        "规范", "规定", "规则", "规划", "法规", "法律", "法定",
        "管理", "管辖", "管控",
        
        # ========== Common action words ==========
        "提高", "提升", "提供", "提出", "提交", "提请", "提案",
        "普适", "普通", "普遍", "普及",
        "验证", "验收", "验算",
        "承担", "承诺", "承接", "承办",
        
        # ========== Tech terms ==========
        "物联", "物理", "物品", "物资", "物业",
        "手机", "电话", "地址", "邮箱", "微信",
        "技术", "信息", "电子", "机械", "材料", "化工", "医药", "生物",
        "制药", "食品", "服装", "纺织", "建筑", "工程", "设计", "咨询",
        "投资", "贸易", "物流", "运输", "通信", "软件", "系统", "智能",
        "环保", "能源", "新能", "汽车", "设备", "仪器", "仪表", "检测",
        "制造", "加工", "印刷", "包装",
        "图形", "图像", "图书", "图谱",
        "计算", "计划", "计量",
        "分析", "分布", "分类", "分割",
        "算法", "学习", "训练", "模型", "数据", "网络",
        "安全", "可靠", "稳定", "高效",
        
        # ========== Time expressions ==========
        "今天", "明天", "昨天", "年度", "年份", "月份",
        
        # ========== Common words ==========
        "这里", "那里", "什么", "怎么", "为什么",
        "可以", "不能", "应该", "需要", "已经", "正在", "继续", "开始",
        "包括", "以及", "等等", "以下", "以上", "以内", "以外",
        
        # ========== 3-char common terms ==========
        "方法论", "计算机", "程序员", "物联网", "区块链", "研发费", "知识产",
        "责任人", "负责人", "联系人", "申请人", "审批人",
        "高新技", "高科技", "高质量", "高效率",
        "金融业", "金融科", "金融服",
        "范围内", "范围为",
        "周期性", "周围的",
        "任务书", "任期内", "任务量",
        
        # ========== 4-char idioms and formal phrases ==========
        "郑重承诺", "郑重声明", "郑重其事",
        "责任在内", "责任到人", "责任追究",
        "范围之内", "范围以内",
        "承诺书", 
    }
    if candidate in non_names:
        return False
        return False
    
    # Check if candidate is a prefix/suffix of common tech terms
    tech_prefixes = {"物联网", "高效性", "金融科", "方法论", "程序员", "计算机", "普适计", "区块链"}
    for term in tech_prefixes:
        if candidate == term[:len(candidate)] and len(candidate) < len(term):
            return False
    
    # Check if name ends with a title character (likely wrong boundary)
    title_chars = {"老", "师", "教", "授", "博", "士", "生", "员", "长", "官", "主", "任"}
    if candidate[-1] in title_chars:
        return False
    
    return True


def _is_in_org_context(text: str, start: int, end: int) -> bool:
    """Check if the candidate is in an organization context."""
    # Look at surrounding text
    context_start = max(0, start - 30)
    context_end = min(len(text), end + 30)
    context = text[context_start:context_end]
    
    # Check for organization keywords
    for word in ORG_CONTEXT_WORDS:
        if word in context:
            return True
    
    # Check if preceded by city name (likely company)
    before_text = text[max(0, start - 10):start]
    for prefix in ORG_PREFIXES:
        if prefix in before_text:
            return True
    
    return False


def _calculate_name_confidence(text: str, start: int, end: int, name: str) -> float:
    """Calculate confidence score based on context."""
    base_confidence = 0.5
    
    # Title characters that often follow names
    title_chars = {"老", "师", "教", "授", "博", "士", "生", "员", "长", "官", "主", "任"}
    
    # CRITICAL: Reduce confidence if in organization context
    if _is_in_org_context(text, start, end):
        return 0.0  # Not a name, it's part of organization
    
    # Check for nearby trigger words
    context_start = max(0, start - 20)
    context_end = min(len(text), end + 20)
    context = text[context_start:context_end]
    
    for trigger in TRIGGER_WORDS:
        if trigger in context:
            base_confidence += 0.25  # Stronger boost for trigger words
            break
    
    # Check for colon before name (strong indicator)
    before = text[max(0, start-3):start]
    if '：' in before or ':' in before:
        base_confidence += 0.3
    
    # Longer names are more likely to be actual names (but 4 char is suspicious)
    if len(name) == 3:
        base_confidence += 0.1
    elif len(name) == 4:
        base_confidence -= 0.1  # 4-char names are less common
    
    # Check if standalone (not part of longer text)
    # Names should have boundaries
    if start > 0:
        char_before = text[start - 1]
        if '\u4e00' <= char_before <= '\u9fff':
            base_confidence -= 0.3  # Part of longer word
    
    if end < len(text):
        char_after = text[end]
        if '\u4e00' <= char_after <= '\u9fff':
            # Don't penalize if followed by title character (e.g., 老师, 教授, 博士)
            if char_after not in title_chars:
                base_confidence -= 0.3  # Part of longer word
    
    # Cap and floor
    return max(0.0, min(base_confidence, 0.9))


def has_trigger_context(text: str, position: int, window: int = 50) -> bool:
    """Check if there are trigger words near the given position."""
    start = max(0, position - window)
    end = min(len(text), position + window)
    context = text[start:end]
    
    return any(trigger in context for trigger in TRIGGER_WORDS)

