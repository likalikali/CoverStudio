"""生成と採譜の中身。カバーモデルは一度読んだら次の生成でも使い回し、別のモデルを選んだときだけ読み直す。

モデルの候補 (find_models):
  export     piano_cover.export の出力 (既定 pretrained/ の下)
  checkpoint 学習のチェックポイント (既定 checkpoints/**/best.pt。隣に planner*.pt があれば差し替えた版も)
  hub        Hugging Face に公開した重み (piano_ar.hub.HF_REPOS の V2・V1。初回にダウンロードする)。一覧の先頭 (既定) は V2

好みのベクトル (piano_cover.style) は、カバーモデルの演奏者の埋め込みの表の後ろに 1 行足して入れる。学習 (train_style) は
お気に入りのカバーから、生成 (generate の style) はその行を演奏者として使う。

楽譜化のモデル (find_score_models、テイクを楽譜 (MusicXML) にする piano_perf2score) も同じ 3 種類から探す。
Hugging Face の重みは V2 の repository の piano_perf2score/ (V1 にはない)。カバーモデルと楽譜化のモデルは
VRAM を空けるため同時には持たず、使う方を読み直す。
"""

from __future__ import annotations

import gc
import json
import os
import pickletools
import random
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .project import Project, file_identity, file_sha1

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().parent / "tsumugi_worker.py"
# 原曲の MIDI に書く SheetSage の拍の形 (小節ごとに 1 つのテンポ)。変えたら既存のプロジェクトの MIDI とテイクを書き直す
BEATS_MAP = "bar"




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



class Cancelled(Exception):
    pass


# ================================================================== モデルの候補
@dataclass
class ModelEntry:
    id: str
    label: str
    kind: str  # export | checkpoint | hub
    spec: str  # load_cover に渡すもの (フォルダ・.pt・repository)
    planner: str | None = None
    num_channels: int | None = None
    tokenizer_type: str | None = None  # time | beat (None only for legacy/unknown checkpoints)


def _checkpoint_has(path: Path, key: bytes) -> bool:
    """torch.save の zip の中の pickle に key の名前があるか (重みを読まずに見分ける)"""
    try:
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith("/data.pkl"))
            with z.open(name) as f:
                head = f.read(1 << 22)
        return key in head
    except (zipfile.BadZipFile, StopIteration, OSError):
        return False


def _is_cover_checkpoint(path: Path) -> bool:
    return _checkpoint_has(path, b"cover_config")


def _checkpoint_tokenizer_type(path: Path) -> str | None:
    """Read the lightweight tokenizer_type field from a torch checkpoint pickle."""
    try:
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.endswith("/data.pkl"))
            with archive.open(name) as stream:
                payload = stream.read(1 << 22)
        strings = [
            value
            for opcode, value, _ in pickletools.genops(payload)
            if opcode.name in {"BINUNICODE", "SHORT_BINUNICODE", "BINUNICODE8", "UNICODE"}
            and isinstance(value, str)
        ]
        for index, value in enumerate(strings[:-1]):
            if value == "tokenizer_type" and strings[index + 1] in {"beat", "time"}:
                return strings[index + 1]
    except (OSError, EOFError, ValueError, zipfile.BadZipFile, StopIteration):
        return None
    return None


def find_models(
    pretrained_dir: Path | None, checkpoints_dir: Path | None, hub_repos: list[str] | tuple[str, ...]
) -> list[ModelEntry]:
    """画面の一覧の先頭が既定のモデルになるので、Hugging Face の公開した重み (hub_repos の順) を先に並べる"""
    from piano_ar.hub import HF_REPO_V1, HF_REPO_V2

    names = {HF_REPO_V2: "V2", HF_REPO_V1: "V1"}
    entries: list[ModelEntry] = [
        ModelEntry(
            f"hub:{repo}",
            f"{names[repo]} ({repo})" if repo in names else f"{repo} (Hugging Face)",
            "hub",
            repo,
            tokenizer_type="beat" if repo == HF_REPO_V2 else "time" if repo == HF_REPO_V1 else None,
        )
        for repo in hub_repos
    ]
    if pretrained_dir is not None and pretrained_dir.is_dir():
        for config_path in sorted(pretrained_dir.rglob("config.json")):
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if config.get("model_type") != "piano_cover":
                continue
            folder = config_path.parent
            rel = folder.relative_to(pretrained_dir.parent).as_posix()
            entries.append(
                ModelEntry(
                    f"export:{rel}",
                    rel,
                    "export",
                    str(folder),
                    None,
                    config["model_config"].get("num_channels"),
                    config.get("tokenizer_type"),
                )
            )
    if checkpoints_dir is not None and checkpoints_dir.is_dir():
        found = [p for p in checkpoints_dir.rglob("best.pt") if _is_cover_checkpoint(p)]
        for path in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True):
            rel = path.relative_to(checkpoints_dir).as_posix()
            tokenizer_type = _checkpoint_tokenizer_type(path)
            entries.append(ModelEntry(f"checkpoint:{rel}", rel, "checkpoint", str(path), tokenizer_type=tokenizer_type))
            for planner in sorted(path.parent.glob("planner*.pt")):
                if "features" in planner.name:
                    continue
                entries.append(
                    ModelEntry(
                        f"checkpoint:{rel}+{planner.name}",
                        f"{rel} + {planner.name}",
                        "checkpoint",
                        str(path),
                        str(planner),
                        tokenizer_type=tokenizer_type,
                    )
                )
    return entries


