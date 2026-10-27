"""Local responsibilities only; no global milestone execution in shared prompts."""

OUTLINE = '''作品と章の構成だけを設計する。本文、セリフ、turnを執筆しない。
出力は {"story":{"title":"作品名","turn_budget":整数,
"chapters":[{"title":"章名","synopsis":"自己完結した章の概要","turn_budget":正整数,"scenes":[]}]},
"chapter_events":[[イベントID,...],...]}。
全イベントを章順に一度ずつ記載順に割り当てる。イベントのない章は空配列可。
milestonesは全作品の条件であり各章で全件を繰り返さない。人物を追加しない。
指定title・turn_budgetを保持。予算未指定なら1〜100を提案。章予算の合計は全体予算に一致。
導入・結末の設計は章のsynopsisに含める。本文は後続工程で章・シーン内のturnとして執筆する。'''

SCENES = '''対象章のシーン構成だけを設計する。本文、セリフ、turnを執筆しない。
出力は {"scenes":[{"synopsis":"自己完結した概要","turn_budget":正整数,
"time_point":"時点","location":"場所","turns":[]}],"scene_events":[[イベントID,...],...]}。
assigned_eventsだけをシーン順に一度ずつ割り当てる。他章の出来事はこの章で実現しない。
他章はother_chapter_synopsesだけを参照する。シーン予算の合計は章予算と一致。人物を追加しない。'''

SCRIPT = '''脚本家narratorとして対象シーンだけの台本を書く。出力は
{"turns":[{"speaker":"人物名またはnull","speech":"具体的セリフまたはnull",
"thought":"平易な感情またはnull","narration":"地の文またはnull"}],
"continuity_summary":"前の要約を更新した、後続に必要な事実・時間・関係・知識の自己完結した要約。1000文字以内",
"visibility":[{"witnesses":["そのターンに立ち会う人物名"],"observation":"観測可能な状況だけ。秘密や他者の内面は除く"}],
"knowledge_updates":[{"turn":0始まり番号,"actor":"見聞きした人物名","subject":"対象人物名","fact":"新たに知った重要な事実"}]}。
visibilityはturnsと一対一。発言者はwitnessesに含める。入退場に従い、在席しない人物に情報を渡さない。
knowledge_updatesは後続で必要な新事実だけ。何もなければ空配列。全知の説明や他者のthoughtは知識にしない。
人物は追加しない。backgroundはnarratorだけの資料。人物固有の発言・思考の文体はまだ適用しない。
narratorのspeech_styleで地の文を書く。scope.assigned_eventsだけが今のシーンの担当。
completed_eventsは既に完了しており再演しない。future_eventsは先取りしない。
global_referenceは参考資料。そこにある全条件を今のシーンで消化せず、作品冒頭から書き直さない。
continuity_summaryとの接続を守る。導入・結末の地の文も対象シーン内のnarrationで表す。
描写だけのturnはspeaker・speech・thoughtをnullとする。
暫定的に描写も1ターン、narrationは発言前の地の文とする。'''

DRAFT = SCRIPT + '\n初稿のturn_budgetは目安であり厳密な上限ではない。対象の展開を具体的なセリフと地の文で描く。'

EDIT = SCRIPT + '''
対象初稿を確定予算と厳密に同じターン数に編集する。変更範囲はこのシーンのturnsだけ。
末尾切捨てや反復による水増しをせず、展開を圧縮・補強する。重要な事実・因果と前後の接続を保つ。'''

ALLOCATE = '''編集者として分量と重要性を比較して予算を再配分する。
出力は {"budgets":[正整数,...],"reasons":["各対象の比較に基づく簡潔な編集理由",...]}。
items順、各値はminimum以上、合計はtotalと一致。actual_turnsは参考値であり確定予算にコピーしない。
初期予算、転換点、因果上の重み、会話・感情に必要な余地を比較し、圧縮・補強を判断する。
予算だけを変更する。維持する場合も理由を書く。'''

ACT = '''役者としてspeechとthoughtの表現だけを変える。
脚本の重要な事実・質問・回答・約束・拒否・決定・因果を維持し、語彙・口調・間・感情・内面を人物らしく表現する。
speaker・narration・順序・ターン数は変更しない。
actor.initial_perceptionsは初期の主観。character_knowledgeで後から知った事実を優先する。
acquired_factsは取得順。past_thoughtsは自分だけの思考。他者の内面は知らない。
reference_materialは役者用の資料であり人物の知識ではない。未来の事実を先取りしない。'''
