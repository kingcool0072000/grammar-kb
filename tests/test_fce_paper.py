"""fce_paper 解析器纯函数单测（无需真实 OCR 数据）。"""
from grammar_kb.fce_paper import (
    OcrLine,
    merge_rows,
    parse_key_page,
    parse_gap_fill_numbered,
    parse_ocr_file,
    _LOOKALIKE,
)


def _lines(*specs):
    out = []
    for s in specs:
        p, y, x, t = s.split("|", 3)
        out.append(OcrLine(page=int(p), y=int(y), x=int(x), text=t))
    return out


class TestOcrCleaning:
    def test_watermark_and_footer_dropped(self):
        text = "9950\t100\t水印jq-kpf\n9610\t500\t23\n2000\t500\t18\n"
        lines = parse_ocr_file(3, text)
        # 页脚水印、页脚区页码丢弃；正文裸数字（题号标记）保留
        assert [l.text for l in lines] == ["18"]

    def test_running_header_kept_for_structure(self):
        text = "500\t500\tTest 1\n1600\t500\tREADING AND USE OF ENGLISH (1 hour 15 minutes)\n"
        lines = parse_ocr_file(7, text)
        assert len(lines) == 2  # 书眉保留（merge_rows 时才过滤）

    def test_cyrillic_normalized(self):
        assert "А".translate(_LOOKALIKE) == "A"


class TestMergeRows:
    def test_page_grouped(self):
        # 跨页同 y 的行不得合并/穿插
        lines = _lines(
            "1|1000|100|first page line", "2|1000|100|second page line",
        )
        rows = merge_rows(lines)
        assert len(rows) == 2

    def test_headers_dropped(self):
        lines = _lines("1|500|500|Test 1", "1|1000|500|body")
        rows = merge_rows(lines)
        assert len(rows) == 1 and rows[0][1][0].text == "body"


class TestKeyPage:
    def test_letter_token_walk_with_ocr_noise(self):
        lines = _lines(
            "120|100|100|Test 1 Key",
            "120|200|100|Reading and Use of English (1 hour 15 minutes)",
            "120|300|100|Part 1",
            "120|400|100|1 D 2 A 3 C 4 B 5 D 6 B 7 C 8 B",
            "120|600|100|Part 6",
            "120|700|100|37 D 38 A 39 E 40 G 41 F 42 C",
            "120|800|100|Part 7",
            "120|900|100|43 D 44 C 45 A 46 C",
        )
        out = parse_key_page(lines)["Reading and Use of English"]
        assert out[1] == "D" and out[8] == "B"
        assert out[37] == "D" and out[40] == "G" and out[42] == "C"
        assert out[43] == "D" and out[46] == "C"

    def test_word_answers_with_glued_noise(self):
        lines = _lines(
            "120|100|100|Test 1 Key",
            "120|200|100|Reading and Use of English (1 hour 15 minutes)",
            "120|300|100|Part 2",
            "120|400|100|9 had / held 11 other 12 and 13 what",
            "120|500|100|14 That / This 101 ome /5 / Thoush 16 be/ come",
        )
        out = parse_key_page(lines)["Reading and Use of English"]
        assert out[9] == "had / held"
        assert out[14].startswith("That / This")
        assert 101 not in out  # OCR 粘连噪声被范围过滤
        assert out[16] == "be/ come"

    def test_listening_cyrillic_and_glued(self):
        # 西里尔 А 在 parse_ocr_file 阶段归一化为 A（这里直接给归一化后输入）；
        # "28 (" 的字母 "(" 丢失 → 游走按序赋值：28 拿到下一个字母 A（错位一位但
        # 不缺号），后续 29 也拿到 A；30 无字母可用缺答（生产中由 300dpi 页避免）
        lines = _lines(
            "121|100|9000|Test 1 Key",
            "121|200|100|Listening (approximately 40 minutes)",
            "121|300|100|Part 4",
            "121|400|100|24 C 25 A 26 B 27 B 28 ( , 29 A 30 A",
        )
        out = parse_key_page(lines)["Listening"]
        assert out[24] == "C" and out[27] == "B"
        assert out[28] == "A"  # "(" 丢失后错位补位
        assert out[29] == "A"


class TestGapFillNumbered:
    def test_inline_and_marker_rows(self):
        lines = _lines(
            "67|2456|900|The thing that first got Joe interested in gorillas was a",
            "67|2456|9000|9",
            "67|6206|5530|14",
            "67|6279|5987|sometimes form part of the gorilla's diet.",
            "67|6716|747|The name",
            "67|6716|5135|15 is used to refer to the young males in a group.",
        )
        out = parse_gap_fill_numbered(lines, (9, 18))
        assert out[9].startswith("The thing that first")
        assert out[14].startswith("sometimes form")
        assert out[15].startswith("The name")
        assert "15" not in out[15]  # 数字已从 stem 中剔除

    def test_marker_distance_limit(self):
        # marker 距最近句子 1002px > 阈值 700 → 不配对（避免误配指令区文本）
        lines = _lines(
            "23|5204|900|Joe uses the word",
            "23|6206|5530|14",
        )
        out = parse_gap_fill_numbered(lines, (9, 18))
        assert 14 not in out
