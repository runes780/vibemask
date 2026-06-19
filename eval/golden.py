"""
Golden evaluation dataset for VibeMask detection accuracy.

Authoring format
----------------
Each sample is authored as::

    {"id": "...", "text": "<realistic snippet>", "entities": [(substring, TYPE), ...]}

Substring-based annotations are resolved to character offsets at load time
(:func:`load_golden`), which avoids the error-prone manual offset counting and
validates that every annotated substring is actually present in the text.

Negatives come for free
-----------------------
Anything in ``text`` that is NOT listed in ``entities`` is implicitly negative:
any detection landing there is a false positive. Realistic snippets therefore
double as the false-positive guardrail (administrative regions, job titles,
project names, amounts, version numbers, ...).

Scope
-----
Organization names are intentionally NOT gold — OPF has no org label and orgs
are public, out of scope for personal-data masking. Only clearly-personal types
are annotated (PERSON, PHONE, EMAIL, IDCN, ADDRESS, URL, DATE_TIME, SECRET,
ACCOUNT_NUMBER). DATE_TIME is annotated only for clearly personal dates
(birth dates); document timestamps are left negative.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .metrics import Entity


# ---------------------------------------------------------------------------
# Seed dataset — extend this list to grow coverage.
# ---------------------------------------------------------------------------

SEED: list[dict] = [
    # --- 人事 / HR ---
    {"id": "hr_01", "text": "联系人：张三，手机号13812345678，电子邮箱zhangsan@example.com",
     "entities": [("张三", "PERSON"), ("13812345678", "PHONE"), ("zhangsan@example.com", "EMAIL")]},
    {"id": "hr_02", "text": "申报人李明华，身份证号110101198801011234，住址北京市朝阳区幸福路123号",
     "entities": [("李明华", "PERSON"), ("110101198801011234", "IDCN"), ("北京市朝阳区幸福路123号", "ADDRESS")]},
    {"id": "hr_03", "text": "负责人王晓明 电话13987654321 邮箱wangxm@company.cn",
     "entities": [("王晓明", "PERSON"), ("13987654321", "PHONE"), ("wangxm@company.cn", "EMAIL")]},
    {"id": "hr_04", "text": "经办人欧阳小雪，工号A123456，办公电话010-87654321",
     "entities": [("欧阳小雪", "PERSON"), ("A123456", "ACCOUNT_NUMBER"), ("010-87654321", "PHONE")]},
    {"id": "hr_05", "text": "员工登记表：姓名赵铁柱，性别男，出生日期19900501",
     "entities": [("赵铁柱", "PERSON")]},

    # --- 学生 / 教育 ---
    {"id": "edu_01", "text": "学生赵铁柱，学号20210101001，联系电话13500001111",
     "entities": [("赵铁柱", "PERSON"), ("13500001111", "PHONE")]},
    {"id": "edu_02", "text": "保送名单共四人：陈一、王二、张三、李四",
     "entities": [("陈一", "PERSON"), ("王二", "PERSON"), ("张三", "PERSON"), ("李四", "PERSON")]},
    {"id": "edu_03", "text": "导师孙志强教授指导本课题研究",
     "entities": [("孙志强", "PERSON")]},
    {"id": "edu_04", "text": "考生周伟，准考证号1054321，身份证120103199507203012",
     "entities": [("周伟", "PERSON"), ("1054321", "ACCOUNT_NUMBER"), ("120103199507203012", "IDCN")]},

    # --- 财务 / 凭证 ---
    {"id": "fin_01", "text": "收款账户6222020200012345678，开户人周伟，开户行工商银行",
     "entities": [("6222020200012345678", "ACCOUNT_NUMBER"), ("周伟", "PERSON")]},
    {"id": "fin_02", "text": "接口鉴权使用API密钥 sk-abc123def456ghi789 请妥善保管",
     "entities": [("sk-abc123def456ghi789", "SECRET")]},
    {"id": "fin_03", "text": "订单总金额为1500.00元，下单时间2024-03-15，订单号ORD20240315001",
     "entities": []},
    {"id": "fin_04", "text": "数据库连接串中的密码 P@ssw0rd!2024 已轮换",
     "entities": [("P@ssw0rd!2024", "SECRET")]},

    # --- 电话 / 日期 混合格式 ---
    {"id": "fmt_01", "text": "联系电话 138-1234-5678，备用座机 010-12345678",
     "entities": [("138-1234-5678", "PHONE"), ("010-12345678", "PHONE")]},
    {"id": "fmt_02", "text": "本人出生于1990年5月1日，身份证号11010119900501123X",
     "entities": [("11010119900501123X", "IDCN")]},
    {"id": "fmt_03", "text": "家庭住址：上海市浦东新区世纪大道100号20楼2001室",
     "entities": [("上海市浦东新区世纪大道100号20楼2001室", "ADDRESS")]},
    {"id": "fmt_04", "text": "公司官网 https://www.example.com/user/123 提供在线服务",
     "entities": [("https://www.example.com/user/123", "URL")]},
    {"id": "fmt_05", "text": "开发文档地址 http://api.example.com/v1/scan 支持批量调用",
     "entities": [("http://api.example.com/v1/scan", "URL")]},

    # --- 复合姓名 / 列表 ---
    {"id": "name_01", "text": "与会专家：诸葛云、司徒雷登、东方白三位老师",
     "entities": [("诸葛云", "PERSON"), ("司徒雷登", "PERSON"), ("东方白", "PERSON")]},
    {"id": "name_02", "text": "项目部由刘总负责，技术由陈工对接，测试由林工跟进",
     "entities": []},
    {"id": "name_03", "text": "值班医生：吴静、郑国华、王磊（按周一至周日轮换）",
     "entities": [("吴静", "PERSON"), ("郑国华", "PERSON"), ("王磊", "PERSON")]},

    # --- 多实体长段落 ---
    {"id": "mix_01", "text": "客户档案：客户编号KH20240001，姓名黄志远，手机13800002222，邮箱huangzy@test.org，身份证310104198812250045，通讯地址广东省深圳市南山区科技园路8号。",
     "entities": [("KH20240001", "ACCOUNT_NUMBER"), ("黄志远", "PERSON"), ("13800002222", "PHONE"), ("huangzy@test.org", "EMAIL"),
                  ("310104198812250045", "IDCN"), ("广东省深圳市南山区科技园路8号", "ADDRESS")]},
    {"id": "mix_02", "text": "投诉人何丽女士反映问题，联系电话13911112222，回访邮箱heli@163.com，期望处理日期2024年6月底前。",
     "entities": [("何丽", "PERSON"), ("13911112222", "PHONE"), ("heli@163.com", "EMAIL")]},

    # --- 负样本为主（精确率守门）---
    {"id": "neg_01", "text": "项目代号Phoenix计划于2024年正式启动，预计三年完成",
     "entities": []},
    {"id": "neg_02", "text": "本系统使用Python 3.11与PostgreSQL 14构建，部署于Kubernetes集群",
     "entities": []},
    {"id": "neg_03", "text": "杭州市和淳安县被列为本次试点地区，相关工作由地方负责",
     "entities": []},
    {"id": "neg_04", "text": "高级教师评定、学科建设与课程改革由学校统一组织",
     "entities": []},
    {"id": "neg_05", "text": "文档版本v2.3.1，最后更新于2024年，作者保留所有权利",
     "entities": []},
    {"id": "neg_06", "text": "工程范围包括土建、机电、装饰三个标段，总造价1.2亿元",
     "entities": []},

    # --- 边界 / 易混淆 ---
    {"id": "edge_01", "text": "订单号13812345678已被系统自动取消，请联系客服",
     "entities": []},
    {"id": "edge_02", "text": "网址example.com和test.site均未备案，请勿访问",
     "entities": []},
    {"id": "edge_03", "text": "工号A123、B456、C789为新入职员工的内部编号",
     "entities": [("A123", "ACCOUNT_NUMBER"), ("B456", "ACCOUNT_NUMBER"), ("C789", "ACCOUNT_NUMBER")]},
    {"id": "edge_04", "text": "请联系张三丰师傅维修电梯，他住在三楼",
     "entities": [("张三丰", "PERSON")]},
]


# ---------------------------------------------------------------------------
# Offset resolution
# ---------------------------------------------------------------------------

def resolve_entities(text: str, annotations: Iterable[tuple[str, str]]) -> list[Entity]:
    """Resolve (substring, type) annotations into validated offset entities.

    Each substring must occur in ``text``; repeated substrings consume the next
    available (non-overlapping) occurrence. Raises ValueError on a missing or
    ambiguous annotation so silent offset drift is impossible.
    """
    resolved: list[Entity] = []
    consumed: list[tuple[int, int]] = []  # (start, end) intervals already taken

    for substring, etype in annotations:
        if not substring:
            raise ValueError(f"empty annotation for type {etype}")
        search_from = 0
        matched_idx = -1
        while True:
            idx = text.find(substring, search_from)
            if idx == -1:
                raise ValueError(
                    f"substring not found in text: {substring!r} (type={etype}, id context below)"
                )
            end = idx + len(substring)
            overlaps = any(not (end <= c0 or idx >= c1) for c0, c1 in consumed)
            if not overlaps:
                matched_idx = idx
                break
            search_from = idx + 1
        consumed.append((matched_idx, matched_idx + len(substring)))
        resolved.append(Entity(matched_idx, matched_idx + len(substring), etype, substring))

    # Sanity: no two resolved spans of the same content should overlap.
    resolved.sort(key=lambda e: e.start)
    for a, b in zip(resolved, resolved[1:]):
        if a.overlaps(b):
            raise ValueError(f"overlapping gold annotations: {a!r} vs {b!r}")

    return resolved


def load_golden(seed: list[dict] | None = None) -> list[tuple[str, str, list[Entity]]]:
    """Load and validate the golden dataset.

    Returns a list of ``(sample_id, text, [Entity, ...])`` tuples ready to feed
    into :func:`eval.metrics.evaluate`.
    """
    seed = seed if seed is not None else SEED
    out: list[tuple[str, str, list[Entity]]] = []
    seen_ids: set[str] = set()
    for record in seed:
        sid = record["id"]
        if sid in seen_ids:
            raise ValueError(f"duplicate sample id: {sid}")
        seen_ids.add(sid)
        text = record["text"]
        entities = resolve_entities(text, record.get("entities", []))
        # Verify reconstructed offsets match the intended substrings.
        for e in entities:
            actual = text[e.start:e.end]
            if actual != e.text:
                raise ValueError(f"offset mismatch in {sid}: {actual!r} != {e.text!r}")
        out.append((sid, text, entities))
    return out


def export_jsonl(path: str | Path) -> None:
    """Write the resolved golden dataset to JSONL (standard NER format)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for sid, text, entities in load_golden():
            payload = {
                "id": sid,
                "text": text,
                "entities": [
                    {"start": e.start, "end": e.end, "type": e.type, "text": e.text}
                    for e in entities
                ],
            }
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    data = load_golden()
    total = sum(len(e) for _, _, e in data)
    types: dict[str, int] = {}
    for _, _, entities in data:
        for e in entities:
            types[e.type] = types.get(e.type, 0) + 1
    print(f"{len(data)} samples, {total} gold entities")
    for t, c in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"  {t:16s} {c}")
