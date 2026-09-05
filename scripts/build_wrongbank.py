# -*- coding: utf-8 -*-
"""重建 wrongbank.js：默认内容来自测验平台错题导出 JSON；
讲次 28 的解析覆盖为「四步推理链」（english-grammar-reasoning 输出）。
用法：uv run python scripts/build_wrongbank.py
"""
import json
import re

SRC = '/Users/maxiangyu/.zcode/workspace/default/quiz_wrong_questions_3350581.json'
OUT = '/Users/maxiangyu/Code/grammar-kb/web/src/data/wrongbank.js'

# 第28讲推理链（结构/锚点/约束/答案；选择题加排除法）
L28_OVERRIDES = {
    2: (
        '① 结构：两句独立，第二句是证据句。\n'
        '② 锚点：You can work on it now＝现在的结果←过去的动作 → 公式⑥ 现在完成时（影响）。\n'
        '③ 约束：主语 I → have；fix 规则动词 → fixed。\n'
        '④ 答案：have fixed（修完所以现在能用）。'
    ),
    4: (
        '① 结构：since 引导时间状语从句 + 主句。\n'
        '② 锚点：since + 过去动作 began → 公式① have/has done。\n'
        '③ 约束：主语 our lives 复数 → have。\n'
        '④ 答案：have changed。'
    ),
    5: (
        '① 结构：主句 + when 时间状语从句。\n'
        '② 锚点：when + 瞬间动作 got home 打断长动作，yesterday 定在过去 → 公式⑤ was doing ... when ... did。\n'
        '③ 约束：Mum 三单 → was；sit 双写 t → sitting。\n'
        '④ 答案：was sitting。'
    ),
    7: (
        '① 结构：It is the first time (that) + 定语从句（that 省略）。\n'
        '② 锚点：固定句型——主句现在时 → 从句用现在完成时（主句过去时才回退 had done）。\n'
        '③ 约束：he 三单 → has；write–wrote–written。\n'
        '④ 答案：has written。'
    ),
    8: (
        '① 结构：单句。\n'
        '② 锚点：in recent years＝从过去延续到现在 → 公式① have/has done。\n'
        '③ 约束：online shopping 单数 → has；become–became–become。\n'
        '④ 答案：has become。'
    ),
    11: (
        '① 结构：同义句转换。\n'
        '② 锚点：not ... ever ＝ never（一词）。\n'
        '③ 约束：空在 have 与 spoken 之间，填否定副词。\n'
        '④ 答案：never。'
    ),
    12: (
        '① 结构：完成时肯定句 → 否定句。\n'
        '② 锚点：完成时否定式 have + not + done。\n'
        '③ 约束：already 只住肯定句，否定句换 yet（放句末）。\n'
        '④ 答案：haven\'t; yet。'
    ),
    13: (
        '① 结构：ago 一次性动作 → for + 时间段延续状态（同义句）。\n'
        '② 锚点：for two days 持续至今 → 公式①\' 完成时 + 可延续状态。\n'
        '③ 约束：borrow 是瞬间动词，× for 时间段 → 换延续动词 keep；Tim 三单 → has。\n'
        '④ 答案：has; kept。'
    ),
    14: (
        '① 结构：同上（四个空的同义句）。\n'
        '② 锚点：for a week → 公式①\' 完成时 + 延续状态。\n'
        '③ 约束：leave 是瞬间动词 → 换 be away (from)；Tom 三单 → has。\n'
        '④ 答案：has; been; away; from。'
    ),
    15: (
        '① 结构：同上。\n'
        '② 锚点：for 10 years → 公式①\' 完成时 + 延续状态。\n'
        '③ 约束：die 是瞬间动词 → 换 be dead（状态）；grandpa 三单 → has。\n'
        '④ 答案：has; been; dead。'
    ),
    17: (
        '① 结构：There be 存在句 + but 今昔对比。\n'
        '② 锚点：but now → 过去曾经是 → used to do。\n'
        '③ 约束：There 后只能跟 be（存在句不配 have）；used to 无被动式。\n'
        '⑤ 排除：A 死于 there be 不配 have；C/D 死于 was used to 被动误用。\n'
        '答案：B. used to be。'
    ),
    21: (
        '① 结构：but 前后两段今昔对比，各一空。\n'
        '② 锚点：第一空“过去常常（现在不了）”→ used to do；第二空 now + “习惯于”→ be used to doing。\n'
        '③ 约束：be used to 的 to 是介词 → 接 doing；used to 的 to 接动词原形。\n'
        '⑤ 排除：A 第二空 walk 死于介词 to；B/D 第一空 was used to 死于被动义；D 第二空 used to walk 死于 now。\n'
        '答案：C. used to; is used to walking。'
    ),
    22: (
        '① 结构：同一个 if 两种身份——第一个 if＝“如果”→ 条件状语从句；第二个 if＝“是否”→ knows 的宾语从句。\n'
        '② 锚点：条件从句表将来也用一般现在时（主将从现）→ is；宾语从句正常表将来 → will rain。\n'
        '③ 约束：条件从句禁 will；宾语从句不受此限。\n'
        '⑤ 排除：B/D 第一空 will be 死于主将从现；A 第二空 rains 死于宾从表将来须 will。\n'
        '答案：C. is; will rain。'
    ),
    26: (
        '① 结构：tell me + when 引导的宾语从句。\n'
        '② 锚点：宾语从句用陈述语序（主语在前）；离开时间在将来 → will。\n'
        '③ 约束：Could you tell me 是委婉请求（非过去时），不把从句拉回 would。\n'
        '⑤ 排除：B/D 死于疑问语序；C 死于 would（时间视角没有回移）。\n'
        '答案：A. when you will leave for Beijing。'
    ),
    27: (
        '① 结构：两个独立句，各说一件事。\n'
        '② 锚点：第一句 for 12 hours a day 表每天的持续状态 → be open（形容词）；第二句 at 10:00 p.m. 是每天规律动作 → 三单 closes。\n'
        '③ 约束：open 作形容词＝营业中；close 作动词＝关闭动作（is close 义为“亲近”不通）；bookstore 三单。\n'
        '⑤ 排除：A 第一空 opens 是动作义不接时长；B 第二空 is closed ≠ 规律动作；C 第二空 is close 义错。\n'
        '答案：D. is open; closes。'
    ),
    33: (
        '① 结构：单句，$30,000 作宾语。\n'
        '② 锚点：several months ago 过去时间点 → 公式⑦ 一般过去时。\n'
        '③ 约束：接宾语必须及物：raise（筹款）及物 ✓；rise（上升）不及物，带宾语非法。\n'
        '⑤ 排除：B/D 死于完成时（ago 是过去点）；C 死于 rose 不及物；D 双死。\n'
        '答案：A. raised。'
    ),
}


