# ハードウェア & 設計ノート (Meural Canvas MC321) — 日本語版

RAM上で完結するライブパイプラインを構築する中で実測で確定させた知見の蒸留。英語版は docs/NOTES.md。

## 端末の事実

- フレームバッファ `/dev/fb0` は 1920x1080 / 32bpp BGRA。`mplayer -vo fbdev2` は
  **スケールしない**ため、ソースは正確に1920x1080であること。
- 純正バイナリ: `mplayer 1.2.1` (libavcodec 56.x)、`python3`、ローカルHTTP API
  `http://<端末IP>/remote/...` (同梱Web UIよりリバース)。
- 4コア ARM (RK3288)、空きRAM 約400MB。1080p15 H.264 のソフトウェアデコードは
  コーデックCPU約33% — 余裕あり。
- 端末時計は数十分ずれることがあり、A/V同期には絶対に使わない。
- 端末→外部の接続は動かない可能性があり、Mac→端末 + 端末→127.0.0.1 は問題なし。

## ローカル remote API

有用なエンドポイント (GETベース、LAN内で認証なし):

```
/remote/identify/
/remote/control_check/{system,video,sleep}/
/remote/get_galleries_json/          /remote/get_gallery_status_json/
/remote/control_command/change_gallery/<id>/
/remote/control_command/change_item/<id>/
/remote/control_command/set_backlight/<n>/
/remote/control_command/{suspend,resume}/
/remote/postcard/                    (multipart upload: 画像/動画)
```

`video_status()` (control_check/video) は**純正動画再生パス**だけを反映する。
直接起動した mplayer は画面に出続けていても status は false — 見た目の問題
だけなので無視してよい。

### rtc「シェル」化のトリック

`/remote/control_command/rtc/<payload>/` はpayloadをおよそ
`sh -c 'eval date -s @<payload>'` として評価する。payloadを `;` で始めると
`date` コンテキストから脱出 — 実質 root の1ライナーシェル。痛い目を見て学んだ
規約:

- URLは**二重デコード**を経由する: リテラル `/` は全て `$(printf '\057')`、
  `:` は `$(printf '\072')` と書く (`push_files.py` の `sl()` / `co()`)。
  これを怠ると 404/400。
- PHPレイヤーの手前に**リクエスト長上限**がある: base64urlチャンクが約4KBを
  超えると **HTMLページ付きHTTP 403で握り潰される** (後でファイルが0バイトに
  なる)。チャンクは ≤2000 バイト (`push_files.py` のデフォルト) にし、必ず
  md5/サイズを検証する。
- payload内の裸の `$()` / `${}` は1段目のデコードで消費される。全 `$` を
  `\$` にエスケープして evalレベルで展開させる。
- payloadに `%s` があると HTTP 400。payload長は ~4KB まで。
- `pkill -f` のパターンに、payload自身に現れるリテラルを含めない (自己マッチ)。
  ブランケットクラスを使う: `pkill -f 'mplaye[r]'`、`'watch[.]sh'`、
  `'ingest[.]py'`。killとstartは**別ペイロード**で実行する。

## mplayer のコマンドラインは命綱

純正 mplayer 1.2.1 の癖 (全て実測):

| オプション | 必要な理由 |
|---|---|
| `-demuxer lavf` | **純正**MPEG-TS demuxer は低ビットレート (~30KB/s) 入力で `TS file format detected` 後にスタールする: 100% CPU空回り + `Cache empty` の洪水、再生が始まらない。lavf demuxerなら数秒で再生開始。 |
| `-vo fbdev2` | `x11` VO は ~4-5分で死ぬ (`XIO: fatal IO error 11`)。fbdev2 は数時間連続で安定。 |
| `-nocorrect-pts` | ライブ配信TS × lavf demux では補正なしの方が予測可能に動く。 |
| `-framedrop -nosound` | 音声自体がないため。ペーシングを単純化。 |
| `-cache 4096 -cache-min 15` | 2秒間隔のセグメント投入と初期fillを吸収。 |

`-lavdopts fast:threads=4` を付けるとこのbuildはほぼ即死 — 絶対に追加しない。

### mplayer ステータス行 (`-quiet`を外すと出る)

```
V:  <位置秒>  <frame>/<frame>  %cpu  %codec ...
```

wall時間差と `V:` 差の比 == 実時間率 (目標 ~1.00x; フレーム差/時間 == fps)。
最も安価な全経路同期プローブ。

## Mac側エンコーダの規則

- **セグメント境界の連続タイムスタンプ**: セグメント muxer は必ず
  `-reset_timestamps 0`。`1` だと各セグメントでPTSリセットし、ストリームに
  `DTS ... out of order` — 多くの人が見るモザイクの温床はまさにこれ。
