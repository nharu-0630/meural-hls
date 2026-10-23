# meural-hls (日本語)

[English](README.md) | 日本語

**Meural Canvas (MC321) の画面いっぱいにライブ動画を表示する — Macから配信、端末のディスク書込みゼロ、クラウド不使用、ファーム無改変。**

Macが映像ソース (実HLSの `.m3u8` URL、または内蔵のアニメデモ) を2秒区切りのMPEG-TSセグメントにエンコードし、HTTPで端末内の小さなingestサーバへ投入します。サーバはセグメントを**端末のRAM上のみ**に保持し (フラッシュには何も書かない)、端末の純正 `mplayer` が `http://127.0.0.1:8484/live.ts` のシームレスな無限TSストリームをフレームバッファ (`/dev/fb0`) にネイティブ1920x1080で描画し続けます。watchdogが自動復旧を担います。

検証済み: 実時間 (=PTS) 同步、15fps、デコードエラーゼロ、フレームバッファ差分で動作確認。

```
Mac                                       端末 (Meural Canvas MC321)
─────────────────────────────────────     ──────────────────────────────────────
ffmpeg (1920x1080@15, 2Mbps 再エンコード)  ingest.py (python3, port 8484, RAMのみ)
   │  2秒MPEG-TSセグメント                    │  /seg?n=N ← セグメントがRAMバッファへ
   └─ HTTP POST ───────────────────────────► │  /live.ts ← 継ぎ目なし連続ストリーム
                                             │            (実時間ペース送出)
hls_push.py (pusher, リトライ対応)            ▼
                                          mplayer -vo fbdev2 → /dev/fb0 → パネル
                                          watch.sh (watchdog: 自動再起動)
```

## 特徴

- **実HLS対応** — `HLS_URL` に任意の `.m3u8` (ライブ/VOD両方) を設定すれば Mac が取得してパネル用に再エンコード。
- **端末に何も残さない** — 端末側2ファイルは `/tmp` に置くので再起動で消える。復活も1コマンド。
- **ファーム無改変** — 純正バイナリ (`python3`, `mplayer`) とRAM上のユーザランドスクリプトのみ。
- **自己修復** — watchdogでmplayer自動再起動、pusherは接続断でもリトライ、dripはソースビットレートに自動同期。
- **きれいに終わる** — `restore_gallery.py` で全停止し、通常のギャラリー表示へ復帰。

## 構成

```
mac/                     Mac側 (司令塔)
  hls_push.py            ffmpegエンコーダ + セグメントpusher (Macで常駐する唯一のプロセス)
  rtc_shell2.py          端末のローカル rtc コマンド (=リモートシェル) 用クライアント
  push_files.py          base64urlチャンクでファイルを端末RAMへ転送
  meural_api.py          ローカルremote-APIクライアント (identify / status / gallery / postcard...)
  deploy_live.py         ワンショット導入: 端末ファイル投入 + サービス起動
  restore_gallery.py     ライブモード停止 + ギャラリー復帰
device/                  端末側 (すべてRAM上で動作)
  ingest.py              :8484 のHTTPサーバ — セグメントをメモリ保持し /live.ts を配信
  watch.sh               mplayer watchdog (死んだら~2秒で自動再起動)
docs/NOTES.md            ハード/設計メモ: 罠と、各オプションをそう定めた理由
docs/NOTES.ja.md         同上 (日本語版)
```

## クイックスタート

必要環境:

