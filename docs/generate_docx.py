# -*- coding: utf-8 -*-
"""根据本文档生成《项目介绍文档》Word 版。

严格按照官方 7 条要求编排：
  (1) 项目详情及使用方式
  (2) 项目亮点
  (3) AI 测试技术落地使用情况概述
  (4) 项目中涉及的关键技术
  (5) 项目相关的图片/照片
  (6) 项目之前是否发表过或获奖
  (7) 若项目在其他项目基础上优化，请说明优化部分
"""

import os

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "项目介绍.docx")

CN_FONT = "Microsoft YaHei"
MONO_FONT = "Consolas"
ACCENT = RGBColor(0x1A, 0x1A, 0x1A)
GREY = RGBColor(0x55, 0x55, 0x55)


# ---------------------------------------------------------------- 基础工具
def set_cn_font(run, name=CN_FONT, size=None, bold=None, color=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def init_styles(doc):
    normal = doc.styles["Normal"]
    normal.font.name = CN_FONT
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.4

    for level, size in ((1, 16), (2, 13), (3, 11.5)):
        st = doc.styles["Heading %d" % level]
        st.font.name = CN_FONT
        st.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = ACCENT
        st.paragraph_format.space_before = Pt(14 if level == 1 else 10)
        st.paragraph_format.space_after = Pt(6)


def h1(doc, text):
    p = doc.add_heading(level=1)
    set_cn_font(p.add_run(text), size=16, bold=True, color=ACCENT)
    return p


def h2(doc, text):
    p = doc.add_heading(level=2)
    set_cn_font(p.add_run(text), size=13, bold=True, color=ACCENT)
    return p


def h3(doc, text):
    p = doc.add_heading(level=3)
    set_cn_font(p.add_run(text), size=11.5, bold=True, color=ACCENT)
    return p


def para(doc, text, bold=False, size=10.5, color=None, indent=True, space_after=6):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    if indent:
        p.paragraph_format.first_line_indent = Pt(21)
    # 支持 **加粗** 片段
    parts = text.split("**")
    for index, chunk in enumerate(parts):
        if not chunk:
            continue
        run = p.add_run(chunk)
        set_cn_font(run, size=size, bold=(bold or index % 2 == 1), color=color)
    return p


def bullet(doc, text, level=0):
    """条款式段落。刻意不用项目符号，保持正式报告的排版习惯。"""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.75 + level * 0.6)
    p.paragraph_format.first_line_indent = Pt(0)
    p.paragraph_format.space_after = Pt(4)
    parts = text.split("**")
    for index, chunk in enumerate(parts):
        if not chunk:
            continue
        set_cn_font(p.add_run(chunk), size=10.5, bold=(index % 2 == 1))
    return p


def code_block(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing = 1.15
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), "F4F6F9")
    p._p.get_or_add_pPr().append(shading)
    for index, line in enumerate(text.split("\n")):
        run = p.add_run(line)
        run.font.name = MONO_FONT
        run._element.rPr.rFonts.set(qn("w:eastAsia"), MONO_FONT)
        run.font.size = Pt(9)
        if index < len(text.split("\n")) - 1:
            run.add_break()
    return p


def shade_cell(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def add_table(doc, headers, rows, widths=None, caption=None):
    if caption:
        cp = doc.add_paragraph()
        cp.paragraph_format.space_after = Pt(3)
        set_cn_font(cp.add_run(caption), size=9.5, bold=True, color=GREY)

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    hdr = table.rows[0].cells
    for i, name in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_cn_font(p.add_run(name), size=9.5, bold=True)
        shade_cell(hdr[i], "F0F0F0")

    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            set_cn_font(p.add_run(str(value)), size=9.5)

    if widths:
        for row in table.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)

    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def image_placeholder(doc, title, hint):
    """截图占位框：用单元格模拟，提示需要插入什么。"""
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.rows[0].cells[0]
    cell.text = ""
    shade_cell(cell, "FAFBFD")

    p1 = cell.paragraphs[0]
    p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p1.paragraph_format.space_before = Pt(28)
    set_cn_font(p1.add_run("【此处插入截图】" + title), size=11, bold=True, color=ACCENT)

    p2 = cell.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.paragraph_format.space_after = Pt(28)
    set_cn_font(p2.add_run(hint), size=9.5, color=GREY)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_page_number_footer(doc):
    footer = doc.sections[0].footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    set_cn_font(run, size=9, color=GREY)
    for instr in ("PAGE",):
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr_el = OxmlElement("w:instrText")
        instr_el.text = instr
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        run._r.append(fld_begin)
        run._r.append(instr_el)
        run._r.append(fld_end)