- **映像のみ (`-an`)**: 音声lavfi入力には `-re` が効かず暴走し、PTS空間を
  破壊する (demuxerが26時間分のPTSを見た!)。端末は `-nosound` なので音声に
  意味がない。
- **ビットレート vs コンテンツ**: なめらかな1080pグラデーションは `veryfast`
  で ~2Mbps 必要。250kbps / ultrafast だと16x16ブロックだらけになる
  (あの「乱れ」はエンコーダ量子化だった — 転送 Corruption は別途解決済み)。
- **実ソースは `superfast`**: リモートHLS入力を `-re` でペーシングしつつ
  24fps→15fps に落とすと、`veryfast` では ~0.95x 実時間 (じわじわ遅れ)。
  `superfast` なら追従。純生成ソース (gradients) は `veryfast` で十分。
- **close-aware push**: ffmpegが現在書込み中のセグメントは絶対にPOSTしない —
  pusherは常に最新1個を除外し、push済みファイルを削除する。4KB未満の
  空ファイル (open済み未書込み) はスキップし、後続に健全セグメントが出現した
  時点で「失われた内容」として諦めて進む (カウンタ詰まり防止)。
- **ffmpeg 9.x の drawbox の罠**: drawbox式内の `t` (タイムスタンプ) が常に0
  (`-vf`配置でもlavfi入力内でも)。`n` 変数はそもそも使用不可 (式パースエラー)。
  デモソースには設計上アニメするフィルタ (`gradients`, `life`, `testsrc2`) を
  使う。

## Ingest サーバ設計 (`device/ingest.py`)

- セグメントはPythonのdict/queueに保持 (`/dev/shm` 任意) — フラッシュに
  触れない。
- `/seg?n=N` でbodyを受領し、同一ロック下で全 `/live.ts` クライアントの
  キューに積む。登録 (末尾3セグメントのスナップ + キュー投入) はアトミック —
  欠落も重複も発生しない。
- **実時間ペース drip**: 各セグメントを1KBチャンクで送り、チャンク毎に
  `gap*1024/len(seg)` 秒sleepする。`gap` は**実測の到着間隔** (フォールバック
  2.0秒)。つまり送出ストリームは**生産側の実時間に完全跟着**する — ffmpegが
  リモートHLS取得で0.9x実時間で走っても、プレイヤへの孔は生じない
  (固定2.0秒ペースだと1セグメント毎に ~0.1秒の孔が出来、分単位でキャッシュが
  枯れる)。
- プレイリスト `/live.m3u8` はデバッグ用。再生は無限TS `/live.ts` 経由で行い、
  HLSは使わない。

## 決め手になった診断法

- **フレームバッファダンプ**: `dd if=/dev/fb0 of=/tmp/fbx.raw bs=8M count=1`
  →取得→ `ffmpeg -f rawvideo -pix_fmt bgra -s 1920x1080 -i fbx.raw frame.png`。
  数秒間隔の2枚差分で「描いていない」か「静止ソースを描いている」かを分離。
- **隣接セグメント2枚のフレームmd5**: **ソース自身**が動くかを証明
  (md5一致=経路が完璧でもソースが静止)。
- **ストリームキャプチャ+Macでのデコード**
  (`curl -N http://<端末>:8484/live.ts -o cap.ts` →
  `ffmpeg -v error -i cap.ts -f null -`): Corruption の位置 (転送か端末デコードか) を局所化。
- **ビット一致ラウンドトリップ**: `curl -X POST --data-binary @seg.ts
  'http://<端末>:8484/seg?n=9999'` → GET `/s9999.ts` → `cmp` — POST経路が
  バイト一致することを証明。

## 忘れてはいけない失敗モード

- pusherをkillすると**孤立ffmpeg**が残る。`pkill -f "[s]eg%06d"` 系の
  ブランケットで掃除しないと番号衝突の温床。
- サーバ再起動すると全rtc payloadが一瞬500/404する。リトライを持つpayloadが
  生き残る — `post()` が6回リトライするのはそのため。
- 起動中のsimple HTTPサーバのCWDをrmすると壊れる (500、サーバ再起動で復旧)。

## 「完成」の形

- 300秒のwall時間 == 300秒の `V:` 進行、15.0 fps、デコエラーゼロ、初回join後の
  `DTS out of order` ゼロ。
- フレームバッファダンプが数秒で差分になる (動作)、現在のソースフレームと一致
  (正しい)。
- 目視: なめらかなフルスクリーン動作、ブロックなし。
