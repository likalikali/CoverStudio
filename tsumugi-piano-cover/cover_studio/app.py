"""Cover Studio の API (FastAPI)。重い処理 (採譜・生成) は Runner の列に流し、画面は状態を問い合わせて進み具合を出す。

  設定         : GET /api/config、POST /api/engine/unload
  プロジェクト : GET/POST /api/projects、GET/PATCH/DELETE /api/projects/{pid}
  原曲         : POST …/transcribe、PUT …/source (手元の MIDI)、GET …/audio、…/source.mid、…/source/view
  テイク       : POST …/takes (生成)、PATCH/DELETE …/takes/{tid}、POST …/takes/{tid}/trim (選んだ秒まで切り捨て) と
                 …/trim/undo (直前の切り捨てを元に戻す)、…/takes/{tid}/splice (原曲で頭を補う) と …/splice/undo、
                 GET …/takes/{tid}/cover.mid、…/view
  好み         : GET/POST /api/styles (お気に入りから学習)、PATCH/DELETE /api/styles/{sid}
  ジョブ       : GET /api/jobs、DELETE /api/jobs/{id} (取り消し・停止)

{pid} はプロジェクトのフォルダ名。web/ のビルド (cover_studio/static) があれば / で配る。
"""

from __future__ import annotations

import logging
import random
import shutil
import uuid
from contextlib import ExitStack, asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .engine import Engine
from .jobs import Job, LiveTranscription, Runner
from .project import Project, now, projects, safe_name
from .styles import StyleStore
from .web import STATIC_DIR

log = logging.getLogger(__name__)

# API を変えたら上げる。画面 (web/src/api.ts の API_VERSION) と違えば、画面がサーバの起動し直しを促す
API_VERSION = 6
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac")
MIDI_EXTS = (".mid", ".midi")
# 画面の設定の既定値 (CoverParams の既定値のうち、Cover Studio だけで変えるもの)。拍のトークンのモデル (b4 / 5a) を
# 聴いて決めた: 原曲のリズムに寄せ、強弱と編曲の性質は学習データのまま。演奏者は公開番号 227 (Hugging Face の V1 / V2 の
# 番号。学習のチェックポイントは学習時の番号なので、同じ 227 でも別の演奏者になる)
STUDIO_DEFAULTS = {"onset_bias": 4.0, "dynamics": 1.0, "fill": 0.0, "span": 0.0, "channel": 227, "top_p": 1.0}


def studio_params(values: dict | None = None) -> dict:
    """生成の設定。style は好みのベクトルの id (Cover Studio だけのもの。あれば演奏者の番号の代わりに使う)"""
    from piano_cover.generate import CoverParams

    values = values or {}
    return {**asdict(CoverParams.from_dict({**STUDIO_DEFAULTS, **values})), "style": values.get("style") or None}


class ContinueFrom(BaseModel):
    take: str
    seconds: float = Field(ge=0)


class GenerateIn(BaseModel):
    params: dict = {}
    model: str | None = None
    count: int = Field(default=2, ge=1, le=4)
    seed: int | None = None
    continue_from: ContinueFrom | None = None


class ProjectPatch(BaseModel):
    title: str | None = None


class ScoreIn(BaseModel):
    model: str | None = None


class StyleIn(BaseModel):
    name: str = ""
    model: str | None = None


class StylePatch(BaseModel):
    name: str


class TakePatch(BaseModel):
    name: str | None = None
    favorite: bool | None = None
    memo: str | None = None


class TrimIn(BaseModel):
    """テイクをどこで切り捨てるか (秒。ピアノロール・再生と同じ時計)"""

    seconds: float = Field(gt=0)


class SpliceIn(BaseModel):
    """原曲で頭をどこまで補うか (秒。この位置より前を原曲の音で埋める)"""

    seconds: float = Field(gt=0)


