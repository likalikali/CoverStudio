from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
MARKER = "MIDI_EXPORT_TIMELINE_GUARD"

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def patch_metadata(text: str) -> tuple[str, bool]:
    if "def score_for_cover(" not in text:
        raise RuntimeError("piano_cover/metadata.py 中找不到 score_for_cover()")
    start = text.index("def score_for_cover(")
    end = text.find("\ndef ", start + 1)
    if end < 0:
        end = len(text)
    block = text[start:end]
    if "score.sort()" in block:
        return text, False
    if "    return score" not in block:
        raise RuntimeError("metadata.py 的 score_for_cover() 结构与预期不同，未修改")
    block = block.replace(
        "    return score",
        "    # Sort event lists before serializing to MIDI.\n    score.sort()\n    return score",
        1,
    )
    return text[:start] + block + text[end:], True

def patch_tokenizer(text: str) -> tuple[str, bool]:
    marker = "    def events_to_score(self, events: np.ndarray):"
    if marker not in text:
        raise RuntimeError("piano_ar/tokenizer.py 中找不到 events_to_score()")
    start = text.index(marker)
    end = text.find("\n    def events_to_midi", start + 1)
    if end < 0:
        raise RuntimeError("tokenizer.py 中找不到 events_to_midi() 边界")
    block = text[start:end]
    if "score.sort()" in block:
        return text, False
    old = "        score.tracks.append(track)\n        return score"
    if old not in block:
        raise RuntimeError("tokenizer.py 的 events_to_score() 结构与预期不同，未修改")
    block = block.replace(old, "        score.tracks.append(track)\n        score.sort()\n        return score", 1)
    return text[:start] + block + text[end:], True

VALIDATOR = r'''

def _validate_cover_midi_export(path: Path, expected_end_seconds: float, expected_notes: int) -> None:
    'Read the written MIDI back and refuse a file whose timeline exploded.'
    from symusic import Score

    check = Score(str(path)).to("second")
    actual_end = float(check.end())
    actual_notes = sum(len(track.notes) for track in check.tracks)

    if expected_notes > 0 and actual_notes != expected_notes:
        raise RuntimeError(
            f"MIDI export validation failed: expected {expected_notes} notes, "
            f"read back {actual_notes} notes from {path.name}"
        )

    # Source metadata can extend to the end of the full song, even for a short preview.
    limit = max(float(expected_end_seconds) + 5.0, float(expected_end_seconds) * 1.05)
    if actual_end > limit:
        raise RuntimeError(
            f"MIDI export timeline is abnormal: expected about {expected_end_seconds:.2f}s, "
            f"but the written file reads as {actual_end:.2f}s. "
            "The temporary file was not installed as the take MIDI."
        )
'''

def patch_engine(text: str) -> tuple[str, bool]:
    if MARKER in text:
        return text, False
    anchor = 'BEATS_MAP = "bar"\n'
    if anchor not in text:
        raise RuntimeError("cover_studio/engine.py 中找不到 BEATS_MAP 锚点，未修改")
    text = text.replace(anchor, anchor + VALIDATOR + "\n", 1)

    old = '''            try:
                score_for_cover(tokenizer, cover.events, source_score).dump_midi(str(d / "cover.mid"))
                metadata_copied = source_score is not None
            except Exception:  # メタデータを写せなくても、テイクは落とさない
                log("原曲のメタデータを写せなかったので、メタデータなしで書き出します")
                tokenizer.events_to_midi(cover.events, d / "cover.mid")
                metadata_copied = False
            (d / "patches.json").write_text(json.dumps(cover.patches), encoding="utf-8")
            notes = int((cover.events[:, KIND] == KIND_NOTE).sum()) if len(cover.events) else 0
'''
    new = f'''            # {MARKER}: sort, write to a temporary file, then validate the round-trip timeline.
            midi_path = d / "cover.mid"
            tmp_midi = d / "cover.tmp.mid"
            notes = int((cover.events[:, KIND] == KIND_NOTE).sum()) if len(cover.events) else 0
            cover_end = events_end_frame(cover.events) / frame_rate if len(cover.events) else 0.0
            expected_with_meta = max(
                cover_end,
                float(source_score.end()) if source_score is not None else 0.0,
            )
            try:
                score = score_for_cover(tokenizer, cover.events, source_score)
                score.sort()
                score.dump_midi(str(tmp_midi))
                _validate_cover_midi_export(tmp_midi, expected_with_meta, notes)
                os.replace(tmp_midi, midi_path)
                metadata_copied = source_score is not None
            except Exception as export_error:
                tmp_midi.unlink(missing_ok=True)
                if source_score is None:
                    raise
                log(f"原曲メタデータ付きの書き出しに失敗: {{export_error}}。メタデータなしで再試行します")
                score = tokenizer.events_to_score(cover.events)
                score.sort()
                score.dump_midi(str(tmp_midi))
                _validate_cover_midi_export(tmp_midi, cover_end, notes)
                os.replace(tmp_midi, midi_path)
                metadata_copied = False
            (d / "patches.json").write_text(json.dumps(cover.patches), encoding="utf-8")
'''
    if old not in text:
        raise RuntimeError(
            "cover_studio/engine.py 的 MIDI 导出代码与预期不一致，未修改。\n"
            "为避免破坏你的项目，请把 engine.py 发给我检查。"
        )
    text = text.replace(old, new, 1)
    return text, True

def main() -> int:
    files = {
        ROOT / "piano_cover" / "metadata.py": patch_metadata,
        ROOT / "piano_ar" / "tokenizer.py": patch_tokenizer,
        ROOT / "cover_studio" / "engine.py": patch_engine,
    }

    # Preflight all files before changing anything.
    for path in files:
        if not path.is_file():
            print(f"[ERROR] 找不到文件：{path.relative_to(ROOT)}")
            print("请把本工具放在 tsumugi-piano-cover 项目根目录，与 piano_cover/、piano_ar/、cover_studio/ 同级。")
            return 2

    changes = {}
    try:
        for path, fn in files.items():
            original = read_text(path)
            updated, changed = fn(original)
            changes[path] = (original, updated, changed)
    except Exception as exc:
        print(f"[ERROR] 预检查失败，没有修改任何文件：{exc}")
        return 3

    changed_paths = [p for p, (_, _, changed) in changes.items() if changed]
    if not changed_paths:
        print("[OK] 这些导出保护已经存在，无需重复修改。")
        return 0

    for path in changed_paths:
        original, updated, _ = changes[path]
        backup = path.with_name(path.name + ".bak_midi_export_fix")
        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(updated, encoding="utf-8", newline="\n")
        print(f"[PATCHED] {path.relative_to(ROOT)}")
        print(f"          备份：{backup.name}")

    print("\n[OK] MIDI 导出补丁已应用。")
    print("修复内容：导出前排序；写入临时 MIDI；回读验证音符数量和总时长；异常文件不会覆盖正式 cover.mid。")
    print("下一步：重启 Cover Studio，生成一个 30s ×1 的新 Take 测试。")
    print("旧的 t0001/t0002 不会自动重写；请保留原文件，先验证新 Take。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
