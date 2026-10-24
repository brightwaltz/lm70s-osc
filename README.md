# LM70S × APC40 mkII × Ableton Live (OSC)

APC40 mkII のノブと Live のクリップで Betopper LM70S（7×8W RGBW ミニムービングヘッド）を動かす仕組み。
Live 12 **Lite**（Max for Live なし）で動くように、Live の中では自作のリモートスクリプトが OSC を送り、外の Python ブリッジがそれを DMX に変換する。

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
- **USB-DMX が見つからない**: `ls /dev/cu.*` でデバイス名を調べ、`--port /dev/cu.usbserial-XXXX` を指定する
- **Speed の向きが逆**: 本体の仕様（0 が最速の機種が多い）。Macro の範囲を Rack 側で反転して調整する
