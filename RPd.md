# RPD/PRD：VibeMask（自动脱敏 + 可逆回填）

**版本**：v0.1（MVP）
**状态**：可开发稿（Dev-ready）
**目标工作流**：Claude Code / Codex CLI / Cursor / 任意“把内容交给 AI、再拿结果落地到文件”的场景
**核心承诺**：**不改变格式**（至少不破坏换行/缩进/结构），仅替换文字；并能在 AI 执行后自动回填原始敏感信息

---

## 1. 背景与问题定义

### 1.1 背景

在“vibe coding”/AI Agent 工作流里，用户经常把真实业务数据（人名、联系方式、身份信息、工单、日志、家校沟通等）直接喂给 AI。
风险是显性的：一旦泄露或误用，后果很难补救；而手工脱敏/回填又慢又容易错。

### 1.2 核心问题（要解决的痛点）

1. **隐私泄露**：敏感信息进入第三方模型或被写入日志/patch
2. **流程成本高**：手工替换与回填重复劳动
3. **格式要求苛刻**：日志对齐、表格排版、JSON/YAML 结构不可破
4. **回填不稳定**：AI 可能改写文本，导致简单 find/replace 失效或误替换

### 1.3 一句话定义

**VibeMask 是一个本地“AI 调用前置代理/包装器”，自动识别并可逆伪匿名化敏感信息，执行 AI 工具后再自动回填原文，并将映射表加密存储在项目外。**

---

## 2. 目标与成功指标

### 2.1 产品目标（Goals）

* **G1 自动脱敏**：提交给 AI 之前自动识别敏感信息并替换为占位符
* **G2 格式无损**：不改变文本结构（至少保持换行/缩进/标点结构不被重排）
* **G3 可逆回填**：AI 输出/修改文件后自动回填真实信息
* **G4 映射外置与加密**：映射表不进入 repo，默认加密存储
* **G5 工作流贴合**：一条命令包住 Claude Code 等工具，不改用户习惯

### 2.2 非目标（Non-goals）

* 不追求企业级 DLP 全覆盖（MVP 先覆盖最常见敏感类型）
* 不做远程审计平台/集中管控（后续可扩展）

### 2.3 成功指标（MVP）

* 人名/手机号/邮箱/身份证等：**召回率 > 90%**（常见中文材料）
* 人名误报：**< 5%**（可通过交互确认/白名单进一步下降）
* 回填成功率：在“占位符未被大幅篡改”情况下 **> 95%**
* 性能：10 万字文本脱敏 **< 2s**；中等文本 **< 300ms**（本地）

---

## 3. 用户与场景

### 3.1 目标用户

* P0：高频用 AI 改文档/写报告/改代码的个人（PM、老师、开发者）
* P1：小团队（共享策略、统一脱敏标准）
* P2：合规敏感行业团队（更严格策略/密钥管理）

### 3.2 典型场景（必须覆盖）

1. 将包含人名/联系方式的需求描述交给 Claude Code 生成实现与说明
2. 将包含敏感字段的日志交给 AI 分析 bug
3. 让 AI 修改 repo 文件（docs、代码注释、配置等）后自动回填
4. 批量处理结构化文本（JSON/YAML/CSV）且不能破坏结构

---

## 4. 产品形态与用户旅程

### 4.1 MVP 形态：CLI Wrapper（最稳、最快落地）

用户只需把原命令前加一层：

* `vibemask run -- claude-code <args...>`
  系统自动：**脱敏输入 → 调用原工具 → 回填输出/回填改动文件**

### 4.2 用户旅程（End-to-End）

1. 用户在终端执行 `vibemask run -- <ai_tool_cmd>`
2. VibeMask 收集输入（prompt、stdin、指定文件、上下文）
3. 本地识别敏感信息 → 替换为占位符（不改格式）
4. 调用 AI 工具执行
5. 捕获输出（stdout/patch）并回填
6. 若 AI 工具改动 repo 文件：通过 `git diff` 找到改动文件并自动回填
7. 映射关系加密写入项目外 vault（可复用、可恢复）

---

## 5. 范围与版本规划

### 5.1 v0.1（MVP）范围

**敏感类型**

* 必选：中文人名（PERSON）
* 必选：手机号（PHONE）、邮箱（EMAIL）、身份证号（IDCN）
* 可选：组织/公司名（ORG）——先做“候选 + 小模型裁判”，不开默认也行
* 可选：地址（ADDRESS）——规则优先，模型辅助（后续增强）

**输入/输出**

