# Plotwell

Python 3.10以降、標準ライブラリだけで動く物語生成CLIです。OpenAI互換APIを使用し、既定の接続先は http://127.0.0.1:9931 です。各生成工程でJSON Schema出力が必要です。

```powershell
Copy-Item .env.example .env
python -m plotwell examples/plot.json
python -m unittest discover -s tests -v
```

環境変数は .env より優先し、--endpoint・--model はさらに優先します。モデル未指定ならモデル一覧の先頭を使用します。

## 入出力

plotの構造は [設計書](story_generation_design.md) と [入力例](examples/plot.json) を参照してください。

- language：本文と最終タイトルの言語。省略時は Japanese。null・空文字は不可。
- target_turns：作品全体の目標ターン数。旧turn_budgetから改名。省略・nullならLLMが1〜100で提案します。
- title・premise：省略・null可。希望タイトルは最終工程で尊重し、必要なら指定言語へ適応します。
- milestones：省略・空配列可。全件必須・記載順で章とシーンに割り当てます。
- characters：narratorと1人以上の人物が必要です。

出力storyはtitle・synopsis・target_turns・chaptersを持ちます。構成情報（synopsis、時間、場所）は英語、最終タイトルと本文（speech・thought・narration）は指定言語です。人物識別子は入力のまま保持します。

人物のターンにはspeechとthoughtの両方を空でない文字列として必須にします。null・空文字・空白だけの値は認めません。speechは言葉だけでなく、人物が口から発する非言語の音も含みます。描写だけのターンではspeech・thoughtをnullにします。旧原稿で人物のspeechまたはthoughtが欠ける場合は、原稿を再生成してください。

章の目標値合計は作品の目標値、シーンの合計は章の目標値に一致させます。実際のターン数の逸脱は許容し、目標値を上書きしません。LLM_MAX_TOKENSはAPI応答のトークン上限で、ターン数とは別です。

## 生成工程

すべての処理指示と再試行指示を英語で行います。

| 工程 | 役割・生成対象 |
| --- | --- |
| 1 | 物語構築の専門家。アイデアから作品・章の英語概要、仮題、目標ターン配分を生成。scenesは空配列 |
| 2 | プロの作家。1章につき1回、英語でシーンを設計。turnsは空配列 |
| 3 | プロの劇脚本家。1シーンにつき1回、英語で会話・心情・地の文を生成。目標ターン数への一致を促すが、不一致だけでは再生成しない |
| 4 | キャラクターを演じる劇役者。人物別・シーン一括でspeech・thoughtを指定言語で表現 |
| 5 | プロの作家。演技済みシーンを基に、指定言語で地の文を整える。前シーンまでの継続要約も参照 |
| 6 | プロの作家。作品・章のsynopsis、指定言語、希望タイトルだけから、作品・全章の最終タイトルを一括生成 |

旧予算再配分とターン数編集は廃止しました。工程4以降は実際のターン数・順序・話者を保持します。工程5は地の文だけ、工程6はタイトルだけを更新します。地の文は発言前に配置します。描写だけのターンも1ターンに数えます。

イベントの割当、構成の空配列、出力形式はコードでも検証します。意味の実現、言語品質、意味上の整合性はモデルに依存します。形式違いは指定回数内で対象工程だけを再生成します。応答上限では同じ要求を再送せず停止します。

## 原稿の保存と再利用

```powershell
python -m plotwell examples/multi_scene.json --stop-after narrator
python -m plotwell --from-narrator data/<共通名>.log
```

--stop-after narratorは工程3までの英語原稿を保存します。再利用では工程4〜6を実行します。演技は人物別・シーン一括のA方式に統一しました。--acting-mode Aは指定可能ですが、旧B方式は廃止しました。

checkpointはversion 4です。SHA-256で原稿と資料を照合し、複製を加工します。version 2・3は元のチェックサムと原稿ハッシュを検証してから移行します。旧turn_budgetを改名し、作品synopsisがない場合は章のsynopsisを順に連結します。version 2の旧opening・endingは除去します。旧原稿の本文・構成情報は元の言語のまま再利用します。

## 人物の認識と知識

characters[B].how_perceived[A]はAから見たBです。本人はselfだけ、他者は自分の名前のキーを優先し、個別キーがない場合だけothersを使います。個別値がnullでもothersは読みません。後から知った事実は初期認識より優先します。

backgroundは構成・脚本担当だけに渡します。演技では他者のthoughtを除き、自分の過去のthoughtと作中で得た事実を参照します。在席・観測・知識取得の内部情報は工程3に同梱し、別のAPI呼出しは行いません。

同一シーンの全役者は共通の元脚本を参照し、他者の生成順に依存しません。シーン間の記憶には確定済み演技を使います。直近12発言・自分の8思考を保持します。未来の脚本資料と人物の現在の知識は区別します。

## 出力とログ

既定でdata/にUTCのyyyymmdd-hhmmss-msZ-uniqueを共通名として保存します。

- .log：入力JSON、API要求・応答、検証失敗、イベント割当、原稿、checkpoint、演技差分、工程結果、例外、使用量を記録。長い設定文は$input参照で重複を省きます。
- .json：成功時だけ最終storyを保存。--stop-after narratorの場合は工程3時点の原稿です。

run_complete.total_turnsは実数、target_turnsは目標値です。llm_usageとrun_completeにAPI使用量を記録します。失敗時は終了コード1、中断時は130。ログには人物の背景や秘密を含みますが、APIキーは記録しません。
