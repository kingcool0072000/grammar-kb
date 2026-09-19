// 专题学习：自学手册页。内容静态打包（由讲义 HTML 精简迁移），
// 「我学完了」按钮把该主题 key 并入 done_keys 写后端
// （PUT /topics/{id}/progress——谁登录记谁的行，跨设备同步）。
// 专题对学生的可见性在前端清单里声明 assignedTo：'malin' 表示仅 malin 可见。
import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'

// ---- 专题清单（当前仅一册：26~28 讲时态专项） ----
// assignedTo 为空＝所有学生可见；'malin'＝仅 malin 的专属专题
const TOPICS = [
  {
    id: 'tense-26-28',
    title: '⏰ 时态错题歼灭手册',
    subtitle: '第 26~28 讲 · 49 道错题按「坑」归类，同样的坑只练一次',
    emoji: '⏰',
    assignedTo: 'malin',
    themes: [
      {
        key: 't1', no: '主题一', title: '现在完成时——「过去和现在的桥梁」',
        score: '15 错 · 头号敌人', color: '#3a7ca5', soft: '#e9f1f7',
        lead: '现在完成时 have/has + done 不是「过去的事」，而是站在现在，回头看过去：要么影响到现在，要么持续到现在，要么算到现在的总账。',
        cards: [
          {
            h: '公式卡 · 锚点速查表（背这个）',
            body: `
<table><tr><th>句中看到这些信号</th><th>用</th><th>例句锚点</th></tr>
<tr><td>since + 过去时间点 / since 从句</td><td class="en">have/has done</td><td class="en">Since we <b>began</b>..., our lives <b>have changed</b></td></tr>
<tr><td>for + 时间段（至今还在算）</td><td class="en">have/has done</td><td class="en">I <b>have been</b> here since five months ago</td></tr>
<tr><td>twice / three times（经历次数）</td><td class="en">have/has done</td><td class="en">I <b>have seen</b> it twice</td></tr>
<tr><td>ever / never / yet / already / so far</td><td class="en">have/has done</td><td class="en"><b>Have</b> you <b>ever</b> visited...?</td></tr>
<tr><td>in recent years / these days</td><td class="en">have/has done</td><td class="en">Online shopping <b>has become</b> popular</td></tr>
<tr><td>后半句是<b>现在的结果</b>：so... / now... / Can you...?</td><td class="en">have/has done</td><td class="en">I <b>have lost</b> my pen. <b>Can</b> you lend me yours?</td></tr>
<tr><td class="en">It is the first / second time (that)...</td><td class="en">从句 have/has done</td><td class="en">It is the first time he <b>has written</b> a book</td></tr>
<tr><td>since 从句<b>里面</b>的动词</td><td class="en">一般过去时</td><td class="en">It is two years since I <b>became</b> a doctor</td></tr>
</table>
<div class="topic-callout warn"><b class="tag">⚠️ 最后一行最容易反</b>since 前半句用完成时，since 从句里的起点动作用过去时——「自从我成为医生（那一刻）已经两年了」，"成为"是过去的一瞬间。</div>`,
          },
          {
            h: '高危辨析 · 完成时 vs 一般过去',
            body: `
<div class="topic-callout"><b class="tag">一句话判断</b>问「做没做 / 影响到现在」（<b>现在算账</b>）→ 完成时；问「过去某时做了什么、做了几件」（<b>过去档案</b>）→ 一般过去。</div>
<p class="en">—How many books ______ they ______? —Five. But they haven't finished reading even one.</p>
<p>问「（当时）借了几本」——借书是<b>已经翻篇的过去动作</b>，Five 只是在报当时的数量 → <b class="en">did, borrow</b>。下句 haven't finished 是完成时？没错，但那是<b>另一句话</b>（读没读完影响现在），别让它把问句带偏。</p>
<p class="en">—______ you ______ your book to the library? —Yes. I returned it <b>yesterday</b>.</p>
<p>问「还了没」——影响<b>现在</b>（现在不欠书了）→ <b class="en">Have, returned</b>；答句补充「昨天还的」，有 yesterday 这个过去点 → returned。</p>
<div class="topic-callout good">看到 <b class="en">yesterday / in 2015 / two days ago</b> → 过去时；看到 <b class="en">yet / ever / since / twice</b> 或<b>现在的结果</b> → 完成时。</div>`,
          },
        ],
        replay: [
          ['26·T1', 'I ________ here since five months ago. <span class="zh">(be)</span>', 'have been', 'since 锚点 → 完成时；here 是副词，be 动词直接用'],
          ['26·T5', 'Her niece ________ here for a long time. <span class="zh">(sit)</span>', 'has sat', 'for + 时间段锚点；niece 三单 → has'],
          ['26·T6', 'It is two years since I ________ a doctor. <span class="zh">(become)</span>', 'became', 'since 从句里的起点动作用过去时——成为医生是过去的一瞬间'],
          ['26·T12', "Andy doesn't want to see the film Coco because he ________ it twice. <span class=\"zh\">(see)</span>", 'has seen', 'twice 经历 + 影响现在（所以不想再看）'],
          ['26·T14', 'What a mess! Who ________ rubbish everywhere! <span class="zh">(leave)</span>', 'has left', '现在的乱＝过去动作的结果'],
          ['26·T32', 'He ________ his bike so he has to walk there. <span class="zh">(lose)</span>', 'has lost', 'so 后面是现在的结果（现在得走路）'],
          ['26·T33', '—How many books ______ they ______? —Five. But they haven\'t finished reading even one.<br>A. did, borrow　B. had, borrowed　C. will, borrow　D. do, borrow', 'A. did, borrow', '问当时借了几本＝过去档案 → 过去时（辨析题，见上方高危辨析）'],
          ['26·T35', '—________you________ your book to the library? — Yes. I returned it yesterday.<br>A. Did, return　B. Have, returned　C. Will, return　D. Do, return', 'B. Have, returned', '问「还了没」影响现在 → 完成时；答句 yesterday 部分才用过去时'],
          ['27·T5', 'Harry Potter is a very nice film. I ________ it twice. <span class="zh">(see)</span>', 'have seen', 'twice 经历锚点'],
          ['27·T11', 'I ________ my pen. Can you lend me yours? <span class="zh">(lose)</span>', 'have lost', '下一句是现在的请求 → 丢了笔影响现在'],
          ['27·T31', '—_______ you ever _______ Hong Kong-Zhuhai-Macao Bridge？ — Not yet.<br>A. Did, visit　B. Do, visit　C. Have, visited　D. Had, visited', 'C. Have, visited', 'ever + yet 双锚点 → 完成时'],
          ['28·T2', 'I ____________ your computer. You can work on it now. <span class="zh">(fix)</span>', 'have fixed', '第二句是证据句：现在的结果 ← 过去的动作'],
          ['28·T4', 'Since we began to use the Internet, our lives ____________ a lot. <span class="zh">(change)</span>', 'have changed', 'since 从句过去时 → 主句完成时；lives 复数 → have'],
          ['28·T7', 'It is the first time he _____________ a book about his childhood memories. <span class="zh">(write)</span>', 'has written', '固定句型：主句 is → 从句完成时（主句 was 才回退 had done）'],
          ['28·T8', 'In recent years, online shopping _____________ increasingly popular. <span class="zh">(become)</span>', 'has become', 'in recent years 锚点；shopping 单数 → has'],
        ],
        drills: [
          ['练 1-1', 'Mr. Wang ________ in this school since 2018. <span class="zh">(teach)</span>', 'has taught', 'since 2018 锚点；三单 → has'],
          ['练 1-2', '—________ you ever ________ Sichuan food? —Yes, twice.', 'Have; tried', 'ever + twice 双锚点'],
          ['练 1-3', 'Look! Somebody ________ the window. The glass is everywhere. <span class="zh">(break)</span>', 'has broken', '现在的结果：玻璃满地'],
          ['练 1-4', 'It is the third time she ________ the first prize. <span class="zh">(win)</span>', 'has won', 'It is the ... time 句型 → 从句完成时'],
          ['练 1-5', '—How many books ________ you ________ during the summer holiday? —Three.', 'did; read', 'during the summer holiday 是过去的档期 → 过去档案用过去时（同 26·T33）'],
        ],
      },
      {
        key: 't2', no: '主题二', title: '瞬间动词不能牵手 for——延续性转换',
        score: '6 错 · 三连错元凶', color: '#3f8f6b', soft: '#e9f4ee',
        lead: '完成时 + for/since 时间段 ⇒ 谓语必须能一直持续。borrow、leave、die、buy 这类「一秒就完成」的动词撑不起一段时间，必须换成对应的状态表达。',
        cards: [
          {
            h: '公式卡 · 瞬间 → 延续 转换表（必背）',
            body: `
<table><tr><th>瞬间动词（× for 时间段）</th><th>延续表达（✓）</th><th>中文</th></tr>
<tr><td class="en">borrow 借</td><td class="en"><b>keep</b></td><td>借了 → 一直留着</td></tr>
<tr><td class="en">buy 买</td><td class="en"><b>have</b></td><td>买了 → 一直拥有</td></tr>
<tr><td class="en">leave 离开</td><td class="en"><b>be away (from)</b></td><td>离开了 → 一直不在</td></tr>
<tr><td class="en">die 死</td><td class="en"><b>be dead</b></td><td>去世了 → 一直是故去状态</td></tr>
<tr><td class="en">begin / start</td><td class="en"><b>be on</b></td><td>开演了 → 一直进行</td></tr>
<tr><td class="en">finish / end</td><td class="en"><b>be over</b></td><td>结束了 → 一直是结束状态</td></tr>
<tr><td class="en">join 加入</td><td class="en"><b>be in / be a member of</b></td><td>加入了 → 一直是成员</td></tr>
<tr><td class="en">make / become friends</td><td class="en"><b>be friends</b></td><td>交了朋友 → 一直是朋友</td></tr>
<tr><td class="en">open</td><td class="en"><b>be open</b></td><td>开门了 → 一直营业</td></tr>
<tr><td class="en">close</td><td class="en"><b>be closed</b></td><td>关门了 → 一直关着</td></tr>
<tr><td class="en">fall asleep</td><td class="en"><b>be asleep</b></td><td>睡着了 → 一直睡着</td></tr>
<tr><td class="en">catch a cold</td><td class="en"><b>have a cold</b></td><td>感冒了 → 一直感冒</td></tr>
</table>
<p style="margin-bottom:4px;font-weight:600">同义句转换套路：</p>
<pre class="topic-formula">主语 + 瞬间动词 + ... + ago（过去一次性动作）
⟹ 主语 + have/has + 延续表达 + ... + for + 时间段</pre>`,
          },
        ],
        replay: [
          ['28·T12/13', 'Tim borrowed the dictionary two days ago.（同义句）Tim ________ ________ the dictionary for two days.', 'has; kept', 'borrow 是瞬间动词 → 换 keep；Tim 三单 → has'],
          ['28·T14', 'Tom left Paris a week ago.（同义句）Tom ________ ________ ________ ________ Paris for a week.', 'has; been; away; from', 'leave → be away from，注意 from 不能丢'],
          ['28·T15', 'His grandpa died 10 years ago.（同义句）His grandpa ________ ________ ________ for 10 years.', 'has; been; dead', 'die → be dead（状态）'],
          ['26·T30', "The teachers ________ the office for a few minutes when we arrived. We didn't meet them.<br>A. had been away from　B. had left　C. have been away from　D. have left", 'A. had been away from', 'when we arrived 是过去参照点 → 过去完成；过去完成也要延续！B 死在 left 瞬间动词 × for a few minutes'],
          ['26·T31', 'They ________ friends since they met in Shanghai.<br>A. have made　B. have become　C. have been　D. have turned', 'C. have been', 'A/B 死在 made/became 是瞬间动词 × since'],
        ],
        drills: [
          ['练 2-1', 'His father died five years ago.（同义句）His father ________ ________ ________ for five years.', 'has been dead', 'die → be dead'],
          ['练 2-2', 'Lily bought the bike two weeks ago.（同义句）Lily ________ ________ the bike for two weeks.', 'has had', 'buy → have'],
          ['练 2-3', 'They joined the club in 2023.（同义句）They ________ ________ ________ the club for about three years.', 'have been in', 'join → be in'],
          ['练 2-4', 'The film began ten minutes ago.（同义句）The film ________ ________ ________ for ten minutes.', 'has been on', 'begin → be on'],
          ['练 2-5', 'Mr. Li left Tianjin three days ago.（同义句）Mr. Li ________ ________ ________ ________ Tianjin for three days.', 'has been away from', 'leave → be away from'],
        ],
      },
      {
        key: 't3', no: '主题三', title: '过去完成时——「过去的过去」',
        score: '5 错', color: '#7c62c0', soft: '#f0ecf9',
        lead: 'had + 过去分词。句中已有一个「过去的时间参照物」，某动作发生在它更早之前。时间轴：[had done 更早] ────── [did 过去参照点] ────── 现在',
        cards: [
          {
            h: '锚点三兄弟（看到就选 had done）',
            body: `
<table><tr><th>锚点</th><th>例</th></tr>
<tr><td class="en"><b>By the time</b> + 过去时从句</td><td class="en">By the time I got up, Mum <b>had cooked</b> breakfast</td></tr>
<tr><td class="en"><b>by the end of</b> + 过去时间</td><td class="en">by the end of <b>last term</b> → <b>had studied</b></td></tr>
<tr><td class="en"><b>Before</b> + 过去动作</td><td class="en">Before she came to China, she <b>had heard</b> about the Great Wall</td></tr>
</table>
<div class="topic-callout">判断口诀：句中两个过去动作，<b>先发生的那个用 had done</b>。</div>
<div class="topic-callout warn"><b class="tag">近亲提醒 · 过去将来时 would do</b>主句是 said / told 等过去动词，内容指将来 → He said he <b class="en">would call</b> me as soon as he got home.</div>`,
          },
        ],
        replay: [
          ['26·T8', 'Jerry ________ 3 short novels by the end of last term. <span class="zh">(study)</span>', 'had studied', 'by the end of + 过去时间 → had done'],
          ['26·T10', 'By the time I got up, my mother ________ the breakfast well. <span class="zh">(cook)</span>', 'had cooked', '起床（过去）之前就做完了 → 过去的过去'],
          ['27·T9', 'By the time I was five, I ________ over a hundred books. <span class="zh">(read)</span>', 'had read', 'By the time + 过去 → had done'],
          ['27·T12', 'Before she came to China, she ________ about the Great Wall through movies. <span class="zh">(hear)</span>', 'had heard', 'hear 发生在 came（过去）之前 → 过去的过去'],
          ['26·T7', 'He said he ________ me as soon as he got home. <span class="zh">(call)</span>', 'would call', 'said（过去）+ 内容指将来 → 过去将来时'],
        ],
        drills: [
          ['练 3-1', 'By the time the police arrived, the thief ________ away. <span class="zh">(run)</span>', 'had run', 'By the time 锚点'],
          ['练 3-2', 'When I got to the station, the train ________. <span class="zh">(leave)</span>', 'had left', '到站之前车已走 → 过去的过去'],
          ['练 3-3', 'She ________ 2,000 English words by the end of last year. <span class="zh">(learn)</span>', 'had learned', 'by the end of + 过去'],
          ['练 3-4', 'He told me he ________ the film before. <span class="zh">(see)</span>', 'had seen', '看过在 told 之前 → had done'],
          ['练 3-5', 'Before we arrived at the cinema, the film ________. <span class="zh">(begin)</span>', 'had begun', 'Before 锚点'],
        ],
      },
      {
        key: 't4', no: '主题四', title: '过去进行时——「过去正在放的电影」',
        score: '5 错', color: '#d8842d', soft: '#fbf1e4',
        lead: 'was / were + doing。时间轴画法：was doing 是一条横线，when 后的 did 是线上扎下的一根针。',
        cards: [
          {
            h: '三大锚点',
            body: `
<pre class="topic-formula">锚点① when 模型：长动作（was doing）＋ when ＋ 瞬间动作（did）
        ——「正……着，突然……」：I was taking a walk when I met him.
锚点② 过去某段/某点正进行：from 4:00 to 6:00 yesterday / at eight last night
锚点③ 语境暗示「此刻」：Hold on! She is fixing...（现在 → is fixing）</pre>`,
          },
        ],
        replay: [
          ['26·T3', 'I met him when I ________ a walk in the park. <span class="zh">(take)</span>', 'was taking', 'met 是针，散步是线'],
          ['26·T29', 'I ________ along the street looking for a place to park when the accident ________.<br>A. went, was occurring　B. have gone, occurred　C. was going, occurred　D. was going, had occurred', 'C. was going, occurred', '长动作 was going ＋ when ＋ 瞬间动作 occurred'],
          ['28·T5', 'Mum _____________ in the garden when I got home yesterday. <span class="zh">(sit)</span>', 'was sitting', 'got home 是针；sit 双写 t → sitting'],
          ['27·T28', 'Tom _______ basketball with his classmates from 4:00 to 6:00 yesterday afternoon.<br>A. is played　B. was playing　C. plays　D. had played', 'B. was playing', '过去某时段正在进行的动作'],
          ['26·T13', '—May I speak to Mary? —Hold on, please. She ________ the dripping tap in the kitchen. <span class="zh">(fix)</span>', 'is fixing', 'Hold on 暗示此刻 → 现在进行（近亲题：别和过去进行混）'],
        ],
        drills: [
          ['练 4-1', 'I ________ my homework when the lights went out. <span class="zh">(do)</span>', 'was doing', 'went out 是针'],
          ['练 4-2', 'They ________ football from 3:00 to 5:00 yesterday afternoon. <span class="zh">(play)</span>', 'were playing', '过去某时段正在进行；they 复数 → were'],
          ['练 4-3', 'What ________ you ________ at eight last night? <span class="zh">(do)</span>', 'were; doing', 'at eight last night 过去时点'],
          ['练 4-4', 'Mary ________ for the bus when it began to rain. <span class="zh">(wait)</span>', 'was waiting', 'began to rain 是针'],
          ['练 4-5', '—Where is Mum? —She ________ the flowers in the garden. <span class="zh">(water)</span>', 'is watering', '问此刻 → 现在进行（近亲题）'],
        ],
      },
      {
        key: 't5', no: '主题五', title: 'if 的两张面孔 & 宾语从句三律',
        score: '4 错', color: '#c25a72', soft: '#faeef1',
        lead: '同一个 if 可能是「如果」（条件从句，主将从现），也可能是「是否」（宾语从句，正常时态）。先分清身份，再定时态。',
        cards: [
          {
            h: '卡 1 · if 双身份',
            body: `
<table><tr><th>if 是谁</th><th>长什么样</th><th>从句时态</th></tr>
<tr><td><b>如果</b>（条件状语从句）</td><td>谈假设：If it is fine, we'll go</td><td><b>主将从现</b>：从句用一般现在表将来，<b>禁 will</b></td></tr>
<tr><td><b>是否</b>（宾语从句）</td><td>跟在 wonder / know / ask 后</td><td>正常时态：该将来就 <b class="en">will</b></td></tr>
</table>
<div class="topic-callout">27·T25 和 28·T22 是同一道题的两张皮：<br><span class="en">We wonder <b>if</b> our parents <b>will come</b>...（wonder 后的 if＝是否 → 宾从，用 will）<br><b>If</b> they <b>come</b>, we'll be glad.（这里的 if＝如果 → 条件句，用 come）</span></div>`,
          },
          {
            h: '卡 2 · 宾语从句三律',
            body: `
<table><tr><th>律</th><th>内容</th></tr>
<tr><td>① 陈述语序</td><td class="en">主语在动词前 → Could you tell me <b>when you will leave</b>（✗ when will you leave）</td></tr>
<tr><td>② 时态呼应</td><td>主句过去 → 从句回移一级（will→would）；但<b>客观真理永远一般现在</b>：<span class="en">The sun <b>rises</b> in the east</span>（哪怕在 told us 后面）</td></tr>
<tr><td>③ Could 是礼貌</td><td class="en">Could you tell me...? 的从句<b>不回移</b>，该将来就 will</td></tr>
</table>`,
          },
        ],
        replay: [
          ['27·T25', "We wonder if our parents will come to our graduating party next weekend. If they _______, we'll be very glad.<br>A. come　B. comes　C. are coming　D. will come", 'A. come', '第二个 if＝如果 → 条件从句主将从现，禁 will'],
          ['28·T22', "—Let's go hiking if it __________ fine tomorrow. —But nobody knows if it __________.<br>A. is; rains　B. will be; rains　C. is; will rain　D. will be; will rain", 'C. is; will rain', '第一个 if＝如果 → 主将从现；第二个 if＝是否 → 宾从正常将来'],
          ['28·T26', 'Could you tell me ________?<br>A. when you will leave for Beijing　B. when will you leave for Beijing　C. when you would leave for Beijing　D. when would you leave for Beijing', 'A', '陈述语序 + Could 是礼貌不是过去 → 该将来就 will'],
          ['26·T15', 'Miss Li told us the sun ________ in the east. <span class="zh">(rise)</span>', 'rises', '宾从里的客观真理 → 永远一般现在'],
        ],
        drills: [
          ['练 5-1', "If it ________ tomorrow, we will go camping. <span class=\"zh\">(not rain)</span>", "doesn't rain", '条件从句主将从现'],
          ['练 5-2', "I don't know if it ________ tomorrow. <span class=\"zh\">(rain)</span>", 'will rain', "know 后的 if＝是否 → 宾从正常将来"],
          ['练 5-3', 'Could you tell me where ________?　A. does he live　B. he lives', 'B. he lives', '陈述语序'],
          ['练 5-4', 'The teacher said light ________ faster than sound. <span class="zh">(travel)</span>', 'travels', '客观真理不回移'],
          ['练 5-5', 'I hear that they ________ back next week. <span class="zh">(come)</span>', 'will come', '主句现在时 → 从句正常将来'],
        ],
      },
      {
        key: 't6', no: '主题六', title: '易混词杀手组',
        score: '13 错 · 选择题送命题', color: '#55688a', soft: '#edeff4',
        lead: 'raise/rise、been to/in/gone、used to、open/close——全是选择题里的送命题，一张卡记住一组。',
        cards: [
          {
            h: '卡 1 · raise vs rise（三讲都考了）',
            body: `
<table><tr><th>词</th><th>及物？</th><th>意思</th><th>记法</th></tr>
<tr><td class="en"><b>raise</b> raised–raised</td><td>✓ 及物（必须带宾语）</td><td>举起；筹款；饲养；提高</td><td><b>raise 有手</b>（要作用于别的东西）</td></tr>
<tr><td class="en"><b>rise</b> rose–risen</td><td>✗ 不及物（自己升）</td><td>升起；上升；上涨</td><td><b>rise 自己飞</b>（太阳升、物价涨）</td></tr>
</table>
<div class="topic-callout good">判断一步走：句里有<b>宾语</b>（钱、手、帽子）→ raise；没宾语、主语自己升 → rise。</div>
<div class="topic-callout warn"><b class="tag">连带规则：不及物动词没有被动式</b>rise / appear / happen / take place 都不能说 was risen / was appeared / was taken place。</div>`,
          },
          {
            h: '卡 2 · has been to / in / gone to',
            body: `
<table><tr><th>短语</th><th>意思</th><th>人现在在哪</th></tr>
<tr><td class="en">has been <b>to</b></td><td>去过（已回来）</td><td>回来了</td></tr>
<tr><td class="en">has been <b>in</b></td><td>一直在（常接 for 时长）</td><td>还在那儿</td></tr>
<tr><td class="en">has <b>gone</b> to</td><td>去了（还没回）</td><td>在路上/在那儿</td></tr>
</table>`,
          },
          {
            h: '卡 3 · used to do vs be used to doing',
            body: `
<table><tr><th>短语</th><th>to 是什么</th><th>意思</th></tr>
<tr><td class="en"><b>used to do</b></td><td>不定式 → 接<b>原形</b></td><td>过去常常（现在不了）</td></tr>
<tr><td class="en"><b>be used to doing</b></td><td>介词 → 接<b>doing</b></td><td>习惯于</td></tr>
</table>`,
          },
          {
            h: '卡 4 · open 与 close 的词性陷阱',
            body: `
<p>· <b class="en">open</b> 可作形容词：<span class="en">be open</span>＝营业中/开着（状态，可接 for 12 hours）</p>
<p>· <b class="en">close</b> 作动词：<span class="en">closes</span>＝关门这个动作（每天规律 → 三单）；<span class="en">is close</span>＝「亲近」，不是关门</p>`,
          },
        ],
        replay: [
          ['27·T35', 'The number of Tik Tok users（抖音用户）_______ sharply since Tik Tok _______ in 2016. It\'s really popular now.<br>A. has risen; appeared　B. have been risen; appeared　C. have raised; was appeared　D. has been raised; was appeared', 'A. has risen; appeared', 'since → 完成时；the number of 三单 → has；rise 不及物无被动；appear 也无被动'],
          ['28·T33', "A British girl ________ over $30,000 for a children's hospital several months ago.<br>A. raised　B. has raised　C. rose　D. has risen", 'A. raised', '有宾语 $30,000 → raise；ago → 过去式 raised'],
          ['26·T17', 'The 1st National Youth Games ________ in Fuzhou in 2015.<br>A. takes place　B. took place　C. is taken place　D. was taken place', 'B. took place', 'in 2015 → 过去时；take place 不及物无被动'],
          ['27·T29', 'As an exchange student, Alan _______ England for one and a half years.<br>A. has been to　B. has been in　C. has gone to　D. has been away', 'B. has been in', 'for 时长 + 人还在 → been in'],
          ['28·T17', '--- There ________ a noisy factory here, but now it has changed into a beautiful park.<br>A. used to have　B. used to be　C. was used to have　D. was used to be', 'B. used to be', 'There be 存在句不配 have；used to 无被动式'],
          ['28·T21', '--- How does Jack usually go to school? --- He ________ ride a bike, but now he ________ there to lose weight.<br>A. used to; is used to walk　B. was used to; is used to walking　C. used to; is used to walking　D. was used to; used to walk', 'C. used to; is used to walking', '今昔对比：过去常常 used to + 原形；now + 习惯于 be used to + doing'],
          ['28·T27', 'The bookstore ________ for 12 hours a day. It ________ at 10:00 p.m.<br>A. opens; closes　B. opens; is closed　C. is open; is close　D. is open; closes', 'D. is open; closes', '状态接时长 be open；每天规律动作三单 closes'],
          ['26·T4', 'He ________ coffee to tea when he was young. <span class="zh">(prefer)</span>', 'preferred', 'when he was young → 过去的习惯 → 一般过去时'],
          ['27·T3', 'Today is a sunny day. We __________ (have) a picnic this afternoon.', 'will have', 'this afternoon → 一般将来时'],
          ['27·T8', 'Lily turned off the lights and then __________ (leave) the classroom.', 'left', 'and 并列，前面 turned 是过去时 → 后面也用过去时'],
          ['27·T21', 'Susan and her sister _______ some photos in the park the day after tomorrow.<br>A. take　B. took　C. will take　D. is going to take', 'C. will take', 'the day after tomorrow → 将来时；主语两人复数，D 单数 is 错'],
          ['27·T24', "--- My car _______ yesterday. Could you please give me a ride tomorrow? --- I'm sorry I can't, I'm _______ Dalian tomorrow morning.<br>A. breaks down; flying at　B. has broken down; flying at　C. broke down; flying to　D. had broken down: flying to", 'C. broke down; flying to', 'yesterday → 过去时；进行时表已定的将来计划；fly to 搭配 to'],
          ['27·T33', "--- Uncle Sam, I have to leave right now. --- What a pity! I _______ you could stay a little longer with us.<br>A. thought　B. think　C. am thinking　D. have thought", 'A. thought', '「我原以为」——现在落空了，想法属于过去 → 过去时'],
          ["28·T11", "I haven't ever spoken to her.（改成同义句）I have __________ spoken to her.", 'never', 'not ... ever ＝ never（一词）'],
        ],
        drills: [
          ['练 6-1', 'The price of vegetables ________ a lot recently.<br>A. has risen　B. has raised　C. has been risen　D. rose', 'A', 'rise 不及物无被动；recently → 完成时'],
          ['练 6-2', "—Where is Tom? —He ________ the library. He'll be back soon.<br>A. has been to　B. has gone to　C. has been in", 'B', '人还没回（一会儿才回来）'],
          ['练 6-3', 'My uncle ________ Beijing for ten years.<br>A. has been to　B. has gone to　C. has been in', 'C', 'for ten years + 人还在'],
          ['练 6-4', "He ________ smoke, but now he doesn't.<br>A. is used to　B. used to　C. uses to", 'B', '过去常常，接原形 smoke'],
          ['练 6-5', 'There ________ a hospital near our school before.<br>A. used to be　B. used to have　C. was used to be', 'A', 'There be 存在句不配 have'],
        ],
      },
    ],
  },
]

