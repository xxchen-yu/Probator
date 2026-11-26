# -*- coding: utf-8 -*-
"""把 screenshots 目录里的截图按固定顺序插入项目介绍文档的占位框。

文件名必须按下表命名（缺哪个就留哪个占位，不影响其他图）：
    01-房产项目首页.png
    02-房产项目对话页.png
    03-LangGraph agent列表.png
    04-平台主界面.png
    05-进度看板.png
    06-用例详情.png
    07-回归对比.png
    08-变异体校准.png
    09-缺陷修复对比.png
    10-CLI门禁.png

用法：python insert_shots.py [截图目录]
"""
import os
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

CN = "Microsoft YaHei"
GREY = RGBColor(0x66, 0x66, 0x66)
MARK = "此处插入截图"
IMG_WIDTH_CM = 15.5

SLOTS = [
    ("01-房产项目首页.png", "图 3-1  开源房产助手前端首页"),
    ("02-房产项目对话页.png", "图 3-2  房源库为空时的实际返回（责任转嫁式拒答）"),
    ("03-LangGraph agent列表.png", "图 3-3  LangGraph 服务可用的 agent 清单"),
    ("04-平台主界面.png", "图 5-1  平台主界面与测试结果总览"),
    ("05-进度看板.png", "图 5-2  执行中的可视化进度看板：四节点流转、完成度环、维度与分层实时进度"),
    ("06-用例详情.png", "图 5-3  用例详情：失败断言给出期望值与实际值（工具参数 location 实际为 null）"),
    ("07-回归对比.png", "图 5-4  与基线对比面板（含采样次数不一致的不可比警告）"),
    ("08-变异体校准.png", "图 5-5  变异体校准结果"),
    ("09-改进方案.png", "图 5-6  改进方案生成：每条方案绑定真实用例与断言，并给出改哪层与验证方式"),
    ("10-CLI门禁.png", "图 5-7  命令行门禁输出：失败用例、回归清单、门禁未通过理由与退出码 1"),
]


def find_placeholders(doc):
    out = []
    for table in doc.tables:
        if len(table.rows) == 1 and len(table.columns) == 1:
            if MARK in table.rows[0].cells[0].text:
                out.append(table)
    return out


def make_caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run(text)
    run.font.name = CN
    run._element.rPr.rFonts.set(qn("w:eastAsia"), CN)
    run.font.size = Pt(9)
    run.font.color.rgb = GREY
    return p


def insert(docx_path, shot_dir, out_path=None):
    doc = Document(docx_path)
    placeholders = find_placeholders(doc)

    inserted, kept = [], []
    for index, table in enumerate(placeholders):
        name, caption = SLOTS[index] if index < len(SLOTS) else ("", "")
        path = os.path.join(shot_dir, name) if name else ""
        if not path or not os.path.exists(path):
            kept.append(name or "(第%d个)" % (index + 1))
            continue

        pic = doc.add_paragraph()
        pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic.paragraph_format.space_before = Pt(6)
        pic.paragraph_format.space_after = Pt(2)
        pic.add_run().add_picture(path, width=Cm(IMG_WIDTH_CM))

        cap = make_caption(doc, caption)

        anchor = table._element
        anchor.addprevious(pic._p)
        anchor.addprevious(cap._p)
        anchor.getparent().remove(anchor)
        inserted.append(name)

    doc.save(out_path or docx_path)
    return inserted, kept


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    shots = sys.argv[1] if len(sys.argv) > 1 else os.path.join(base, "screenshots")
    target = os.path.join(base, "项目介绍.docx")
    inserted, kept = insert(target, shots)
    print("已插入 %d 张:" % len(inserted))
    for item in inserted:
        print("  +", item)
    if kept:
        print("仍缺 %d 张:" % len(kept))
        for item in kept:
            print("  -", item)
