# LM70S × APC40 mkII × Ableton Live (OSC)

APC40 mkII のノブと Live のクリップで Betopper LM70S（7×8W RGBW ミニムービングヘッド）を動かす仕組み。
Live 12 **Lite**（Max for Live なし）で動くように、Live の中では自作のリモートスクリプトが OSC を送り、外の Python ブリッジがそれを DMX に変換する。
Live を使わずに APC40 から直接 OSC を出すモード（[8. Ableton なしモード](#8-ableton-なしモードapc40--osc-直結)）と、DMX 側を別マシンで動かす構成（[9. 別マシン構成](#9-別マシン構成サーバーで-dmx-を出す)）もある。
DMX を別の人のブリッジ（例: 先方 Mac の UDP 9004）が受け持つときは [10. 別のブリッジへ中継](#10-別のブリッジへ中継先方の-mac-など) を使う。

```
APC40 mkII ──USB──▶ Ableton Live 12 Lite
                     ├─ Control Surface 1: APC40 mkII（クリップ起動・ノブ → Rack の Macro）
                     ├─ トラック「LM70S」: 空の Instrument Rack（Macro = 照明パラメータ）
                     └─ Control Surface 2: LM70S_OSC（Macro とフェーダーを監視して OSC 送信）
                                │  OSC/UDP 127.0.0.1:9000  /lm70s/pan など
                                ▼
                     bridge/osc2dmx.py ──USB──▶ USB-DMX インターフェース ──XLR 3pin──▶ LM70S
```

LM70S 本体は DMX 入力しか持たないので、**USB-DMX インターフェースが別途必要**です。

| インターフェース | `--output` |
|---|---|
| ENTTEC DMX USB Pro / DMXKing ultraDMX Micro などの「Pro 互換」 | `enttec-pro`（推奨・安定） |
| ENTTEC Open DMX USB や安価な FTDI 製 USB-DMX ケーブル | `open-dmx` |
| Art-Net ノード（LAN 経由） | `artnet --artnet-host <IP>` |

## 1. LM70S 本体の設定

本体パネル（MODE/ESC・UP・DOWN・ENTER）で次のように設定する。

- チャンネルモード: **14CH**（9CH でも動くが pan/tilt が 8bit になる）
- DMX アドレス: **001**
- 動作モード: DMX

USB-DMX → DMX ケーブル → LM70S の DMX IN の順につなぐ。チェーンの最後にターミネーターを付けると安定する。

## 2. ブリッジの準備

```bash
cd ~/dev/lm70s-osc
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

（この Mac ではもう `.venv` を作ってある）

## 3. リモートスクリプトのインストール

```bash
./install_remote_script.sh
```

`~/Music/Ableton/User Library/Remote Scripts/LM70S_OSC` にコピーされる（この Mac ではインストール済み）。`remote_script/LM70S_OSC/config.py` を変えたら再実行する。

## 4. Live の設定

1. Live を再起動 → **Settings > Link, Tempo & MIDI**
2. Control Surface 1: `APC40 mkII`、Input/Output: `APC40 mkII`（通常の設定のまま）
3. Control Surface 2: `LM70S_OSC`、Input/Output: **None**
4. MIDI トラックを 1 本作って名前を **`LM70S`** にする
5. Instruments から **Instrument Rack**（中身は空でよい）をそのトラックへドロップ
6. Rack の Macro を表示し、Macro 9-12 も使うなら「+」で 16 個まで増やす
7. ステータスバーに `LM70S_OSC: track 'LM70S': ... -> 127.0.0.1:9000` と出れば OK

MIDI Effect Rack ではなく Instrument Rack にするのは、トラックにボリュームフェーダーを持たせて、それをマスター輝度として使うため。

## 5. ブリッジを起動

まずはハードなしで値の変化を確認する:

```bash
.venv/bin/python bridge/osc2dmx.py
```

LM70S を実際に動かす（USB-DMX を自動検出）:

```bash
.venv/bin/python bridge/osc2dmx.py --output enttec-pro --monitor
```

Live なしで照明だけ確認する（別ターミナル）:

```bash
.venv/bin/python tools/osc_send.py demo
```

Ctrl+C で止めると、ヘッドは動かさずに消灯してから終了する。

## 6. APC40 mkII での操作

トラック `LM70S` を選ぶと、DEVICE CONTROL の 8 ノブが Rack の Macro に割り当たる（Blue Hand）。**DEV. LOCK** を押しておくと、他のトラックを選んでもノブは照明のまま。

| ノブ | Macro | パラメータ | LM70S（14CH） |
|---|---|---|---|
| 1 | 1 | Pan | ch1-2（16bit） |
| 2 | 2 | Tilt | ch3-4（16bit） |
| 3 | 3 | Dimmer | ch6 Master 8-134 |
| 4 | 4 | Strobe（0 で OFF、右へ回すほど速い） | ch6 Master 135-239 |
| 5-8 | 5-8 | Red / Green / Blue / White | ch7-10 |
| BANK → 1 | 9 | Speed（モーター速度） | ch5 |
| BANK → 2 | 10 | Color（内蔵カラーパターン） | ch11 |
| BANK → 3 | 11 | Color Speed | ch12 |
| BANK → 4 | 12 | Program（内蔵 Auto/Sound。0 で DMX 操作） | ch13 |
| LM70S トラックのフェーダー | — | Master（0dB 以上で 100%） | Dimmer に掛け算 |

- Dimmer とストロボは本体では同じ ch6 を共有している。ブリッジが「Dimmer > 0 で、Strobe > 0 ならストロボ、0 なら調光」として ch6 の値を作る
- Macro の名前を `Tilt` などのパラメータ名に変えると、番号に関係なくその名前が使われる（並べ替えたいとき用）

## 7. 照明をクリップで演出する

LM70S トラックに空の MIDI クリップを作り、Clip View の **Envelopes** で Device = Instrument Rack、Control = Macro を選んでオートメーションを描く。たとえば「Pan をサイン波で振る」「Red→Blue に色を変える」。そのクリップを APC40 のグリッドから起動すれば、ローンチクオンタイズで曲の小節頭にそろって照明が切り替わる。音楽のシーンと照明のクリップを同じシーン行に置けば、シーンボタン一つで両方が切り替わる。

## 8. Ableton なしモード（APC40 → OSC 直結）

`tools/apc2osc.py` が APC40 mkII を直接読み、照明専用コントローラーとして OSC を送る。Live は不要。

```
APC40 mkII ──USB──▶ tools/apc2osc.py ──OSC/UDP :9000──▶ bridge/osc2dmx.py ──USB──▶ USB-DMX ──▶ LM70S
```

Live と同時に APC40 を使うと LED と操作が競合する。Live を終了するか、Live の Control Surface から APC40 mkII を外してから起動する。

```bash
.venv/bin/python bridge/osc2dmx.py --output enttec-pro --monitor
```

別ターミナルで:

```bash
.venv/bin/python tools/apc2osc.py
```

| 操作 | 内容 |
|---|---|
| DEVICE CONTROL ノブ 1-8 | Pan / Tilt / Dimmer / Strobe / Red / Green / Blue / White（Ableton 経由と同じ並び。LED リングに値を表示） |
| TRACK CONTROL ノブ 1-4（上段） | Speed / Color / Color Speed / Program |
| トラックフェーダー 1-8 | `FIXTURES` の 1-8 台目の Dimmer |
| マスターフェーダー | 全台の Master（明るさの掛け算） |
| TRACK SELECT 1-8 / MASTER | ノブとパッドの送り先を 1-8 台目 / 全台（`/all`）に切り替える。選択中のボタンが点灯 |
| クリップグリッド（上の段から） | 色 / 位置 / ストロボ / 内蔵 Auto・Sound プログラム / ルック。最後に押したパッドが明るく光る |
| STOP ALL CLIPS | ブラックアウトの ON/OFF（点灯中 = 消灯中） |
| SHIFT + STOP ALL CLIPS | 選択中のターゲットをリセット |

- パッドの中身、ノブの割り当て、台数（`FIXTURES`）は `tools/apc2osc_config.py` で変える。値は float = 0.0-1.0、int = DMX 生値（OSC と同じ）
- 送り先を切り替えると、LED リングはその台の値を表示し、ノブもそこから続きを回せる
- 起動直後は何も送らない（ブリッジの初期値 = 中央向き・消灯のまま）。触ったパラメータから送られる
- 終了すると APC40 の LED を消す

## 9. 別マシン構成（サーバーで DMX を出す）

ブリッジは Linux でも動く。USB-DMX を Raspberry Pi などのサーバーにつなぎ、APC40（または Live）側の Mac から LAN 越しに OSC を送れる。

```
APC40 mkII ─USB─▶ Mac（apc2osc.py または Live + LM70S_OSC）
                    │ OSC/UDP 9000（LAN）
                    ▼
                  サーバー / Raspberry Pi（osc2dmx.py）─USB─▶ USB-DMX ─▶ LM70S
```

サーバーにフォルダをコピーする（`.venv` は除く）:

```bash
rsync -a --exclude .venv ~/dev/lm70s-osc/ pi@raspberrypi.local:lm70s-osc/
```

サーバー側（DMX 側に必要なのは python-osc と pyserial だけ）:

```bash
cd ~/lm70s-osc && python3 -m venv .venv && .venv/bin/pip install python-osc pyserial
```

USB シリアルを一般ユーザーで開けるようにする（一度ログインし直す）:

```bash
sudo usermod -aG dialout $USER
```

```bash
.venv/bin/python bridge/osc2dmx.py --output enttec-pro --monitor
```

USB-DMX は Linux では `/dev/ttyUSB*` か `/dev/ttyACM*` として自動検出される。ブリッジは `0.0.0.0:9000` で待ち受けるので、ファイアウォールで UDP 9000 を開けておく。

Mac 側は送り先をサーバーにする:

- Ableton なしモード: `.venv/bin/python tools/apc2osc.py --host raspberrypi.local`（または IP アドレス。常に使うなら `apc2osc_config.py` の `OSC_HOST`）
- Ableton 経由: `remote_script/LM70S_OSC/config.py` の `OSC_HOST` をサーバーの IP にして `./install_remote_script.sh` を再実行

APC40 をサーバーに直接つないで、サーバー上で `apc2osc.py` を動かしてもよい（Mac 不要）。その場合はサーバーでも `pip install -r requirements.txt` する。python-rtmidi のビルドに `sudo apt install libasound2-dev` が必要なことがある。

## 10. 別のブリッジへ中継（先方の Mac など）

DMX を自分の `osc2dmx.py` ではなく、別の人が動かしている OSC→DMX ブリッジで出すときの構成。送信側（Live + LM70S_OSC、`apc2osc.py`、`osc_send.py`）は今まで通り `127.0.0.1:9000` に送り、`tools/osc_relay.py` が相手の受け口へ転送する。

```
APC40 mkII ─▶ Live + LM70S_OSC または apc2osc.py ─OSC 127.0.0.1:9000─▶ tools/osc_relay.py
                                                                          │ OSC/UDP（LAN）
                                                                          ▼
                                                  先方の Mac（例: UDP 9004）─▶ USB-DMX ─▶ LM70S
```

`osc2dmx.py` と同じポート（9000）で待ち受けるので、中継を使うときは `osc2dmx.py` を止めておく。相手の IP は先方の README を見る（つなぎ直すと変わることがある）。

相手が `/lm70s/...` をそのまま受けられる場合（そのまま転送）:

```bash
.venv/bin/python tools/osc_relay.py --target <先方の IP>:9004
```

相手のアドレスの頭が違う場合は付け替える（例: `/lm70s/dimmer` → `/light/1/dimmer`）:

```bash
.venv/bin/python tools/osc_relay.py --target <先方の IP>:9004 --rename /lm70s=/light/1
```

相手が DMX チャンネル単位でしか受けない場合は、こちらで LM70S のチャンネル値（0-255）に変換してから送る。Dimmer と Strobe の合成や Pan/Tilt の 16bit 化も、`osc2dmx.py` と同じ計算でこちら側で済ませる。変わったチャンネルだけを送り、取りこぼし対策に 1 秒ごとに全チャンネルを送り直す:

```bash
.venv/bin/python tools/osc_relay.py --target <先方の IP>:9004 --mode channels --channel-address "/dmx/{ch}"
```

- `--channel-address` の `{ch}` は 1-512、`{ch0}` は 0-511
- `--channel-value float` で 0.0-1.0 の float にする
- `--fixture lm70s=1:14` で本体のアドレスとモードを合わせる（相手側の本体設定と一致させる）
- `--dry-run` を付けると送らずに表示だけする。相手につなぐ前に中身を確かめられる

UDP は届いたかどうかが送信側では分からない。相手側の受信表示（ステータスページなど）で確かめる。

## OSC アドレス（TouchOSC などから直接送る場合）

float は 0.0-1.0 の正規化値、int は DMX の生値（0-255）として扱う。

| アドレス | 内容 |
|---|---|
| `/lm70s/<param>` | pan tilt dimmer strobe red green blue white speed color color_speed program master |
| `/lm70s/reset` | 1 を送ると Reset 値を 5 秒保持（本体のリセット動作） |
| `/all/<param>` | 全フィクスチャに送る |
| `/blackout` | 1 = 全消灯、0 = 解除 |
| `/dmx/<ch>` / `/dmx/clear` | 1ch 単位の生値上書き / 上書きの解除 |

ブリッジは `0.0.0.0:9000` で待ち受けるので、同じネットワークのスマホからも送れる。

## 複数台

```bash
.venv/bin/python bridge/osc2dmx.py --output enttec-pro \
  --fixture lm70s=1:14 --fixture lm70s2=15:14
```

2 台目の本体アドレスは 015 にする。Live から全台を同時に動かすなら `config.py` の `OSC_PREFIX` を `"/all"` にして `./install_remote_script.sh` を再実行する。

## トラブルシューティング

- **ステータスバーに何も出ない**: Live のログ `~/Library/Preferences/Ableton/Live 12.4.7/Log.txt` で `LM70S_OSC` を検索する
- **ブリッジに何も届かない**: `--verbose` を付けると受信した OSC をすべて表示する
- **光らない**: Dimmer とフェーダーの両方が上がっているか確認する（Master は両者の掛け算）。`/dmx/6 100i` のような生値で本体の反応を確かめられる
- **USB-DMX が見つからない**: `ls /dev/cu.*`（Linux は `ls /dev/ttyUSB* /dev/ttyACM*`）でデバイス名を調べ、`--port /dev/cu.usbserial-XXXX` を指定する。Linux で Permission denied なら `dialout` グループに入っているか確認する
- **Ableton なしモードで LED がおかしい / 反応が二重になる**: Live が APC40 を Control Surface として使っていないか確認する
- **Speed の向きが逆**: 本体の仕様（0 が最速の機種が多い）。Macro の範囲を Rack 側で反転して調整する
