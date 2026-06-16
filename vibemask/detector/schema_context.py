"""
Schema/context detector for structured Chinese Office documents.

This layer targets high-signal labels that survive text flattening, such as
"姓名：张三" or "学号：20240101". It complements Privacy Filter, which is
contextual but not table-schema aware.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..core.span import EntityType, SourceType, Span


PERSON_LABELS = (
    "姓名",
    "申报人",
    "联系人",
    "负责人",
    "申请人",
    "填报人",
)
ACCOUNT_LABELS = (
    "学号",
    "工号",
    "账号",
    "帐号",
    "账户",
    "银行账号",
    "银行卡号",
    "证件号",
)
SECRET_LABELS = (
    "api key",
    "apikey",
    "api_key",
    "token",
    "secret",
    "password",
    "passwd",
    "密码",
    "密钥",
)


PERSON_VALUE = r"([\u4e00-\u9fff]{2,4})"
ACCOUNT_VALUE = r"([A-Za-z0-9][A-Za-z0-9_-]{5,31})"
SECRET_VALUE = r"([A-Za-z0-9][A-Za-z0-9_.:/+=-]{5,127})"
SEPARATOR = r"(?:[ \t]*[：:=][ \t]*|[ \t]*\n[ \t]*)"


NON_PERSON_SUFFIXES = (
    "局",
    "厅",
    "委",
    "办",
    "院",
    "校",
    "处",
    "科",
    "所",
    "中心",
    "公司",
    "单位",
    "大学",
    "学院",
    "学校",
    "教育局",
)

NON_PERSON_VALUES = {
    "专业",
    "个人主页",
    "手机号码",
    "联系电话",
    "联系方式",
    "邮箱",
    "电子邮箱",
    "单位",
    "所在单位",
    "学院",
    "学校",
    "部门",
    "职称",
    "职务",
    "奖项",
    "序号",
    "学历学位",
    "学历",
    "学位",
    "所学专业",
    "毕业院校",
    "性别",
    "民族",
    "籍贯",
    "政治面貌",
}


def detect_schema_context(text: str) -> list[Span]:
    """Detect sensitive values that are explicitly introduced by field labels."""
    if not text:
        return []

    spans: list[Span] = []
    spans.extend(_detect_person_fields(text))
    spans.extend(_detect_account_fields(text))
    spans.extend(_detect_secret_fields(text))
    return spans


def _detect_person_fields(text: str) -> Iterable[Span]:
    labels = "|".join(re.escape(label) for label in PERSON_LABELS)
    pattern = re.compile(rf"(?:{labels}){SEPARATOR}{PERSON_VALUE}")

    for match in pattern.finditer(text):
        value = match.group(1)
        if _looks_like_public_org(value):
            continue
        yield Span(
            start=match.start(1),
            end=match.end(1),
            text=value,
            type=EntityType.PERSON,
            source=SourceType.SCHEMA,
            confidence=0.98,
            reason="schema:person_field",
        )


def _detect_account_fields(text: str) -> Iterable[Span]:
    labels = "|".join(re.escape(label) for label in ACCOUNT_LABELS)
    pattern = re.compile(rf"(?:{labels}){SEPARATOR}{ACCOUNT_VALUE}", re.IGNORECASE)

    for match in pattern.finditer(text):
        value = match.group(1)
        yield Span(
            start=match.start(1),
            end=match.end(1),
            text=value,
            type=EntityType.ACCOUNT_NUMBER,
            source=SourceType.SCHEMA,
            confidence=0.95,
            reason="schema:account_field",
        )


def _detect_secret_fields(text: str) -> Iterable[Span]:
    labels = "|".join(re.escape(label) for label in SECRET_LABELS)
    pattern = re.compile(rf"(?:{labels}){SEPARATOR}{SECRET_VALUE}", re.IGNORECASE)

    for match in pattern.finditer(text):
        value = match.group(1).rstrip("，,。；;")
        end = match.start(1) + len(value)
        yield Span(
            start=match.start(1),
            end=end,
            text=value,
            type=EntityType.SECRET,
            source=SourceType.SCHEMA,
            confidence=0.95,
            reason="schema:secret_field",
        )


def _looks_like_public_org(value: str) -> bool:
    if value in NON_PERSON_VALUES:
        return True
    return any(value.endswith(suffix) for suffix in NON_PERSON_SUFFIXES)
