# VibeMask 检测准确率基线报告

> 评测框架：`eval/`（首次建立）。数据集：`eval/golden/`（33 样本 / 46 标注实体，含负样本）。
> 匹配准则：类型一致 + IoU ≥ 0.5 记为 TP；同时追踪边界漂移与 exact-match。
> 日期：2026-06-15。环境：Python 3.11，MLX 后端 + jieba/spacy 可用。

## 头条结论

**加入 MLX 模型后，整体 F1 反而下降（81.0% → 66.1%）**。模型目前的集成方式**主动损害**了准确率——不是模型不行，是模型的输出在到达合并层之前/之中被破坏了。

| 引擎 | Precision | Recall | **F1** | 耗时/样本 |
|---|---:|---:|---:|---:|
| `regex`（仅正则） | 93.8% | 32.6% | 48.4% | 0.01 ms |
| `det`（正则+schema+中文姓名，**无模型**） | 89.5% | 73.9% | **81.0%** | 65 ms |
| `hybrid`（全管道 + MLX 模型） | 55.1% | 82.6% | 66.1% | 546 ms |

→ **确定性层（无模型）F1 = 81%，是目前最强的配置。** 模型把精确率从 89.5% 拖到 55.1%。

## 分类型对比（F1）

| 类型 | regex | det | hybrid | 说明 |
|---|---:|---:|---:|---|
| EMAIL | 100% | 100% | 100% | 正则完美 |
| IDCN（身份证） | **100%** | **100%** | **0%** | ⚠️ 加模型后召回从 100% 跌到 0% |
| PHONE | 80% | 80% | 94% | 模型补回了座机 |
| PERSON | 0% | 86% | 76% | 中文姓名靠 smart_detector |
| ADDRESS | 0% | 0% | 60% | 只有模型能做（recall 100%，precision 43%） |
| SECRET | 0% | 0% | 100% | 只有模型能做 |
| ACCOUNT_NUMBER | 0% | 0% | 11.8% | recall 100% 但 precision **6.2%**（碎片化误报） |
| URL | 67% | 67% | 67% | 一致漏一个 |

## 三个已定位的根因（均有复现证据）

### 根因 1 — MLX span 碎片化 bug（精确率崩塌的主因）

MLX 后端在「CJK 紧邻长数字串」时，BIOES→span 转换产生**重叠碎片**：

```
输入: 学生赵铁柱，学号20210101001，联系电话13500001111
输出: [2:3]ADDRESS '赵'  [3:4]PERSON '铁'  [4:11]ADDRESS '柱，学号202'
      [11:14]ACCOUNT '101'  [14:17]ADDRESS '010'  [17:19]ACCOUNT '01'
      [27:35]PHONE '00001111'   ← 连电话都被切碎，丢了 '135' 前缀
```

- 同一个名字「赵铁柱」在 `hr_05`（无长数字串邻接）里识别**正确**，在 `edu_01`（邻接 `20210101001`）里被**撕成 3 段 3 种类型**。
- 这导致 hybrid 的 ACCOUNT_NUMBER 出现 **15 个垃圾 FP**（`'199'`、`'00501'`、`'101'`、`'01'`…）。
- **判断**：不是模型笨，是 `privacy_filter_mlx.py` 的 token offset → span 重建逻辑在特定分词下出错。需 root-cause 排查。

### 根因 2 — 合并层让模型 over-reach 否决正确的 regex 检测（召回回归）

```
输入: 申报人李明华，身份证号110101198801011234，住址...
模型: [7:29] ACCOUNT_NUMBER '身份证号110101198801011234'   ← 吞了标签"身份证号"，类型还判错
regex:[10:28] IDCN '110101198801011234'                     ← 正确
合并: 保留模型的 ACCOUNT，丢弃 regex 的 IDCN
```

- 机制（`core/merger.py:73-78`）：模型 span **包含** regex span，且 `TYPE_PRIORITY[ACCOUNT]=105 >= IDCN=100` → regex 的正确 IDCN 被判 `should_accept=False` 丢弃。
- **后果**：IDCN 召回 100%（det）→ 0%（hybrid）。一个正确的确定性检测被错误模型 span 毁掉。

### 根因 3 — 模型边界漂移（可后处理修复）