* 输入：prompt（文本）、stdin、指定文件（txt/md/log/json/yaml）
* 输出：stdout 回填；`git diff` 改动文件回填

**核心能力**

* 不改变格式：保持原换行、缩进、结构
* 可逆：占位符唯一化，可完整回填
* 映射表外置、默认加密
* `--dry-run` 预览替换清单
* `--interactive` 人名等高风险项支持逐条确认

### 5.2 v0.2+（增强）

* 更强 NER（可插拔：本地模型/HanLP/LTP 等）
* 结构化对齐：固定宽字段/表格对齐的“长度保持策略”完善
* IDE 插件（Cursor/VSCode）
* 团队共享 vault（密钥/权限）

---

## 6. 关键设计：隐私识别机制（规则 + 候选 + Ollama 小模型）

> 核心思想：**别让小模型通读全文当扫描器**，而让它当“候选裁判 + 漏网侦察（可选）”。
> 这样快、稳、可控、误报低。

### 6.1 三层防线（默认启用）

**L1 规则识别（确定性强，优先）**

* Regex：手机号、邮箱、身份证、银行卡、IP、固定格式订单号等
* 结构化字段：JSON/YAML 中 key 命中（可配置 key 列表，如 `name/phone/email/id/address`）

**L2 候选挖掘（召回优先）**

* 中文姓氏表 + 名字长度（2–4 字）+ 上下文触发词（老师/同学/家长/联系人/收件人/抄送/致电/微信等）
* 冒号结构（`姓名：张三`）
* 名单模式（每行 2–4 字、连续多行）

L2 输出的是 **候选 spans**，不直接认定为敏感。

**L3 Ollama 小模型裁判（精度提升、压误报）**

* 仅对 L2 候选做判定：是否为敏感实体、类型、是否应脱敏、置信度
* 输出必须为 JSON 且带 **offset**（start/end），程序做硬校验
* 失败回退：若模型不可用，则用规则+启发式（并提示风险）

### 6.2 “强约束输出 + 硬校验”（防模型胡说）

模型输出结构（示例）：

* `decisions[]`：`{text,start,end,type,should_mask,confidence}`

程序端硬校验：

* `chunk_text[start:end] == text` 否则丢弃
* start/end 边界检查
* 枚举 type 限定
* 冲突/重叠 span 合并：**更长优先 > 规则优先 > 置信度高优先**

### 6.3 触发优化（减少模型调用）

只有在满足条件的行/块才调用 Ollama：

* 行包含触发词/字段名/名单结构
* 发现疑似人名候选
* 高风险文件类型（如家校沟通记录、工单对话）

---

## 7. 占位符策略（可逆 + 不破坏格式）

### 7.1 设计原则

* **唯一性**：每个实体必须映射到唯一占位符（否则无法回填）
* **自然性**：占位符尽量像自然文本的一部分，降低被 AI 改写概率
* **格式无损**：不改变换行、缩进、结构；尽量保持长度（尤其表格/日志）

### 7.2 默认占位符（建议方案）

**中文人名（按长度生成“假名”）**

* 2 字名：`孙甲`、`赵乙`…
* 3 字名：`赵甲乙`、`钱丙丁`…
* 4 字名：`李甲乙丙`…
  特点：像名字、长度可控、可稳定回填。

**手机号**

* `13812345678` → `138****5678`（长度不变、结构不变）

**邮箱**

* `abc@corp.com` → `u001@corp.com`（可配置：域名是否保留；更安全是也替换域名）

**身份证**

* 保留前后位，中间掩码（长度不变）

> 说明：你最初想要“赵XX”，可作为显示风格，但实际实现必须“赵甲乙/赵A01”这种才能唯一回填。

---

## 8. 映射表（Vault）与密钥管理

### 8.1 存储位置（必须项目外）

默认路径：

* macOS/Linux：`~/.vibemask/projects/<project_fingerprint>/vault.sqlite`
* Windows：`%APPDATA%/vibemask/projects/<project_fingerprint>/vault.sqlite`

`project_fingerprint` 可用 repo 路径 + git remote + salt 哈希生成（避免泄露真实路径）。

### 8.2 加密策略（MVP 必做）

当前实现采用 A；B 保留为无系统密钥环环境的后续兼容方案：

* A（已实现）：每项目独立随机 256-bit 密钥存入 OS Keychain/Keyring；原始值、会话映射和输入输出文件名使用 AES-256-GCM 字段级加密。旧明文库首次打开时事务迁移；密钥缺失或 Keyring 不可用时失败关闭，不降级为明文。
* B：用户 passphrase 派生密钥（Argon2/PBKDF2），vault 加密

