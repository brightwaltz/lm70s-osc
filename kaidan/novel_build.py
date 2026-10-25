#!/usr/bin/env python3
"""Cut the sound-novel pads (novel.json) out of a recording.

The cut plans give times on the timestamped script, which runs at a different pace from the recording.
The script text is aligned to the whisper transcript of the recording character by character, so every
script phrase gets its time in the recording; cuts then snap to the nearest pause.

  novel_build.py            write audio/cache/novel/<pad>.npy and manifest.json
  novel_build.py --verify   also transcribe every pad with whisper-cli and compare it with the script text
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_audio import SR, decode, load_silences  # noqa: E402

PHRASE_SNAP = 0.4  # seconds; phrases often follow each other without a pause, so only use a pause this close
MIN_RUN = 3  # matching characters in a row before a script phrase is tied to the transcript
CHAR_SEC = 0.1  # speaking time per character, to step back from a run to the phrase start
PRE_ROLL = 0.15  # extra lead-in when a cut falls between words without a pause
STRIP = re.compile(r"[\s、。「」『』・…ー〜！？!?,.\-()（）]")
MODEL = os.path.join(HERE, "models", "ggml-large-v3-turbo.bin")


def norm(text):
    """Letters only, katakana folded to hiragana (the script writes ハッと where whisper hears はっと)."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in STRIP.sub("", text))


def parse_clock(text):
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def load_script(path, section):
    """[(script seconds, phrase text)] of one story of the timestamped script."""
    text = open(path, encoding="utf-8").read()
    start = text.index(section)
    nxt = re.search(r"\n\d+\. ", text[start + len(section):])
    body = text[start:start + len(section) + (nxt.start() if nxt else len(text))]
    return [(parse_clock(m.group(1)), m.group(2).strip())
            for m in re.finditer(r"（(\d\d:\d\d:\d\d)）([^（]*)", body)]


def speech_spans(a, b, silences):
    """Parts of [a, b] outside the detected pauses (whisper segments often begin inside a pause)."""
    spans = [(a, b)]
    for s0, s1 in silences:
        if s1 <= a or s0 >= b:
            continue
        spans = [piece for x0, x1 in spans for piece in ((x0, min(x1, s0)), (max(x0, s1), x1)) if piece[1] - piece[0] > 0.01]
    return spans or [(a, b)]


def load_whisper(path, window, silences):
    """Every transcript character with its time, spread over the speech (not the pauses) of its segment."""
    chars, times = [], []
    for seg in json.load(open(path))["transcription"]:
        a, b = seg["offsets"]["from"] / 1000.0, seg["offsets"]["to"] / 1000.0
        if not window[0] <= a < window[1]:
            continue
        s = norm(seg["text"])
        spans = speech_spans(a, b, silences)
        total = sum(x1 - x0 for x0, x1 in spans)
        for k, c in enumerate(s):
            at = total * k / max(1, len(s))
            for x0, x1 in spans:
                if at <= x1 - x0:
                    break
                at -= x1 - x0
            chars.append(c)
            times.append(x0 + at)
    return chars, times


def boundary(silences, t):
    """(end of the phrase before t, start of the phrase at t), cut in the pause nearest t.

    Both sides of a boundary use the same pause, so neighbouring pads never overlap.
    """
    best, best_d = None, PHRASE_SNAP
    for s0, s1 in silences:
        d = 0.0 if s0 <= t <= s1 else min(abs(t - s0), abs(t - s1))
        if d <= best_d:
            best, best_d = (s0, s1), d
    if best is None:
        cut = max(0.0, t - PRE_ROLL)
        return cut, cut
    s0, s1 = best
    return min(s1, s0 + 0.3), max(s0, s1 - 0.12)


def align(phrases, wchars, wtimes):
    """Recording time where every script phrase starts; phrases whisper missed are interpolated.

    Only runs of MIN_RUN matching characters count: a lone 'で' or 'あ' often pairs with the
    previous phrase. The start is projected back from the first such run at CHAR_SEC per character.
    """
    uchars, owner, first_char = [], [], []
    for i, (_, text) in enumerate(phrases):
        first_char.append(len(uchars))
        for c in norm(text):
            uchars.append(c)
            owner.append(i)
    matcher = difflib.SequenceMatcher(None, uchars, wchars, autojunk=False)
    blocks = matcher.get_matching_blocks()
    starts = [None] * len(phrases)
    for block in blocks:
        short = block.size >= 2 and owner[block.a] == owner[block.a + block.size - 1] and \
            len(norm(phrases[owner[block.a]][1])) <= 3  # e.g. 「何が？」 has only two letters
        if block.size < MIN_RUN and not short:
            continue
        for k in range(block.size):
            u = block.a + k
            i = owner[u]
            if starts[i] is None:
                starts[i] = wtimes[block.b + k] - (u - first_char[i]) * CHAR_SEC
    known = [i for i, t in enumerate(starts) if t is not None]
    if len(known) < 2:
        raise SystemExit("台本と文字起こしがほとんど一致しません。recording と script_section を確認してください。")
    for i, t in enumerate(starts):
        if t is not None:
            continue
        before = max((k for k in known if k < i), default=None)
        after = min((k for k in known if k > i), default=None)
        if before is None or after is None:
            starts[i] = starts[before if after is None else after]
            continue
        span = phrases[after][0] - phrases[before][0] or 1
        starts[i] = starts[before] + (starts[after] - starts[before]) * (phrases[i][0] - phrases[before][0]) / span
    for i in range(1, len(starts)):  # keep the order even where the alignment wobbles
        starts[i] = max(starts[i], starts[i - 1])
    matched = sum(b.size for b in blocks if b.size >= MIN_RUN)
    return starts, matched / max(1, len(uchars))


