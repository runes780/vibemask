"""
Post-processing for OpenAI Privacy Filter spans.

The model can occasionally attach surrounding school/work-history text to
private_date spans in Chinese resumes. This module trims that overreach into a
more useful institution span when the institution is explicit in the text.
"""

from __future__ import annotations

import re

from ..core.span import EntityType, SourceType, Span


INSTITUTION_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{2,30}(?:学校|大学|学院|幼儿园|中学|小学|师范学校)"
)


# Trailing title/role words the model attaches after a Chinese personal name.
# Multi-char titles only — single-char suffixes (总/工 as in 刘总/陈工) are
# deliberately excluded: matching them risks cutting legitimate text (总结/工作)
# and the surname+title case is a labeling judgment, not a boundary bug.
_PERSON_TITLES = (
    "老师|师傅|女士|先生|同志|同学|经理|主任|总裁|教授|医生|厂长|局长|书记|"
    "校长|工程师|律师|会计师|护士长|代表|委员|董事|顾问"
)
# Earliest point to cut: an optional "N位" quantifier followed by a title.
_PERSON_TITLE_CUT = re.compile(r"(?:[数两三四五六七八九一二二三四五六七八九十]+位)?(?:" + _PERSON_TITLES + r")")
_PERSON_TRAILING_PAREN = re.compile(r"(?:\([^)]*\)|（[^）]*）)+$")


# Column-header / field-name words. These are never valid PII *values* — they are
# labels — so any detector (schema/heuristic/model) flagging one of them exactly
# is a false positive (e.g. schema flagging the header "手机号" as a PERSON). We
# drop such spans after collection, before merging.
FIELD_LABEL_WORDS = frozenset({
    "姓名", "名字", "名称", "昵称", "本人姓名",
    "手机", "手机号", "手机号码", "联系电话", "电话", "电话号码", "座机", "固定电话",
    "办公室电话", "办公电话", "办公电话号码", "宅电", "家庭电话",
    "邮箱", "电子邮箱", "电子邮件", "email", "e-mail",
    "身份证", "身份证号", "身份证号码", "证件号", "证件号码", "证件类型", "证件",
    "地址", "住址", "通讯地址", "联系地址", "家庭住址", "常住地址",
    "工号", "员工号", "员工编号", "学号", "学籍号",
    "客户编号", "客户号", "会员号", "会员编号",
    "准考证号", "考生号", "考号",
    "性别", "年龄", "生日", "出生日期", "出生年月", "民族", "国籍",
    "备注", "说明", "类型", "状态", "序号", "编号",
})

# Common non-name words the model mis-flags as PERSON in tabular / financial
# documents (e.g. ``万元``, ``单位``, ``利润``). These are never valid person
# names, so any PERSON span matching one exactly is a false positive. Applies
# cross-source (model / heuristic / schema).
NON_PERSON_WORDS = frozenset({
    # measure / currency / finance
    "万元", "万", "亿元", "元", "美元", "人民币", "千元", "百元",
    "金额", "总额", "总计", "合计", "共计", "小计", "单价", "总价", "均价",
    "利润", "收入", "支出", "成本", "资产", "负债", "税额", "税费", "税费总额",
    "利润总额", "营业收入", "主营业务收入", "净利润", "营业额", "产值",
    "数量", "人数", "比例", "占比", "百分比", "比率",
    # org / common nouns (never a person name)
    "单位", "企业", "公司", "集团", "中心", "部门", "科室", "项目", "产品", "业务",
    "机构", "组织", "体系", "系统", "平台",
    # education / institution descriptors common in gov/edu docs
    "民办", "公办", "校办", "国办", "办学", "办学规模", "在校生", "教职工", "教职工数",
})


def is_field_label(text: str) -> bool:
    """True if ``text`` is a column-header / field-name word (not a PII value)."""
    return text.strip() in FIELD_LABEL_WORDS


def is_false_person(text: str) -> bool:
    """True if a PERSON span's text is a common non-name word or a bare single
    CJK char (always fragmentation, never a valid 2+ char name)."""
    t = text.strip()
    if not t:
        return True
    if t in NON_PERSON_WORDS:
        return True
    # Single CJK char is never a complete person name.
    if len(t) == 1 and "一" <= t <= "鿿":
        return True
    return False



# Personal-identifier labels: the value after the label IS personal data
# (identifies a natural person) and should be masked, keeping only the value
# (the label word stays in the surrounding text so the LLM still sees the field).
_PERSONAL_ID_LABELS = (
    "员工编号", "员工号", "工号",
    "学籍号", "学号",
    "准考证号", "考生号", "考号",
    "客户编号", "客户号",
    "会员编号", "会员号",
    "病历号", "就诊卡号", "健康卡号",
    "档案号", "案件号",
    "社保卡号", "社保号", "公积金号",
    "银行卡号", "卡号",
)