### 8.3 Vault 数据模型（逻辑字段）

* `entity_id`（UUID）
* `project_id`
* `type`（PERSON/PHONE/EMAIL/IDCN/ORG/ADDRESS）
* `original_text`（加密存储）
* `masked_text`（明文；只含无原始 PII 的类型化占位符，用于索引和恢复查找）
* `created_at / last_seen_at`
* `source`（regex/heuristic/llm_judge/schema）
* `confidence`

---

## 9. 回填机制（文本 + 文件两条链路）

### 9.1 回填 stdout / patch

* 仅替换“本次 session 生成的 masked_text”（避免误替换）
* 替换顺序：按 token 长度从长到短（避免子串覆盖）
* 回填后输出给用户（或继续传递给下游）

### 9.2 回填 repo 改动文件（Claude Code 强需求）

* 执行后：`git diff --name-only` 获取改动文件列表
* 对每个文件内容做回填（同样仅替换 session token 或项目 token）
* 安全策略：

  * 默认只处理文本文件类型（md/txt/log/json/yaml/py/js/ts…可配置）
  * 二进制/超大文件跳过并告警

### 9.3 回填失败策略

* 若发现占位符疑似被改写（匹配不到）：

  * 输出“未回填列表”统计（不泄露原文）
  * 提供 `vibemask restore --from-vault <file>` 手动再次尝试
  * 可选：提示用户开启更“稳定”的占位符策略（更自然、更短）

---

## 10. 配置与可用性

### 10.1 配置文件（可选，repo 内允许）

`.vibemask.yaml`（只放策略，不放映射）

* enable_types：PERSON/PHONE/EMAIL/IDCN/ORG…
* preserve_email_domain：true/false
* length_preserve_mode：off/basic/strict
* include_paths / exclude_paths
* ollama：model、endpoint、timeout、enabled
* schema_keys：结构化字段 key 列表
* whitelist：项目白名单词（产品名、术语等）

### 10.2 CLI 命令（MVP）

* `vibemask run -- <cmd...>`
* `vibemask mask <file|stdin> -> masked`
* `vibemask restore <file|stdin> -> restored`
* `vibemask diff-restore`（仅对 git diff 文件回填）
* `vibemask status`（检查 vault 是否在 repo 外、加密状态、最近会话统计）
* `vibemask dry-run ...`（预览将替换内容）

---

## 11. 功能需求（Functional Requirements）

### FR-1 识别引擎

* FR-1.1：Regex/Schema 识别（手机号、邮箱、身份证、字段 key）
* FR-1.2：候选挖掘（中文人名候选生成）
* FR-1.3：Ollama 裁判（JSON+offset 输出，硬校验，失败回退）
* FR-1.4：Span 合并与冲突解决（规则优先、最长优先、置信度优先）

### FR-2 脱敏替换

* FR-2.1：生成唯一占位符（按类型/长度策略）
* FR-2.2：替换不破坏格式（保持换行/缩进/结构）
* FR-2.3：支持 strict 模式：尽量保持长度（表格/日志）

### FR-3 Vault 管理

* FR-3.1：项目外存储
* FR-3.2：默认加密
* FR-3.3：同一实体稳定映射（项目内多次出现一致）

### FR-4 回填引擎

* FR-4.1：回填 stdout/patch
* FR-4.2：回填 git diff 改动文件
* FR-4.3：回填安全策略（仅 session token / 可配置项目 token）

### FR-5 预览与交互

* FR-5.1：dry-run 输出替换计划与统计
* FR-5.2：interactive 逐条确认（尤其人名误报场景）
* FR-5.3：白名单（词条级/文件级）

---

## 12. 非功能需求（NFR）

* **性能**：中等文本 <300ms；10 万字 <2s（不含 AI 工具本身）
* **可靠性**：失败默认“宁可不发，也不裸发”（fail-closed 可配置）
* **安全**：vault 加密；日志不记录原文；映射不进入 repo
* **可移植**：macOS 优先，兼容 Linux/Windows
* **可观测**：输出统计（替换了多少 PERSON/PHONE…），不泄露原文

---

## 13. 风险与对策

1. **中文人名误报（把产品名当人名）**
   对策：候选→Ollama 裁判；白名单；交互确认；上下文触发词增强

2. **AI 改写占位符导致回填失败**
   对策：占位符自然化；token 短且稳定；失败时给未回填统计与恢复命令