def _is_beat_score_checkpoint(path: Path) -> bool:
    """拍の上の演奏を楽譜にする学習 (--beat-input) のチェックポイントか。秒の演奏のもの (2・3 段目) は除く"""
    if not _checkpoint_has(path, b"perf2score_config"):
        return False
    import torch

    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=False, mmap=True)
        return bool((checkpoint.get("args") or {}).get("beat_input"))
    except Exception:  # noqa: BLE001  読めないものは候補にしない
        return False


def find_score_models(
    pretrained_dir: Path | None, checkpoints_dir: Path | None, hub_repos: list[str] | tuple[str, ...]
) -> list[ModelEntry]:
    """楽譜化のモデルの候補。一覧の先頭が既定なので、Hugging Face の公開した重みを先に並べる (V1 には楽譜化のモデルがない)"""
    from piano_ar.hub import HF_REPO_V1, HF_REPO_V2

    entries = [
        ModelEntry(f"score:hub:{repo}", f"V2 ({repo})" if repo == HF_REPO_V2 else f"{repo} (Hugging Face)", "hub", repo)
        for repo in hub_repos
        if repo != HF_REPO_V1
    ]
    if pretrained_dir is not None and pretrained_dir.is_dir():
        for config_path in sorted(pretrained_dir.rglob("config.json")):
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if config.get("model_type") == "piano_perf2score":
                folder = config_path.parent
                rel = folder.relative_to(pretrained_dir.parent).as_posix()
                entries.append(ModelEntry(f"score:export:{rel}", rel, "export", str(folder)))
    if checkpoints_dir is not None and checkpoints_dir.is_dir():
        found = [p for p in checkpoints_dir.rglob("best.pt") if _is_beat_score_checkpoint(p)]
        for path in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True):
            rel = path.relative_to(checkpoints_dir).as_posix()
            entries.append(ModelEntry(f"score:checkpoint:{rel}", rel, "checkpoint", str(path)))
    return entries


# ================================================================== tsumugi
@dataclass
class TsumugiSetup:
    dir: Path
    python: Path
    ffmpeg_dir: Path | None = None
    compile: bool = False
    device: str = "auto"

    @classmethod
    def detect(cls, tsumugi_dir: Path | None, python: Path | None, ffmpeg_dir: Path | None) -> TsumugiSetup | None:
        d = (tsumugi_dir or REPO_ROOT / ".tsumugi" / "tsumugi").resolve()
        if not (d / "instrument_agnostic_amt").is_dir():
            return None
        if python is None:
            for candidate in (d / ".venv" / "Scripts" / "python.exe", d / ".venv" / "bin" / "python"):
                if candidate.is_file():
                    python = candidate
                    break
            else:
                python = Path(sys.executable)
        if ffmpeg_dir is None and (d.parent / "ffmpeg-shared" / "bin").is_dir():
            ffmpeg_dir = d.parent / "ffmpeg-shared" / "bin"
        return cls(d, python, ffmpeg_dir)


