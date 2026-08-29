// 《FCE 冲刺宝典》结构化数据——源自 FCE考点.pdf（66 页），人工清洗整理。
// 结构：
//   FCE_DAYS   Day 1~19（kind: lesson 语法讲解 / exercise 直击考点练习）
//   FCE_PHRASES 120 条常考语用词组
//   FCE_CLOZE  选词填空 10 题
// 已修复 PDF 提取乱码（—般→一般、even i£→even if 等）与断行。

export const FCE_DAYS = [
  // ======================= 连接词 ======================= //
  {
    day: 1,
    title: '连接词 Linking Words (1)',
    kind: 'lesson',
    sections: [
      {
        name: 'A. 因果关系',
        points: [
          {
            rule: 'as / because / since（既然）/ for + 句子，表示"因为"',
            examples: [['We couldn\'t go out because the weather was bad.', '因为天气不好，我们不能出门。']],
          },
          {
            rule: 'because of = due to = owing to = on account of + 名词或 V-ing；接句子时用 the fact that',
            examples: [
              ['Owing to medical advance, people can live much longer now.', '由于医学进步，人们现在能活得更久。'],
              ['Owing to the fact that medical science has advanced, people can live much longer now.', '同上（接句子的说法）。'],
              ['Because of the bad weather, the flight was cancelled.', '因为坏天气，航班被取消了。'],
            ],
          },
          {
            rule: 'therefore 是副词，不能直接连接两个句子（用 and therefore 或句号分开）',
            examples: [
              ['We were unable to get funding and therefore had to abandon the project.', '我们拿不到资金，因此不得不放弃项目。'],
              ['We were unable to get funding. Therefore, we had to abandon the project.', '同上（分句写法）。'],
            ],
          },
          {
            rule: 'so 是连词，可以直接连接两个句子',
            examples: [
              ['We were unable to get funding, so we had to abandon the project.', '我们拿不到资金，所以只好放弃项目。'],
            ],
          },
        ],
      },
      {
        name: 'B. 目的关系',
        points: [
          {
            rule: 'in order to do = so as to do 表示"为了"',
            examples: [['I learn English in order to study abroad in the future.', '我学英语是为了将来出国留学。']],
          },
          {
            rule: 'in order that = so that + 句子，表示"为了"，后面多接情态动词',
            examples: [['I learn English every day so that I can study abroad in the future.', '我每天学英语，为的是将来能出国留学。']],
          },
        ],
      },
      {
        name: 'C. 让步关系',
        points: [
          {
            rule: 'although / though / even though / even if + 句子，表示"即使"；后面的句子不可以加 but',
            examples: [['Although he had no experience, he got the role.', '尽管他没有经验，他还是得到了那个角色。']],
          },
          {
            rule: 'despite = in spite of + 名词或 V-ing；接句子时用 despite the fact that',
            examples: [
              ['Despite having no experience, he got the role.', '尽管没有经验，他还是得到了角色。'],
              ['Despite the fact that he had no experience, he got the role.', '同上（接句子的说法）。'],
              ['Although / Even though he had no experience, he got the role.', '同上（although 版本）。'],
            ],
          },
          {
            rule: 'nevertheless（即使如此）/ however（但是）都是副词，不能直接连接两个句子',
            examples: [
              ['Many people think playing games is a waste of time. Nevertheless / However, it can\'t be denied that playing games has a lot of benefits.', '很多人认为玩游戏是浪费时间。然而，不可否认游戏有很多好处。'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 2,
    title: '连接词 Linking Words (2)',
    kind: 'lesson',
    sections: [
      {
        name: 'D. 并列、递进关系',
        points: [
          {
            rule: 'besides / apart from / in addition to / as well as + n. / doing，表示"除此之外还…"',
            examples: [
              ['I like basketball as well as football.', '除了足球我还喜欢篮球。'],
              ['Apart from making you relaxed, doing sports can keep you fit.', '运动除了让你放松，还能让你保持健康。'],
            ],
          },
        ],
      },
      {
        name: 'E. 条件关系',
        points: [
          {
            rule: 'unless 除非 = if not',
            examples: [
              ['I won\'t go to the party unless you go.', '除非你去，否则我不会去聚会。'],
              ['I won\'t go to the party if you don\'t go.', '同上（if not 版本）。'],
            ],
          },
          {
            rule: 'not ... until 直到…才。经常考倒装句型或强调句型',
            examples: [
              ['I didn\'t go to bed until I finished my homework.', '做完作业我才睡觉。'],
              ['Not until I finished my homework, did I go to bed.', '同上（倒装：主句部分倒装）。'],
              ['It was not until I finished my homework that I went to bed.', '同上（强调句：not until 放在一起）。'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 3,
    title: '直击考点 · 关键句型转换',
    kind: 'exercise',
    intro: 'FCE 句型转换题型：用给定的大写关键词改写句子。',
    items: [
      { q: 'Nicky is the only person who has signed up for the trip. (NOBODY)', stem: 'Apart from ______ their name down for the trip.', a: 'Apart from Nicky, nobody has put / written (their name down).' },
      { q: 'It was windy and raining but we still went to the beach. (SPITE)', stem: 'We went to the beach ______ wind and rain.', a: 'in spite of wind' },
      { q: 'Despite not feeling well, Lisa went to the cinema with her friends. (ALTHOUGH)', stem: 'Lisa went to the cinema with her friends ______ well.', a: 'although she was not feeling' },
      { q: "Unfortunately, I only realized I'd lost my keys when I arrived home. (UNTIL)", stem: "Unfortunately, ______ I arrived home that I realized I'd lost my keys.", a: 'it was not until' },
      { q: 'We went to the beach even though the weather was very wet. (FACT)', stem: 'We went to the beach despite ______ raining heavily.', a: 'the fact that it was' },
      { q: 'The group continued to walk despite rain starting to fall. (EVEN)', stem: 'The group carried ______ started to rain.', a: 'on walking even though it' },
      { q: 'I am planning to go to the football match, unless they cancel it because of the weather. (DUE)', stem: 'If the football match ______ the weather, I am planning to go to it.', a: "isn't cancelled due to" },
      { q: 'He sings in the show and dances in it as well. (ONLY)', stem: 'Not ______ in the show, he also dances in it.', a: 'only does he sing（倒装）' },
      { q: 'The only vegetables that Helen dislikes is cabbage. (VEGETABLES)', stem: 'Helen ______ from cabbages.', a: 'likes all the vegetables apart' },
      { q: 'Thick fog prevented the plane from landing. (UNABLE)', stem: 'The plane ______ of the thick fog.', a: 'was unable to land because' },
    ],
  },
  // ======================= 定语从句 ======================= //
  {
    day: 4,
    title: '定语从句 Relative Clause (1)',
    kind: 'lesson',
    sections: [
      {
        name: 'A. 概念',
        points: [
          {
            rule: '定语从句：在复合句里充当定语的从句，通常紧靠在所修饰的名词或代词后面',
            examples: [['This is the book that my father bought me yesterday.', '这是我爸爸昨天给我买的那本书。（主句 This is the book. / 定语从句 that my father bought me yesterday / 先行词 the book / 关系代词 that，在从句中作宾语）']],
          },
          {
            rule: '先行词：被定语从句修饰的名词或代词；关系词：引导定语从句的词，分为关系代词和关系副词',
            examples: [
              ['The time when he arrives is not known.', '他到达的时间还不确定。（关系副词 when 在从句中作状语——从句不缺主语也不缺宾语时只能填关系副词）'],
            ],
          },
          {
            rule: '关系代词 who / whom / whose / which / that 主要作主语、宾语；关系副词 when / where / why 作状语。注意：关系词没有 how，也没有 what！！！',
          },
        ],
      },
      {
        name: 'B. 关系代词的一般用法',
        points: [
          {
            rule: '关系代词在从句中充当主语或宾语；作宾语时常被省略。that 代指人或物；who 代指人；whom 是 who 的宾格只能作宾语；which 代指物；whose 表所属（先行词与 whose 后面的词构成所属关系）',
          },
          {
            rule: '如何选定关系词：① 划定定语从句 → ② 判断从句缺什么成分 → ③ 缺主语/宾语选关系代词，否则选关系副词 → ④ 看先行词决定选哪一个',
            examples: [
              ['This is the factory ______ we visited last year.', ' visited 后缺宾语 → 关系代词；factory 是物 → that / which（作宾语还可省略）'],
              ['He has a book ______ cover is very beautiful.', 'book 与 cover 是所属关系（the book\'s cover）→ whose。注意：用 whose 时先行词不一定是人'],
              ['This is the man ______ helped me yesterday.', '答案：who'],
              ['The teacher ______ you want to see is coming.', '答案：who / whom / 省略'],
              ['I met a boy ______ father was an astronaut.', '答案：whose'],
              ["Here is the coat ______ will be made to you.", '答案：which / that'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 5,
    title: '定语从句 Relative Clause (2)',
    kind: 'lesson',
    sections: [
      {
        name: 'C. 关系副词的一般用法',
        points: [
          {
            rule: '关系副词 when / where / why 在从句中作状语，分别表示时间、地点和原因',
          },
          {
            rule: 'when 的先行词通常是 time / day / season / age / occasion 等时间名词；where 的先行词通常是 place / city / town / village / house / case / situation / scenes 等地点或情形名词；why 的先行词只能是 reason',
          },
          {
            rule: '关系副词 when 和 where 有时可用"介词 + which"代替，why 可用 for which 代替',
            examples: [
              ['Beijing is the place where I was born. = Beijing is the place in which I was born.', '北京是我的出生地。'],
              ['Is this the reason why he refused our offer? = Is this the reason for which he refused our offer?', '这就是他拒绝我们提议的原因吗？'],
            ],
          },
          {
            rule: '注意：先行词虽然是时间或地点，但若在从句中作主语或宾语，要用关系代词',
            examples: [
              ['The factory ______ his father worked has closed.', '从句不缺主宾 → 关系副词；地点 → where / in which'],
              ['The factory ______ was built in 1978 has closed.', '从句缺主语 → 关系代词；物 → that / which'],
            ],
          },
        ],
      },
      {
        name: 'D. 限定性 vs 非限定性定语从句',
        points: [
          {
            rule: '形式区别：限制性定语从句与先行词不用逗号隔开；非限制性用逗号隔开',
          },
          {
            rule: '意义区别：限制性从句提供必要信息、限定先行词范围，去掉后主句含义不完整；非限制性从句只提供附加说明，去掉后主句意义仍明确',
            examples: [
              ["I don't like people who are never on time.", '限制性：去掉从句只剩 I don\'t like people，语义不清。'],
              ['My mother, who is 50 years old, lives with me now.', '非限制性：去掉从句主句仍然完整。'],
              ['Beijing, which is the capital of China, has developed into an international city.', '非限制性：北京已发展成国际化大都市。'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 6,
    title: '直击考点 · 填写关系词',
    kind: 'exercise',
    intro: 'FCE 阅读改错/语法填空高频：在真实语篇中填定语从句关系词。',
    items: [
      { q: 'Cat owners, ______ have to keep returning to their old address in order to bring their cat home, tend to find the homing instinct simply irritating rather than useful or interesting!', a: 'who' },
      { q: 'A breakthrough came in the 1880s with the development of lighter materials ______ also enabled mass production of cans.', a: 'which / that' },
      { q: 'Keeping food for long periods of time was historically a huge problem. This proved especially crucial in times ______ agricultural production was severely limited by weather or crop failure.', a: 'when' },
      { q: 'Reading extended texts such as novels or biographies, ______ requires intense concentration for a considerable period of time, helps to lengthen attention spans in children.', a: 'which（非限定性，指代前面整件事）' },
      { q: 'Researchers found that those ______ had attended lectures containing humor scored significantly higher than the other students.', a: 'who / that' },
      { q: 'Horses also have the ability to turn their ears from side to side, ______ is particularly important for wild horses because they need to know where danger is coming from.', a: 'which' },
      { q: 'Recently, folding bicycles have become very popular in Japan, particularly in congested urban areas like Tokyo, a city ______ every square centimeter of space is in great demand.', a: 'where / in which' },
      { q: 'Just as most cookery schools in Ireland, Kathleen initially copied the classical dishes of France and Italy and other countries ______ have a reputation for excellent food.', a: 'which / that' },
      { q: 'This was the aim of the designers of the Freedom Ship, ______ aim it was to create a vast floating city, complete with hospitals, schools, shops and even an airport.', a: 'whose' },
      { q: 'Research clearly shows that experts ______ at the time had different interpretations of dinosaurs. The way in ______ these various interpretations have evolved demonstrates how scientific ideas often develop.', a: 'who；which（the way in which 固定搭配）' },
    ],
  },
  // ======================= 条件句 ======================= //
  {
    day: 7,
    title: '条件句 Conditional (1)',
    kind: 'lesson',
    sections: [
      {
        name: 'A. If 条件句（四种条件句）',
        points: [
          {
            rule: '零条件句：if + 一般现在时，主句一般现在时——条件发生时结果一定会发生',
            examples: [['If we heat ice, it melts.', '如果我们给冰加热，它就会融化。']],
          },
          {
            rule: '第一条件句：if + 一般现在时，主句一般将来时——条件满足时结果可能会在将来发生',
            examples: [['If I pass this exam, my parents will give me a motorbike.', '如果我通过考试，我父母会给我买一辆摩托车。']],
          },
          {
            rule: '第二条件句：if + 一般过去时，主句 would do——对现在情况的虚拟，条件与目前事实相反',
            examples: [
              ['If you lived in London, I would visit you at weekends.', '如果你住在伦敦，我会周末去看你。（事实上你目前并不住在伦敦）'],
              ['If I were / was in Disneyland in HK, I would be thrilled.', '如果我在香港迪士尼，我会非常兴奋。'],
            ],
          },
          {
            rule: '第三条件句：if + 过去完成时，主句 would have done——对过去情况的虚拟，条件与过去事实相反',
            examples: [
              ['If I had seen Karen, I would have given her your message.', '如果我当时看见 Karen，我就把你的消息带给她了。'],
              ['If she had had some money on her, she would have taken a taxi.', '如果她当时身上有钱，她就打车了。'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 8,
    title: '条件句 Conditional (2)',
    kind: 'lesson',
    sections: [
      {
        name: 'B. wish / if only',
        points: [
          {
            rule: 'wish + 一般过去时：与现在事实不符，希望现在是另一种情况',
            examples: [['I wish / if only I knew the answer.', '我希望我知道答案。（事实是自己不知道）']],
          },
          {
            rule: 'wish + 过去完成时：与过去事实不符，对过去的遗憾',
            examples: [['I wish I had read more books when I was at college.', '我希望大学时多读些书。（当时没读，感到遗憾）']],
          },
          {
            rule: 'wish + sb. would do sth.：希望别人做某事，常含埋怨、不耐烦的语气',
            examples: [
              ['I wish / if only it would stop raining tomorrow.', '我希望明天雨能停。'],
              ['I wish / if only he would shut up.', '我希望他能住嘴。'],
            ],
          },
        ],
      },
      {
        name: 'C. 常考特殊结构',
        points: [
          {
            rule: "It's time (that)... 到…时候了，后面接句子用一般过去时",
            examples: [['It is time we had lunch.', '我们该吃午饭了。']],
          },
          {
            rule: 'would rather 我宁愿…，后面接句子用一般过去时',
            examples: [["I would rather you didn't come.", '我宁愿你不要来。']],
          },
        ],
      },
    ],
  },
  {
    day: 9,
    title: '直击考点 · 关键句型转换',
    kind: 'exercise',
    intro: '重点考 wish / if only / would rather / it\'s time 虚拟结构。',
    items: [
      { q: 'I regret not listening to my teacher today. (WISH)', stem: 'I ______ attention to my teacher today.', a: "wish I hadn't paid" },
      { q: "I didn't go skating because I was too tired. (would)", stem: '______ I hadn\'t been so tired.', a: 'I would have gone skating if' },
      { q: "It's a pity I didn't see Jane before she went on holiday. (WISH)", stem: 'I ______ Jane before she went on holiday.', a: 'wish I had seen' },
      { q: 'I missed the train because I got to the station late. (CAUGHT)', stem: 'If I had got to the station on time, ______ the train.', a: 'I would have caught' },
      { q: 'I really regret eating all the chocolate. (WISH)', stem: 'I really ______ all that chocolate.', a: "wish I hadn't eaten" },
      { q: 'Wear some warm clothes because it might get cold later. (CASE)', stem: 'Wear some warm clothes ______ cold later.', a: 'in case it gets' },
      { q: "My parents don't like me lending my skateboard to my friends. (rather)", stem: 'My parents would ______ my friends borrow my skateboard.', a: "rather I didn't let" },
      { q: "Please don't look at my painting yet because I haven't finished it. (RATHER)", stem: "I'd ______ my painting.", a: "rather you didn't look at" },
      { q: 'Harry was only able to play the piece perfectly because he had practiced it for hours. (HAVE)', stem: 'Harry ______ able to play the piece perfectly if he hadn\'t practiced it for hours.', a: "wouldn't have been" },
      { q: "It's a pity I forgot to bring my coat with me, because it's absolutely freezing! (WISH)", stem: "I ______ to bring my coat with me, because it's absolutely freezing!", a: "wish I hadn't forgotten" },
      { q: "'You urgently need to learn some manners, young man!' said Aunt Helena. (high)", stem: "'It's ______ some manners, young man!' said Aunt Helena.", a: 'high time you learnt' },
      { q: 'You need to do your homework now. (TIME)', stem: 'It ______ homework done.', a: 'is time you got your / is time to get your' },
      { q: "It's a shame I arrived late at the party. (TURNED)", stem: 'I wish ______ late to the party.', a: "I hadn't turned up" },
      { q: 'She only bought the book because the teacher said it was good. (HAVE)', stem: "She wouldn't ______ the teacher hadn't said it was good.", a: 'have bought the book if' },
      { q: 'Daisy regretted eating so much cake. (WISH)', stem: "'I wish ______ so much cake,' said Daisy.", a: "I hadn't eaten" },
    ],
  },
  // ======================= 动词搭配 ======================= //
  {
    day: 10,
    title: '动词 + to do / doing / that 从句',
    kind: 'lesson',
    sections: [
      {
        name: 'A. Verb + to infinitive',
        points: [
          {
            rule: 'attempt to do（努力尝试）/ manage to do（成功做成）/ refuse to do（拒绝）/ offer to do（主动要求）/ mean to do（打算）',
          },
          {
            rule: 'persuade sb. to do（劝某人）/ remind sb. to do（提醒某人）/ expect to do / expect sb. to do（期待）/ warn sb. to do（警告某人）',
          },
        ],
      },
      {
        name: 'B. Verb + doing',
        points: [
          {
            rule: 'recommend / suggest doing（建议）/ avoid doing（避免）/ carry on doing（继续）',
          },
          {
            rule: 'deny doing（否认）/ admit doing（承认）/ regret doing（后悔）/ give up doing（放弃）',
          },
        ],
      },
      {
        name: 'C. Verb + that 从句',
        points: [
          {
            rule: 'admit / suggest / recommend 等既可接 doing 也可接 that 从句（suggest / recommend 从句用 should + 动词原形，should 可省略）；remind sb. that ...',
            examples: [
              ['She admitted taking the money. = She admitted she had taken the money.', '她承认拿了钱。'],
              ['I suggest / recommend adding some sugar. = I suggest / recommend we (should) add some sugar.', '我建议加些糖。'],
              ['I called Jane and reminded her (that) the conference had been cancelled.', '我打电话给 Jane，提醒她会议已取消。'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 11,
    title: '直击考点 · 关键句型转换',
    kind: 'exercise',
    intro: '重点考动词搭配互相转换（manage/allow/let/refuse/carry on/give up…）。',
    items: [
      { q: 'Adam hates volleyball, so how did the coach manage to persuade him to join the team? (SUCCEED)', stem: 'Adam hates volleyball, so how did the coach ______ him to join the team?', a: 'succeed in persuading' },
      { q: 'My parents often allow me to go shopping by myself. (LET)', stem: 'My parents often ______ my own.', a: 'let me go shopping on' },
      { q: "Jane's parents wouldn't let her go to the party. (REFUSED)", stem: "Jane's parents ______ her to go to the party.", a: 'refused to allow' },
      { q: 'Seeing Pete sitting in the cafe was a real surprise. (EXPECTED)', stem: 'I really ______ Pete sitting in the cafe.', a: "hadn't expected to see" },
      { q: '（原书此题题干未收录在 PDF 中）', stem: '—', a: 'to discourage her from eating' },
      { q: "The teacher didn't think about the fact that it might rain when she planned the school trip. (ACCOUNT)", stem: 'The teacher failed ______ the fact that it might rain when she planned the school trip.', a: 'to take into account / to take account of' },
      { q: 'Jack does not want to work for his uncle any longer. (CARRY)', stem: "Jack doesn't want ______ for his uncle.", a: 'to carry on working' },
      { q: "The waiter carried the tray very carefully so that he wouldn't spill any of the drinks. (AVOID)", stem: 'The waiter carried the tray very carefully so as ______ any of the drinks.', a: 'to avoid spilling' },
      { q: "I wasn't able to get to the airport on time because of the bad weather. (PREVENTED)", stem: 'The bad weather ______ to the airport on time.', a: 'prevented me from getting' },
      { q: 'I promised that I would think carefully about the job offer. (GIVE)', stem: 'I promised ______ the job offer.', a: 'to give thought to' },
      { q: 'I read only the first three chapters of the book because it was boring. (GAVE)', stem: 'I ______ the book after the first three chapters because it was so boring.', a: 'gave up reading' },
      { q: 'Sigmund accidentally left the door unlocked over the weekend. (MEAN)', stem: 'Sigmund ______ the door unlocked over the weekend.', a: "didn't mean to leave" },
      { q: 'Could you close the window, please? (MIND)', stem: 'Would ______ the window, please?', a: 'you mind closing' },
      { q: 'Jo has stopped drinking coffee. (UP)', stem: 'Jo ______ coffee.', a: 'has given up drinking' },
      { q: "'Shall we go to the cinema?' said Maisie. (SUGGESTED)", stem: 'Maisie ______ the cinema.', a: 'suggested going to / we (should) go' },
    ],
  },
  // ======================= 被动语态 ======================= //
  {
    day: 12,
    title: '被动语态 Passive Voice (1)',
    kind: 'lesson',
    sections: [
      {
        name: 'A. 被动语态',
        points: [
          {
            rule: '基本形式：be done，意义：…被做。用被动往往是为了突出动作本身，不强调谁发出的动作',
            examples: [
              ['The bridge was built in 1990.', '这座桥建于 1990 年。'],
              ['The bridge was built in 1990 by the local villagers.', '要表明动作发出者时用 by。'],
            ],
          },
        ],
      },
      {
        name: 'B. 常考时态被动语态表',
        table: {
          head: ['时态', '构成', '例句'],
          rows: [
            ['一般现在时', 'am / is / are + done', 'The room is cleaned every day.'],
            ['一般过去时', 'was / were + done', 'The room was cleaned yesterday.'],
            ['一般将来时', 'will be + done / be going to be + done', 'The room will be cleaned tomorrow.'],
            ['现在进行时', 'am / is / are + being + done', 'The room is being cleaned now.'],
            ['过去进行时', 'was / were + being + done', 'The room was being cleaned at that moment.'],
            ['现在完成时', 'have / has been + done', 'The room has already been cleaned.'],
            ['过去完成时', 'had been + done', 'The room had been cleaned.'],
            ['将来完成时', 'will have been + done', 'The room will have been cleaned.'],
            ['情态动词', '情态动词 + be done', 'The room should be cleaned.'],
          ],
        },
        points: [
          {
            rule: '不同时态用不同形式的被动语态，但都是在基本形式 be done 上的转变',
          },
        ],
      },
    ],
  },
  {
    day: 13,
    title: '被动语态 Passive Voice (2)',
    kind: 'lesson',
    sections: [
      {
        name: 'C. 常考被动结构 1：have / get sth. done',
        points: [
          {
            rule: 'have sth. done / get sth. done 让别人为你做…',
            examples: [
              ['I had my hair cut yesterday.', '昨天我去剪头发了。（头发不是自己剪的）'],
              ['I will have my computer repaired tomorrow.', '我明天要去修电脑。（让别人修）'],
            ],
          },
          {
            rule: 'have sth. done 还可以表示遭受、不好的经历',
            examples: [['I had my watch stolen this morning.', '今天早上我的手表被偷了。']],
          },
        ],
      },
      {
        name: 'D. 常考被动结构 2：It is said that...',
        table: {
          head: ['时态', '主动', '被动 1', '被动 2'],
          rows: [
            ['一般现在时', 'People say she dances.', 'It is said that she dances.', 'She is said to dance.'],
            ['一般将来时', 'People say that she will dance.', 'It is said that she will dance.', 'She is said to dance.'],
            ['一般过去时', 'People say that she danced.', 'It is said that she danced.', 'She is said to have danced.'],
            ['现在完成时', 'People say that she has danced.', 'It is said that she has danced.', 'She is said to have danced.'],
            ['现在进行时', 'People say that she is dancing.', 'It is said that she is dancing.', 'She is said to be dancing.'],
          ],
        },
      },
      {
        name: 'E. 常考被动结构 3：主动表被动',
        points: [
          {
            rule: 'need doing sth. 需要被做（need 是实义动词，有人称和时态变化，后接 V-ing 用主动形式表被动）',
            examples: [
              ['My hair needs cutting.', '我需要理发了。'],
              ['The flowers need watering.', '这些花需要浇水了。'],
            ],
          },
          {
            rule: 'be worth doing sth. 值得一做，也是主动表被动',
            examples: [['The book is worth reading.', '这本书值得一读。']],
          },
        ],
      },
    ],
  },
  {
    day: 14,
    title: '直击考点 · 关键句型转换',
    kind: 'exercise',
    intro: '重点考被动语态各种结构（get sth done / is said to / need doing…）。',
    items: [
      { q: 'The bike is quite old so you should ask someone to check the brakes before you ride it. (get)', stem: 'This bike is quite old so you should ______ before you ride.', a: 'get the brakes checked' },
      { q: 'This computer package includes all the software. (INCLUDED)', stem: 'All the software ______ this computer package.', a: 'is included in' },
      { q: "My teacher let me leave the lesson early because I wasn't feeling well. (allowed)", stem: 'I ______ the lesson early because I wasn\'t feeling well.', a: 'was allowed to leave' },
      { q: 'They say that the new sports center is fantastic. (SAID)', stem: 'The new sports center ______ fantastic.', a: 'is said to be' },
      { q: "They didn't expect many people would come to the beach party because of the weather. (EXPECTED)", stem: 'Few people ______ up at the beach party because of the weather.', a: 'were expected to turn / show' },
      { q: "They didn't sell many programs at the match. (FEW)", stem: 'Very ______ at the match last Saturday.', a: 'few programs were sold' },
      { q: 'Clothing companies are selling an increasing number of goods on the internet. (BOUGHT)', stem: 'An increasing number of goods ______ clothing companies on the internet.', a: 'are being sold by' },
      { q: 'When Alex has finished his essay, a friend is going to check the spelling for him. (CHECKED)', stem: 'When Alex has finished his essay, he is going to ______ a friend.', a: 'have the spelling checked by' },
      { q: 'I have to charge my phone each day. (CHARGING)', stem: 'My phone ______ a day.', a: 'needs charging' },
      { q: 'There is no point in waiting for the bus. (WORTH)', stem: 'It ______ for the bus.', a: 'is not worth waiting' },
      { q: 'My friends are bringing the music for the party. (BEING)', stem: 'The music for the party ______ my friends.', a: 'is being brought by' },
      { q: 'Our local supermarket employs over 200 people. (ARE)', stem: 'Over 200 people ______ our local supermarket.', a: 'are employed by' },
      { q: "Farmers in the US grow a large proportion of the world's wheat. (IS)", stem: "A large proportion of the world's wheat ______ farmers in the US.", a: 'is grown by' },
      { q: 'My car is being repaired tomorrow. (HAVING)', stem: "I'm ______ tomorrow.", a: 'having my car repaired' },
      { q: "Someone stole Jane's purse while she was out. (HAD)", stem: 'Jane ______ while she was out.', a: 'had her purse stolen' },
    ],
  },
  // ======================= 情态动词 ======================= //
  {
    day: 15,
    title: '情态动词 Modal Verbs',
    kind: 'lesson',
    sections: [
      {
        name: 'A. be able to do vs can do',
        points: [
          {
            rule: 'be able to do 能够做某事，有各种时态变化，可与 can / could 互换；can 表示现在有能力，could 表示过去有能力',
            examples: [
              ['He was able to escape from the fire.', '他得以从火中逃生。'],
              ['I lost my mobile phone a few days ago, but I was able to find it.', '几天前手机丢了，但我又找到了。'],
            ],
          },
        ],
      },
      {
        name: 'B. 情态动词表示推测',
        points: [
          {
            rule: '对现在的推测：must be 一定是（强肯定）；can\'t / couldn\'t 不可能（强否定，注意不是 mustn\'t——mustn\'t 表示禁止）；may / might / could 可能（不确定肯定）；may not / might not 可能不（不确定否定）',
            examples: [
              ["It must be from Steven because he's in the USA.", '这一定是 Steven 寄来的，因为他在美国。'],
              ["It can't / couldn't be Steve because that's not his writing.", '这肯定不是 Steve，因为这不是他的字。'],
              ["The parcel may / might / could be from Dad's friend Tony.", '包裹可能是爸爸的朋友 Tony 寄来的。'],
              ["The parcel may not / might not be from Dad's friend Tony.", '包裹可能不是 Tony 寄来的。'],
            ],
          },
          {
            rule: '对过去的推测：情态动词 + have done。must have done 一定做了；can\'t / couldn\'t have done 一定没发生；could / may / might have done 可能已经做了；may / might not have done 可能没做',
            examples: [
              ['It must have rained last night.', '昨晚一定下雨了。（= I\'m sure it rained last night.）'],
              ["He can't / couldn't have got there yet.", '他不可能已经到了。'],
              ['He might / may / could have stopped for a few days on the way.', '他可能在路上耽搁了几天。'],
              ['She may not have finished her homework.', '她可能没完成作业。'],
            ],
          },
        ],
      },
      {
        name: 'C. 情态动词表示虚拟语气',
        points: [
          {
            rule: 'can / could / might have done 本可能做某事，但事实上没做',
            examples: [['He could have got hurt. But luckily, he was not injured.', '他本可能受伤的，但幸运的是没有。']],
          },
          {
            rule: "should have done = ought to have done 本应该做某事，事实上没做（考试经常考！！！）",
            examples: [['You should have remembered her birthday. She must be very angry now.', '你本该记住她的生日的。她现在一定很生气。']],
          },
          {
            rule: "needn't have done 本没必要做某事，但事实上做了；对比 didn't need to do 没必要做（也没做）",
            examples: [
              ["You needn't have cooked dinner for me. I ate on the train.", '你本没必要给我做晚饭的，我在火车上吃了。（饭已做好）'],
              ["We bought a take-away meal so I didn't need to cook.", '我们点了外卖，所以不用做饭了。（当时还没做饭）'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 16,
    title: '直击考点 · 关键句型转换',
    kind: 'exercise',
    intro: '重点考情态动词 + have done 的推测与虚拟。',
    items: [
      { q: "I'm sure Simon went home early because I can't see him anywhere. (MUST)", stem: "Simon ______ home early because I can't see him anywhere.", a: 'must have gone' },
      { q: "It's possible that George didn't get my text message. (may)", stem: 'George ______ my text message.', a: 'may not have got' },
      { q: 'I think I can come to your party after all. (SHOULD)', stem: 'I ______ to your party after all.', a: 'should be able to' },
      { q: "It's a pity Sarah didn't tell us which bus to take to her house. (OUGHT)", stem: 'Sarah ______ us which bus to take to her house.', a: 'ought to have told' },
      { q: 'It was impossible for me to know which road to follow. (NOT)', stem: 'I ______ known which road to follow.', a: 'could not / cannot have' },
      { q: "During the quiz, I couldn't think of the correct answer to the winning question. (COME)", stem: 'During the quiz, I was not ______ the correct answer to the winning question.', a: 'able to come up with' },
      { q: 'Mr. Bateman was wrong to say that John had lost my keys. (SHOULD)', stem: 'Mr. Bateman ______ that John had lost my keys.', a: "shouldn't have said" },
      { q: 'It is possible that the teachers didn\'t see you cheating on the test. (MAY)', stem: "The teachers ______ cheating on the test.", a: 'may not have seen you' },
      { q: 'Tammy realizes that it was wrong to be so rude to the waiter. (BEEN)', stem: 'Tammy realizes that ______ so rude to the waiter.', a: "she shouldn't have been" },
      { q: "The bus was supposed to take us to Assisi, but it didn't. (SHOULD)", stem: "The bus ______ us to Assisi, but it didn't.", a: 'should have taken' },
    ],
  },
  // ======================= 间接引语 ======================= //
  {
    day: 17,
    title: '间接引语 Reported Speech (1)',
    kind: 'lesson',
    sections: [
      {
        name: 'A. 人称、时态的变化',
        points: [
          {
            rule: '直接引语变间接引语：人称相应变化，时态倒退一个',
            examples: [
              ["I said to Lucas, 'You are not allowed to speak in class.'（直接引语）", '—'],
              ["I said to Lucas that he wasn't allowed to speak in class.（间接引语）", '—'],
              ["He said, 'I haven't seen another boat.'", "He said he hadn't seen another boat."],
            ],
          },
        ],
      },
      {
        name: 'B. 时态变化原则：倒退一个时态',
        table: {
          head: ['直接引语', '间接引语'],
          rows: [
            ['一般现在时（I do）', '一般过去时（I did）'],
            ['一般过去时（I did）', '过去完成时（I had done）'],
            ['现在进行时（I am doing）', '过去进行时（I was doing）'],
            ['过去进行时（I was doing）', '过去完成进行时（I had been doing）'],
            ['现在完成时（I have done）', '过去完成时（I had done）'],
            ['一般将来时（I will do）', '过去将来时（I would do）'],
            ['将来完成时（I will have done）', '过去完成将来时（I would have done）'],
            ['情态动词（I can / may / must）', 'I could / might / had to'],
            ['过去完成时（I had done）', '不变（I had done）'],
            ['should / ought to / might / could', '不变'],
            ['must（表"必须"）', 'had to'],
          ],
        },
      },
    ],
  },
  {
    day: 18,
    title: '间接引语 Reported Speech (2)',
    kind: 'lesson',
    sections: [
      {
        name: 'C. 语序变化',
        points: [
          {
            rule: '疑问句变间接引语要用陈述语序（主语在前、谓语在后）；一般疑问句添加 if / whether',
            examples: [
              ["'What's the weather like?' she asked him. → She asked him what the weather was like.", '特殊疑问句：陈述语序。'],
              ["'Can you help me?' he asked. → He asked (me) whether I could help him.", '一般疑问句：加 whether / if。'],
            ],
          },
        ],
      },
      {
        name: '时间状语、地点状语变化',
        table: {
          head: ['直接引语', '间接引语'],
          rows: [
            ['tonight', 'that night'],
            ['last month', 'previous month / the month before'],
            ['yesterday', 'the day before'],
            ['three weeks ago', 'three weeks before'],
            ['next year', 'the next year / the following year'],
            ['tomorrow', 'the next day'],
            ['here', 'there'],
            ['this / these', 'that / those'],
            ['now', 'then / at that time'],
          ],
        },
      },
      {
        name: '常考转述动词搭配',
        table: {
          head: ['搭配', '含义'],
          rows: [
            ['ask / order / remind / advise / tell / warn sb. to do sth.', '转述动词 + sb + to do'],
            ['offer / refuse / threaten / promise / agree to do sth.', '转述动词 + to do'],
            ['apologize (to sb.) for doing sth.', '道歉'],
            ['accuse sb. of doing sth.', '指控'],
            ['deny / admit / regret doing sth.', '否认 / 承认 / 后悔'],
            ['suggest / recommend doing sth.', '建议'],
          ],
        },
        points: [
          {
            rule: "变成间接引语也可以直接使用转述动词 + to do / doing",
            examples: [
              ["'Don't touch the wire!' he exclaimed. → He warned me not to touch the wire.", '否定：warn sb. not to do。'],
            ],
          },
        ],
      },
    ],
  },
  {
    day: 19,
    title: '直击考点 · 关键句型转换',
    kind: 'exercise',
    intro: '重点考间接引语（时态倒退、陈述语序、转述动词搭配）。',
    items: [
      { q: "'I'm sorry I didn't do my homework,' said Maria. (NOT)", stem: 'Maria apologized ______ her homework.', a: 'for not having done' },
      { q: 'The teacher asked me whether I was interested in history. (FIND)', stem: "'Do ______?' the teacher asked me.", a: 'you find history interesting' },
      { q: "'All your complaints will be investigated by my staff tomorrow,' said the bank manager. (LOOK)", stem: 'The bank manager promised that his staff ______ all our complaints the next day.', a: 'would look into' },
      { q: "'Last week, I unexpectedly met an old friend on the train,' said the man. (RUN)", stem: 'The man said that ______ an old friend on the train unexpectedly last week.', a: 'he had run into' },
      { q: "'There's been a rise of over ten percent in the price of the tickets,' said Sue. (GONE)", stem: 'Sue said that the price of the tickets ______ than ten percent this year.', a: 'had gone up more' },
      { q: "Last Saturday my friend asked me, 'Do you want to see a film tonight?' (WHETHER)", stem: 'Last Saturday my friend asked me ______ a film that night.', a: 'whether I wanted to see' },
      { q: "'Can I take the flowers from your window display?' Carole asked the shopkeeper. (IF)", stem: 'Carole asked the shopkeeper ______ take the flowers from the window display.', a: 'if she could' },
      { q: "'When did you get back from your holiday in Morocco, Clara?' asked Jim. (ASKED)", stem: 'Jim ______ got back from her holiday in Morocco.', a: 'asked Clara when she had' },
      { q: "'I didn't break the key,' said Elena. (DENIED)", stem: 'Elena ______ the key.', a: 'denied breaking / denied she had broken' },
      { q: "'Don't go near that dog! It bites people,' the woman said to me. (WARNED)", stem: 'The woman ______ near that dog because it bit people.', a: 'warned me not to go' },
      { q: "'I must get a new battery for my discman,' said Fiona. (HAD)", stem: 'Fiona ______ get a new battery for her discman.', a: 'said she had to' },
      { q: "'Don't forget that you have to post that important letter,' my mother said. (REMINDED)", stem: 'My mother ______ to post that important letter.', a: 'reminded me that I had' },
      { q: "'Why didn't you invite me to the dance, John?' asked Sue. (INVITED)", stem: 'Sue asked John why ______ to the dance.', a: "he hadn't invited her" },
      { q: "'It was me that ate the last piece of cake,' Sam said. (ADMITTED)", stem: 'Sam ______ the last piece of cake.', a: 'admitted eating / admitted that he had eaten' },
      { q: "'You should come swimming with me after school,' Tracy said to me. (TO)", stem: 'Tracy said that ______ swimming with her after school.', a: 'I ought to go / should go' },
    ],
  },
]

// 语用常考词组与句型（原书 120 条，编号 61 原文缺失，此处顺序保留）
export const FCE_PHRASES = [
  ['According to many people', '据很多人讲'],
  ['advise sb. to do', '建议某人做某事'],
  ['allow sb. to do = let sb. do sth.', '允许某人做某事'],
  ['a number of + 可数名词复数', '大量的'],
  ['an amount of + 不可数名词', '大量的'],
  ['appeal to = attract', '吸引'],
  ['As far as I am concerned', '在我看来'],
  ['as long as + 句子', '只要 = provided / providing that'],
  ['associate A with B', '把 A、B 联系起来'],
  ['at the height of ...', '在…的鼎盛时期'],
  ['at the same rate / speed', '同样的速度'],
  ['at the same time', '与此同时'],
  ['at the turn of the 20th century', '在 19、20 世纪之交'],
  ["at one's disposal", '任某人支配'],
  ['be asleep = drop off', '睡着了'],
  ['be away from', '远离'],
  ['be bound to do sth.', '肯定会做…'],
  ['be charged with', '被指控犯了…罪行'],
  ['be disposed of', '被清除掉'],
  ['be due to do sth.', '预定，预期'],
  ['be in the mood to do sth.', '有心情做某事'],
  ['be knowledgeable about', '在…方面有知识，了解…'],
  ['be likely to do = it is possible / probable that ... will happen', '可能会发生…'],
  ['be unlikely to do sth.', '不大可能做某事'],
  ['be allowed to do sth. = let sb. do sth.', '允许某人做某事'],
  ['be opposed to sth. = be not in favor of sth.', '不支持'],
  ['be referred to as = be known as', '被叫做'],
  ['be responsible for sth.', '对…负责'],
  ['be willing to do sth.', '愿意做某事'],
  ['be worth doing sth.', '值得做某事'],
  ['blame sb. for doing sth.', '批评某人做某事'],
  ['bring about changes', '带来变化'],
  ['cope with = handle', '处理'],
  ['come as a surprise to sb.', '让某人感到惊讶'],
  ['come to / arrive at / reach a conclusion', '得出结论'],
  ['contact sb. = get / stay / keep in touch with sb.', '联系某人'],
  ['cut down on food = reduce the food', '减少'],
  ['cut out food = stop eating the food', '戒掉'],
  ['daydream about sth.', '做白日梦'],
  ["decide to do = make up one's mind to do sth.", '下定决心做某事'],
  ['deny doing sth.', '否认做了某事'],
  ['despite the fact that + 句子', '尽管 = in spite of the fact that + 句子'],
  ['dress up as ...', '装扮成…'],
  ['drop off', '辍学，退出，打盹儿'],
  ['expect sb. to do sth.', '期待某人做某事'],
  ['fall out = have an argument', '吵架'],
  ['feel at home', '感觉自在、舒服'],
  ['find it difficult to do = have difficulty doing sth.', '感到做…很困难'],
  ['find sth. + adj.', '感到某事怎么样'],
  ['force sb. to do sth.', '强迫某人做某事'],
  ['from an educational point of view', '从教育的角度'],
  ['get a degree', '得到学位'],
  ["get on one's nerves = irritate sb.", '惹某人生气'],
  ['get sth. started', '开始做某事'],
  ['go on a diet', '节食'],
  ['have difficulty in doing sth.', '做…有困难'],
  ['have sth. done（遭受）', 'have her purse stolen 钱包被偷'],
  ['have trouble in doing sth. = have trouble with sth.', '做…有困难'],
  ['have access to', '可以接触到…，有权利使用…'],
  ['He is bound to be late. = It is certain that he will be late.', '他肯定会迟到。'],
  ['head for + 地方', '驶向某个地方'],
  ['hold-up (n.)', '耽搁，延误'],
  ['in any one year = in any given year', '任何一年'],
  ['in fact = in reality', '事实上'],
  ['in his case', '就他而言'],
  ['in order to do', '为了做某事'],
  ['in other words', '换句话说'],
  ['in spite of = despite + n. / v-ing', '尽管'],
  ['in time', '及时'],
  ['It took a few more years to do sth.', '花时间做某事'],
  ["It wasn't until 1940s that ...", '直到…才（强调句）'],
  ["It's high time you learnt some manners.", '你该学学规矩。（It\'s time + 一般过去时句子）'],
  ['lack of experience', '缺乏经验'],
  ['lead to = result in', '导致'],
  ['learn a great deal about the world', '了解很多关于世界的事情'],
  ['let sb. off', '从轻处罚某人'],
  ['let sb. / sth. in', '让…进来（let the fresh air in / let me in）'],
  ['make a discovery', '发现'],
  ['manage to do = succeed in doing sth.', '成功做某事'],
  ['mass production', '批量生产，大规模生产（固定搭配）'],
  ['mind doing sth.', '介意做某事'],
  ["needn't have done", '本没必要做某事'],
  ['object to doing sth.', '反对做某事'],
  ['on the coast of', '在…海岸'],
  ['on the contrary', '相反'],
  ['one ... another', '一个，还有一个'],
  ['other than = except', '除了…之外（The form cannot be signed by anyone other than yourself.）'],
  ['over the last 20 years', '在过去的 20 年'],
  ['pay off', '得到回报（Their efforts seem to be paying off.）'],
  ['play an important part / role in our daily lives', '在日常生活中扮演重要的角色'],
  ['prevent sb. from doing sth.', '阻止某人做某事'],
  ['put sb. off sth. / doing sth.', '阻止某人做某事'],
  ['protect A from sth.', '保护 A 不受到某事的侵害'],
  ['provide sb. with sth.', '给某人提供某物'],
  ['rather than = instead of', '而不是'],
  ['regard sth. as + n. / adj.', '把某物视作…'],
  ['regret doing sth.', '后悔做了某事'],
  ['remind sb. that + 句子 / remind sb. to do sth. = remind sb. of sth.', '提醒某人做某事'],
  ['remove A from B', '把 A 从 B 那里清除掉'],
  ['set up a company', '建立公司'],
  ['settle down', '定居，稳定下来'],
  ['should do = be supposed to do sth.', '应该做某事'],
  ['should have done', '本应该做某事'],
  ["shouldn't have done", '本不应该做某事'],
  ['since then', '从那以后（完成时标志）'],
  ['so far', '到目前为止'],
  ['stop doing sth. = give up doing sth.', '停止做某事'],
  ['stop sth. (from) deteriorating', '阻止破坏/恶化'],
  ['suggest doing sth. = suggest sb. (should) do sth.', '建议做某事'],
  ['take note of sth. = pay attention to sth.', '关注，留意'],
  ['take off', '起飞；（事业）获得成功（The packaging revolution really took off.）'],
  ['take up a sport', '从事运动'],
  ['tell sb. off for doing sth.', '责备某人做某事'],
  ['turn up = show up = appear', '出现，到达'],
  ['the great majority of ...', '大多数…'],
  ['throw away', '扔掉'],
  ['to tell you the truth = to be honest', '说实话'],
  ['turn on (the radio)', '打开（收音机）'],
  ['turn to sb. for help = ask sb. for help', '向某人寻求帮助'],
]

// 直击考点 · 选词填空（答案与解析来自原书第 66 页）
export const FCE_CLOZE = [
  { q: 'When Phuket in Thailand first became a popular tourist destination, people there were unable to ______ with the increase in rubbish that 2 million visitors a year produce.', options: ['a. handle', 'b. treat', 'c. cope', 'd. check'], a: 'c', note: 'cope with 处理' },
  { q: 'New hotels in India caused a huge increase in water consumption, ______ many local people to walk considerable distance to get clean water.', options: ['a. forcing', 'b. making', 'c. encouraging', 'd. urging'], a: 'a', note: 'force sb. to do sth. 迫使某人做某事' },
  { q: 'Some forty years later, however, the packaging revolution really ______ when companies making the cans stopped using tin.', options: ['a. set out', 'b. burst in', 'c. showed up', 'd. took off'], a: 'd', note: 'take off（事业）成功' },
  { q: 'I have a particular area which I am ______ for.', options: ['a. dependable', 'b. reliable', 'c. sensible', 'd. responsible'], a: 'd', note: 'be responsible for sth. 对…负责' },
  { q: 'This record has been broken on many ______.', options: ['a. time', 'b. times', 'c. occasions', 'd. incidents'], a: 'c', note: 'on many occasions 在很多场合' },
  { q: 'The majority of children ______ an effort to save for the future.', options: ['a. make', 'b. do', 'c. have', 'd. try'], a: 'a', note: 'make an effort to do sth. 努力做某事' },
  { q: 'The 13-year-olds who took ______ in the survey seem to respond to the situation by saving more than half of their cash.', options: ['a. part', 'b. place', 'c. share', 'd. piece'], a: 'a', note: 'take part in 参加（活动）' },
  { q: 'Researchers think fans of shooter games are better than non-players when it ______ to building trust and cooperation.', options: ['a. requires', 'b. goes', 'c. involves', 'd. comes'], a: 'd', note: 'when it comes to 当谈到…时' },
  { q: 'Each year a considerable ______ of competitors have to retire from the race owing to exhaustion or coldness.', options: ['a. amount', 'b. sum', 'c. total', 'd. number'], a: 'd', note: 'a considerable number of + 可数名词复数，大量的' },
  { q: 'To achieve this, we need to make sure everyone has ______ to the services and facilities they need.', options: ['a. opening', 'b. contact', 'c. access', 'd. touch'], a: 'c', note: 'have access to 可接触到' },
]