def esc(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def main() -> None:
    data = json.load(open(SRC, encoding='utf-8'))
    bank = {}
    for d in data:
        lec = int(re.search(r'(\d{1,2})\.', d['测验']).group(1))
        for q in d['错题']:
            exp = (q.get('解析') or '').strip()
            if exp == '略':
                exp = ''
            bank.setdefault(lec, {})[q['题号']] = {'q': q.get('题目') or '', 'exp': exp}

    for num, chain in L28_OVERRIDES.items():
        bank.setdefault(28, {})[num] = {'q': bank[28][num]['q'], 'exp': chain}

    out = [
        '// 哈一错题题库：题目内容 + 解析，按 讲次 → 题号 索引。',
        '// 来源：测验平台错题导出 JSON；讲次 28 的解析为四步推理链（结构/锚点/约束/答案）。',
        '// 数据重新导出后需再生成一次（scripts/build_wrongbank.py）。',
        'export const WRONG_BANK = {',
    ]
    for lec in sorted(bank):
        out.append(f'  {lec}: {{')
        for num in sorted(bank[lec]):
            it = bank[lec][num]
            out.append(f'    {num}: {{ q: {esc(it["q"])}, exp: {esc(it["exp"])} }},')
        out.append('  },')
    out.append('}')
    open(OUT, 'w', encoding='utf-8').write('\n'.join(out))
    n = sum(len(v) for v in bank.values())
    print(f'重建 {OUT}：{len(bank)} 讲 / {n} 题（L28 覆盖 {len(L28_OVERRIDES)} 条推理链）')


if __name__ == '__main__':
    main()