- **Mac** (Linux/BSDでも可): Python 3.11以降、[uv](https://docs.astral.sh/uv/)、libx264入りの `ffmpeg`
- **端末**: 同一LAN上の Meural Canvas MC321、純正ファーム (ローカル `http://<端末IP>/remote/...` APIが有効)

```bash
uv sync --locked
export MEURAL_HOST=<端末のIP>

# 1) 一回だけ導入 (数KBのpythonを端末RAMへ投入してサービス起動)
uv run python mac/deploy_live.py

# 2) Macのpusherを起動 — 内蔵アニメデモソース
uv run python mac/hls_push.py

#    実HLSストリームの場合:
HLS_URL=https://example.invalid/path/live.m3u8 uv run python mac/hls_push.py

# 画面は約30秒以内にライブ映像へ切り替わります (最初のGOPがバッファを満たすまで)。
```

### 設定リファレンス

| 変数 | 対象 | 意味 |
|---|---|---|
| `MEURAL_HOST` | すべてのMac側スクリプト | 端末のIP |
| `HLS_URL` | `hls_push.py` | 実HLSソース。未設定=内蔵グラデーションデモ |
| `MEURAL_GALLERY_ID` | `restore_gallery.py` | 終了時に戻すギャラリーid |

### 端末上のファイル (すべてRAM)

| パス | 役割 |
|---|---|
| `/tmp/ingest.py` | ingestサーバ、:8484 をlisten (`/seg`, `/pl`, `/live.ts`, `/live.m3u8`) |
| `/tmp/watch.sh` | `mplayer` のwatchdogループ |
| `/tmp/mv.log` | mplayerログ (ステータス行に再生位置が出る) |

### 通常モードへの復帰

```bash
MEURAL_GALLERY_ID=<ギャラリーid> uv run python mac/restore_gallery.py
```

watchdogとplayerを停止 (端末側コマンド) した後、ローカルAPI
`/remote/control_command/change_gallery/<id>` を呼びます。端末は通常の
デジタルフレーム動作に戻ります。後片付けは不要 — RAM専用の構成要素は
次の再起動で消滅し、起動しない限り無害です。

## 仕組み — 設計のポイント

**RAMの中でシームレスな「無限ライブ」 (`device/ingest.py`)。** pusherは閉じた
(書込み完了した) セグメントだけを `/seg?n=<n>` へPOSTします。サーバは直近
~14セグメントをPythonバッファに保持し、接続中の全クライアントのキューにも
積みます。`GET /live.ts` は**終わりのないMPEG-TS**を返します: まずバッファ末尾
3セグメントを実時間ペースで送出し、以後はライブキューを継ぎ目なく流します。
セグメントのタイムスタンプが連続で、かつdrip速度が**ソースの実ビットレートに
自動追従**する (各セグメントを実時間の2秒かけて送る) ため、プレイヤは
飢えずも遅れません。

**再生 (`device/watch.sh`)。** 端末純正の mplayer 1.2 がループバックのストリームを:

```
-nocorrect-pts -framedrop -nosound -cache 4096 -cache-min 15 \
-demuxer lavf -vo fbdev2 http://127.0.0.1:8484/live.ts
```

で再生します。`-demuxer lavf` は**必須** (純正MPEG-TS demuxerは低ビットレートで
スタールします。docs/NOTES.md参照)。`fbdev2` は1080pフレームを `/dev/fb0` に
直書きするため、ソースは **正確に1920x1080** である必要があります。

**エンコーダ (`mac/hls_push.py`)。** x264 `superfast`、2Mbps、15fps、GOP 30、
**音声なし** (`-an`)、セグメント境界で連続タイムスタンプ。pusherは出力
ディレクトリを監視し、*書込み中でない* closedセグメントのみをPOSTし、
失敗時はリトライします。デバッグ用の最小HLSプレイリスト配信も持ちます。

**リモート実行 (`mac/rtc_shell2.py`)。** 導入には端末ローカルAPIの `rtc`
エンドポイント (引数をシェルコマンドとして評価する) を使います。payloadの
規約 (`/` と `:` をprintfオクタルでエスケープする等) はスクリプト内に実装済み、
詳細は docs/NOTES.md。

## 永続化とバックアップ

端末側は意図的にRAM専用です。このリポジトリが正規のバックアップです。
再起動後は:

```bash
uv run python mac/deploy_live.py     # ファイル再投入 + サービス再起動
uv run python mac/hls_push.py        # Mac pusher (実HLSなら HLS_URL=...)
```

端末内で再起動後も残したい場合は `/home/meural/` 等の永続領域にコピーして
`deploy_live.py` のパスを変えられますが、**RAM専用が一番安全です** (端末に
痕跡を残さない)。

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| 画像は出るのに静止している | `/tmp/mv.log` の `V:` 行が ~1秒/秒で進むか確認。`/dev/fb0` のダンプ差分方法はNOTES参照 |
| mplayerが `TS file format detected` から進まない | `-demuxer lavf` が無い |
| モザイク/ブロック状乱れ | コンテンツに対しビットレート不足 (1080pのなめらか映像は2Mbps目安) |
| 数分でプレイヤが死ぬ | `-vo fbdev2` 固定 (x11は長時間で落ちる) + `-demuxer lavf` で解決済み |
| rtc payloadが HTTP 500 | payloadにリテラル `/` や裸の `$()` がある — `push_files.py` のヘルパを使う |
| watchdogが再起動ループ | 先に停止: `pkill -f 'watch[.]sh'`、次に `pkill -9 -f 'mplaye[r]'` (別コマンドで実行) |

ハード固有の発見事項の全リストは **docs/NOTES.ja.md** (英語版は docs/NOTES.md)。

## 免責

このプロジェクトは**自分の所有する端末**を、純正ローカルAPIと純正バイナリ
だけでLAN内から操作します。ファームは一切改変せず、再起動で残骸も消えます。
端末の利用規約の遵守と表示内容の責任は利用者にあります。自己責任でお願いします。

## License

MIT

## 開発・検証

ホスト側は `pyproject.toml` と `uv.lock` で管理します。端末側の
`ingest.py` は標準ライブラリのみを使用し、Python 3.5の構文互換性を
テストしています。端末へのuvや追加パッケージのインストールは不要です。

```bash
uv sync --locked
uv run ruff check mac device tests
uv run ruff format --check mac device tests
uv run mypy
uv run python -m unittest discover -s tests -v
```

入力がH.264/yuv420p・1920x1080・15fps・2秒間隔のキーフレームである場合
（meural-signageの標準横画面出力など）は、`HLS_URL` と同時に
`HLS_COPY=1` を設定すると二重エンコードを省略できます。それ以外は既定の
再エンコードを使用してください。

pusherは実行ごとの一時ディレクトリとセッションIDを使用し、送信失敗した
セグメントから順に再試行します。プレイリストには送信成功済みのものだけを
掲載し、未送信の閉じたセグメントが30個を超えると停止します。端末側は
直近15セグメント、最大8視聴クライアント、各8個の待機キューに制限します。
セグメント上限は4MiB、プレイリスト上限は64KiBです。遅いクライアントは
切断し、watchdogに再接続させます。再送は重複再生せず、`/seg` の任意の
`session` パラメータが変わると旧状態を破棄します。未知のHTTPパスは404を返します。

`postman/simulations/ingest-retry.sim.yaml` は実装の契約に基づくローカル
検証用モックです。最初の6回の送信を503にする条件を、実際のencoder/pusherで
検証できます。実行手順は `scripts/check_retry.py` を参照してください。