# ================================================================== 本体
class Engine:
    def __init__(
        self,
        models: list[ModelEntry],
        device: str = "auto",
        tsumugi: TsumugiSetup | None = None,
        sheetsage_model: str | None = None,
        frames_pca: Path | None = None,
        score_models: list[ModelEntry] | None = None,
    ) -> None:
        self.models = models
        self.score_models = score_models or []
        self.device_name = device
        self.tsumugi = tsumugi
        self.sheetsage_model = sheetsage_model  # None なら sheetsage2_refined の既定 (Hugging Face)
        # 原曲の音声の特徴を使うモデル (frame_dims) の、特徴を作る PCA (piano_cover.audio_frames fit の出力)
        self.frames_pca = frames_pca or REPO_ROOT / "data" / "audio_frames" / "original" / "pca.npz"
        self._model = None
        self._model_id: str | None = None
        self._sources: OrderedDict[tuple, object] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ モデル
    @property
    def device(self):
        import torch

        if self.device_name == "auto":
            return torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return torch.device(self.device_name)

    @property
    def loaded(self) -> str | None:
        return self._model_id

    def entry(self, model_id: str | None) -> ModelEntry:
        if not self.models:
            raise ValueError(
                "使えるモデルがありません (--pretrained-dir / --checkpoints-dir / --hub-repo を確かめてください)"
            )
        if model_id is None:
            return self.models[0]
        for entry in self.models:
            if entry.id == model_id:
                return entry
        raise ValueError(f"モデルがありません: {model_id}")

    def score_entry(self, model_id: str | None) -> ModelEntry:
        if not self.score_models:
            raise ValueError("楽譜化のモデルがありません (--hub-repo / --checkpoints-dir を確かめてください)")
        if model_id is None:
            return self.score_models[0]
        for entry in self.score_models:
            if entry.id == model_id:
                return entry
        raise ValueError(f"楽譜化のモデルがありません: {model_id}")

    def model(self, entry: ModelEntry, log: Callable[[str], None] = print):
        if self._model_id == entry.id:
            return self._model
        self.unload()
        from piano_cover.hub import load_cover
        from piano_cover.style import style_slot

        log(f"モデルを読んでいます: {entry.label}")
        self._model = load_cover(entry.spec, planner=entry.planner, device=self.device)
        self._model_id = entry.id
        from piano_ar.beat_tokenizer import BeatTokenizer

        entry.tokenizer_type = "beat" if isinstance(self._model.tokenizer, BeatTokenizer) else "time"
        entry.num_channels = self._model.decoder.config.num_channels
        style_slot(self._model)  # 好みのベクトルを入れる行
        return self._model

    def score_model(self, entry: ModelEntry, log: Callable[[str], None] = print):
        """楽譜化のモデル (model, tokenizer, banned)。カバーモデルを持っていれば手放してから読む"""
        if self._model_id == entry.id:
            return self._model
        self.unload()
        from piano_perf2score.hub import load_perf2score

        log(f"楽譜化のモデルを読んでいます: {entry.label}")
        self._model = load_perf2score(entry.spec, device=self.device)
        self._model_id = entry.id
        return self._model

    def unload(self) -> None:
        """モデル (カバーか楽譜化) を手放して VRAM を空ける (採譜の前・UI のボタン)"""
        if self._model is None:
            return
        self._model = None
        self._model_id = None
        self._sources.clear()
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _source(self, project: Project, model, progress: Callable[[float, str], None], log: Callable[[str], None]):
        from piano_ar.beat_tokenizer import BeatTokenizer
        from piano_cover.generate import prepare_source

        self._source_beats(project, progress, log)
        path = project.source_path
        annotation = None
        c = model.config
        if c.sheetsage_beats or c.sheetsage_melody or c.frame_dims or isinstance(model.tokenizer, BeatTokenizer):
            annotation = self._sheetsage(project, progress, log)
        # COVERSTUDIO_SOURCE_CACHE_HASH_V1: copies can preserve mtime_ns while contents change.
        # A cache hit must depend on the actual MIDI/annotation bytes, not timestamps alone.
        key = (
            file_identity(path),
            self._model_id,
            file_identity(annotation) if annotation is not None else None,
        )
        frames = self._frames(project, progress, log) if c.frame_dims else None
        if frames is not None:
            key += (file_identity(frames),)
        if key not in self._sources:
            frame_data = None
            if frames is not None:
                with np.load(frames) as saved:
                    frame_data = saved["frames"].copy()
            self._sources[key] = prepare_source(path, model.tokenizer, model.config, annotation, frame_data)
            while len(self._sources) > 8:
                self._sources.popitem(last=False)
        self._sources.move_to_end(key)
        return self._sources[key]

    def _source_beats(
        self, project: Project, progress: Callable[[float, str], None], log: Callable[[str], None]
    ) -> None:
        """原曲の MIDI の拍 (テンポと拍子) を、音源から予測した SheetSage2-Refined の拍にする (音などの秒の位置は変えない)。
        画面の拍の線・カバーの MIDI に写すテンポと拍子・V1 のモデルが読む拍が、どれも SheetSage の拍になる。
        音源のないプロジェクトは tsumugi の拍のまま。音源と書き方 (beats_map、小節ごとのテンポは "bar") ごとに 1 回だけ
        (source の beats に印を付ける)"""
        from piano_cover.metadata import apply_sheetsage_beats

        audio = project.audio_path
        if audio is None or not audio.is_file() or not project.has_source:
            return
        source = project.data.get("source") or {}
        sha1 = project.data.get("audio_sha1")
        # COVERSTUDIO_SOURCE_CACHE_HASH_V1: audio SHA1 alone does not identify a
        # SheetSage annotation after it has been regenerated or replaced.
        previously_mapped = (
            source.get("beats") == "sheetsage"
            and source.get("beats_audio_sha1") == sha1
            and source.get("beats_map") == BEATS_MAP
        )
        known_annotation_hash = source.get("beats_annotation_sha1")
        if previously_mapped and not known_annotation_hash:
            # Legacy files contain no annotation fingerprint. Avoid modifying
            # already-mapped MIDI without proof that its beat map is stale.
            return
        if previously_mapped:
            existing_annotation = project.dir / "sheetsage.json"
            if existing_annotation.is_file() and file_sha1(existing_annotation) == known_annotation_hash:
                return

        annotation_path = self._sheetsage(project, progress, log)
        annotation_hash = file_sha1(annotation_path)
        if previously_mapped and annotation_hash == known_annotation_hash:
            return
        annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
        if previously_mapped:
            # If a fingerprinted annotation changed, preserve the previous
            # source MIDI before remapping its tempo/signature metadata.
            import shutil
            backup = project.dir / "source.before_annotation_change.mid"
            if not backup.is_file():
                shutil.copy2(project.source_path, backup)
        if apply_sheetsage_beats(project.source_path, annotation):
            project.update(source={**source, "beats": "sheetsage", "beats_audio_sha1": sha1,
                                   "beats_map": BEATS_MAP, "beats_annotation_sha1": annotation_hash})
            log("原曲の MIDI の拍を SheetSage2-Refined の拍にしました")

    # MERT_ROLLBACK_PIPELINE: restore independent SheetSage and frame extraction.
    _MERT_PIPELINE = "mert_separate_v1"

    def _needs_sheet_sage_compute(self, project: Project, model) -> bool:
        """Whether the original, separate analysis passes need to run.

        A frame cache from the earlier fused-MERT patch is *not* reused: its
        old SHA1-only metadata cannot distinguish the different computation path.
        """
        audio = project.audio_path
        if audio is None or not audio.is_file() or not project.has_source:
            return False

        sha1 = project.data.get("audio_sha1")
        annotation_path = project.dir / "sheetsage.json"
        annotation_valid = False
        if annotation_path.is_file():
            try:
                annotated = json.loads(annotation_path.read_text(encoding="utf-8"))
                annotation_valid = (
                    annotated.get("audio_sha1") == sha1
                    and annotated.get("analysis_pipeline") == self._MERT_PIPELINE
                )
            except (OSError, ValueError, json.JSONDecodeError):
                annotation_valid = False

        source = project.data.get("source") or {}
        beats_valid = (
            source.get("beats") == "sheetsage"
            and source.get("beats_audio_sha1") == sha1
            and source.get("beats_map") == BEATS_MAP
        )
        from piano_ar.beat_tokenizer import BeatTokenizer

        needs_annotation = (
            not beats_valid
            or bool(model.config.sheetsage_beats)
            or bool(model.config.sheetsage_melody)
            or isinstance(model.tokenizer, BeatTokenizer)
        )
        if needs_annotation and not annotation_valid:
            return True

        if model.config.frame_dims:
            import hashlib

            pca = Path(getattr(model, "frames_pca_path", None) or self.frames_pca)
            if not pca.is_file():
                return True
            pca_sha1 = hashlib.sha1(pca.read_bytes()).hexdigest()
            frames_path = project.dir / "frames.npz"
            if not frames_path.is_file():
                return True
            try:
                with np.load(frames_path) as saved:
                    if (
                        str(saved["audio_sha1"]) != str(sha1)
                        or str(saved["pca_sha1"]) != pca_sha1
                        or "analysis_pipeline" not in saved.files
                        or str(saved["analysis_pipeline"]) != self._MERT_PIPELINE
                    ):
                        return True
            except Exception:
                return True
        return False

    def _resolve_sheetsage_model(self) -> Path:
        """Preserve the existing local-first Hugging Face model resolution."""
        model = self.sheetsage_model
        if model is None:
            from sheetsage2_refined.pipeline import DEFAULT_MODEL

            model = DEFAULT_MODEL
        local = Path(str(model)).expanduser()
        if local.is_dir():
            return local.resolve()
        if local.is_absolute() or local.exists():
            raise FileNotFoundError(f"SheetSage model folder does not exist: {local}")

        from huggingface_hub import snapshot_download

        try:
            cached = Path(snapshot_download(str(model), local_files_only=True))
            if (cached / "config.json").is_file():
                return cached
        except Exception:
            pass
        downloaded = Path(snapshot_download(str(model)))
        if not (downloaded / "config.json").is_file():
            raise FileNotFoundError(f"SheetSage model config.json was not found: {downloaded}")
        return downloaded

    def _sheetsage(self, project: Project, progress: Callable[[float, str], None], log: Callable[[str], None]) -> Path:
        """Original SheetSage beat/melody pass, independent from FrameExtractor.

        Recompute annotations from the previous fused-MERT pipeline, keep a
        backup of the old file, and mark the output as separately computed.
        """
        import shutil

        out = project.dir / "sheetsage.json"
        audio = project.audio_path
        sha1 = project.data.get("audio_sha1")
        if out.is_file():
            try:
                previous = json.loads(out.read_text(encoding="utf-8"))
                if (
                    previous.get("audio_sha1") == sha1
                    and previous.get("analysis_pipeline") == self._MERT_PIPELINE
                ):
                    return out
            except (OSError, ValueError):
                pass

        if audio is None or not audio.is_file():
            raise FileNotFoundError(
                "このモデルは原曲の音声から拍とメロディを予測して使います。音源のあるプロジェクトで生成してください"
            )
        import torch

        from piano_ar.sheetsage import SheetSageAnnotator

        progress(0.0, "beats")
        log(f"[MERT separate] 原曲の拍とメロディを予測しています: {audio.name}")
        annotator = SheetSageAnnotator(self._resolve_sheetsage_model(), device=str(self.device))
        try:
            # No on_states callback: this is the original annotation algorithm.
            result = annotator.annotate(audio)
        finally:
            del annotator
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        result["audio_sha1"] = sha1
        result["analysis_pipeline"] = self._MERT_PIPELINE
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        try:
            if out.is_file():
                backup = project.dir / "sheetsage.before_mert_rollback.json"
                if not backup.exists():
                    shutil.copy2(out, backup)
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        log(f"拍 {len(result['beats'])}・メロディ {len(result['melody'])} 音")
        return out

    def _frames(self, project: Project, progress: Callable[[float, str], None], log: Callable[[str], None]) -> Path:
        """Original second, independent MERT forward pass for frame features.

        The previous patch stored fused and original frames using the same
        audio/PCA SHA1 pair. Reject all unmarked legacy caches once, and write
        the pipeline marker to keep future runs safe and fast.
        """
        import hashlib
        import shutil

        out = project.dir / "frames.npz"
        audio = project.audio_path
        pca = Path(getattr(self._model, "frames_pca_path", None) or self.frames_pca)
        if not pca.is_file():
            raise FileNotFoundError(f"原曲の音声の特徴を作る PCA がありません: {pca} (--frames-pca で指定)")
        sha1 = project.data.get("audio_sha1")
        pca_sha1 = hashlib.sha1(pca.read_bytes()).hexdigest()
        if out.is_file():
            try:
                with np.load(out) as saved:
                    if (
                        str(saved["audio_sha1"]) == str(sha1)
                        and str(saved["pca_sha1"]) == pca_sha1
                        and "analysis_pipeline" in saved.files
                        and str(saved["analysis_pipeline"]) == self._MERT_PIPELINE
                    ):
                        return out
            except Exception:
                pass

        if audio is None or not audio.is_file():
            raise FileNotFoundError("このモデルは原曲の音声の特徴を使います。音源のあるプロジェクトで生成してください")
        import torch

        from piano_cover.audio_frames import FrameExtractor, FrameProjector

        progress(0.0, "beats")
        log(f"[MERT separate] 原曲の音声の特徴を作っています: {audio.name}")
        extractor = FrameExtractor(self._resolve_sheetsage_model(), device=str(self.device))
        try:
            frames = FrameProjector(extractor, pca)(audio)
        finally:
            del extractor
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        tmp = out.with_suffix(".tmp.npz")
        np.savez(
            tmp,
            frames=frames,
            audio_sha1=np.str_(sha1),
            pca_sha1=np.str_(pca_sha1),
            analysis_pipeline=np.str_(self._MERT_PIPELINE),
        )
        try:
            if out.is_file():
                backup = project.dir / "frames.before_mert_rollback.npz"
                if not backup.exists():
                    shutil.copy2(out, backup)
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
        log(f"特徴 {len(frames)} フレーム (原始独立 MERT 推理)")
        return out

    # ------------------------------------------------------------ 生成
    def generate(
        self,
        project: Project,
        take_ids: list[str],
        progress: Callable[[float, str], None],
        should_stop: Callable[[], bool],
        log: Callable[[str], None],
        style: list[float] | None = None,
    ) -> None:
        """take.json (Project.new_takes で作ったもの) の設定で、take_ids の本数を一度に生成して書き込む。
        style (好みのベクトル) を渡すと、演奏者の番号の代わりにそれを使う"""
        import torch

        from piano_ar.evaluation import events_end_frame
        from piano_ar.tokenizer import KIND, KIND_NOTE
        from piano_cover.generate import CoverParams, generate_covers
        from piano_cover.metadata import score_for_cover

        with self._lock:
            # Move the take out of "queued" as soon as the worker starts. Model
            # loading and source analysis can each take minutes on a cold cache.
            for take_id in take_ids:
                project.update_take(take_id, state="running", error=None)
            progress(0.0, "loading")  # 画面が言語に合わせて訳す (web/src/i18n.ts の progressText)
            first = project.take(take_ids[0])
            entry = self.entry(first["model"]["id"])
            model = self.model(entry, log)
            params = CoverParams.from_dict(first["params"])
            if style is not None:
                from piano_cover.style import set_style

                params.channel = set_style(model, style)
            elif params.channel and entry.num_channels and params.channel >= entry.num_channels:
                raise ValueError(f"演奏者の番号は 1〜{entry.num_channels - 1} です")
            if should_stop():
                raise Cancelled()

            # V2 source analysis uses the 2.7GB SheetSage/MERT encoder. On a
            # 6GB card, temporarily move the cover model to CPU so the encoder
            # and its activations do not compete for the same VRAM.
            needs_source_analysis = self._needs_sheet_sage_compute(project, model)
            target_device = self.device
            if target_device.type == "cuda" and needs_source_analysis:
                log("SheetSage のためカバーモデルを一時的に CPU へ退避します")
                model.to("cpu")
                torch.cuda.empty_cache()
                try:
                    source = self._source(project, model, progress, log)
                finally:
                    model.to(target_device)
                    torch.cuda.empty_cache()
                log("カバーモデルを GPU に戻しました")
            else:
                source = self._source(project, model, progress, log)

            if should_stop():
                raise Cancelled()
            tokenizer = model.tokenizer
            frame_rate = tokenizer.config.frame_rate

            prompt = None
            keep = first.get("continue_from")
            if keep:
                # 指定した時刻を含まないパッチまで (生成は原曲の最初の音から始まるので lead を引く)
                patches = project.take_patches(keep["take"])
                if source.grid is not None:
                    starts = source.grid.beats[source.grid.bar_starts]
                    count = int(np.searchsorted(starts, keep["seconds"] * frame_rate, side="right") - 1)
                else:
                    count = int((keep["seconds"] * frame_rate - source.lead) // tokenizer.patch_frames)
                prompt = patches[: max(0, min(count, len(patches)))] or None

            def on_patch(done: int, total: int) -> None:
                if should_stop():
                    raise Cancelled()
                if source.grid is not None:
                    starts = source.grid.beats[source.grid.bar_starts]
                    elapsed = starts[min(done, len(starts) - 1)] / frame_rate
                    duration = starts[min(total, len(starts) - 1)] / frame_rate
                else:
                    elapsed = done * tokenizer.config.patch_seconds
                    duration = total * tokenizer.config.patch_seconds
                progress(done / total, f"{elapsed:.0f}/{duration:.0f}")

            seed = first["seed"]
            torch.manual_seed(seed)
            random.seed(seed)
            np.random.seed(seed % (2**32))
            covers = generate_covers(model, source, params, num_samples=len(take_ids), prompt=prompt, progress=on_patch)

        # 原曲のメタデータ (テンポ・拍子・調・コードマーカー) は、書き出す前に Score に入れて
        # 1 回だけ書き出す。書き出してから写すと秒 -> tick の丸めが 2 回起きて、音が少しずつずれる
        source_score = None
        if project.has_source:
            try:
                from symusic import Score

                source_score = Score(str(project.source_path)).to("second")
            except Exception:
                log("原曲のメタデータを読めなかったので、メタデータなしで書き出します")
        source_created = (project.data.get("source") or {}).get("created")
        source_beats = (project.data.get("source") or {}).get("beats_map")

        for take_id, cover in zip(take_ids, covers):
            d = project.take_dir(take_id)
            # MIDI_EXPORT_TIMELINE_GUARD: sort, write to a temporary file, then validate the round-trip timeline.
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
                log(f"原曲メタデータ付きの書き出しに失敗: {export_error}。メタデータなしで再試行します")
                score = tokenizer.events_to_score(cover.events)
                score.sort()
                score.dump_midi(str(tmp_midi))
                _validate_cover_midi_export(tmp_midi, cover_end, notes)
                os.replace(tmp_midi, midi_path)
                metadata_copied = False
            (d / "patches.json").write_text(json.dumps(cover.patches), encoding="utf-8")
            project.update_take(
                take_id,
                state="done",
                duration=events_end_frame(cover.events) / frame_rate if len(cover.events) else 0.0,
                notes=notes,
                # どの原曲から作ったかと、メタデータを入れ終えた印 (古いテイクを後から直すときに使う)
                source_created=source_created,
                source_beats=source_beats,
                metadata_copied=metadata_copied,
            )

    # ------------------------------------------------------------ 好みの学習
    def train_style(
        self,
        model_id: str,
        favorites: list[tuple[Project, dict]],
        init: list[int],
        progress: Callable[[float, str], None],
        should_stop: Callable[[], bool],
        log: Callable[[str], None],
    ) -> dict:
        """favorites (お気に入りのテイク) から好みのベクトルを学習する (piano_cover.style)。init の演奏者の埋め込みの平均から始める。
        原曲が作り直されたテイクと、音源のない曲 (原曲の拍とメロディを作れない) は使わない。結果を dict で返す"""
        import torch

        from piano_ar.beat_tokenizer import BeatTokenizer
        from piano_ar.config import TokenizerConfig
        from piano_ar.tokenizer import PianoTokenizer
        from piano_cover.style import StyleExample, StyleSettings, style_slot, train_style

        with self._lock:
            entry = self.entry(model_id)
            progress(0.0, "loading")
            model = self.model(entry, log)
            if not isinstance(model.tokenizer, BeatTokenizer):
                raise ValueError("好みの学習は拍のトークンのモデル (V2 以降) だけで使えます")
            reader = PianoTokenizer(TokenizerConfig(frame_rate=model.tokenizer.config.frame_rate))
            examples = []
            for i, (project, take) in enumerate(favorites):
                if should_stop():
                    raise Cancelled()
                progress(0.0, f"prepare:{i + 1}/{len(favorites)}")
                label = f"{project.data['title']} {take.get('name') or '#' + str(take['number'])}"
                source_created = (project.data.get("source") or {}).get("created")
                midi = project.take_midi(take["id"])
                if take.get("source_created") not in (None, source_created) or not midi.is_file():
                    log(f"使えないので外します (原曲を作り直した後か、MIDI がない): {label}")
                    continue
                try:
                    source = self._source(project, model, progress, log)
                except FileNotFoundError as e:
                    log(f"使えないので外します ({e}): {label}")
                    continue
                patches = None
                if take["model"]["id"] == entry.id and (project.take_dir(take["id"]) / "patches.json").is_file():
                    patches = project.take_patches(take["id"])
                    events = model.tokenizer.patches_to_events(patches, source.grid)
                else:  # 別のモデルのテイクは MIDI から (原曲の時刻のまま)
                    events, _ = reader.midi_to_events(midi, trim=False)
                examples.append(StyleExample(source, events, patches))
                log(f"使います: {label}")
            if not examples:
                raise ValueError("使えるお気に入りがありません")

            base = style_slot(model).base.weight.detach()
            start = base[torch.tensor(init or [0], device=base.device)].float().mean(0)
            settings = StyleSettings()

            def on_step(done: int, total: int, loss: float) -> None:
                if should_stop():
                    raise Cancelled()
                progress(done / total, f"style:{done}/{total}:{loss:.3f}")

            log(f"{len(examples)} 本で学習します (更新 {settings.steps} 回)")
            result = train_style(model, examples, start, settings, on_step)
        log(f"損失 {result.loss_before:.4f} -> {result.loss_after:.4f}、初期値からの距離 {result.distance:.2f}")
        return {
            "vector": [round(float(v), 6) for v in result.vector],
            "used": len(examples),
            "loss_before": result.loss_before,
            "loss_after": result.loss_after,
            "distance": result.distance,
        }

    # ------------------------------------------------------------ 楽譜化
    def score(
        self,
        project: Project,
        take_id: str,
        progress: Callable[[float, str], None],
        should_stop: Callable[[], bool],
        log: Callable[[str], None],
    ) -> int:
        """テイクのカバーを楽譜 (takes/<id>/score.musicxml) にし、小節の数を返す。モデルは take.json の score.model。
        拍は原曲の音声の SheetSage2-Refined の拍 (V2 のカバーを作ったときのもの)、コード記号は原曲の MIDI のコードマーカー"""
        from piano_perf2score.generate import score_from_cover
        from piano_score.musicxml import write_musicxml

        with self._lock:
            take = project.take(take_id)
            entry = self.score_entry(take["score"]["model"]["id"])
            midi = project.take_midi(take_id)
            if not midi.is_file():
                raise FileNotFoundError("このテイクはまだ生成していません")
            if project.audio_path is None and not (project.dir / "sheetsage.json").is_file():
                raise FileNotFoundError("楽譜にするには原曲の拍が要ります。音源のあるプロジェクトで使ってください")
            annotation = json.loads(self._sheetsage(project, progress, log).read_text(encoding="utf-8"))
            progress(0.0, "loading")
            model, tokenizer, banned = self.score_model(entry, log)

            def on_measure(done: int, total: int) -> None:
                if should_stop():
                    raise Cancelled()
                progress(done / max(total, 1), f"bars:{done}/{total}")

            log(f"楽譜にしています: {take_id}")
            chords = project.source_path if project.has_source else None
            measures = score_from_cover(
                model, tokenizer, midi, annotation, chords, banned=banned, on_measure=on_measure
            )
        name = take.get("name") or f"#{take['number']}"
        out = project.take_dir(take_id) / "score.musicxml"
        tmp = out.with_suffix(".tmp")
        write_musicxml(measures, tmp, title=f"{project.data['title']} {name}")
        tmp.replace(out)
        log(f"{len(measures)} 小節")
        return len(measures)

    # ------------------------------------------------------------ 採譜
    def transcribe(
        self,
        project: Project,
        progress: Callable[[float | None, str], None],
        should_stop: Callable[[], bool],
        log: Callable[[str], None],
        live: Callable[[str, dict], None] | None = None,
    ) -> None:
        """live(種類, 中身) には worker が流す途中経過 (確定したノートなど。tsumugi_worker.emit) を渡す"""
        if self.tsumugi is None:
            raise RuntimeError("tsumugi が見つかりません (--tsumugi-dir で場所を指定してください)")
        audio = project.audio_path
        if audio is None:
            raise RuntimeError("音源のないプロジェクトは採譜できません")
        # tsumugi は VRAM を多く使うので、カバーモデルを手放してから走らせる
        self.unload()
        out = project.dir / "source.tsumugi.mid"
        command = [
            str(self.tsumugi.python),
            "-X",
            "utf8",
            str(WORKER),
            "--tsumugi-dir",
            str(self.tsumugi.dir),
            "--audio",
            str(audio),
            "--out",
            str(out),
            "--work",
            # ステムの音声など大きい作業ファイル (1 曲数百 MB) は、プロジェクト (Google ドライブのこともある) に置かない
            str(Path(tempfile.gettempdir()) / "cover_studio_tsumugi" / project.data["created"].replace(":", "")),
        ]
        if self.tsumugi.ffmpeg_dir:
            command += ["--ffmpeg-dir", str(self.tsumugi.ffmpeg_dir)]
        if self.tsumugi.compile:
            command.append("--compile")
        command += ["--device", self.tsumugi.device]
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        env.pop("VIRTUAL_ENV", None)
        progress(None, "starting")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(self.tsumugi.dir),
        )
        stopped = threading.Event()

        def watch() -> None:
            while process.poll() is None:
                if should_stop():
                    stopped.set()
                    process.kill()
                    return
                stopped.wait(0.5)

        threading.Thread(target=watch, daemon=True).start()
        assert process.stdout is not None
        for line in process.stdout:  # tqdm の \r も行の区切りになる
            line = line.rstrip()
            if line.startswith("@@"):
                kind, _, payload = line[2:].partition(" ")
                if live is not None:
                    try:
                        live(kind, json.loads(payload))
                    except json.JSONDecodeError:
                        pass
            elif line:
                log(line)
                progress(None, line[-120:])
        code = process.wait()
        stopped.set()
        if should_stop():
            raise Cancelled()
        if code != 0 or not out.is_file():
            raise RuntimeError(f"tsumugi が失敗しました (終了コード {code})。ログを確かめてください")
        project.set_source(out, "tsumugi")
        self._source_beats(project, progress, log)
        out.unlink()

    def describe(self) -> dict:
        return {
            "models": [asdict(m) for m in self.models],
            "score_models": [asdict(m) for m in self.score_models],
            "loaded": self._model_id,
            "device": str(self.device),
            "tsumugi": self.tsumugi is not None,
        }