- `wangxm@company.cn` → `箱wangxm@company.cn`（吞了前一个字符「箱」）
- `何丽` → `何丽女士`（吞了后缀「女士」）
- `privacy_postprocess.py` 目前只修「日期 over-reach」，未覆盖这类。

## 模型的真实价值（不要因噎废食）

模型是 **ADDRESS / SECRET / ACCOUNT 召回的唯一来源**（确定性层都是 0%）。一旦修好集成 bug，hybrid 的合理目标应是 **>88% F1**（det 的 81% 基线 + 模型补回 ADDRESS/SECRET/ACCOUNT 召回且不破坏精确率）。

## 对路线图的影响（数据修订了之前的判断）

原假设："主要靠 OPF 模型 + 把文档解析好"。
数据说：**在"靠模型"之前，模型集成本身是坏的**。优先级应改为：

1. **P0-a 修 MLX 碎片化 bug**（根因 1）→ 直接砍掉 ~15 个 FP，精确率回升。这是单点最高杠杆。
2. **P0-b 修合并层否决逻辑**（根因 2）→ 让高优先级确定性源（REGEX/SCHEMA）的精确检测不被模型 over-reach 覆盖；IDCN 应回到 100%。
3. **P0-c 边界后处理**（根因 3）→ trim 标签前缀/头衔后缀。
4. **之后**才进入"文档解析增强"（generalize `privacy_context.py`）与"取真实置信度"。
5. 评测框架已就位，每一步改动都用 `python -m eval.runner` 量化回归。

## 模型单独评测（回答"单是 OpenAI 模型怎么样"）

| 引擎 | Precision | Recall | F1 | 耗时/样本 |
|---|---:|---:|---:|---:|
| `model`（MLX 移植版，单独） | 51.6% | 71.7% | **60.0%** | 499 ms |

分类型（model-only MLX）：EMAIL 100%、SECRET 100%、ADDRESS R=100%/P=43%、PHONE 87%、PERSON 68%、**IDCN 0%**、**ACCOUNT R=100%/P=6.2%**。

**关键结论：60% 不是模型的真实天花板，是被解码缺陷污染的数字。**

### 碎片化的精确根因 — MLX 用 argmax 而非 viterbi

`privacy_filter_mlx.py:51` 用逐 token 贪心 `mx.argmax`，产生**非法 BIOES 序列**。对 `学生赵铁柱，学号20210101001`，模型实际吐出：

```
赵 → I-private_address      ← 全是 I-，无 B- 开头；且类型翻转
铁 → I-private_person       (address → person)
柱 → I-private_address      (person → address)
```

argmax 允许这种翻转，解码器每次类型变就 flush → 一个名字被撕成 3 段碎片。

而原生 OPF 后端用 `decode_mode="viterbi"`（`privacy_filter.py:39,91`）做约束解码，强制转移合法。`HybridDetector.privacy_decode_mode` 参数（`hybrid.py:33`）**只传给 OPF 后端，MLX 后端 `__init__` 根本不接收**——viterbi 路径在 MLX 上从未接通。

**→ 单点最高杠杆修复：给 MLX 后端接上 viterbi / BIOES 合法性修复。** 预计把 model-only 从 60% 拉到高得多。原生 OPF（viterbi）是干净参照，待跑确认。

## 修复进展（评测驱动）

两个集成 bug 已修，均有数字佐证：

| 配置 | P | R | **F1** | 改动 |
|---|---:|---:|---:|---|
| hybrid 原始（argmax + 旧 merge） | 55.1% | 82.6% | 66.1% | 基线 |
| hybrid + **viterbi 解码** | 58.7% | 80.4% | 67.9% | `privacy_filter_mlx.py`：argmax→约束 viterbi |
| hybrid + viterbi + **merge 修复** | 65.1% | 89.1% | 75.2% | `core/merger.py`：确定性 span 不被模型 over-reach 否决 |
| hybrid + viterbi + merge + **person 修剪** | 69.8% | 95.7% | 80.7% | `privacy_postprocess.py`：剥人名尾部头衔/括号 |
| hybrid + … + **类型化掩码 + 标识符策略** | 79.4% | 96.2% | **87.0%** | `placeholder.py` 重写 + 标识符 label 处理 |
| det（无模型，参照） | 89.5% | 73.9% | 81.0% | 未变 |