3. **映射表泄露**
   对策：项目外 + 默认加密 + Keychain/Passphrase；提供 `status` 检查

4. **结构化文件被破坏**
   对策：按行/分块处理，替换仅改文本；严格不改变引号/逗号/缩进；提供 strict 模式

---

## 14. 验收标准（MVP DoD）

* `vibemask run -- claude-code ...` 端到端可用：输入脱敏、输出回填、diff 文件回填
* vault 不进入 repo，且默认加密可验证
* dry-run 可输出替换计划
* Ollama 裁判模式可开关，模型不可用可回退
* 在 10 个真实样本文档上：人名/手机号/邮箱/身份证识别与回填正确率满足指标

---

## 15. 里程碑（可执行）

* **T0（1–2 天）POC**：Regex + 人名候选 + 占位符唯一化 + JSON vault + restore
* **T1（1 周）MVP**：SQLite vault + 加密 + git diff 回填 + dry-run/interactive + 配置文件
* **T2（2–3 周）v0.2**：NER 插件化 + 更强对齐策略 + IDE/编辑器入口（可选）

---

## 16. 技术落地建议（一句话）

**先把 CLI Wrapper + git diff 回填做出来**，它对 Claude Code 这种“会改文件”的 agent 价值最大；识别上用“规则/候选兜底 + Ollama 裁判”做精度与速度平衡，这套组合最不容易翻车。

---

如果你下一步要进入“开工级”，我建议我再给你补两份东西（同样按 PRD 标准写）：

1. **Span 合并与替换顺序规范**（这是回填正确性的地基）
2. **Ollama 裁判的 Prompt 与 JSON schema**（保证模型输出稳定且可校验）



好，下面我把 PRD 往“**开发能直接照着写**”的层级继续补齐：核心是两份你点名要的硬骨头——**Span 合并/替换规范** + **Ollama 裁判 Prompt & JSON Schema**，再补一套 CLI wrapper 的工程落地细节、配置样例与测试用例清单。

---

# PRD 续篇：实现规范与技术附录（Dev-ready）

## 附录 A：Span 统一数据结构与优先级体系（必做，不然必翻车）

### A1. Span 结构（内部标准）

```ts
Span {
  start: number;        // 全局 offset，包含起始字符
  end: number;          // 全局 offset，不包含 end（Python slicing 风格）
  text: string;         // 原始子串，必须等于 input[start:end]
  type: "PERSON"|"PHONE"|"EMAIL"|"IDCN"|"ORG"|"ADDRESS";
  source: "regex"|"schema"|"heuristic"|"llm_judge"|"llm_scan";
  confidence: number;   // 0~1
  reason?: string;      // 可选：用于 debug，不落敏感日志
  priority: number;     // 计算得出：越大越优先
}
```

### A2. 优先级（Priority）计算规则

你要一个“确定性优先”的秩序，不然会出现：手机号里的数字被地址规则切碎、姓名被组织名覆盖等。

建议：

* `schema`（结构化字段命中）priority = 100
* `regex`（强规则）priority = 90
* `llm_judge`（候选裁判）priority = 70
* `heuristic`（启发式候选）priority = 50
* `llm_scan`（主动扫描）priority = 60（比 heuristic 高，但仍低于 regex/schema）

最终 priority = base + (len(text) / 100) + confidence

> 让更长、更确定的 span 赢。

---

## 附录 B：Span 合并与冲突解决（最关键的正确性规范）

### B1. 基本定义

* **重叠**：`max(a.start,b.start) < min(a.end,b.end)`
* **包含**：`a.start<=b.start && a.end>=b.end`

### B2. 合并流程（确定性）

输入：候选 spans（来自 regex/schema/heuristic/llm）

**Step 0：硬校验（必须）**

* 若 `input[start:end] != text` → 丢弃（尤其 LLM 输出）
* start/end 越界 → 丢弃
* text 空、长度异常（>256 可疑）→ 丢弃或降权

**Step 1：排序**
按：

1. `start` 升序
2. `priority` 降序
3. `length(end-start)` 降序

**Step 2：扫描选择（Greedy but safe）**
维护一个 `accepted[]`

* 逐个取 span s：

  * 若与 accepted 中任何 span **不重叠** → accept
  * 若重叠：

    * 如果 s **完全包含**已 accept 的 span 且 s.priority 更高 → 替换（remove 被包含者）
    * 如果 s 被已 accept 的 span 完全包含 → 丢弃（除非 s.type 更敏感且 priority 差距很大，可配置）
    * 如果部分重叠（交叉）：

      * 默认：保留 priority 高者，丢弃另一方
      * 例外：可以允许“切分”吗？**MVP 不建议切分**，切分会破坏回填与格式稳定性