// ---- 渲染 ----------------------------------------------------------------- //

function visibleTopics(user) {
  return TOPICS.filter(
    (t) => !t.assignedTo || t.assignedTo === user,
  )
}

function qList(items, cls) {
  return items
    .map(
      ([tag, text, key, why]) => `
<li>
  <span class="topic-qtag">${escapeHtml(tag)}</span>
  <p class="topic-qtext">${text}</p>
  <button class="topic-reveal-btn">看答案</button>
  <div class="topic-answer"><span class="key">${escapeHtml(key)}</span><div class="why">${escapeHtml(why)}</div></div>
</li>`,
    )
    .join('')
}

export async function mountTopics(el, { role } = {}) {
  const auth = getAuth()
  const me = auth ? auth.user : ''
  const list = visibleTopics(me)

  el.innerHTML = `
    <div class="topic-page">
      <div class="topic-hero">
        <p class="eyebrow">和爸学 · 专题学习</p>
        <h1>📚 我的专题</h1>
        <p class="sub">爸爸给你准备的专属手册。一个主题一个主题学，学完一个点一下「我学完了」。</p>
      </div>
      <div class="topic-list" id="topic-list"></div>
    </div>`

  if (!list.length) {
    el.querySelector('#topic-list').innerHTML =
      '<div class="topic-empty">暂无可用的专题</div>'
    return
  }

  const listEl = el.querySelector('#topic-list')

  // ---- 专题卡片入口 ----
  async function renderList(progressMap) {
    listEl.innerHTML = list
      .map((t) => {
        const p = progressMap[t.id] || { done_keys: [] }
        const total = t.themes.length
        const done = t.themes.filter((x) => p.done_keys.includes(x.key)).length
        const pct = total ? Math.round((done / total) * 100) : 0
        return `
      <article class="topic-card" data-topic="${t.id}" style="--tc:${t.themes[0].color}">
        <div class="topic-card-head">
          <span class="topic-emoji">${t.emoji}</span>
          <div>
            <h2>${escapeHtml(t.title)}</h2>
            <p>${escapeHtml(t.subtitle)}</p>
          </div>
          <span class="topic-badge ${done === total && total ? 'all-done' : ''}">${done}/${total}</span>
        </div>
        <div class="topic-mini-bar"><i style="width:${pct}%"></i></div>
      </article>`
      })
      .join('')
    listEl.querySelectorAll('.topic-card').forEach((card) => {
      card.addEventListener('click', () => {
        const id = card.dataset.topic
        location.hash = `/topics/${id}`
      })
    })
  }

  // ---- 进度加载 ----
  let progressMap = {}
  try {
    const rows = await api.topicsProgress()
    progressMap = Object.fromEntries(rows.map((r) => [r.topic_id, r]))
  } catch { /* 后端不可达时降级为空进度，按钮本地乐观更新 */ }
  renderList(progressMap)

  // ---- 手册详情页（hash: #/topics/{id}） ----
  async function renderDetail(topicId) {
    const t = list.find((x) => x.id === topicId)
    if (!t) {
      location.hash = '/topics'
      return
    }
    let p
    try {
      p = await api.topicProgress(topicId)
    } catch {
      p = { done_keys: [] }
    }
    const doneSet = new Set(p.done_keys || [])

    el.innerHTML = `
    <div class="topic-page">
      <button class="fce-back-btn" id="topic-back">← 返回专题列表</button>
      <div class="topic-hero detail" style="--tc:${t.themes[0].color}">
        <p class="eyebrow">和爸学 · 专题学习${t.assignedTo ? ` · ${escapeHtml(t.assignedTo)} 的专属专题` : ''}</p>
        <h1>${t.emoji} ${escapeHtml(t.title)}</h1>
        <p class="sub">${escapeHtml(t.subtitle)}</p>
        <div class="topic-progress-row">
          <div class="topic-bar"><i id="topic-bar-i"></i></div>
          <span id="topic-progress-label"></span>
        </div>
      </div>
      <nav class="topic-toc" id="topic-toc">
        ${t.themes
          .map(
            (x) =>
              `<a href="#th-${x.key}" data-key="${x.key}" style="--tc:${x.color}"><i class="dot"></i>${escapeHtml(x.no)} ${escapeHtml(x.title.split('——')[0])}</a>`,
          )
          .join('')}
      </nav>
      ${t.themes
        .map((th) => {
          const done = doneSet.has(th.key)
          return `
      <section class="topic-theme" id="th-${th.key}" style="--tc:${th.color};--tcs:${th.soft}" data-key="${th.key}">
        <div class="topic-theme-head">
          <span class="topic-no">${escapeHtml(th.no)}</span>
          <h2>${escapeHtml(th.title)}</h2>
          <span class="topic-score">${escapeHtml(th.score)}</span>
        </div>
        <p class="topic-lead">${th.lead}</p>
        ${th.cards
          .map(
            (c) => `
        <h3 class="topic-sub">${escapeHtml(c.h)}</h3>
        <div class="topic-cardbox">${c.body}</div>`,
          )
          .join('')}
        <h3 class="topic-sub">错题回放（先想再点开）</h3>
        <ul class="topic-replay">${qList(th.replay)}</ul>
        <h3 class="topic-sub">变式练习（换马甲的同款题）</h3>
        <ul class="topic-drill">${qList(th.drills)}</ul>
        <div class="topic-done-row">
          <button class="topic-done-btn ${done ? 'done' : ''}" data-key="${th.key}">
            ${done ? '✅ 这个主题我学完了' : '🌱 这个主题我学完了'}
          </button>
          <span class="topic-done-hint">${done ? '已记录 · 点一下可取消' : '学完点这里，爸爸能看到'}</span>
        </div>
      </section>`
        })
        .join('')}
    </div>`

    el.querySelector('#topic-back').addEventListener('click', () => {
      location.hash = '/topics'
    })

    // 揭示答案
    el.querySelectorAll('.topic-reveal-btn').forEach((btn) => {
      btn.addEventListener('click', () => btn.closest('li').classList.add('open'))
    })

    // 进度条
    const barI = el.querySelector('#topic-bar-i')
    const label = el.querySelector('#topic-progress-label')
    function refreshProgress() {
      const total = t.themes.length
      const done = t.themes.filter((x) => doneSet.has(x.key)).length
      barI.style.width = `${total ? Math.round((done / total) * 100) : 0}%`
      label.textContent = `${done}/${total} 个主题`
      // 导航点同步打勾态
      el.querySelectorAll('#topic-toc a').forEach((a) => {
        a.classList.toggle('done', doneSet.has(a.dataset.key))
      })
    }
    refreshProgress()

    // 我学完了：乐观更新 + PUT 后端
    el.querySelectorAll('.topic-done-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const key = btn.dataset.key
        const nowDone = !doneSet.has(key)
        nowDone ? doneSet.add(key) : doneSet.delete(key)
        btn.classList.toggle('done', nowDone)
        btn.textContent = nowDone ? '✅ 这个主题我学完了' : '🌱 这个主题我学完了'
        btn.closest('.topic-done-row').querySelector('.topic-done-hint').textContent =
          nowDone ? '已记录 · 点一下可取消' : '学完点这里，爸爸能看到'
        refreshProgress()
        try {
          await api.topicPutProgress(t.id, [...doneSet], t.assignedTo || '')
        } catch {
          btn.closest('.topic-done-row').querySelector('.topic-done-hint').textContent =
            '⚠️ 保存失败，稍后会重试（进度暂存本页）'
        }
      })
    })
  }

  // 路由：#/topics 列表；#/topics/{id} 详情
  const segs = (location.hash.replace(/^#\/?/, '') || '').split('?')[0].split('/')
  if (segs[0] === 'topics' && segs[1]) {
    await renderDetail(segs[1])
  }
}
