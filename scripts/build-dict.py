#!/usr/bin/env python3
"""
从 ECDICT SQLite 构建浏览器用的分桶 JSON 词典。

数据源: https://github.com/skywind3000/ECDICT
  releases/download/1.0.28/ecdict-sqlite-28.zip → 解压出 ecdict.sqlite

用法: python3 build-dict.py /tmp/ecdict/ecdict.sqlite [输出目录 dict/]

产物: 每个桶一个 JSON 文件（按小写词头前两个字符命名，如 dict/ap.json），
      结构 { "apple": {"w":..,"p":..,"t":..,"d":..,"c":..,"o":..}, ... }
      浏览器端按需 fetch 对应桶，本地离线查词、无需任何外部 API。
"""
import json, math, os, re, sqlite3, sys, unicodedata

SRC = sys.argv[1] if len(sys.argv) > 1 else '/tmp/ecdict/ecdict.sqlite'
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dict')

WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’\- ]{0,39}$")
KEEP_TAGS = {'zk', 'gk', 'cet4', 'cet6', 'ky', 'toefl', 'ielts', 'middle', 'high'}
FRQ_CAP = 60000          # 词频排名（BNC/COCA 合并频次）前 6 万保留
POS_RE = re.compile(r'^(?:[a-z]+\.)(?=\s|[^a-z])')

def clean_lines(s, max_lines, max_chars):
    if not s:
        return ''
    s = unicodedata.normalize('NFC', s).replace('\r\n', '\n').replace('\r', '\n').replace('\\n', '\n')
    lines = []
    for ln in s.split('\n'):
        ln = ln.strip()
        if ln.startswith('*'):
            ln = ln[1:].strip()
        if not ln:
            continue
        if len(ln) > max_chars:
            cut = ln[:max_chars]
            if ';' in cut[40:]:
                cut = cut[:cut.rfind(';')]
            ln = cut.rstrip(';，, ') + '…'
        lines.append(ln)
        if len(lines) >= max_lines:
            break
    return '\n'.join(lines)

def best(existing, cand):
    return cand if cand[0] > existing[0] else existing

def main():
    os.makedirs(OUT, exist_ok=True)
    db = sqlite3.connect(SRC)
    cur = db.execute('''
        SELECT word, phonetic, definition, translation, collins, oxford, tag, bnc, frq, exchange
        FROM stardict
        WHERE (translation IS NOT NULL AND translation != '')
           OR (definition  IS NOT NULL AND definition  != '')
    ''')
    words = {}   # lower -> (score, entry)
    infl = {}    # 变形 -> 原形（去重用 dict）
    n_seen = 0
    for word, phon, defi, trans, collins, oxford, tag, bnc, frq, exchange in cur:
        n_seen += 1
        if not WORD_RE.match(word):
            continue
        trans = (trans or '').strip()
        defi = (defi or '').strip()
        if not trans and not defi:
            continue
        collins = collins or 0
        oxford = 1 if oxford else 0
        frq = frq or 0
        bnc = bnc or 0
        tags = set((tag or '').lower().split())
        keep = (oxford or collins >= 1 or frq and frq <= FRQ_CAP
                or bnc and bnc <= FRQ_CAP or tags & KEEP_TAGS)
        if not keep:
            continue
        lw = word.lower()
        score = ((4 if trans else 0) + (2 if phon else 0) + (1 if defi else 0)
                 + collins + 3 * oxford + (1 if tags & KEEP_TAGS else 0))
        entry = {
            'w': word,
            'p': phon or '',
            't': clean_lines(trans, 3, 90),
            'd': clean_lines(defi, 2, 120),
            'c': collins,
            'o': oxford,
        }
        if lw not in words or score > words[lw][0]:
            words[lw] = (score, entry)
        # 变形 -> 原形（p过去式 d过去分词 i现在分词 3三单 r比较级 t最高级 s复数 0原形指针）
        if exchange:
            for tok in exchange.split('/'):
                m = re.match(r"^(?:[pdi3rst0]):([A-Za-z][A-Za-z'’\- ]{0,39})$", tok)
                if m:
                    f = m.group(1).lower()
                    if f != lw and f not in words:
                        infl[f] = lw
    db.close()

    buckets = {}
    for lw, (_, e) in words.items():
        key = (lw[:2] + '__')[:2]
        buckets.setdefault(key, {})[lw] = e
    # 变形以 {"r": 原形} 存进各自桶；已有独立词条的变形不覆盖
    for f, lemma in infl.items():
        if f not in words:
            key = (f[:2] + '__')[:2]
            buckets.setdefault(key, {})[f] = {'r': lemma}

    total = 0
    for key, obj in buckets.items():
        path = os.path.join(OUT, key + '.json')
        data = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        with open(path, 'wb') as f:
            f.write(data)
        total += len(data)

    sizes = sorted(len(json.dumps(o, ensure_ascii=False, separators=(',', ':'))) for o in buckets.values())
    print(f'seen={n_seen}  kept={len(words)}  buckets={len(buckets)}')
    print(f'total={total/1e6:.1f}MB  bucket min/med/max={sizes[0]//1024}/{sizes[len(sizes)//2]//1024}/{sizes[-1]//1024} KB')
    for probe in ('apple', 'went', 'panda', 'photosynthesis', 'launch', 'calf', 'mice'):
        if probe in words:
            e = words[probe][1]
            print(f'  {probe:16s} ->', (e['t'] or e['d']).split('\n')[0][:60])
        elif probe in infl:
            print(f'  {probe:16s} -> 原形 {infl[probe]}')
        else:
            print(f'  {probe:16s} -> MISS')

if __name__ == '__main__':
    main()