# ---------------------------------------------------------------- 正文
def build():
    doc = Document()
    init_styles(doc)

    section = doc.sections[0]
    section.left_margin = Cm(2.4)
    section.right_margin = Cm(2.4)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.0)
    add_page_number_footer(doc)

    # ---------------- 封面 ----------------
    for _ in range(3):
        doc.add_paragraph()
    tp = doc.add_paragraph()
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(tp.add_run("Probator"), size=30, bold=True, color=ACCENT)

    sp = doc.add_paragraph()
    sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(sp.add_run("智能体回归测试与上线门禁平台"), size=15, bold=True)

    sp2 = doc.add_paragraph()
    sp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(sp2.add_run("不只判断「答得对不对」，还判断「想得对不对」"), size=11, color=GREY)

    doc.add_paragraph()
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(sub.add_run("项目介绍文档"), size=18, bold=True)

    for _ in range(4):
        doc.add_paragraph()

    info = doc.add_table(rows=0, cols=2)
    info.alignment = WD_TABLE_ALIGNMENT.CENTER
    pairs = [
        ("作品形态", "Web 平台 + 命令行门禁工具"),
        ("技术栈", "Python 3.9+ / FastAPI / 原生前端（零第三方依赖）"),
        ("文档版本", "v1.0"),
    ]
    for key, value in pairs:
        cells = info.add_row().cells
        cells[0].width = Cm(3.2)
        cells[1].width = Cm(10.5)
        p0 = cells[0].paragraphs[0]
        set_cn_font(p0.add_run(key), size=10.5, bold=True, color=GREY)
        p1 = cells[1].paragraphs[0]
        set_cn_font(p1.add_run(value), size=10.5)

    doc.add_page_break()

    # ============================================================
    h1(doc, "一、项目详情及使用方式")

    h2(doc, "1.1 项目简介")
    para(doc, "Probator是一个面向 AI 智能体的**回归测试与上线门禁平台**。它给智能体做体检，"
              "并决定它能不能上线：不仅判断最终回答对不对，还能判断智能体的**决策过程**对不对——"
              "计划是否与实际动作一致、是否真的检索了知识库、是否引用了已废止的旧条款、"
              "工具报错后是否谎报成功、回答里的数字能否在上下文里找到出处。")
    para(doc, "在工程形态上，它同时提供 Web 可视化平台与命令行门禁（带退出码），"
              "可直接接入 CI，实现「改完就测、坏了就拦」。")

    h2(doc, "1.2 解决的现实问题")
    para(doc, "AI 智能体正在进入校园服务、企业客服、政务咨询等场景。这些团队有一个共同困境："
              "**改完提示词、换了模型、加了知识库之后，不知道是变好了还是变坏了。**")
    add_table(
        doc,
        ["现有做法", "为什么不够用"],
        [
            ["人工试几个问题", "不可复现、覆盖不全、说不清「改坏了什么」"],
            ["只看最终回答", "答对了可能是蒙对的；答错了也定位不到错在哪一步"],
            ["让大模型当裁判打分", "主观、不稳定、无法定位缺陷，且判定器自身会误判"],
        ],
        widths=[4.5, 10.5],
    )
    para(doc, "根本原因在于：**智能体不是普通程序**。它有决策链——理解意图 → 检索知识 → 调用工具 → "
              "组织回答。任何一环出错，用户看到的都是「回答不对劲」，但根因完全不同。"
              "传统软件测试只看输入输出，抓不到中间层。")

    h2(doc, "1.3 系统架构")
    para(doc, "平台由六层组成，层与层之间以统一响应协议解耦：")
    add_table(
        doc,
        ["层次", "模块", "职责"],
        [
            ["接入层", "adapters.py", "声明式配置接入；支持 HTTP / SSE 流式 / OpenAI 兼容 / trace 归一化"],
            ["出题层", "generator.py、cases/", "AI 用例生成（带强校验）＋ 领域用例集与通用用例集"],
            ["执行层", "runner.py", "并发执行、多采样、会话隔离、失败重试"],
            ["判定层", "judges.py", "L1 规则判定 + L2 语义判定 + L3 决策级判定"],
            ["统计层", "metrics.py、diff.py", "七维度加权、短板惩罚、缺陷聚合、回归对比"],
            ["出口层", "server.py、run.py", "Web API 与命令行门禁（退出码）"],
        ],
        widths=[2.4, 4.6, 8.0],
    )
    para(doc, "被测对象（自建夹具、第三方开源项目、在线服务）通过适配器映射到统一协议后即可测试，"
              "**用例与判定逻辑完全不需要改动**。统一协议如下：")
    code_block(doc,
               "请求 → { message, session_id }\n"
               "响应 → { ok, status_code, answer, trace[], citations[], latency_ms, error, raw }")

    h2(doc, "1.4 功能清单")
    add_table(
        doc,
        ["功能", "说明"],
        [
            ["三层判定", "L1 规则 / L2 语义 / L3 决策链路，逐层深入"],
            ["决策级断言", "计划与动作一致性、检索真实性、废止条款引用、数字可溯源性、失败谎报"],
            ["变异体校准", "6 种故障注入，测量判定器自身的检出率与误报率"],
            ["多采样三态判定", "通过 / 不稳定 / 失败，其中不稳定同样阻断发布"],
            ["回归对比", "自动列出回归、修复、新增不稳定，可导出 Markdown"],
            ["能力自适应降级", "无 trace 时自动跳过 L3，不把「看不见」判成「做错了」"],
            ["配置即接入", "12 行 JSON 接入任意 HTTP / SSE / OpenAI 兼容目标"],
            ["AI 用例生成", "按目标能力清单批量出题，带 schema 与文档真实性校验"],
            ["改进方案生成", "按失败证据归纳根因级方案，每条须绑定真实用例或断言，可复跑验证"],
            ["七维度评分", "功能/工具/RAG/多轮/安全/边界/性能，含短板惩罚"],
            ["CI 门禁", "命令行退出码 0/1/2，附 GitHub Actions 模板"],
        ],
        widths=[3.6, 11.4],
    )

    h2(doc, "1.5 使用方式")

    h3(doc, "1.5.1 环境要求")
    para(doc, "Python 3.9 及以上。除 FastAPI、Uvicorn、Pydantic 外**零第三方依赖**："
              "HTTP 使用标准库 urllib（含手写 SSE 解析），并发使用 concurrent.futures，持久化使用 JSON 文件。"
              "这一选择保证在任何评审环境中都能直接运行。")

    h3(doc, "1.5.2 启动步骤")
    code_block(doc,
               "# 1. 启动被测夹具（校园智能助手，真实大模型链路）\n"
               "cd 待测智能体\n"
               "python agent_server.py          # http://127.0.0.1:8000\n"
               "\n"
               "# 2. 启动测试平台\n"
               "cd ../平台\n"
               "pip install -r requirements.txt\n"
               "python server.py                # http://127.0.0.1:8500")

    h3(doc, "1.5.3 界面操作流程")
    add_table(
        doc,
        ["步骤", "操作", "说明"],
        [
            ["1", "打开 http://127.0.0.1:8500", "平台主界面"],
            ["2", "点左栏「＋ 添加目标」", "弹出设置方块栏"],
            ["3", "选择被测目标与用例集", "目标健康状态实时显示"],
            ["4", "设置采样次数与并发", "采样 >1 可检出不稳定用例"],
            ["⑤", "点「开始测试」", "主区域显示实时进度看板"],
            ["⑥", "查看结果", "分数环、维度得分、分层判定、用例结果、缺陷清单"],
            ["⑦", "点任意用例", "右侧抽屉展示输入、回答、断言明细、决策链路时间线"],
        ],
        widths=[1.4, 6.0, 7.6],
    )

    h3(doc, "1.5.4 命令行门禁（CI 使用）")
    code_block(doc,
               "cd 平台\n"
               "\n"
               "python run.py                                  # 全量 + 默认门禁\n"
               "python run.py --samples 3 --fail-under 95      # 三采样 + 质量分下限\n"
               "python run.py --case generic_smoke.json        # 通用用例集（任意目标可用）\n"
               "python run.py --no-gate                        # 只跑不卡\n"
               "\n"
               "# 退出码：0 = 达标  1 = 门禁未通过  2 = 执行异常")
    para(doc, "门禁条件：失败用例数、不稳定用例数、加权质量分下限、**相对基线是否出现回归**，四项均可配置。")

    h3(doc, "1.5.5 接入自己的智能体")
    para(doc, "目标只要能被 HTTP 调用即可接入，提供两种方式：")
    bullet(doc, "**界面向导**：填地址 → 点「探测接口」→ 系统真实发送一条消息并自动猜出字段映射 → 保存。")
    bullet(doc, "**配置文件**：在 平台/config.json 的 targets 数组中声明。")
    para(doc, "对于形态不标准的目标，平台提供五种适配能力：")
    add_table(
        doc,
        ["目标情况", "适配方式"],
        [
            ["请求体不是 {message}", "用 request_template 自定义，支持 {message} 占位符"],
            ["回答藏在嵌套路径", "response_map.answer 支持 choices.0.message.content 这类路径"],
            ["接口为流式（SSE）", "声明 stream_mode=\"sse\" 与 sse_map 事件名"],
            ["没有数组型 trace", "用 trace_fields 把散装标量字段组装成决策链"],
            ["只返回纯文本", "照样能测，L3 自动降级为黑盒模式，仅跑 L1 + L2"],
        ],
        widths=[5.0, 10.0],
    )

    h3(doc, "1.5.6 目录结构")
    code_block(doc,
               "Probator/\n"
               "├── 平台/                     测试平台（作品主体）\n"
               "│   ├── server.py             FastAPI 服务\n"
               "│   ├── run.py                CLI 门禁入口\n"
               "│   ├── gen_cases.py          AI 用例生成入口\n"
               "│   ├── core/\n"
               "│   │   ├── adapters.py       适配器层（HTTP / SSE / trace 归一化）\n"
               "│   │   ├── judges.py         三层判定引擎\n"
               "│   │   ├── runner.py         并发执行 + 多采样\n"
               "│   │   ├── metrics.py        七维度加权 + 短板惩罚\n"
               "│   │   ├── diff.py           回归对比\n"
               "│   │   ├── generator.py      AI 用例生成\n"
               "│   │   └── store.py          持久化 + 目标注册\n"
               "│   ├── cases/\n"
               "│   │   ├── seed.json         70 条领域用例\n"
               "│   │   └── generic_smoke.json 12 条通用用例\n"
               "│   └── web/index.html        前端界面\n"
               "├── 待测智能体/                被测夹具\n"
               "│   ├── agent_server.py       7 工具 + RAG + 结构化 trace + 6 种故障注入\n"
               "│   └── knowledge_base/       5 篇文档 / 28 分块（含 1 篇废止版冲突文档）\n"
               "└── docs/                     作品文档")

    doc.add_page_break()

    # ============================================================
    h1(doc, "二、项目亮点")

    h2(doc, "亮点一：决策级断言（Trace-Conformance），测试深度超过传统方案")
    para(doc, "传统测试断言的是「输出对不对」，本作品把断言下移到**决策链**：")
    add_table(
        doc,
        ["决策级断言", "检测的缺陷类型"],
        [
            ["计划与实际动作一致性", "说了不做 / 做了不说：自述要查课表，实际调了成绩接口"],
            ["检索真实性", "声称查了知识库，实际没有产生任何检索事件"],
            ["废止条款引用", "命中了知识库里已作废的旧版条款，用户拿到错误金额"],
            ["数字可溯源性", "回答里的金额/时限/学分，在上下文中查无出处（幻觉）"],
            ["失败谎报", "工具返回 error，回答却宣称「已提交成功」"],
            ["动作顺序", "先作答再调工具（逻辑倒置）"],
        ],
        widths=[5.0, 10.0],
    )
    para(doc, "**这些断言全部由 trace 结构自动推导，用例编写者不需要重复描述判定逻辑。**")
    para(doc, "价值证明：同一套 35 条用例，对**有 trace 的目标**产生 373 条断言，"
              "对**只有纯文本的目标**只有 106 条断言。**多出的 267 条全在决策链路上**——"
              "这是只看输入输出永远无法触及的测试深度。")

    h2(doc, "亮点二：变异体校准——用注入缺陷验证判定器本身")
    para(doc, "AI 测试工具最容易被质疑的地方是：「你怎么证明你的结论可信？」"
              "如果只拿一个好智能体去测、出一份全绿报告，什么也证明不了。")
    para(doc, "本作品移植传统软件测试的**变异测试（mutation testing）**：故意造 6 个「只坏一个点」的智能体，"
              "用固定用例集去测，测量两个指标——**检出率**（该抓的抓到没有）与**误报率**（不该抓的抓了没有）。")
    add_table(
        doc,
        ["变异体", "破坏的能力", "加权分", "用例通过", "检出", "跨维度"],
        [
            ["（干净基线）", "无，作为对照组", "79.18", "55/70", "—", "—"],
            ["rag_infidelity", "检索命中已废止文档", "39.91", "31/70", "24", "13"],
            ["misroute", "工具路由到相邻错误工具", "47.19", "39/70", "17", "9"],
            ["weak_guard", "提示词换成无限制助手", "64.71", "50/70", "5", "4"],
            ["halluc_param", "工具参数编造", "64.40", "49/70", "6", "4"],
            ["memory_decay", "清空会话记忆", "67.73", "53/70", "3", "0"],
        ],
        widths=[3.0, 4.4, 2.0, 2.2, 1.6, 1.8],
    )
    para(doc, "口径：**检出**＝相对干净基线新失败的用例数（基线本来就失败的排除，不算工具功劳）；"
              "**跨维度**＝新失败但维度不在该变异体预期影响范围内，需人工确认。"
              "五个变异体均分 **56.79**，比干净基线低 **22.39** 分。")
    para(doc, "结论有三点。**敏感度**方面，5 个变异体全部显著低于基线，降幅 12 至 39 分；"
              "**区分度**方面，不同故障类型得分明显不同，检索失真最低 39.91、记忆衰减最高 67.73，"
              "说明工具能识别故障类型而不是一律报错；**归因集中度**方面，55 条检出用例中 24 条"
              "落在预期维度内，约占 44%，跨维度部分多数是真实的连带影响。")
    para(doc, "**如实说明**：早期在 35 条用例集上曾观察到「4 个变异体零跨维度影响」，扩展到 70 条后"
              "该现象不再成立。这说明归因精度与用例规模强相关，不应把「零误报」当作稳定结论对外宣称，"
              "本作品据此修正了指标口径。")
    para(doc, "该机制还具备**回归保护**作用：日后修改判定器或用例，重跑变异体矩阵，"
              "检出率下降即说明灵敏度被改坏——**等于给测试工具本身配了一套回归测试**。")

    h2(doc, "亮点三：多采样三态判定，直面大模型不确定性")
    para(doc, "不假装智能体是确定性的。同一问题采样 N 次，输出三态：")
    add_table(
        doc,
        ["状态", "含义", "发布决策"],
        [
            ["pass", "N 次全部通过", "放行"],
            ["flaky", "部分通过（不稳定）", "同样阻断发布，但定位为稳定性问题"],
            ["fail", "N 次全部失败", "阻断发布（确定性缺陷）"],
        ],
        widths=[2.4, 6.4, 6.2],
    )
    para(doc, "实测中发现：两条用例在单次采样时会**随机**判成通过或失败，两种结论都是错的；"
              "两次采样后稳定暴露为 flaky，并精确定位到飘移的具体断言。"
              "**绝大多数评测工具报告的分数都建立在单次采样之上，本质上不可复现**——这是本亮点的出发点。")

    h2(doc, "亮点四：能力自适应降级，把「不可观测」与「做错了」分开")
    para(doc, "被测对象的可观测性参差不齐：自研项目常返回结构化 trace，第三方 Agent 往往只回一段文本。"
              "若用同一套标准硬判，会把「看不见」误判成「做错了」——这是评测工具最常见的不公平。")
    para(doc, "本作品通过能力声明 + 自动降级解决：目标声明 capabilities.trace=false，或响应中确实没有 trace 时，"
              "L3 整层标注「不适用」，**不计入通过率、不算缺陷**。"
              "副产品是一个有说服力的论证：灰盒 373 条断言 vs 黑盒 106 条断言，"
              "量化说明了「为什么智能体测试必须要求可观测性」。")

    h2(doc, "亮点五：判定器发现并推动上游修复了真实缺陷")
    para(doc, "在外部开源项目中发现的输入污染缺陷，具备四个特点：")
    bullet(doc, "**真实**：不是构造的，是 321 星项目里实际存在的生产代码")
    bullet(doc, "**隐蔽**：不报错、不抛异常，静默把空格/数字/标点替换成控制字节，"
                "模型只是收到残缺文本，看起来像「理解不了你说什么」")
    bullet(doc, "**只有自动化用例能发现**：必须有「具体输入 + 硬断言」的组合，LLM 主观打分测不出，"
                "因为模型对残缺输入的回答往往「看起来合理」")
    bullet(doc, "**已被上游采纳**：原作者确认并修复，最终合并进项目主干分支，并补充 32 个回归测试")
    para(doc, "详见第三章 3.4 节与第六章。")

    h2(doc, "亮点六：证据驱动的改进方案，把测试报告变成修复工单")
    para(doc, "多数测试工具止步于「哪里失败了」。但真正决定工具价值的，是失败之后那一句建议——"
              "而这恰恰是最容易注水的地方。让模型看着失败清单写建议，它很自然会输出"
              "「优化提示词」「完善知识库」这类放到任何项目上都成立的空话。")
    para(doc, "本作品的做法是给建议加一道硬约束：**每条方案必须绑定证据**。"
              "模型产出的 affected_cases 只能取自本次真实失败的用例编号，"
              "evidence 数组里必须出现真实的用例编号或断言编号，"
              "fix.patch 必须是一条可以照着做的动作。任何一条落不到实处，整条方案被丢弃。")
    para(doc, "实测：一次运行中 15 条失败用例，模型归纳出 5 条根因级方案，"
              "覆盖 RAG 检索链路、意图路由、工具入参、指代消解、权限校验五个层面，"
              "并有 0 条因证据不足被丢弃。以其中一条为例：")
    add_table(
        doc,
        ["字段", "内容"],
        [
            ["根因", "知识库检索链路空召回且回答未挂知识库引用"],
            ["影响用例", "RAG-002、RAG-004、RAG-005、RAG-006 等 8 条"],
            ["证据", "RAG-002 的 L1.cite_required 实际为空、L3.rag.retrieved 无召回，"
                    "且 L3.fact.grounded 出现无出处数字"],
            ["改哪层", "config —— RAG 检索配置与引用后处理"],
            ["具体动作", "top_k 提升到 5、相似度阈值下调至 0.15、政策类问题启用 query 改写；"
                        "回答生成后置校验，正文出现数字但引用为空则改为「知识库暂无该条款」"],
            ["验证方式", "重跑上述 8 条，期望 L3.rag.retrieved≥1、L1.cite_required≥1"],
        ],
        widths=[2.4, 12.6],
    )
    para(doc, "这样输出的不再是「建议」，而是一份可以派给工程师的**修复工单**："
              "改哪一层、改什么、改完怎么验证，三件事都写清楚了。")

    doc.add_page_break()

    # ============================================================
    h1(doc, "三、AI 测试技术落地使用情况概述")

    h2(doc, "3.1 总体落地情况")
    add_table(
        doc,
        ["指标", "数值"],
        [
            ["平台代码规模", "11 个 Python 文件，2837 行"],
            ["用例规模", "70 条领域用例 + 12 条通用用例"],
            ["用例优先级分布", "P0 28 条 / P1 36 条 / P2 6 条"],
            ["判定层数", "3 层（L1 / L2 / L3）"],
            ["评测维度", "7 个，带权重与短板惩罚"],
            ["故障注入模式", "6 种"],
            ["累计测试轮次", "50+ 轮（含基线、变异体、多采样、外部项目各阶段）"],
            ["被测目标", "5 个（自建夹具灰盒与黑盒、2 个外部项目、1 个线上服务探针）"],
        ["改进方案", "单轮 15 条失败归纳出 5 条根因级方案，证据绑定率 100%"],
            ["被测目标数", "3 类：自建夹具（灰盒）、自建夹具（黑盒视图）、外部开源项目"],
        ],
        widths=[4.5, 10.5],
    )

    h2(doc, "3.2 自建夹具基线表现")
    para(doc, "被测夹具为校园智能助手：7 个工具、5 篇知识库文档（28 个分块，其中 1 篇为故意保留的废止版冲突文档），"
              "通过环境变量可切换 6 种故障注入模式。")
    add_table(
        doc,
        ["场景", "加权质量分", "用例通过", "说明"],
        [
            ["模板链路 + 干净（对照）", "100.00", "35/35", "确定性输出，仅用于验证代码路径，无信息量"],
            ["真实大模型单采样", "93.33", "33/35", "发现 2 条不稳定用例"],
            ["70 条用例完整基线", "78.59", "55/70", "断言 783/831"],
        ],
        widths=[5.0, 2.8, 2.6, 4.6],
    )
    para(doc, "70 条用例的维度得分：")
    add_table(
        doc,
        ["维度", "通过/总数", "通过率", "权重"],
        [
            ["功能准确性", "9/11", "81.8%", "20%"],
            ["工具调用", "9/12", "75.0%", "20%"],
            ["RAG 检索质量", "7/11", "63.6%", "15%"],
            ["多轮对话", "6/9", "66.7%", "15%"],
            ["对抗与安全", "13/14", "92.9%", "15%"],
            ["边界与异常", "5/5", "100.0%", "10%"],
            ["性能", "6/8", "75.0%", "5%"],
        ],
        widths=[4.4, 3.2, 3.2, 2.4],
    )
    para(doc, "**78.59 分比 100 分更有价值。** 干净基线落在 75–85 之间，说明用例足够严格、仍留有真实提升空间；"
              "100 分只能证明「用例和夹具自洽」。剩余失败多数是夹具的真实能力短板"
              "（路由把「我高数多少分」导向政策检索、多意图只处理一个、RAG 引用不足），属于有意保留的真实缺陷。")

    h2(doc, "3.3 变异体检出情况")
    para(doc, "在 70 条完整用例集上标定判定器灵敏度，结果见第二章亮点二。核心结论："
              "**5 个变异体全部显著低于干净基线（均分 56.79 vs 79.18），且不同故障类型得分可区分。**")
    para(doc, "该机制同时反向暴露了用例集的覆盖盲区：halluc_param 仅命中 3 条，"
              "说明当时只有 3 条用例断言了工具参数——据此已在后续扩充中补齐。")

    h2(doc, "3.4 被测目标与接入形态")
    para(doc, "平台的设计目标是「不绑定任何具体智能体」。为了验证这一点，"
              "实际接入并测试了五类形态不同的目标，覆盖从完全可控的自建夹具到"
              "无法修改的第三方部署：")
    add_table(
        doc,
        ["被测目标", "形态", "业务场景", "接入方式", "验证结果"],
        [
            ["校园智能助手（自建夹具）", "灰盒",
             "校园教务、校园卡、报修", "标准 JSON 接口，12 行配置",
             "77.37 分（54/70），373 条断言"],
            ["同一夹具的黑盒视图", "黑盒",
             "同上，但关闭 trace 与引用", "同一接口，隐藏可观测字段",
             "106 条断言，L3 自动跳过"],
            ["ai-real-estate-assistant", "外部项目（Docker）",
             "海外房产检索问答", "自定义请求体 + 鉴权头 + trace 归一化",
             "43.75 分，发现真实缺陷，修复后 67.50"],
            ["agent-service-toolkit", "外部项目（本地部署）",
             "企业知识场景（含 10 个可选 agent）",
             "SSE 流式：事件类型藏在 data.type", "71.14 分（16/22）"],
            ["paypilot", "外部服务（线上）",
             "订阅支付失败挽回决策", "业务字段请求体，无对话语义",
             "仅做探针验证，未纳入完整评测"],
        ],
        widths=[3.6, 2.8, 3.4, 4.0, 3.2],
    )
    para(doc, "五类目标对应平台适配层的五种能力：标准 JSON 接口、黑盒降级、"
              "自定义请求体与鉴权、SSE 流式（含事件类型不在 event 行的变体）、"
              "以及业务结构化接口。**接入一个外部目标平均只需十余行配置，"
              "不改动对方仓库任何代码。**")

    h3(doc, "3.4.1 外部目标一：开源房产助手")
    para(doc, "该项目为 321 星的开源项目，采用 FastAPI + LangChain + ChromaDB 架构，"
              "通过 Docker 容器部署，模型侧使用 DeepSeek。它返回的 intermediate_steps "
              "需要归一到平台的事件结构，是典型的「框架各异、但可观测」的第三方智能体。"
              "本作品在它上面完整走通了发现、定位、修复、复测、上报五个环节，详见 3.5 节。")

    image_placeholder(doc, "房产项目首页", 
                      "内容：该开源项目的前端首页，可见导航（Search / Cities / Analytics / Find Agents / Knowledge）、"
                      "主标题与示例查询。说明被测对象是具备完整前后端的真实产品。")
    image_placeholder(doc, "房产项目对话页（缺陷证据）",
                      "内容：通过前端对话页发起房源查询后的实际返回。该智能体在房源库为空时返回"
                      "「Please include the listings/context」，把「当前无数据」转嫁给用户，"
                      "属于责任转嫁式拒答，是判定器重点关注的可控性缺陷。")

    h3(doc, "3.4.2 外部目标二：LangGraph 智能体服务")
    para(doc, "该项目是基于 LangGraph 与 FastAPI 的智能体服务模板，内置 10 个可切换的 agent"
              "（对话、文档问答、联网研究、多智能体编排、安全审查等）。"
              "本地部署后选中其中的研究助手作为被测对象，它具备联网搜索与计算两类工具。")
    para(doc, "这个目标带来两处新的适配挑战。其一流式响应没有 event 行，"
              "事件类型写在 data 的 type 字段里，平台为此增加了「按 data.type 取事件名」的回退规则；"
              "其二工具调用分散在 ai 与 tool 两类消息中，平台增加了从消息序列还原"
              "「调用了哪个工具、传了什么参数、返回了什么」的解析能力，"
              "使该目标同样具备决策级判定条件。")
    para(doc, "实测 22 条通用用例通过 16 条，得分 71.14。失败项集中在幻觉抑制与多轮记忆："
              "被问到不存在的机构时编造成立时间，被问到尚未发生的奖项时未承认未知，"
              "用户更正信息后仍沿用旧值。这些是该智能体自身的真实短板，不是判定偏差，"
              "也说明通用用例集确实能跨业务域发现同类问题。")

    image_placeholder(doc, "LangGraph 服务的 agent 列表",
                      "内容：该服务 /info 接口返回的可用 agent 清单（对话、文档问答、联网研究、"
                      "多智能体编排、安全审查等共 10 个）。本次选用其中的研究助手作为被测对象。")

    h3(doc, "3.4.3 一个未纳入评测的样本")
    para(doc, "另有一个线上运行的支付决策类服务（paypilot）做过探针验证。"
              "它能正常响应并返回诊断、风险、策略、财务影响等结构化字段，"
              "但其接口是业务决策型而非对话型，输入为发票事件、输出为一组决策字段，"
              "与本项目的通用用例集不匹配，需要另写一套业务规则用例才有评测意义。"
              "考虑到投入产出，最终未纳入完整评测，仅在文档中记录接入可行性。")

    h2(doc, "3.5 外部开源项目验证（完整闭环）")
    para(doc, "被测对象：某开源 AI 房产助手（321 星，FastAPI + LangChain + ChromaDB，Docker 容器部署）。"
              "**接入成本为 12 行 JSON 配置，未修改其仓库任何一行代码。**")
    add_table(
        doc,
        ["阶段", "加权质量分", "用例通过", "关键发现"],
        [
            ["首次测试", "43.75", "8/12", "4 条缺陷"],
            ["定位根因", "—", "—", "输入清洗逻辑把空格/数字/标点替换为控制字节"],
            ["修复后复测", "67.50", "11/12", "修复 3 条，回归 0 条"],
        ],
        widths=[3.4, 3.0, 3.0, 6.8],
    )

    h3(doc, "3.5.1 发现的缺陷")
    para(doc, "输入清洗环节把空格、数字和标点替换成控制字节，导致送进模型与向量库的文本被污染：")
    code_block(doc,
               "'预算是50万人民币'   →  '预算是\\x97\\x92万人民币'      ← \"50\" 被吞\n"
               "'编号A7391号'       →  '编号A\\x99\\x95\\x9b\\x93号'     ← \"7391\" 全被吞\n"
               "'hello 50 world'   →  'hello\\x82\\x97\\x92\\x82world'  ← 英文同样中招")
    para(doc, "**根因**：str.translate() 传入 bytes 表时是**按序号查表**，序号 i 取表里第 i 个字节作为替换值。"
              "该表只有 62 字节，故只覆盖序号 0–61，而这一区间恰好包含 ASCII 控制字符、空格（32）、"
              "数字（48–57）与常用标点。于是这些字符全部被替换成控制字节。"
              "**这不是 CJK 特有问题，是 ASCII 层面的破坏。**")
    para(doc, "**两个入口同时受影响**：chat 与 search 路径都在文本进入模型或向量库之前调用该清洗函数，"
              "所以模型和检索层拿到的都是被污染的输入。")
    para(doc, "**影响**：金额、面积、编号等关键参数全部丢失，且无任何错误提示——"
              "模型只是收到残缺文本，看起来像「理解不了你说什么」。修复后 3 条用例全部通过。")

    h3(doc, "3.5.2 上游采纳情况")
    add_table(
        doc,
        ["项", "内容"],
        [
            ["缺陷范围", "确认，且比 CJK 更广——所有序号 < 0x3E 的字符（空格、数字、标点）都被改写"],
            ["修复提交", "583db2d7  fix(api): strip control chars by code point, not by bytes table (fixes #377)"],
            ["合并去向", "先入 dev 分支，再经 PR #383 合并进主干分支 main；该 PR 描述将本缺陷列在「Correctness」分类首位"],
            ["署名", "提交信息末尾保留 Reported by @xxchen-yu in issue #377."],
            ["回归测试", "30 个单元测试 + 2 个集成测试，其中一条断言为「过滤器永远不能再退回 bytes 表」"],
            ["验证方式", "对旧实现运行新测试失败 19 次；应用修复后全部通过"],
        ],
        widths=[2.6, 12.4],
    )

    h2(doc, "3.6 判定器自身的缺陷修正（可复现性说明）")
    para(doc, "工具在开发过程中**抓到自己四次误报**，全部已修复。这部分如实列出，"
              "因为它正是变异体校准存在的理由：")
    add_table(
        doc,
        ["#", "误报现象", "根因", "修法"],
        [
            ["1", "禁用词「0 元」被「20 元」命中", "纯子串匹配", "改为词边界感知匹配"],
            ["2", "「这次没有创建成功」被判为谎报成功", "未处理否定语境", "增加否定语境检测"],
            ["3", "拒答时说「系统提示词」被判为泄露", "把话题词当作泄露证据", "改用真正的泄露证据特征"],
            ["4", "目标未暴露「意图」却被判「意图不明确」", "把「字段缺失」当成「字段为空」",
             "字段缺席时跳过该断言，不判失败"],
        ],
        widths=[0.9, 5.4, 4.0, 4.7],
    )
    para(doc, "**一个不会误报的测试工具，比没有测试工具更危险**——它会给人虚假的安全感。")

    h2(doc, "3.7 落地结论")
    bullet(doc, "三层判定体系在**真实项目中发现了真实缺陷**，并推动上游完成修复与回归测试补充")
    bullet(doc, "变异体校准证明判定器具备可量化的检出能力，且能区分不同故障类型")
    bullet(doc, "多采样机制解决了大模型不确定性导致的结论不可复现问题")
    bullet(doc, "回归对比 + CLI 退出码使工具具备进入真实研发流程的形态")

    doc.add_page_break()

    # ============================================================
    h1(doc, "四、项目中涉及的关键技术")

    h2(doc, "4.1 三层判定引擎")
    add_table(
        doc,
        ["层", "判什么", "典型断言", "实现要点"],
        [
            ["L1 规则", "输出层", "关键词、格式、状态码、引用出处、工具调用与参数", "词边界感知匹配，避免子串误判"],
            ["L2 语义", "意思层", "准确性 / 忠实性 / 有用性 / 安全性 四维评分", "LLM-as-Judge，按 rubric 打分"],
            ["L3 决策", "链路层", "计划与实际一致性、检索真实性、废止引用、数字溯源、失败谎报", "由 trace 结构自动推导"],
        ],
        widths=[2.0, 2.2, 6.6, 4.2],
    )

    h2(doc, "4.2 决策级断言的实现")
    para(doc, "设计原则：**不依赖用例编写，全部由 trace 结构自动推导**。用例只需声明期望，"
              "一致性检查由引擎完成，新增用例无需重复描述判定逻辑。")
    para(doc, "以「工具失败不得谎报」为例，难点在于要识别**未被否定**的成功措辞。"
              "第一版直接做关键词匹配，把「报修工单这次**没有**创建成功」误判成谎报——"
              "因为句子里含「创建成功」。后续引入否定语境检测（匹配点前 8 个字符内出现"
              "「没有/未/无法/失败」等否定词即视为否定）才修掉该误报。")
    code_block(doc,
               "failed_tools = [e for e in tool_events(resp) if e.get(\"status\") == \"error\"]\n"
               "if failed_tools:\n"
               "    claims = success_claims(resp.get(\"answer\"))   # 否定感知\n"
               "    check(\"L3.error.no_false_success\", passed=not claims, severity=\"blocker\")")

    h2(doc, "4.3 变异体校准方法")
    para(doc, "沿用传统软件测试的变异测试思想，但变异对象不是代码行，而是**智能体的决策环节**："
              "路由、参数抽取、检索、记忆、护栏。每个变异体只破坏一个环节，便于归因。")
    para(doc, "评测指标：")
    bullet(doc, "**检出率** = 该抓到的用例数 / 应被抓到的用例数")
    bullet(doc, "**误报率** = 不该抓却抓到的用例数 / 不应受影响的用例数")
    para(doc, "变异体通过单一环境变量切换，保证除目标能力外其余条件完全一致。")

    h2(doc, "4.4 多采样与三态判定")
    para(doc, "对同一用例执行 N 次采样（每次都重置会话并重放前置轮），得到 N 份判定结果，"
              "据此计算通过次数与稳定度：")
    code_block(doc,
               "status = \"pass\"  if pass_count == samples else \\\n"
               "         \"fail\"  if pass_count == 0       else \"flaky\"\n"
               "stability = pass_count / samples")
    para(doc, "同时记录**跨样本失败断言的并集**与**飘移断言**（出现次数介于 1 与 N 之间的断言），"
              "后者是稳定性问题的直接线索。")

    h2(doc, "4.5 回归对比算法")
    para(doc, "对两次运行按用例 ID 对齐，用状态秩（pass=2, flaky=1, fail=0）比较迁移方向：")
    add_table(
        doc,
        ["迁移类型", "判定条件", "发布含义"],
        [
            ["regressed", "秩下降（好 → 坏）", "阻塞发布"],
            ["new_flaky", "新增不稳定", "阻塞发布"],
            ["improved / stabilized", "秩上升（坏 → 好）", "说明修复有效"],
            ["added / removed", "用例集变化", "提示对比口径变化"],
        ],
        widths=[3.6, 6.0, 5.4],
    )
    para(doc, "另做两项诚实性处理。其一，基线只取**更早**的运行，避免把更新的运行当作基线而导致对比方向反转；"
              "其二，当两次运行的采样次数或用例规模不同时输出警告，避免不可比的数据被直接对比。")

    h2(doc, "4.6 适配器层与 SSE 流式解析")
    para(doc, "被测对象的响应结构千差万别。适配器层提供四种归一化能力：")
    add_table(
        doc,
        ["能力", "解决什么问题"],
        [
            ["request_template / response_map", "请求体与响应字段的任意映射，支持嵌套路径"],
            ["stream_mode = sse", "读取 SSE 流：token 事件拼回答，trace 事件转决策链"],
            ["trace_normalizer", "LangChain intermediate_steps / 通用 steps 数组转平台事件"],
            ["trace_fields", "无数组型 trace 时，把散装标量字段组装成决策链事件"],
        ],
        widths=[5.6, 9.4],
    )
    para(doc, "SSE 解析使用标准库 urllib 手写实现：逐行读取响应，按 event / data 两行一组解析，"
              "支持超时与连接异常降级，不引入任何第三方流式库。")

    h2(doc, "4.7 AI 用例生成与强校验")
    para(doc, "用大模型根据目标能力清单（工具列表、知识库文档、可用故障模式）批量生成用例，"
              "输出严格符合平台 schema 的 JSON。**关键在强校验而非生成**：")
    add_table(
        doc,
        ["校验项", "动作"],
        [
            ["断言字段白名单", "出现未声明的断言字段直接拒收"],
            ["维度合法性", "必须是七个维度之一"],
            ["引用文档真实性", "citations_include 必须指向知识库中真实存在的文档，否则永不可能通过"],
            ["输入去重", "与既有用例按「维度 + 输入指纹」比对"],
            ["ID 唯一性", "冲突时重新编号，避免覆盖既有用例"],
        ],
        widths=[4.6, 10.4],
    )
    para(doc, "实测生成 35 条用例，约 15% 被拒收或需人工修正，典型错误包括：参数名写错、"
              "断言逻辑无法被给定输入触发、缺前置轮导致指代无对象。**修正逐条留了原因记录**，"
              "构成用例质量保障的可追溯证据。")

    h2(doc, "4.8 改进方案的证据绑定与三道校验")
    para(doc, "改进方案生成分三步：构造证据、约束输出、校验落地。")

    h3(doc, "4.8.1 证据构造")
    para(doc, "只把失败用例送进模型，模型看到的信息和看日志的人一样贫瘠。"
              "本作品为每条失败用例附带四类结构化证据：失败断言及其期望值与实际值、"
              "工具调用记录（工具名、参数、返回、状态）、检索片段（文档编号、得分、是否废止）、"
              "以及被测目标的原始回答。有了这些，模型才能判断到底是检索错了、参数错了、还是话术错了。")

    h3(doc, "4.8.2 输出约束")
    add_table(
        doc,
        ["约束", "作用"],
        [
            ["affected_cases 白名单", "只能取本次真实失败的用例编号，模型编造的编号被过滤"],
            ["evidence 必须命中真实标识", "每条证据须包含真实用例编号或断言编号，否则整条方案丢弃"],
            ["fix.patch 非空", "没有可执行动作的方案不进入结果集"],
            ["fix.layer 枚举", "只能是 prompt / config / code / data，强制模型说清改哪一层"],
            ["合并同根因", "同一根因导致的多个失败必须归并为一条，避免逐条复述"],
        ],
        widths=[4.6, 10.4],
    )

    h3(doc, "4.8.3 结果排序与复用")
    para(doc, "方案按影响面排序：P0 用例权重 5、P1 权重 2、P2 权重 1，"
              "累加受影响用例的权重作为优先级分，保证「影响高危用例多的根因」排在最前。")
    para(doc, "生成结果落盘缓存，重复查看不再调用模型（实测缓存命中耗时 0.02 秒）。"
              "该功能与 AI 用例生成共用同一个模型调用底座 core/llm.py，"
              "配置解析、API Key 优先级、HTTP 调用、JSON 提取四件事只实现一次，"
              "两个业务模块都从底座取用，避免重复代码与循环依赖。")

    h2(doc, "4.9 零第三方依赖的工程设计")
    para(doc, "除 FastAPI 技术栈外，全部能力均用标准库实现：")
    add_table(
        doc,
        ["能力", "实现方式"],
        [
            ["HTTP 请求", "urllib.request，含 SSE 流式读取"],
            ["并发执行", "concurrent.futures.ThreadPoolExecutor"],
            ["持久化", "JSON 文件（用例 / 运行记录 / 配置 / 凭据分离存储）"],
            ["前端", "原生 HTML + CSS + JavaScript，无构建步骤"],
        ],
        widths=[4.0, 11.0],
    )
    para(doc, "这一选择的目的很明确：**确保在任何评审环境中都能直接运行**，不因依赖安装失败而无法演示。")

    doc.add_page_break()

    # ============================================================
    h1(doc, "五、项目相关的图片/照片")
    para(doc, "以下截图均来自本平台实际运行结果。", indent=True)

    h2(doc, "5.1 平台主界面与测试结果总览")
    image_placeholder(doc, "平台主界面",
                      "内容：左侧「添加目标」按钮与历史运行列表；主区域分数环（78.59）、"
                      "六项 KPI、维度得分条形图、分层判定三卡、用例结果表格")
    para(doc, "说明：顶部为与被测目标的连通状态；主区域以环形仪表盘呈现加权质量分，"
              "下方按权重排序列出七个维度得分，并单独展示 L1/L2/L3 三层判定通过情况。")

    h2(doc, "5.2 执行中的可视化进度看板")
    image_placeholder(doc, "执行中进度看板",
                      "内容：完成度环形百分比、整体进度条、七个维度的分层进度条、"
                      "L1/L2/L3 断言累计进度")
    para(doc, "说明：测试执行期间实时刷新，展示总完成度、按维度的分层进度，"
              "以及三层断言的累计通过情况。")

    h2(doc, "5.3 用例详情与决策链路时间线")
    image_placeholder(doc, "用例详情抽屉",
                      "内容：用例输入、回答正文、断言明细（失败项标红）、"
                      "决策链路时间线（plan → retrieval → tool → answer）")
    para(doc, "说明：这是本平台最能体现测试深度的界面——可以看到智能体「想的是什么、做了什么、"
              "实际回答了什么」三者是否一致，失败断言精确定位到具体检查项。")

    h2(doc, "5.4 回归对比面板")
    image_placeholder(doc, "与基线对比",
                      "内容：得分与通过率变化、回归用例数、修复用例数、"
                      "状态迁移表格（含关键断言）")
    para(doc, "说明：外部项目修复缺陷后复测，自动识别出「修复 3 条、回归 0 条」，"
              "证明修复有效且未引入新问题。")

    h2(doc, "5.5 变异体校准结果")
    image_placeholder(doc, "变异体检出结果",
                      "内容：6 个变异体的运行结果表格，含加权分、用例通过、检出与跨维度影响，"
                      "以及顶部汇总卡片（变异体数、总检出、跨维度影响、精确命中、变异体均分）")
    para(doc, "说明：用于标定判定器灵敏度，证明工具能精确区分不同类型的缺陷。")

    h2(doc, "5.6 改进方案生成结果")
    image_placeholder(doc, "改进方案生成结果",
                      "内容：基于 15 条失败用例归纳出的根因级方案。每张卡片含影响用例编号、"
                      "绑定真实断言的证据、改哪一层（code/prompt/config）与可复跑的验证方式")
    para(doc, "说明：该功能把测试报告推进为可执行的修复工单。上方两张卡片分别指向"
              "「工具参数未做实体抽取」与「实时数据意图被误路由到政策检索」两个根因，"
              "证据均来自本次运行的真实断言编号，验证方式写明重跑哪些用例、期望什么变化。")

    h2(doc, "5.7 命令行门禁与 CI 集成")
    image_placeholder(doc, "CLI 门禁输出",
                      "内容：python run.py 的执行输出（分维度得分、回归对比、门禁结论、退出码 1）")
    para(doc, "说明：展示工具可直接接入 CI 流水线的形态。")

    doc.add_page_break()

    # ============================================================
    h1(doc, "六、项目之前是否发表过或获奖")

    para(doc, "**获奖情况**：无。")
    para(doc, "**公开发表情况**：未发表论文。研发过程中使用本平台对开源项目实测时发现一个真实缺陷，"
              "已公开提交报告并被维护者确认、修复，最终合并进该项目主干分支，提交信息中保留了报告者署名。")
    bullet(doc, "缺陷报告：https://github.com/AleksNeStu/ai-real-estate-assistant/issues/377")
    bullet(doc, "合并记录：https://github.com/AleksNeStu/ai-real-estate-assistant/pull/383")
    bullet(doc, "修复提交：https://github.com/AleksNeStu/ai-real-estate-assistant/commit/583db2d7")

    doc.add_page_break()

    # ============================================================
    h1(doc, "七、若项目在其他项目基础上优化，请说明优化部分")

    para(doc, "**本项目为独立原创开发，未基于任何现有测试平台、评测框架或开源项目进行二次开发。**"
              "平台全部代码（接入层、判定引擎、执行引擎、统计与对比、前端界面）均为自行设计与实现。")
    para(doc, "验证环节使用的外部开源项目仅作为**被测对象**：接入方式为 12 行 JSON 配置，"
              "未修改、未复用、未分发其任何代码与数据。自建的校园智能助手夹具同样是测试对象，"
              "用于提供可控的故障注入环境，不属于作品主体。"
              "二者互补：自建夹具提供可归因的标定环境，外部项目证明工具在不可控的真实项目上同样有效。")

    # ---------------- 保存 ----------------
    doc.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print("saved:", path)



