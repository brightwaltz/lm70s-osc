#!/usr/bin/env python3
"""Cut the prepared recordings into one PCM file per story, using stories.json.

Boundaries are moved to the nearest pause (from transcripts/silence_<src>.tsv, made with ffmpeg silencedetect)
so a part never starts or ends mid-word. Output: audio/cache/<id>.npy (int16 mono) and audio/cache/manifest.json
with the sample offsets of every part and scare.
"""

import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 44100
SNAP_WINDOW = 2.0
FADE = 0.02


def load_silences(code):
    path = os.path.join(HERE, "transcripts", "silence_%s.tsv" % code)
    rows = []
    if os.path.exists(path):
        for line in open(path):
            cols = line.split()
            if len(cols) == 2:
                rows.append((float(cols[0]), float(cols[1])))
    return rows


def snap(silences, t, edge):
    """edge 'start': just before speech resumes after a pause; 'end': just after speech stops."""
    best = None
    for s0, s1 in silences:
        mark = s1 if edge == "start" else s0
        if abs(mark - t) <= SNAP_WINDOW and (best is None or abs(mark - t) < abs(best - t)):
            best = mark
    if best is None:
        return t
    return max(0.0, best - 0.12) if edge == "start" else best + 0.25


def decode(src, start, end):
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "%.3f" % start, "-to", "%.3f" % end,
           "-i", src, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    pcm = np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, dtype=np.int16).copy()
    n = min(len(pcm) // 2, int(FADE * SR))
    if n:
        ramp = np.linspace(0.0, 1.0, n)
        pcm[:n] = (pcm[:n] * ramp).astype(np.int16)
        pcm[-n:] = (pcm[-n:] * ramp[::-1]).astype(np.int16)
    return pcm


def build_story(story, sources, silences):
    ranges = []
    for code, start, end in story["ranges"]:
        ranges.append((code, snap(silences[code], start, "start"), snap(silences[code], end, "end")))

    def offset(code, t):
        acc = 0
        for rcode, start, end in ranges:
            if rcode == code and start - 0.5 <= t <= end:
                return acc + int(max(0.0, t - start) * SR)
            acc += int((end - start) * SR)
        raise ValueError("%s: %s %.1f is outside its ranges" % (story["id"], code, t))

    parts = []
    for i, (code, t) in enumerate(story["parts"]):
        snapped = ranges[0][1] if i == 0 else snap(silences[code], t, "start")
        parts.append(offset(code, snapped))
    scares = [offset(code, t) for code, t in story["scares"]]
    if parts != sorted(parts):
        raise ValueError("%s: parts are out of order after snapping: %s" % (story["id"], parts))

    pcm = np.concatenate([decode(sources[code], start, end) for code, start, end in ranges])
    return pcm, parts, scares, ranges


def main():
    data = json.load(open(os.path.join(HERE, "stories.json")))
    sources = {code: os.path.expanduser(path) for code, path in data["sources"].items()}
    missing = [p for p in sources.values() if not os.path.exists(p)]
    if missing:
        raise SystemExit("録音が見つかりません: %s" % ", ".join(missing))
    silences = {code: load_silences(code) for code in sources}
    out_dir = os.path.join(HERE, "audio", "cache")
    os.makedirs(out_dir, exist_ok=True)

    manifest = {"sample_rate": SR, "part_names": data["part_names"], "stories": []}
    for story in data["stories"]:
        pcm, parts, scares, ranges = build_story(story, sources, silences)
        np.save(os.path.join(out_dir, story["id"] + ".npy"), pcm)
        manifest["stories"].append({
            "id": story["id"], "title": story["title"], "grid": story["grid"], "mood": story["mood"],
            "summary": story["summary"], "length": len(pcm), "parts": parts, "scares": scares,
        })
        part_times = " ".join("%d:%02d" % divmod(int(p / SR), 60) for p in parts)
        print("%-9s %-12s %5.1f min  parts %s  scares %d" % (
            story["id"], story["title"], len(pcm) / SR / 60, part_times, len(scares)), file=sys.stderr)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("wrote %d stories to %s" % (len(manifest["stories"]), out_dir), file=sys.stderr)


if __name__ == "__main__":
    main()