- **viterbi**（`privacy_filter_mlx.py`）：约束 BIOES 转移合法性，消除 `I-address→I-person→I-address` 翻转导致的碎片化。model-only 60.0%→64.1%。
- **merge 修复**（`core/merger.py`）：模型 span（不同类型）包含精确 REGEX/SCHEMA span 时，确定性 span 胜出。**IDCN 召回 0%→100%**。
- **person 修剪**（`privacy_postprocess.py`）：剥 PERSON 尾部头衔（师傅/女士/老师/教授…）与括号。**PERSON F1 73.5%→85.7%**。
- **类型化掩码**（`placeholder.py` 重写）：所有掩码改为 `{{TYPE_NNNNNN:shape}}`，shape 全脱敏（数字→#、字母→X、CJK→某，保留分隔符结构）。下游 LLM 既知类型又知形态，且不泄露原值；无损可逆。
- **标识符策略**（`privacy_postprocess.py` + golden 重标）：工号/学号/准考证号/客户编号/会员号/卡号/病历号 等 = 个人标识 → 剥标签只掩值（标签词留在原文，如「工号 {{ACCOUNT_001:X######}}」）；订单号/合同号/发票号/流水号 等 = 交易 → 整段丢弃。**ACCOUNT F1 22.2%→93.3%**。

### 当前里程碑（hybrid v4 = 87.0% F1 / 96.2% recall）

hybrid **首次明确超过 det**（87.0 vs 81.0），且召回 96.2% 远高于 det 的 73.9%。累计 **+20.9 F1**。仅剩 2 个 FN。63 个测试全绿。

剩余 13 个 FP 主要是策略判断与边界残留：
- **称谓+姓**（PERSON FP）：`刘总/陈工/林工`、`张三丰师傅` 类 —— 弱标识/边界，部分可继续 trim。
- **订单号当电话**（PHONE FP）：`订单号13812345678` 里 regex 把 11 位当手机 —— regex 无上下文，已知局限。
- **边界垃圾**（DATE `199`/`005`、PHONE `电话`）：少量，可继续 trim。

## 文档解析：DOCX 表格（真实文件路径）

把 `privacy_context.py` 的"表头:值"重建从 XLSX 泛化到 DOCX 表格——即"把文档解析好喂模型"的落地：

1. **表格坐标抽取**（`ooxml_adapter.py`）：DOCX 表格 cell 的 location 从 `part:N` 升级为 `part:tbl{i}.r{j}.c{k}`，暴露行列结构（非表格段落保持原格式，向后兼容）。
2. **DOCX 表格上下文**（`privacy_context.detect_docx_table_context`）：重建 `姓名: 王晓明 | 手机号: 13812345678 | 身份证号: 110101…`，**只把值喂模型，表头当 label**——模型不再把表头误判为实体，且值的类型有列先验。
3. **字段标签抑制**（`privacy_postprocess.is_field_label`）：source-agnostic，任何检测器（schema/heuristic/model）把表头词（姓名/手机号/身份证号/…）误报为 PII 时，merge 前丢弃。

**实测**（带 PII 表格的 DOCX，走真实 `detect_document` 路径）：
- 召回 **8/8**（姓名/手机/身份证，内联+表格全覆盖）。
- 表头误报 **0**（修前 schema 把 `手机号` 报成 PERSON）。
- **无损往返**：mask → 表头原样保留、值类型化（`{{PERSON_000001}}`/`{{PHONE_000001:###########}}`/`{{IDCN_000001:##################}}`）→ restore 与原文逐字一致。

文本 eval 同步受益：字段标签过滤顺手干掉一个 `电话` FP，hybrid **87.0% → 87.7% F1**。66 个测试全绿（+3 个 DOCX 表格测试）。

### 模型离线运行

模型已缓存于 `~/.cache/huggingface`；网络不稳时需 `HF_HUB_OFFLINE=1` 运行 eval/CLI。

## 真实文件验证（合成评测之外的考验）

跑仓库里的真实 docx/xlsx，暴露了合成 golden 完全漏掉的问题：

| 文件 | 类型 | 结果 |
|---|---|---|
| `37173e4a.docx` | 杭州企业技术中心名单（公司+地区） | **0 spans ✓**（无个人 PII，正确不误报公司名/地区） |
| `a2c96041.xlsx` | 政府信息公开目录模板 | **0 spans ✓**（字段标签无实际值） |
| `7931b76a.docx` | 企业财务指标表 | 修前 9 FP（`办公室电话`/`万元`×5/`单位`/`万`）→ **修后 0** |
| `b1868fc7.xlsx` | 浙大教师名单 | 62 PERSON（真实姓名，召回 ✓）+ 66 URL（教师主页） |
| `2022062910.xlsx` | 学生名单 | 39 PERSON（真实姓名，召回 ✓） |

**关键修复**：财务表里模型把 `万元`/`单位`/`办公室电话`/单字 `万` 全报成 PERSON——`NON_NAME_WORDS` 只过滤 heuristic 不过滤模型，且缺财务词。新增跨来源过滤（`privacy_postprocess`）：
- 扩展 `FIELD_LABEL_WORDS`（复合电话标签 `办公室电话` 等）；
- 新增 `NON_PERSON_WORDS`（财务/度量/常用词：万元/利润/金额/单位/企业…）+ 单字 CJK PERSON 抑制；
- 在 hybrid merge 前 source-agnostic 应用。

**收益**：真实财务文档 9 FP→0；文本 eval 同步受益 **87.7% → 90.1% F1**（FP 12→9，召回不降）。教训：**合成评测会漏掉真实文档的失败模式**，真实文件验证不可省。

### 大文档 OOM（生产级 bug）

转换后的真实文件 `浙江大学2015年推荐免试生公示名单.xlsx`（**46,729 字符，~2400 学生**）让 MLX 检测器一次性吃下整段文本 → 尝试分配 **14.6GB logits 张量 → Metal OOM 崩溃**。`detect()` 没有分块——这是架构分析里早就点过的"上下文窗口/分块"缺口。

**修复**（`privacy_filter_mlx.py`）：窗口化推理——512-token 窗口 + 64-token 重叠 + 跨窗口去重。tokenizer 的 offset_mapping 本就映射到原文，所以每窗口 span 自带全局 offset，重叠区按 (start,end,type,text) 去重即可。

**结果**：崩溃 → 检出 **2415 实体**（1228 姓名 + 1187 学号）；XLSX 行上下文正确把「姓名」配姓名、「学号」配学号值。eval 不变（样本都 <512 token，单窗口）。**无此修复工具在大文件上不可用**。

### 全量真实文件扫描（8 个，含 3 个遗留格式转换）

| 文件 | 内容 | 检测 |
|---|---|---|
| 企业技术中心名单 | 公司+地区 | 0 ✓（无误报） |
| 政府公开目录模板 | 字段标签 | 0 ✓ |
| 企业财务表 | 万元/利润/单位 | 0 ✓（修前 9 FP） |
| 浙大免试生名单（46K 字） | 2400 学生 | 1228 姓名 + 1187 学号 ✓（修前 OOM） |
| 浙大教师名单 | 教职工 | 62 姓名 + 66 主页 URL |
| 学生名单 | 学生 | 39 姓名 ✓ |
| 国家统计局浙江调查总 | 通讯信息 | 17 姓名 + 电话 + 地址 + 账号 |
| 中小学教师职称 | 评审材料 | 4 姓名 + 电话 ✓（修前含 `民办` FP） |

真实数据共暴露并修复 **3 个合成评测完全看不见的问题**：财务词当人名、大文档 OOM、教育词（`民办`）当人名。剩余仅为边角（两个电话并一个 span、地址含邮政编码标签、`沈老师` 称谓+姓）。

**结论**：工具在真实 gov/edu 目标文档上可用——大文件不崩、无个人 PII 的文档不误报、有 PII 的文档高召回。

## 复现命令

```bash
python -m eval.runner --engine regex   --report eval/reports/regex.json
python -m eval.runner --engine det     --report eval/reports/det.json
python -m eval.runner --engine hybrid  --report eval/reports/hybrid.json
```

完整 JSON 报告（含每个 FP/FN/drift 明细）见 `eval/reports/*.json`。
