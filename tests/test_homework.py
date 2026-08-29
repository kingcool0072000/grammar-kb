"""作业卷解析（homework.parse_paper_text 纯函数）单测。"""

from grammar_kb.homework import (
    HwQuestion,
    parse_homework_filename,
    parse_paper_text,
)

# 第2讲风格：连续式编号（I 大题 9-20，II 大题 21-35）+ 表格空格 1-8
TEXT_CONTINUOUS = """睿爸小屋
哈一（初中语法）
作业卷02 1 | 3
第二讲作业卷
I. Fill in the blanks with the words in proper forms
9. These are _________brothers. (he)
10. ---Is this _________ watch? (she)
II. Choose the best answer
21. This is my good friend. ________ name is Tony.
A. He
B. His
C. She
D. Her
22. Let's just relax and enjoy _________. (we)
睿爸小屋    哈一（初中语法）
"""

# 第4讲风格：重启式编号（I 大题 1-10 = 平台 1-10，II 大题重排 1-10 = 平台 11-20）
TEXT_RESTART = """第四讲作业卷
I. 中译英
1. ____________ 太平洋
2. ____________ 颐和园
II. 从括号内选择正确的选项填入空格
1.
A Friend of __________ has gone to America. ( her, hers )
2.
I take my dog everywhere with __________. ( me, myself )
III. 选择最佳答案
21. I live near the station. It's only about ten ________ walk.
A. minute's
B. minute
"""


def test_filename():
    assert parse_homework_filename("04.综合复习一_作业卷.pdf") == (4, "综合复习一")
    assert parse_homework_filename("22动词时态1_作业卷.pdf") == (22, "动词时态1")
    assert parse_homework_filename("10.介词1_作业卷 .pdf") == (10, "介词1")
    # 非作业卷文件
    assert parse_homework_filename("12.连词1_讲义.pdf") == (None, "")


def test_continuous_numbering():
    qs = parse_paper_text(TEXT_CONTINUOUS)
    assert [q.qnum for q in qs] == [9, 10, 21, 22]
    q21 = next(q for q in qs if q.qnum == 21)
    assert "name is Tony" in q21.stem
    assert q21.options == ["He", "His", "She", "Her"]


def test_restart_numbering_offset():
    """重启式大题编号：平台题号 = 卷面累计题数。
    （真实卷：I 大题 10 题 → II 大题 1/2 映射为 11/12；
    此处 I 大题只有 2 题，故 II-1/II-2 → 3/4。）"""
    qs = parse_paper_text(TEXT_RESTART)
    nums = [q.qnum for q in qs]
    assert nums == [1, 2, 3, 4, 21]
    q3 = next(q for q in qs if q.qnum == 3)
    assert "A Friend of" in q3.stem
    q4 = next(q for q in qs if q.qnum == 4)
    assert "take my dog" in q4.stem


def test_noise_and_options_merged():
    """页眉页脚被剔除；不完整的选项序列（A 后直接 C）不吞题干。"""
    text = """
作业卷07
I. 选择
1. The Browns ________ watching TV.
A. was
C. were
"""
    qs = parse_paper_text(text)
    assert len(qs) == 1
    # C 不是预期字母 B → 并回题干（保信息不丢失）
    assert "The Browns" in qs[0].stem


def test_section_requires_dot():
    """行首罗马数字无点号（如 "I live near..." 题干）不当大题标题。"""
    text = "1. I live near the station.\n2. It is fine."
    qs = parse_paper_text(text)
    assert len(qs) == 2
    assert all(not q.section for q in qs)


def test_qnum_object_direct():
    q = HwQuestion(qnum=1, stem="x")
    assert q.options == [] and not q.is_cell