def create_app(root: str | Path, engine: Engine) -> FastAPI:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for p in projects(root):
        p.recover()
    styles = StyleStore(root)
    styles.recover()

    def open_project(pid: str) -> Project:
        d = root / pid
        if pid in ("", ".", "..") or Path(pid).name != pid or not (d / "project.json").is_file():
            raise HTTPException(404, f"プロジェクトがありません: {pid}")
        return Project(d)

    # ------------------------------------------------------------ ジョブの中身
    def handle(job: Job) -> None:
        def progress(value: float | None, message: str) -> None:
            job.progress, job.message = value, message

        if job.kind == "style":  # 曲に属さないジョブ。takes[0] が好みの id
            style = styles.update(job.takes[0], state="running", error=None)
            favorites = []
            for ref in style["takes"]:
                try:
                    project = open_project(ref["project"])
                    favorites.append((project, project.take(ref["take"])))
                except (HTTPException, KeyError):
                    job.add_log(f"見つからないので外します: {ref['project']} {ref['take']}")
            result = engine.train_style(
                style["model"]["id"], favorites, style["init"], progress, job._stop.is_set, job.add_log
            )
            styles.update(job.takes[0], state="done", trained=now(), **result)
            return

        p = open_project(job.project)
        if job.kind == "transcribe":
            p.update(transcribe={"state": "running", "error": None})
            live = job._live = LiveTranscription()

            def log(line: str) -> None:
                job.add_log(line)
                live.on_log(line)

            engine.transcribe(p, progress, job._stop.is_set, log, live.on_live)
        elif job.kind == "score":
            take_id = job.takes[0]
            p.update_take(take_id, score={**p.take(take_id)["score"], "state": "running", "error": None})
            measures = engine.score(p, take_id, progress, job._stop.is_set, job.add_log)
            p.update_take(
                take_id, score={**p.take(take_id)["score"], "state": "done", "measures": measures, "created": now()}
            )
        else:
            style = p.take(job.takes[0])["params"].get("style")
            vector = None
            if style:
                try:
                    vector = styles.get(style).get("vector")
                except KeyError:
                    vector = None
                if vector is None:
                    raise ValueError("好みのベクトルが見つかりません (消したか、まだ学習が終わっていません)")
            engine.generate(p, job.takes, progress, job._stop.is_set, job.add_log, style=vector)

    def on_end(job: Job) -> None:
        if job.kind == "style":
            try:
                style = styles.get(job.takes[0])
            except KeyError:
                return  # 消された
            if style["state"] in ("queued", "running"):
                state = "cancelled" if job.state == "cancelled" else "error"
                styles.update(style["id"], state=state, error=job.error)
            return
        try:
            p = open_project(job.project)
        except HTTPException:
            return  # プロジェクトごと消された
        if job.kind == "transcribe":
            state = {"done": "done", "cancelled": "cancelled"}.get(job.state, "error")
            p.update(transcribe={"state": state, "error": job.error})
            return
        if job.kind == "score":
            try:
                score = p.take(job.takes[0]).get("score") or {}
            except KeyError:
                return
            if score.get("state") in ("queued", "running"):
                state = "cancelled" if job.state == "cancelled" else "error"
                p.update_take(job.takes[0], score={**score, "state": state, "error": job.error})
            return
        for take_id in job.takes:
            try:
                take = p.take(take_id)
            except KeyError:
                continue
            if take["state"] in ("queued", "running"):
                state = "cancelled" if job.state == "cancelled" else "error"
                p.update_take(take_id, state=state, error=job.error)

    runner = Runner(handle, on_end)

    def project_busy(pid: str) -> bool:
        """Project jobs plus style training, which reads favorite takes across projects."""
        return bool(runner.active_for(pid)) or any(job.kind == "style" for job in runner.active_for(""))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        runner.close()

    app = FastAPI(title="Cover Studio", lifespan=lifespan)
    app.state.runner = runner
    app.state.engine = engine

    def detail(p: Project) -> dict:
        data = p.data
        jobs = runner.jobs(p.id)
        live, scoring = {}, {}
        for job in jobs:
            if job.active and job.kind == "generate":
                for take_id in job.takes:
                    live[take_id] = job
            elif job.active and job.kind == "score":
                scoring[job.takes[0]] = job
        takes = []
        for take in p.takes():
            job = live.get(take["id"])
            if job is not None:
                take = {**take, "job": job.id, "progress": job.progress, "message": job.message}
            job = scoring.get(take["id"])
            if job is not None and take.get("score"):
                score = {**take["score"], "job": job.id, "progress": job.progress, "message": job.message}
                take = {**take, "score": score}
            takes.append(take)
        transcribe = data.get("transcribe")
        job = next((j for j in jobs if j.kind == "transcribe"), None)
        if transcribe is not None and job is not None:
            transcribe = {**transcribe, "job": job.id, "message": job.message, "log": job.log[-80:]}
            if job.active:
                transcribe["state"] = job.state
        return {
            "id": p.id,
            "title": data["title"],
            "created": data["created"],
            "audio": p.audio_path.name if p.audio_path else None,
            "source": data.get("source") if p.has_source else None,
            "transcribe": transcribe,
            "takes": takes,
            "busy": any(j.active for j in jobs),
        }

    def summary(p: Project) -> dict:
        data = p.data
        takes = p.takes()
        return {
            "id": p.id,
            "title": data["title"],
            "created": data["created"],
            "audio": p.audio_path.name if p.audio_path else None,
            "has_source": p.has_source,
            "transcribe": data.get("transcribe"),
            "takes": len(takes),
            "favorites": sum(t.get("favorite", False) for t in takes),
            "busy": bool(runner.active_for(p.id)),
        }

    def save_upload(upload: UploadFile, exts: tuple[str, ...]) -> Path:
        name = Path(upload.filename or "").name
        if Path(name).suffix.lower() not in exts:
            raise HTTPException(400, f"対応していないファイルです ({' / '.join(exts)}): {name}")
        path = root / ".uploads" / uuid.uuid4().hex / name
        path.parent.mkdir(parents=True)
        with open(path, "wb") as f:
            shutil.copyfileobj(upload.file, f)
        return path

    def submit_transcribe(p: Project) -> Job:
        if engine.tsumugi is None:
            raise HTTPException(400, "tsumugi が見つからないので採譜できません (MIDI を一緒に入れてください)")
        p.update(transcribe={"state": "queued", "error": None})
        return runner.submit("transcribe", p.id)

    # ------------------------------------------------------------ 設定
    @app.get("/api/config")
    def config():
        return {
            **engine.describe(),
            "defaults": studio_params(),
            "busy": any(j.active for j in runner.jobs()),
            "api_version": API_VERSION,
        }

    @app.post("/api/engine/unload")
    def unload():
        if any(j.state == "running" for j in runner.jobs()):
            raise HTTPException(409, "実行中のジョブがあります")
        engine.unload()
        return engine.describe()

    # ------------------------------------------------------------ プロジェクト
    @app.get("/api/projects")
    def list_projects():
        return [summary(p) for p in projects(root)]

    @app.post("/api/projects")
    def create_project(
        audio: Annotated[UploadFile | None, File()] = None,
        midi: Annotated[UploadFile | None, File()] = None,
        title: Annotated[str, Form()] = "",
    ):
        """音源 (と、あれば採譜済みの MIDI) からプロジェクトを作る。MIDI がなければ tsumugi の採譜を始める"""
        if audio is None and midi is None:
            raise HTTPException(400, "音源か MIDI のどちらかが要ります")
        audio_path = save_upload(audio, AUDIO_EXTS) if audio is not None else None
        midi_path = save_upload(midi, MIDI_EXTS) if midi is not None else None
        try:
            name = title.strip() or Path((audio_path or midi_path).name).stem
            p = Project.create(root, name, audio_path, midi_path)
        finally:
            for path in (audio_path, midi_path):
                if path is not None:
                    shutil.rmtree(path.parent, ignore_errors=True)
        if midi_path is None:
            submit_transcribe(p)
        return detail(p)

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return detail(open_project(pid))

    @app.patch("/api/projects/{pid}")
    def update_project(pid: str, body: ProjectPatch):
        p = open_project(pid)
        if body.title is not None and body.title.strip():
            p.update(title=body.title.strip())
        return detail(p)

    @app.delete("/api/projects/{pid}", status_code=204)
    def delete_project(pid: str):
        with runner.project_lock(pid):
            p = open_project(pid)
            if project_busy(pid):
                raise HTTPException(409, "実行中・待ちのジョブがあります")
            p.delete()
            runner.forget(pid)

    # ------------------------------------------------------------ 原曲
    @app.post("/api/projects/{pid}/transcribe")
    def transcribe(pid: str):
        with runner.project_lock(pid):
            p = open_project(pid)
            if project_busy(pid):
                raise HTTPException(409, "この曲の処理中は採譜を始められません。処理が終わってから再試行してください")
            submit_transcribe(p)
            return detail(p)

    @app.get("/api/projects/{pid}/transcribe/live")
    def transcribe_live(pid: str, since: int = 0):
        """採譜中に確定したノート (since 番目から後ろ) とステムごとの進み具合。採譜していなければ空"""
        open_project(pid)
        job = next((j for j in runner.jobs(pid) if j.kind == "transcribe"), None)
        if job is None or job._live is None:
            return {"seq": 0, "events": [], "stems": [], "stage": None, "active": bool(job and job.active)}
        return {**job._live.since(since), "active": job.active}

    @app.put("/api/projects/{pid}/source")
    def upload_source(pid: str, midi: Annotated[UploadFile, File()]):
        path = save_upload(midi, MIDI_EXTS)
        try:
            with runner.project_lock(pid):
                p = open_project(pid)
                if project_busy(pid):
                    raise HTTPException(
                        409,
                        "採譜・生成・楽譜化・好みの学習など、処理中は原曲 MIDI を差し替えられません。処理が終わってから再試行してください",
                    )
                p.set_source(path, "upload")
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        return detail(p)

    @app.get("/api/projects/{pid}/audio")
    def get_audio(pid: str):
        p = open_project(pid)
        if p.audio_path is None or not p.audio_path.is_file():
            raise HTTPException(404, "音源がありません")
        return FileResponse(p.audio_path)

    @app.get("/api/projects/{pid}/source.mid")
    def get_source_midi(pid: str):
        p = open_project(pid)
        if not p.has_source:
            raise HTTPException(404, "原曲の MIDI はまだありません")
        return FileResponse(p.source_path, media_type="audio/midi", filename=f"{safe_name(p.data['title'])}_source.mid")

    @app.get("/api/projects/{pid}/source/view")
    def get_source_view(pid: str):
        from .midi_view import source_view

        p = open_project(pid)
        if not p.has_source:
            raise HTTPException(404, "原曲の MIDI はまだありません")
        return source_view(p.source_path)

    # ------------------------------------------------------------ テイク
    @app.post("/api/projects/{pid}/takes")
    def generate(pid: str, body: GenerateIn):
        with runner.project_lock(pid):
            p = open_project(pid)
            if not p.has_source:
                raise HTTPException(400, "原曲の MIDI がまだありません")
            try:
                entry = engine.entry(body.model)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            if body.continue_from is not None:
                try:
                    source_take = p.take(body.continue_from.take)
                except KeyError as e:
                    raise HTTPException(404, f"テイクがありません: {body.continue_from.take}") from e
                if source_take["state"] != "done":
                    raise HTTPException(400, "生成が終わったテイクからだけ続きを作れます")
            params = studio_params(body.params)
            if params["style"]:
                try:
                    style = styles.get(params["style"])
                except KeyError as e:
                    raise HTTPException(400, "好みが見つかりません (消したかもしれません)") from e
                if style["state"] != "done":
                    raise HTTPException(400, "この好みはまだ学習が終わっていません")
                if style["model"]["id"] != entry.id:
                    raise HTTPException(
                        400, f"この好みは {style['model']['label']} で学習したものです。同じモデルで使ってください"
                    )
            record = {
                "params": params,
                "model": {"id": entry.id, "label": entry.label, "tokenizer_type": entry.tokenizer_type},
                "seed": body.seed if body.seed is not None else random.randrange(2**31),
                "count": body.count,
                "continue_from": body.continue_from.model_dump() if body.continue_from else None,
            }
            takes = p.new_takes(body.count, record)
            runner.submit("generate", pid, [t["id"] for t in takes])
            return detail(p)

    def find_take(p: Project, tid: str) -> dict:
        try:
            return p.take(tid)
        except KeyError as e:
            raise HTTPException(404, f"テイクがありません: {tid}") from e

    @app.patch("/api/projects/{pid}/takes/{tid}")
    def update_take(pid: str, tid: str, body: TakePatch):
        with runner.project_lock(pid):
            p = open_project(pid)
            find_take(p, tid)
            return p.update_take(tid, **body.model_dump(exclude_none=True))

    @app.delete("/api/projects/{pid}/takes/{tid}", status_code=204)
    def delete_take(pid: str, tid: str):
        with runner.project_lock(pid):
            p = open_project(pid)
            find_take(p, tid)
            if any(tid in j.takes for j in runner.active_for(pid)) or any(
                j.kind == "style" for j in runner.active_for("")
            ):
                raise HTTPException(409, "生成中・待ち、または好みの学習中のテイクは先に処理を止めてください")
            p.delete_take(tid)

    # ------------------------------------------------------------ 切り捨て・頭の補完 (後処理)
    def take_edit_allowed(p: Project, pid: str, tid: str) -> dict:
        """後処理 (切り捨て・頭の補完) してよい状態か。問題なければテイクを返す"""
        take = find_take(p, tid)
        if take["state"] != "done":
            raise HTTPException(400, "生成が終わったテイクだけ処理できます")
        if any(tid in j.takes for j in runner.active_for(pid)) or any(j.kind == "style" for j in runner.active_for("")):
            raise HTTPException(409, "生成中・待ち、または好みの学習中のテイクは先に処理を止めてください")
        return take

    @app.post("/api/projects/{pid}/takes/{tid}/trim")
    def trim_take(pid: str, tid: str, body: TrimIn):
        """テイクの MIDI を選んだ秒で切り捨てる (終わりの外れた音を捨てる後処理。元に戻せる)"""
        from .midi_trim import trim_take_midi

        with runner.project_lock(pid):
            p = open_project(pid)
            take = take_edit_allowed(p, pid, tid)
            path = p.take_midi(tid)
            if not path.is_file():
                raise HTTPException(404, "まだ生成していません")
            validate_take_midi(p, take, path)
            try:
                result = trim_take_midi(path, body.seconds)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            # 楽譜は切り捨て前の音に合わせて作ってあるので、作り直し前提で消す
            score_path = p.take_dir(tid) / "score.musicxml"
            score_path.unlink(missing_ok=True)
            p.update_take(
                tid,
                duration=result["duration"],
                notes=result["notes"],
                trim={"cut": result["cut"], "removed": result["removed"], "created": now()},
                score=None,
            )
            return detail(p)

    @app.post("/api/projects/{pid}/takes/{tid}/trim/undo")
    def undo_trim(pid: str, tid: str):
        """直前の切り捨てを元に戻す (バックアップは 1 段階だけ持っている)"""
        from .midi_trim import undo_take_trim

        with runner.project_lock(pid):
            p = open_project(pid)
            take_edit_allowed(p, pid, tid)
            path = p.take_midi(tid)
            if not path.is_file():
                raise HTTPException(404, "まだ生成していません")
            try:
                result = undo_take_trim(path)
            except FileNotFoundError as e:
                raise HTTPException(404, str(e)) from e
            score_path = p.take_dir(tid) / "score.musicxml"
            score_path.unlink(missing_ok=True)
            p.update_take(tid, duration=result["duration"], notes=result["notes"], trim=None, score=None)
            return detail(p)

    @app.post("/api/projects/{pid}/takes/{tid}/splice")
    def splice_take(pid: str, tid: str, body: SpliceIn):
        """選んだ秒より前を原曲 MIDI の音で埋める (モデルが前奏を飛ばしたときの後処理。元に戻せる)"""
        from .midi_splice import splice_head_from_source

        with runner.project_lock(pid):
            p = open_project(pid)
            take = take_edit_allowed(p, pid, tid)
            if not p.has_source:
                raise HTTPException(400, "原曲の MIDI がないので頭を補えません")
            path = p.take_midi(tid)
            if not path.is_file():
                raise HTTPException(404, "まだ生成していません")
            validate_take_midi(p, take, path)
            try:
                result = splice_head_from_source(path, p.source_path, body.seconds)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            score_path = p.take_dir(tid) / "score.musicxml"
            score_path.unlink(missing_ok=True)
            p.update_take(
                tid,
                duration=result["duration"],
                notes=result["notes"],
                splice={"cut": result["cut"], "added": result["added"], "removed": result["removed"], "created": now()},
                score=None,
            )
            return detail(p)

    @app.post("/api/projects/{pid}/takes/{tid}/splice/undo")
    def undo_splice(pid: str, tid: str):
        """直前の頭の補完を元に戻す (バックアップは 1 段階だけ持っている)"""
        from .midi_splice import undo_splice as undo_head_splice

        with runner.project_lock(pid):
            p = open_project(pid)
            take_edit_allowed(p, pid, tid)
            path = p.take_midi(tid)
            if not path.is_file():
                raise HTTPException(404, "まだ生成していません")
            try:
                result = undo_head_splice(path)
            except FileNotFoundError as e:
                raise HTTPException(404, str(e)) from e
            score_path = p.take_dir(tid) / "score.musicxml"
            score_path.unlink(missing_ok=True)
            p.update_take(tid, duration=result["duration"], notes=result["notes"], splice=None, score=None)
            return detail(p)

    def expected_take_midi_end(p: Project, take: dict) -> float | None:
        expected = float(take.get("duration") or 0.0)
        if p.has_source:
            try:
                from .midi_repair import midi_duration_seconds

                expected = max(expected, midi_duration_seconds(p.source_path))
            except Exception:
                log.debug("原曲 MIDI の長さを検証できませんでした", exc_info=True)
        return expected if expected > 0 else None

    def validate_take_midi(p: Project, take: dict, path: Path) -> None:
        from .midi_repair import ensure_midi_timeline

        try:
            ensure_midi_timeline(path, expected_take_midi_end(p, take))
        except Exception as exc:
            raise HTTPException(422, f"このテイクの MIDI 時間軸が壊れています: {exc}") from exc

    @app.get("/api/projects/{pid}/takes/{tid}/cover.mid")
    def get_take_midi(pid: str, tid: str):
        p = open_project(pid)
        take = find_take(p, tid)
        path = p.take_midi(tid)
        if not path.is_file():
            raise HTTPException(404, "まだ生成していません")
        # 旧版で発生した 28-bit MIDI delta wrap を、配布前に一度だけ安全に復旧する。
        validate_take_midi(p, take, path)
        # メタデータなしで書かれた古いテイクだけ、ここで 1 回だけ直す (書き込みは一時ファイル -> 置換)。
        # 毎回書き直すと秒 -> tick の丸めが積もって音が少しずつずれるうえ、同時のダウンロードと
        # ぶつかって書きかけのファイルを返すことがある。直したら take.json に印を残す。
        source_created = (p.data.get("source") or {}).get("created")
        recorded = take.get("source_created")
        if recorded is not None:
            same_source = recorded == source_created
        else:  # 印がない古いテイク: 原曲がテイクより後に作られていれば、別の原曲に入れ替わっている
            same_source = source_created is None or source_created <= (take.get("created") or "")
        # 原曲の拍を書き直した (SheetSage の拍にした・小節ごとのテンポにした) 後のテイクも、同じように 1 回だけ写し直す
        source_beats = (p.data.get("source") or {}).get("beats_map")
        stale = not take.get("metadata_copied") or take.get("source_beats") != source_beats
        if p.has_source and stale and same_source:
            try:
                from piano_cover.metadata import copy_metadata

                copy_metadata(p.source_path, path)
                p.update_take(tid, metadata_copied=True, source_beats=source_beats)
            except Exception:  # 写せなくても、中身 (音) は返す
                log.exception("cover.mid にメタデータを写せませんでした: %s", path)
        validate_take_midi(p, take, path)
        name = f"{safe_name(p.data['title'])}_{take['name'] or tid}.mid"
        return FileResponse(path, media_type="audio/midi", headers={"Content-Disposition": _attachment(name)})

    @app.post("/api/projects/{pid}/takes/{tid}/score")
    def score_take(pid: str, tid: str, body: ScoreIn):
        """テイクを楽譜 (MusicXML) にするジョブを始める。原曲の拍 (SheetSage2-Refined) が要るので音源のあるプロジェクトだけ"""
        with runner.project_lock(pid):
            p = open_project(pid)
            take = find_take(p, tid)
            if take["state"] != "done":
                raise HTTPException(400, "生成が終わったテイクだけを楽譜にできます")
            if p.audio_path is None and not (p.dir / "sheetsage.json").is_file():
                raise HTTPException(400, "楽譜にするには原曲の拍が要ります。音源のあるプロジェクトで使ってください")
            if any(j.kind == "score" and tid in j.takes for j in runner.active_for(pid)):
                raise HTTPException(409, "このテイクは楽譜にしているところです")
            try:
                entry = engine.score_entry(body.model)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            score = {"state": "queued", "error": None, "model": {"id": entry.id, "label": entry.label}}
            p.update_take(tid, score={**(take.get("score") or {}), **score})
            runner.submit("score", pid, [tid])
            return detail(p)

    @app.get("/api/projects/{pid}/takes/{tid}/score.musicxml")
    def get_take_score(pid: str, tid: str):
        p = open_project(pid)
        take = find_take(p, tid)
        path = p.take_dir(tid) / "score.musicxml"
        if not path.is_file():
            raise HTTPException(404, "まだ楽譜にしていません")
        name = f"{safe_name(p.data['title'])}_{take['name'] or tid}.musicxml"
        return FileResponse(
            path,
            media_type="application/vnd.recordare.musicxml+xml",
            headers={"Content-Disposition": _attachment(name), "Cache-Control": "no-cache"},
        )

    @app.get("/api/projects/{pid}/takes/{tid}/view")
    def get_take_view(pid: str, tid: str):
        from .midi_view import cover_view

        p = open_project(pid)
        take = find_take(p, tid)
        path = p.take_midi(tid)
        if not path.is_file():
            raise HTTPException(404, "まだ生成していません")
        validate_take_midi(p, take, path)
        return cover_view(path)

    # ------------------------------------------------------------ 好み
    def style_view(style: dict) -> dict:
        """一覧に返す形 (ベクトルは除き、学習中なら進み具合を付ける)"""
        view = {k: v for k, v in style.items() if k != "vector"}
        job = next((j for j in runner.jobs("") if j.kind == "style" and j.takes[0] == style["id"]), None)
        if job is not None and job.active:
            view.update(job=job.id, state=job.state, progress=job.progress, message=job.message)
        if job is not None:
            view["log"] = job.log[-40:]
        return view

    def favorite_takes() -> list[dict]:
        out = []
        for p in projects(root):
            for take in p.takes():
                if take.get("favorite") and take["state"] == "done":
                    out.append(
                        {
                            "project": p.id,
                            "title": p.data["title"],
                            "take": take["id"],
                            "number": take["number"],
                            "name": take["name"],
                            "model": take["model"]["id"],
                            "channel": int(take["params"].get("channel") or 0),
                            "style": take["params"].get("style"),
                            "audio": p.audio_path is not None,
                        }
                    )
        return out

    @app.get("/api/styles")
    def list_styles():
        return {"styles": [style_view(s) for s in styles.list()], "favorites": favorite_takes()}

    @app.post("/api/styles")
    def create_style(body: StyleIn):
        """今のお気に入り (全曲の、音源のある曲のもの) から、好みのベクトルを学習するジョブを始める"""
        with ExitStack() as stack:
            for project in sorted(projects(root), key=lambda item: item.id):
                stack.enter_context(runner.project_lock(project.id))
            try:
                entry = engine.entry(body.model)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            favorites = [f for f in favorite_takes() if f["audio"]]
            if not favorites:
                raise HTTPException(400, "音源のある曲のお気に入りがありません")
            # 初期値: このモデルで作ったお気に入りの演奏者 (なければ全部のお気に入りの演奏者) の埋め込みの平均。
            # 好みで作ったテイクの演奏者の番号は意味がないので数えない
            numbered = [f for f in favorites if not f["style"]]
            init = [f["channel"] for f in numbered if f["model"] == entry.id] or [f["channel"] for f in numbered] or [0]
            if entry.num_channels:
                init = [c if c < entry.num_channels else 0 for c in init]
            name = body.name.strip() or f"好み {len(styles.list()) + 1}"
            style = styles.create(
                name,
                {"id": entry.id, "label": entry.label},
                [{"project": f["project"], "take": f["take"]} for f in favorites],
                init,
            )
            runner.submit("style", "", [style["id"]])
            return style_view(style)

    def find_style(sid: str) -> dict:
        try:
            return styles.get(sid)
        except KeyError as e:
            raise HTTPException(404, f"好みがありません: {sid}") from e

    @app.patch("/api/styles/{sid}")
    def rename_style(sid: str, body: StylePatch):
        find_style(sid)
        if not body.name.strip():
            raise HTTPException(400, "名前が空です")
        return style_view(styles.update(sid, name=body.name.strip()))

    @app.delete("/api/styles/{sid}", status_code=204)
    def delete_style(sid: str):
        find_style(sid)
        if any(j.kind == "style" and j.takes[0] == sid for j in runner.active_for("")):
            raise HTTPException(409, "学習中・待ちの好みは先に止めてください")
        styles.delete(sid)

    # ------------------------------------------------------------ ジョブ
    @app.get("/api/jobs")
    def list_jobs(project: str | None = None):
        return [j.to_dict() for j in runner.jobs(project)]

    @app.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str):
        job = runner.cancel(job_id)
        if job is None:
            raise HTTPException(404, f"ジョブがありません: {job_id}")
        return job.to_dict()

    # ------------------------------------------------------------ 画面
    @app.exception_handler(ValueError)
    def value_error(_, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str):
        """web/ のビルドを配る。ファイルでないパス (/p/<id> など画面の中の行き先) には index.html を返す"""
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        if not (STATIC_DIR / "index.html").exists():
            return RedirectResponse("/docs")
        file = (STATIC_DIR / path).resolve()
        if path and file.is_file() and file.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _attachment(name: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(name)}"