class Script:
    def __init__(self, phrases, starts, end_of_story):
        self.phrases = phrases
        self.starts = starts
        self.end_of_story = end_of_story
        self.index = {t: i for i, (t, _) in reversed(list(enumerate(phrases)))}

    def find(self, clock):
        t = parse_clock(clock)
        if t not in self.index:
            raise SystemExit("台本に %s で始まる句がありません" % clock)
        return self.index[t]

    def span(self, spec):
        """'a-b' or 'a' -> (first, last) phrase index."""
        a, _, b = spec.partition("-")
        return self.find(a), self.find(b or a)

    def start(self, i):
        return self.starts[i]

    def end(self, i):
        return self.starts[i + 1] if i + 1 < len(self.starts) else self.end_of_story

    def text(self, first, last):
        return "".join(self.phrases[k][1] for k in range(first, last + 1))


def build_pad(pad, script, silences, source):
    ranges, text = [], []
    for spec in pad["phrases"]:
        first, last = script.span(spec)
        start = boundary(silences, script.start(first))[1]
        end = boundary(silences, script.end(last))[0]
        if end <= start + 0.3:
            end = script.end(last)
        ranges.append([start, end, first, last])
        text.append(script.text(first, last))
    head, tail = pad.get("trim", (0.0, 0.0))  # manual fix where the transcript's timing drifts
    if head:
        ranges[0][0] = boundary(silences, ranges[0][0] + head)[1]
    if tail:
        ranges[-1][1] = boundary(silences, ranges[-1][1] + tail)[0]
    pieces = [decode(source, start, end) for start, end, _, _ in ranges]
    pcm = np.concatenate(pieces)

    def offset(cue):
        if cue == "end":
            return max(0, len(pcm) - int(0.4 * SR))
        clock, _, shift = cue.partition("+")
        k = script.find(clock)
        acc = 0
        for (start, end, first, last), piece in zip(ranges, pieces):
            if first <= k <= last:
                t = max(start, script.start(k)) + float(shift or 0)
                return acc + min(len(piece) - 1, int(max(0.0, t - start) * SR))
            acc += len(piece)
        raise SystemExit("%s: %s はパッドの句に含まれていません" % (pad["title"], cue))

    cues = {kind: [offset(c) for c in pad.get(kind, [])] for kind in ("scare", "creak")}
    return pcm, [(round(s, 2), round(e, 2)) for s, e, _, _ in ranges], "".join(text), cues


def pad_id(pad):
    return "c%dr%d" % tuple(pad["pos"])


def write_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.asarray(pcm, dtype=np.int16).tobytes())


def transcribe(pcm):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pad.wav")
        write_wav(path, pcm)
        out = subprocess.run(["whisper-cli", "-m", MODEL, "-l", "ja", "-nt", "-np", "-f", path],
                             capture_output=True, text=True, check=True).stdout
    return out.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--novel", default=os.path.join(HERE, "novel.json"))
    ap.add_argument("--verify", action="store_true", help="transcribe every pad with whisper-cli (slow)")
    args = ap.parse_args()

    novel = json.load(open(args.novel))
    stories = json.load(open(os.path.join(HERE, "stories.json")))
    code = novel["recording"]
    source = os.path.expanduser(stories["sources"][code])
    if not os.path.exists(source):
        raise SystemExit("録音が見つかりません: %s" % source)
    window = novel["window"]
    phrases = load_script(os.path.join(HERE, novel["script"]), novel["script_section"])
    silences = load_silences(code)
    wchars, wtimes = load_whisper(os.path.join(HERE, "transcripts", "%s.json" % code), window, silences)
    starts, coverage = align(phrases, wchars, wtimes)
    script = Script(phrases, starts, window[1])
    print("台本 %d 句を録音 %s に対応づけ（文字の一致 %.0f%%）" % (len(phrases), code, coverage * 100), file=sys.stderr)

    out_dir = os.path.join(HERE, "audio", "cache", "novel")
    os.makedirs(out_dir, exist_ok=True)
    manifest = {"sample_rate": SR, "title": novel["title"], "edition": novel.get("edition", ""), "pads": []}
    main_total = 0
    for pad in novel["pads"]:
        pcm, ranges, text, cues = build_pad(pad, script, silences, source)
        pid = pad_id(pad)
        np.save(os.path.join(out_dir, pid + ".npy"), pcm)
        entry = {"id": pid, "col": pad["pos"][0], "row": pad["pos"][1], "kind": pad["kind"], "title": pad["title"],
                 "text": text, "length": len(pcm), "ranges": ranges, "scare": cues["scare"], "creak": cues["creak"],
                 "after": pad.get("after"), "note": pad.get("note", "")}
        manifest["pads"].append(entry)
        if pad["kind"] != "trap":
            main_total += len(pcm)
        line = "%-5s %-5s %5.1fs  %-16s %s" % (pid, pad["kind"], len(pcm) / SR, pad["title"],
                                               " ".join("%.1f-%.1f" % r for r in ranges))
        if args.verify:
            heard = transcribe(pcm)
            ratio = difflib.SequenceMatcher(None, norm(text), norm(heard), autojunk=False).ratio()
            line += "\n      一致 %.0f%%%s  聞こえた: %s" % (ratio * 100, "  ← 要確認" if ratio < 0.6 else "", heard)
        print(line, file=sys.stderr)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("本筋 %d パッドで %d:%02d、切り貼りミス %d パッド → %s" % (
        sum(1 for p in novel["pads"] if p["kind"] != "trap"), *divmod(int(main_total / SR), 60),
        sum(1 for p in novel["pads"] if p["kind"] == "trap"), out_dir), file=sys.stderr)


if __name__ == "__main__":
    main()
