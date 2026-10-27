# ストーリー構成・台本生成プログラム 設計方針

## 基本方針

ユーザーのアイデアと人物設定を基に、作品・章・シーン・ターンの順で具体化する。英語で物語構成と脚本を作り、指定言語で演技、地の文、タイトルを仕上げる。すべてのLLMへの処理指示は英語で行い、入力文章は素材として扱う。人物設定と指定条件を尊重し、未指定部分は創作する。

1ターンは発言とそれに付随する心情・地の文を基本単位とする。地の文だけのターンも1ターンに数える。目標ターン数への一致を促すが、不一致を許容する。予算再配分・ターン数を合わせる編集工程は設けない。

## 入力plot

```python
plot = {
    'title': None,
    'premise': None,
    'language': 'Japanese',
    'target_turns': None,
    'milestones': [],
    'characters': {
        'narrator': {'speech_style': None},
        'character name': {
            'speech_style': None,
            'thought_style': None,
            'background': None,
            'how_perceived': {'self': None, 'others': None, 'other character name': None},
        },
    },
}
```

titleは希望タイトル、premiseは前提・初期状況・世界観である。languageは最終本文とタイトルの言語で、省略時はJapanese。明示的なnull・空文字は認めない。target_turnsは正整数の目標値で、旧turn_budgetから改名。未指定・nullなら工程1で1〜100を提案する。milestonesは全件必須・記載順とする。

charactersにはnarratorと1人以上の人物を必要とする。人物識別子は変換せず保持する。speech_styleは発言または語り手の文体、thought_styleは内面表現の文体、backgroundは作者だけが参照する背景・秘密である。

## 出力story

```python
story = {'title': None, 'synopsis': None, 'target_turns': None, 'chapters': []}
chapter = {'title': None, 'synopsis': None, 'target_turns': None, 'scenes': []}
scene = {'synopsis': None, 'target_turns': None, 'time_point': None, 'location': None, 'turns': []}
turn = {'speaker': None, 'speech': None, 'thought': None, 'narration': None}
```

各階層のsynopsisは英語の自己完結した概要とする。作品synopsisは結末を含む全体の展開と因果関係を示す設計資料であり、読者向け紹介文ではない。time_point・locationも英語とする。

最終titleとspeech・thought・narrationはplot.languageで書く。工程1の作品・章タイトルは英語の仮題で、最終工程で更新する。指定タイトルは最終工程で尊重し、必要に応じ指定言語へ適応する。

人物のターンではspeech・thoughtを必ず両方含め、それぞれ空でない文字列とする。null・空文字・空白だけの値は認めない。speechには言葉と、人物が口から発する非言語の音を含める。脚本生成と演技の指示では音の具体例を列挙せず、場面に応じて直接speechに表す。すべてのターンへ非言語の音を付ける指示にはしない。描写だけのターンは引き続きspeech・thoughtをnullにする。

章の目標値合計は作品の目標値、シーンの合計は章の目標値に一致させる。実ターン数はlen(scene['turns'])で取得し、目標値を上書きしない。APIのmax_tokensは応答トークン上限で、目標ターン数とは独立である。

導入・結末の設計はsynopsisに含め、本文はシーン内のnarrationで表す。独立したopening・endingは設けない。描写だけのターンはspeaker・speech・thoughtをnullにする。narrationは発言前の地の文として扱う。

## 生成工程

| 工程 | 役割と処理 |
| --- | --- |
| 1 | 物語構築の専門家として、英語で作品・章の仮題とsynopsis、目標ターン配分を生成。各章のscenesは空配列。イベントを章順に一度ずつ割当 |
| 2 | プロの作家として、対象の第i章を構成するシーンを英語で生成。1章につき1回。作品synopsis・対象章・他章synopsisを参照。turnsは空配列。担当イベントだけを割当 |
| 3 | プロの劇脚本家として、1シーンにつき1回、具体的会話・心情・地の文を英語で生成。目標数と一致させるよう指示するが、不一致を許容。人物固有の文体はまだ適用しない |
| 4 | プロの劇役者として、人物別・シーン一括で担当speech・thoughtを指定言語で表現。人物の文体、時点ごとの知識・認識を適用 |
| 5 | プロの作家として、演技済みシーン・元の地の文・語り手の文体・前シーンまでの継続要約を参照し、指定言語でnarrationだけを更新。1シーンにつき1回 |
| 6 | プロの作家として、作品synopsis・順序付き章synopsis・出力言語・希望タイトルだけから、作品と全章の最終タイトルを一括生成。本文は渡さない |

工程3は作品・対象章のsynopsis、現在のシーン、過去の継続要約、担当・完了・未来イベントを参照する。完了イベントを再演せず、未来イベントを先取りしない。継続要約（1000文字以内）、各ターンの在席・観測情報、重要な新規知識を同じ応答に含める。在席情報はターンと一対一で発言者を含め、知識更新は実際の目撃者が知り得た事実に限る。

工程4は重要な事実・質問・回答・約束・拒否・決定・因果・情報開示時点を保持する。話者・地の文・順序・ターン数は変更せず、speech・thoughtの両方を必須とする。同一シーンでは全員が共通の工程3の原稿を参照し、他者の演技の生成順に依存しない。シーン間の記憶には確定済み演技を使う。

工程5は会話・心情と矛盾せず、元の出来事・動作・時間・場所を維持する。新たな出来事や秘密の先行開示を加えない。話者・会話・心情・順序・ターン数を変更しない。地の文だけのターンは空にしない。英語の継続要約を同じ応答で更新する。

## 知識の非対称性

characters[B].how_perceived[A]はAから見たBを表す。本人はselfだけを参照し、他者は自分の名前のキーを優先する。キーがない場合だけothersを使い、個別値がnullでもothersは読まない。後から知った事実は初期認識より優先する。

backgroundは構成・脚本担当だけが参照し、役者に直接渡さない。他者のthoughtも除去する。脚本資料と現在の人物知識を区別し、未来の事実を先取りしない。直近12発言と自分の8思考を保持する。意味上の知識境界の精度はLLMに依存する。

## 検証と再利用

形式、空配列、イベント割当、目標値合計、演技担当インデックス、地の文・タイトル出力件数を検証する。意味・言語品質は生成指示に依存し、そのための追加LLM呼出しは設けない。通常は1対象につき1応答で、形式違い時だけ回数制限付きで再試行する。

工程3直後の原稿と内部情報をcheckpoint version 4として保存する。再利用では工程4〜6だけを実行する。チェックサムと原稿ハッシュを照合し、元のcheckpointを変更しない。

旧version 2・3は元のハッシュ検査後にtarget_turnsへ改名する。作品synopsisがない場合は章のsynopsisを順に連結し、version 2の旧opening・endingを除去する。旧本文と構成情報の言語は保持する。新規plot入力はtarget_turnsを使用する。