# Transaction / non-personal labels: the value is an order/contract/invoice
# reference, not personal data. The whole span is dropped.
_TRANSACTION_LABELS = (
    "订单编号", "订单号",
    "合同编号", "合同号", "协议编号", "协议号",
    "发票代码", "发票号码", "发票号",
    "交易流水号", "流水号", "交易号",
    "批次号", "批号",
    "运单号", "快递单号",
    "序列号", "编号", "序号",
)

_TRANSACTION_LABELS_SET = set(_TRANSACTION_LABELS)
_ALL_ID_LABELS = sorted(set(_PERSONAL_ID_LABELS) | set(_TRANSACTION_LABELS), key=len, reverse=True)
# A CJK label followed immediately by an alphanumeric value. Longest labels
# first so "客户编号" wins over the "编号" suffix.
_ID_LABEL_PREFIX = re.compile(r"^(?:" + "|".join(_ALL_ID_LABELS) + r")(?=[A-Za-z0-9])")

# Sentinel signalling "drop this span entirely".
_DROP = object()


def refine_privacy_filter_spans(spans: list[Span], text: str) -> list[Span]:
    refined: list[Span] = []
    for span in spans:
        new = _refine_private_date_overreach(span, text)
        if new is not None:
            span = new
        new = _trim_person_overreach(span, text)
        if new is not None:
            span = new
        ident = _refine_identifier_label(span, text)
        if ident is _DROP:
            continue
        if ident is not None:
            span = ident
        refined.append(span)
    return refined


def _refine_identifier_label(span: Span, text: str):
    """Strip a personal-identifier label prefix from an ACCOUNT span, or drop a
    transaction-label span. Keeps the label word in the surrounding text so a
    downstream LLM still reads "工号 {{ACCOUNT_001:…}}" and knows the field kind.
    """
    if span.type != EntityType.ACCOUNT_NUMBER:
        return None
    m = _ID_LABEL_PREFIX.match(span.text)
    if not m:
        return None
    label = m.group(0)
    if label in _TRANSACTION_LABELS_SET:
        return _DROP
    value = span.text[m.end():]
    if not value:
        return _DROP
    new_start = span.start + m.end()
    if text[new_start:span.end] != value:
        return None
    return Span(
        start=new_start,
        end=span.end,
        text=value,
        type=span.type,
        source=span.source,
        confidence=span.confidence,
        reason=(span.reason or "") + "+id_label_strip",
    )


def _trim_person_overreach(span: Span, text: str) -> Span | None:
    """Trim non-name suffixes the model attaches to a PERSON span.

    Handles trailing parentheticals (王磊（按周一...）) and trailing title/role
    words with an optional quantifier (张三丰师傅..., 东方白三位老师, 何丽女士).
    Only applies when the remainder is still a plausible name (>= 2 CJK chars),
    so it never strips a name down to a bare surname.
    """
    if span.type != EntityType.PERSON:
        return None

    s = span.text
    s = _PERSON_TRAILING_PAREN.sub("", s)
    cut = _PERSON_TITLE_CUT.search(s)
    if cut is not None:
        s = s[: cut.start()]

    if s == span.text:
        return None

    cjk = sum(1 for c in s if "一" <= c <= "鿿")
    if len(s) < 2 or cjk < 2:
        return None

    new_end = span.start + len(s)
    if text[span.start:new_end] != s:
        return None

    return Span(
        start=span.start,
        end=new_end,
        text=s,
        type=EntityType.PERSON,
        source=span.source,
        confidence=span.confidence,
        reason=(span.reason or "") + "+person_trim",
    )


def _refine_private_date_overreach(span: Span, text: str) -> Span | None:
    if span.type != EntityType.DATE_TIME:
        return None
    if "privacy_filter:private_date" not in (span.reason or ""):
        return None

    # Keep plain dates intact. Only intervene when the span contains narrative text.
    if len(span.text) <= 16 and not any("\u4e00" <= char <= "\u9fff" for char in span.text):
        return None
    if not any(marker in span.text for marker in ("学习", "毕业", "结业", "专业")):
        return None

    match = INSTITUTION_PATTERN.search(span.text)
    if not match:
        return None

    start = span.start + match.start()
    end = span.start + match.end()
    institution = text[start:end]
    return Span(
        start=start,
        end=end,
        text=institution,
        type=EntityType.ORG,
        source=SourceType.PRIVACY_FILTER,
        confidence=span.confidence,
        reason="privacy_filter:private_date_refined_org",
    )