**Step 3：类型冲突规则（显式表）**

* PHONE/EMAIL/IDCN 一律压过 PERSON/ORG/ADDRESS（避免数字串被当名字）
* schema 压过一切
* regex 压过非 regex

---

## 附录 C：替换计划与“格式无损”替换算法（不漂移的做法）

### C1. 为什么替换会漂移？

你替换前后长度变化，会导致后续 span 的 offset 全错。

### C2. 解决方案：**先定 span，后倒序替换**

生成 `Replacement {start,end,original,masked}` 列表后：

* 按 `start` **降序**排序（从文本末尾向前替换）
* 逐个对原字符串做 slice 拼接替换
  这样无论长度怎么变，都不会影响“更靠前”的 start/end。

### C3. 长度保持策略（给你三档，MVP 先 basic）

* `off`：不保证等长（最快）
* `basic`：人名同长度；手机号/身份证/邮箱总体长度不变（推荐默认）
* `strict`：对固定宽字段/表格对齐做更激进的长度保持（v0.2）

**basic 规则建议**

* PERSON：**严格保持同长度**（2→2，3→3）
* PHONE/IDCN：掩码保持同长度
* EMAIL：本地部分用 `uNNN` 填充到同长度（域名保留时长度容易变化，建议做 padding）

---

## 附录 D：占位符生成规范（唯一、稳定、抗模型改写）

### D1. 唯一性与稳定性

* 同一 project 内，同一 `original_text + type` → 生成并复用同一个 `masked_text`
* masked 生成时写入 vault，后续命中直接用 vault

### D2. PERSON 假名生成（推荐实现）

* 姓氏：从常用姓氏列表循环/哈希选取（赵钱孙李…）
* 名：用“甲乙丙丁戊己庚辛壬癸 + 子丑寅卯…”编码，按长度补齐
* 举例：

  * `张三` → `赵甲`
  * `王小明` → `钱甲乙`
  * `欧阳娜娜`（4字）→ `孙甲乙丙`

### D3. 防“误回填”

每个 masked token 必须满足：

* 在原文中出现频率极低（避免与原词碰撞）
* 尽量“像自然中文”，减少模型二次改写概率
  （用 `[[NAME_001]]` 虽然稳，但更容易被模型改写/清理格式；你要的是“赵甲乙”这种自然 token）

---

## 附录 E：Ollama 小模型（qwen 4b模型）裁判：Prompt 规范 + JSON Schema（可直接开做）

### E1. 调用策略（推荐默认：候选裁判）

输入给模型的不是全文，而是：

* `chunk_text`（原文片段，保持原样）
* `candidates[]`（来自 heuristic 的候选 span）
  模型只回答：这些候选哪些该脱敏、类型是什么、置信度。

### E2. JSON Schema（强约束）

**模型必须输出：**

```json
{
  "decisions": [
    {
      "start": 12,
      "end": 15,
      "text": "王小明",
      "type": "PERSON",
      "should_mask": true,
      "confidence": 0.92
    }
  ]
}
```

### E3. 系统提示词（System）

要点：**只输出 JSON**、**不得新增候选**（裁判模式）、**不得改写文本**。

> System（建议）

* 你是隐私实体判定器。
* 你只能对给定 candidates 做判断，不得新增实体。
* 你必须输出严格 JSON，禁止任何解释、markdown、注释。
* start/end 必须是 candidates 里对应的值，不得修改。
* type 只能从枚举中选择：PERSON/ORG/ADDRESS/PHONE/EMAIL/IDCN。

### E4. 用户提示（User 模板）

包含三段：枚举、chunk、candidates。

> User（模板）

* chunk_text: """...原文..."""
* candidates: [{start,end,text,reason}, ...]
* 任务：对每个 candidate 判断 should_mask/type/confidence。若不是敏感信息，should_mask=false，type 可填 "PERSON" 但 should_mask=false（或 type="OTHER" 若你允许枚举扩展）。

### E5. 程序端硬校验（必须写进实现）

* parse JSON 失败 → fallback（只用 regex/schema + heuristic）
* 对每条 decision：

  * 必须完全匹配一个 candidate（start/end/text 三者一致）
  * 必须满足 `chunk_text[start:end]==text`
  * confidence 范围 0~1
  * should_mask=false 的一律丢弃（不进入最终 spans）

