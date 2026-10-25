# LM70S_OSC 設定 — 変更したら Live を再起動（または Control Surface を一度 None に戻して再選択）

# このトラック名の最初の Rack（Instrument Rack 推奨）の Macro を監視する（大文字小文字は無視）
TRACK_NAME = "LM70S"

# OSC 送信先（osc2dmx.py の --listen と合わせる）
OSC_HOST = "127.0.0.1"
OSC_PORT = 9000

# 送信アドレスの先頭。複数台を同時に動かすなら "/all"
OSC_PREFIX = "/lm70s"

# Macro 番号 -> パラメータ名。APC40 mkII の Device ノブは 1-8、BANK → で 9-16。
# Macro の名前をパラメータ名（例: "Tilt"）に変えると、番号に関係なくその名前が優先される。
MACROS = {
    1: "pan",
    2: "tilt",
    3: "dimmer",
    4: "strobe",
    5: "red",
    6: "green",
    7: "blue",
    8: "white",
    9: "speed",
    10: "color",
    11: "color_speed",
    12: "program",
}

# トラックのボリュームフェーダーを全体の明るさ（0dB 以上で 100%）として送る。None で無効
FADER_PARAM = "master"
FADER_UNITY = 0.85  # Live のボリューム値で 0dB に当たる位置

# update_display は約 100ms ごとに呼ばれる
STRUCTURE_CHECK_TICKS = 5   # トラック/Rack/Macro 名の変化を確認する間隔（約 0.5 秒）
RESEND_TICKS = 10           # 全値を再送する間隔（約 1 秒、ブリッジを後から起動しても同期する）

# --- MIDI コントローラー（AKAI MPK mini mk2 の工場出荷設定 Program 1 に合わせてある）---
# この Control Surface の Input に MPKmini2 を選ぶと有効。値は上と同じ 0.0-1.0
MIDI_CHANNEL = 0  # MIDI ch 1（ノブ・パッド・鍵盤・ジョイスティックすべて ch 1）

# ノブの CC 番号 -> パラメータ名。K1-K8 = CC 1-8。
# K1 の CC 1 はジョイスティックの上下と同じ番号なので strobe にする（倒している間だけ光る）
MIDI_KNOBS = {
    1: "strobe",
    2: "pan",
    3: "tilt",
    4: "dimmer",
    5: "red",
    6: "green",
    7: "blue",
    8: "white",
}

# ノブの位置が今の値を通り過ぎるまで効かないパラメータ（触った瞬間に値が飛ばないように）
MIDI_PICKUP = {"pan", "tilt", "dimmer", "red", "green", "blue", "white"}

# ジョイスティックの左右（ピッチベンド）で Pan を動かす。倒し切ったときの 1 秒あたりの変化量。
# 0 で無効、マイナスで向きが逆
JOYSTICK_PAN_SPEED = 0.25
JOYSTICK_DEADZONE = 0.05

# パッドのノート番号 -> (名前, {パラメータ: 値})。押すとまとめてその値にする。
# Bank A = ノート 44-51、Bank B（PAD BANK ボタン）= 32-39。鍵盤の 48-51 も同じノートなので反応する
MIDI_PADS = {
    # Bank A: 色
    44: ("Red", {"red": 1.0, "green": 0.0, "blue": 0.0, "white": 0.0}),
    45: ("Orange", {"red": 1.0, "green": 0.35, "blue": 0.0, "white": 0.0}),
    46: ("Yellow", {"red": 1.0, "green": 0.9, "blue": 0.0, "white": 0.0}),
    47: ("Green", {"red": 0.0, "green": 1.0, "blue": 0.0, "white": 0.0}),
    48: ("Cyan", {"red": 0.0, "green": 1.0, "blue": 1.0, "white": 0.0}),
    49: ("Blue", {"red": 0.0, "green": 0.0, "blue": 1.0, "white": 0.0}),
    50: ("Purple", {"red": 0.6, "green": 0.0, "blue": 1.0, "white": 0.0}),
    51: ("White", {"red": 0.0, "green": 0.0, "blue": 0.0, "white": 1.0}),
    # Bank B: 位置とルック
    32: ("Center", {"pan": 0.5, "tilt": 0.5}),
    33: ("Left", {"pan": 0.35, "tilt": 0.5}),
    34: ("Right", {"pan": 0.65, "tilt": 0.5}),
    35: ("Up", {"pan": 0.5, "tilt": 0.35}),
    36: ("Down", {"pan": 0.5, "tilt": 0.65}),
    37: ("Warm wash", {"dimmer": 1.0, "strobe": 0.0, "red": 1.0, "green": 0.45, "blue": 0.1, "white": 0.3}),
    38: ("Cool wash", {"dimmer": 1.0, "strobe": 0.0, "red": 0.0, "green": 0.3, "blue": 1.0, "white": 0.2}),
    39: ("Dark", {"dimmer": 0.0, "strobe": 0.0}),
}
