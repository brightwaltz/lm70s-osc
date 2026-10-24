# apc2osc.py の設定 — 変更したら apc2osc.py を再起動
# 値の書き方は osc2dmx.py と同じ: float = 0.0-1.0 の正規化値、int = DMX の生値（0-255）

# 送信先（osc2dmx.py を動かしているマシン）。--host / --port で上書きできる
OSC_HOST = "127.0.0.1"
OSC_PORT = 9000

# osc2dmx.py の --fixture 名。Track Select 1-8 がこの順に対応し、MASTER ボタンで /all（全台）
FIXTURES = ["lm70s"]

# DEVICE CONTROL の 8 ノブ（右側）。Ableton 経由のときと同じ並び
DEVICE_KNOBS = ["pan", "tilt", "dimmer", "strobe", "red", "green", "blue", "white"]

# 上段の TRACK CONTROL 8 ノブ。None は未使用
TRACK_KNOBS = ["speed", "color", "color_speed", "program", None, None, None, None]

# トラックフェーダー 1-8 = FIXTURES[i] のこのパラメータ（台数ぶんだけ有効）。None で無効
TRACK_FADER = "dimmer"

# マスターフェーダー = /all/<これ>（全体の明るさ）
MASTER_FADER = "master"

# クリップグリッド 5 段 × 8 パッド（上の段から）。各パッド: (名前, パッドの色 RGB, 送るパラメータ)
# 押すと選択中のターゲット（Track Select / MASTER）に送る。None は空きパッド
PRESET_ROWS = [
    # 色
    [
        ("Red", (255, 0, 0), {"red": 1.0, "green": 0.0, "blue": 0.0, "white": 0.0}),
        ("Orange", (255, 96, 0), {"red": 1.0, "green": 0.35, "blue": 0.0, "white": 0.0}),
        ("Yellow", (255, 255, 0), {"red": 1.0, "green": 0.9, "blue": 0.0, "white": 0.0}),
        ("Green", (0, 255, 0), {"red": 0.0, "green": 1.0, "blue": 0.0, "white": 0.0}),
        ("Cyan", (0, 255, 255), {"red": 0.0, "green": 1.0, "blue": 1.0, "white": 0.0}),
        ("Blue", (0, 0, 255), {"red": 0.0, "green": 0.0, "blue": 1.0, "white": 0.0}),
        ("Purple", (160, 0, 255), {"red": 0.6, "green": 0.0, "blue": 1.0, "white": 0.0}),
        ("White", (255, 255, 255), {"red": 0.0, "green": 0.0, "blue": 0.0, "white": 1.0}),
    ],
    # 位置（pan, tilt）。設置の向きに合わせて調整する
    [
        ("Center", (0, 120, 255), {"pan": 0.5, "tilt": 0.5}),
        ("Left", (0, 120, 255), {"pan": 0.35, "tilt": 0.5}),
        ("Right", (0, 120, 255), {"pan": 0.65, "tilt": 0.5}),
        ("Up", (0, 120, 255), {"pan": 0.5, "tilt": 0.35}),
        ("Down", (0, 120, 255), {"pan": 0.5, "tilt": 0.65}),
        ("Audience L", (0, 120, 255), {"pan": 0.4, "tilt": 0.7}),
        ("Audience R", (0, 120, 255), {"pan": 0.6, "tilt": 0.7}),
        ("Ceiling", (0, 120, 255), {"pan": 0.5, "tilt": 0.1}),
    ],
    # ストロボ（0 = OFF、右ほど速い）
    [
        ("Strobe off", (255, 255, 255), {"strobe": 0.0}),
        ("Strobe 1", (255, 255, 255), {"strobe": 0.05}),
        ("Strobe 2", (255, 255, 255), {"strobe": 0.2}),
        ("Strobe 3", (255, 255, 255), {"strobe": 0.35}),
        ("Strobe 4", (255, 255, 255), {"strobe": 0.5}),
        ("Strobe 5", (255, 255, 255), {"strobe": 0.65}),
        ("Strobe 6", (255, 255, 255), {"strobe": 0.8}),
        ("Strobe 7", (255, 255, 255), {"strobe": 1.0}),
    ],
    # 本体内蔵の Auto / Sound プログラム（ch13 の生値。0 で DMX 操作に戻る）
    [
        ("Manual", (255, 160, 0), {"program": 0}),
        ("Auto 1", (255, 160, 0), {"program": 10}),
        ("Auto 4", (255, 160, 0), {"program": 55}),
        ("Auto 8", (255, 160, 0), {"program": 120}),
        ("Sound 1", (255, 0, 120), {"program": 135}),
        ("Sound 4", (255, 0, 120), {"program": 180}),
        ("Sound 8", (255, 0, 120), {"program": 250}),
        None,
    ],
    # ルック（複数パラメータをまとめて呼び出す）
    [
        ("Warm wash", (255, 120, 40), {"pan": 0.5, "tilt": 0.6, "dimmer": 1.0, "strobe": 0.0,
                                       "red": 1.0, "green": 0.45, "blue": 0.1, "white": 0.3}),
        ("Cool wash", (60, 120, 255), {"pan": 0.5, "tilt": 0.6, "dimmer": 1.0, "strobe": 0.0,
                                       "red": 0.0, "green": 0.3, "blue": 1.0, "white": 0.2}),
        ("Full white", (255, 255, 255), {"dimmer": 1.0, "strobe": 0.0,
                                         "red": 1.0, "green": 1.0, "blue": 1.0, "white": 1.0}),
        ("Dark", (40, 40, 40), {"dimmer": 0.0, "strobe": 0.0}),
        None,
        None,
        None,
        None,
    ],
]