### E6. “漏网侦察”模式（v0.2 可选）

如果你要让模型主动找实体：

* 必须要求输出 offset
* 必须分块（按行/按段）
* 必须做 `substring 校验`
  否则你会掉进 offset 地狱。

---

## 附录 F：CLI Wrapper 落地细节（与 Claude Code 类工具的兼容策略）

### F1. 包装方式（MVP）

* `vibemask run -- <cmd...>`
  实现上：

1. 读取 stdin / argv 中的 prompt 参数（按工具适配器实现）
2. 对 prompt 脱敏后调用子进程执行 `<cmd...>`
3. 捕获 stdout/stderr → 回填后再输出到终端
4. 运行前后用 git diff 找改动文件回填

### F2. Git 回填策略（Claude Code 重点）

* 运行后：

  * `git diff --name-only` 获取 files
  * 对 files 做回填（文本文件白名单）
* 回填写回文件前可选：

  * `--backup`：生成 `.vibemask.bak`（默认关闭，企业场景可开）
  * `--check`：写前校验文件仍含 masked token（无则跳过）

### F3. 文件类型白名单（默认）

* 文本：`.md .txt .log .json .yaml .yml .py .js .ts .java .go .rs .sql`
* 跳过：图片、pdf、zip、二进制、超大文件（> 5MB 默认跳过，可配置）

---

## 附录 G：配置样例（你可以直接用）

### G1. `.vibemask.yaml`

```yaml
enabled_types: [PERSON, PHONE, EMAIL, IDCN]
ollama:
  enabled: true
  model: qwen2.5:3b
  endpoint: http://localhost:11434
  timeout_ms: 2000

masking:
  length_preserve_mode: basic
  email:
    preserve_domain: true
  person:
    style: chinese_pseudoname   # 赵甲乙风格
    stable_within_project: true

paths:
  include: ["**/*"]
  exclude: ["**/.git/**", "**/node_modules/**", "**/dist/**"]

schema_keys:
  - name
  - username
  - student_name
  - parent_name
  - phone
  - mobile
  - email
  - id_card
  - address

whitelist:
  - 王者荣耀
  - 赵云   # 如果项目里这不是人名而是游戏角色/术语，可白名单
```

---

## 附录 H：测试用例清单（验收必备）

### H1. 基础识别

* 手机号/邮箱/身份证：多种格式、带空格/带括号
* 人名：2/3/4 字；复姓；夹杂英文；在句首句中句尾
* JSON/YAML：字段命中识别，值为字符串/数组/嵌套对象

### H2. 冲突与重叠

* `张三 13812345678`：姓名与手机号相邻
* `email: abc@xx.com`：冒号结构
* 一段数字既像订单号又像手机号：优先 PHONE（regex）

### H3. 格式无损

* 表格对齐（空格、tab）替换前后行长度变化控制（basic/strict）
* 日志固定结构：`[time] user=张三 action=...` 不能破坏键值结构
* JSON 不能破坏引号与逗号

### H4. 回填

* stdout 回填：替换后文本能完整还原
* git diff 文件回填：Claude Code 改 docs 后回填正确
* 部分 token 被改写：系统能报告未回填统计，不误回填

### H5. 安全

* vault 必须在 repo 外
* vault 加密开关验证
* 日志不得输出 original_text

---

## 附录 I：失败策略（Fail-Closed vs Fail-Open）

默认建议：**Fail-Closed（宁可不发也不裸发）**

* Ollama 不可用/解析失败 → 仍可用 regex+schema（对人名可能误报/漏报，给用户提示）
* vault 写入失败 → 停止执行并提示（避免“脱敏但无法回填”的事故）

可配置：Fail-Open（更顺滑，但风险更高）

* 只适合非敏感环境

---

## 接下来最值得做的“开工清单”（按优先级）

1. Span 规范 + 冲突解决（附录 A/B）
2. 替换倒序算法 + basic 长度策略（附录 C）
3. Vault（先 sqlite，再加密）
4. Ollama 裁判模式（附录 E）
5. git diff 文件回填（Claude Code 场景闭环）

---

如果你下一步希望我再继续，我建议进入“**更像工程设计文档**”的层级：我可以把 `候选挖掘规则（中文人名）`、`姓氏表策略`、`chunk 分块与全局 offset 映射`、以及 `Ollama 调用的具体 payload（HTTP）` 都写成一份 **Tech Spec**，让你/开发者直接照着实现。
