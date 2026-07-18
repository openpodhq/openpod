"""``clip`` — precise, local moment extraction.

Cuts a word-ish-accurate span out of an episode's audio into a **local file the
user owns**. No publishing, no re-hosting — that regulated surface is Stage 2.
Boundaries snap to transcript cue edges (the naval-clipper trick) so cuts land
on sentence boundaries rather than mid-word.

Requires ``ffmpeg`` on PATH and access to the source audio (a podcast enclosure,
or a downloadable media URL via the ``youtube`` extra).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .asr import has_ffmpeg
from .card import render_card_html, render_card_png
from .config import Workspace
from .deeplink import build_deeplink
from .library import Library, LibraryEntry
from .models import SourceRef, Transcript, format_timestamp, slugify


@dataclass
class ClipResult:
    path: Path                              # the clean library master — never mutated
    start: float
    end: float
    quote: str
    deeplink: Optional[str]
    card_path: Optional[Path] = None       # card.html — always written when make_card
    card_png_path: Optional[Path] = None   # card.png — best-effort, needs openpod[card-png]
    has_video: bool = False                # the produced clip carries video
    capability_note: Optional[str] = None  # honest degrade, stated up front
    # Presentation layer (all derivatives; the master above stays clean).
    captions_path: Optional[Path] = None   # soft-sub sidecar (srt)
    captions: Optional[dict] = None        # CaptionResult.to_dict()
    label: Optional[str] = None            # the label text used, if any
    export_dir: Optional[Path] = None      # working-folder copies landed here
    export_paths: list = None              # everything copied/rendered there
    aspect: str = "original"               # aspect of the export derivative
    style: str = "plain"                   # burned-caption rendering style
    # Until clip.setup_done, this carries the one-time multiple-choice
    # setup questions the interface should ask (then save via settings).
    first_use: Optional[dict] = None

    def __post_init__(self) -> None:
        if self.export_paths is None:
            self.export_paths = []


def snap_to_cues(transcript: Transcript, start: float, end: float,
                 *, pad: float = 0.0) -> tuple[float, float]:
    """Expand [start, end] outward to the nearest enclosing cue boundaries."""
    if not len(transcript):
        return max(0.0, start - pad), end + pad
    starts = [c.start for c in transcript.cues]
    # snap start down to the cue that contains it
    snapped_start = max((s for s in starts if s <= start), default=starts[0])
    # snap end up to the end of the cue containing `end`
    snapped_end = end
    for c in transcript.cues:
        c_end = c.end if c.end is not None else c.start
        if c.start <= end <= max(c_end, c.start):
            snapped_end = max(c_end, end)
            break
    else:
        later = [c.start for c in transcript.cues if c.start >= end]
        if later:
            snapped_end = later[0]
    return max(0.0, snapped_start - pad), snapped_end + pad


def speaker_label(source, template: str = "{name}, {role}") -> Optional[str]:
    """Render the name plate from structured meta — never from agent memory.

    Uses the primary speaker (or the first). Template fields absent on the
    speaker collapse cleanly ("{name}, {role}" with no role -> just the name).
    """
    speakers = getattr(source, "speakers", None) or []
    chosen = next((s for s in speakers if s.get("primary")), None) \
        or (speakers[0] if speakers else None)
    if not chosen or not chosen.get("name"):
        return None
    # {title} and {role} are aliases — episode meta may carry either.
    title = chosen.get("title") or chosen.get("role") or ""
    fields = {"name": chosen.get("name") or "", "title": title,
              "role": title, "show": chosen.get("show") or ""}
    label = template
    for key, val in fields.items():
        label = label.replace("{" + key + "}", val)
    label = label.strip().strip(",").strip()
    return " ".join(label.split()) or None


def resolve_speakers(source, ws: Workspace) -> Optional[list]:
    """Structured speakers for an episode: the source's own ``speakers``
    field, else the workspace's optional ``speakers.yaml`` keyed by show
    slug — one place to teach OpenPod who hosts what, forever."""
    speakers = getattr(source, "speakers", None)
    if speakers:
        return speakers
    path = ws.dot / "speakers.yaml"
    show = getattr(source, "show", None)
    if not show or not path.exists():
        return None
    try:
        import yaml

        table = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    from .models import slugify as _slug

    for key, val in table.items():
        if _slug(str(key)) == _slug(show):
            return val if isinstance(val, list) else None
    return None


def clip(entry_id_or_link: str, start: float, end: float, *,
         workspace: Optional[Workspace] = None, snap: bool = True,
         audio_path: Optional[str] = None, reencode: bool = False,
         make_card: bool = True, video: Optional[bool] = None,
         captions: Optional[str] = None, lang: Optional[str] = None,
         captions_file: Optional[str] = None, word_level: bool = False,
         label: Optional[bool] = None, label_text: Optional[str] = None,
         hook: Optional[str] = None, aspect: Optional[str] = None,
         style: Optional[str] = None,
         out_dir: Optional[str] = None) -> ClipResult:
    """Cut a clip. ``video=None`` (default) preserves video whenever the
    source has it; ``video=False`` forces audio-only; ``video=True`` demands
    video and reports honestly (``capability_note``) when the source is
    audio-only — *before* a mismatched deliverable, not after.

    The two-artifact contract (see settings schema): the file written under
    the library is the **clean master** and is never mutated. Presentation —
    captions (``off|soft|burn``), a speaker label, working-folder copies
    (``out_dir`` / ``clip.export_dir``) — is rendered as **derivatives**,
    burned files landing only in the export dir. Unset arguments fall back
    to ``settings.yaml``."""
    if end <= start:
        raise ValueError("end must be greater than start")
    if not has_ffmpeg():
        raise RuntimeError(
            "clip requires ffmpeg on your PATH. Install it (e.g. `brew install ffmpeg`)."
        )

    ws = (workspace or Workspace())
    library = Library(ws)
    entry = library.get(entry_id_or_link)
    if entry is None:
        raise ValueError(
            f"no caught episode with id {entry_id_or_link!r}. "
            "Run `openpod catch <link>` first, then clip by its entry id."
        )

    transcript = entry.read_transcript()
    source = entry.source()
    if snap and transcript is not None:
        start, end = snap_to_cues(transcript, start, end)

    quote = ""
    if transcript is not None:
        quote = " ".join(c.text for c in transcript.window(start, end)).strip()

    # Get the media — via the shared cache, video-preserving by default.
    from .media import get_media, source_has_video

    want_video = source_has_video(source) if video is None else video
    m = get_media(entry.entry_id, source, workspace=ws,
                  want_video=want_video, audio_path=audio_path)
    media = str(m.path)

    capability_note = None
    if want_video and not m.has_video:
        capability_note = (
            "this source provides no video track — the clip is audio-only")

    entry.clips_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{int(start)}-{int(end)}-{slugify(quote[:40], fallback='clip')}"
    ext = Path(media).suffix or ".m4a"
    out = entry.clips_dir / f"{stem}{ext}"

    _ffmpeg_cut(media, start, end, out, reencode=reencode)

    deeplink = build_deeplink(source, start) if source else None
    meta = {
        "start": start, "end": end, "quote": quote,
        "deeplink": deeplink, "source": source.to_dict() if source else None,
    }
    meta_path = entry.clips_dir / f"{stem}.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    card_path = None
    card_png_path = None
    if make_card and source is not None and quote:
        card_path = entry.clips_dir / f"{stem}.card.html"
        card_path.write_text(render_card_html(source, start, quote, deeplink),
                             encoding="utf-8")
        png_candidate = entry.clips_dir / f"{stem}.card.png"
        if render_card_png(card_path, png_candidate):
            card_png_path = png_candidate

    result = ClipResult(path=out, start=start, end=end, quote=quote,
                       deeplink=deeplink, card_path=card_path,
                       card_png_path=card_png_path,
                       has_video=m.has_video and out.suffix.lower() in
                       (".mp4", ".mkv", ".webm", ".mov"),
                       capability_note=capability_note)
    _apply_presentation(result, entry, ws, captions_mode=captions, lang=lang,
                        captions_file=captions_file, word_level=word_level,
                        label_flag=label, label_text=label_text, hook=hook,
                        aspect=aspect, style=style, out_dir=out_dir,
                        source=source, meta_path=meta_path,
                        transcript=transcript)
    if not ws.effective_settings()["clip"]["setup_done"]:
        result.first_use = first_use_questions(ws)
    return result


# Aspect presets for export derivatives — centered crop, no upscaling.
# The platform mapping is a hint, not a rule: vertical = TikTok / Reels /
# Shorts, square = feed posts, wide = YouTube / X.
ASPECT_FILTERS = {
    "original": None,
    "vertical": "crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)'",
    "square": "crop=w='min(iw,ih)':h='min(iw,ih)'",
    "wide": "crop=w='min(iw,ih*16/9)':h='min(ih,iw*9/16)'",
}


def first_use_questions(ws: Workspace) -> dict:
    """The one-time clip setup, as multiple-choice data.

    The product ships the questions so every interface (any agent, the CLI,
    the app) asks the same thing the same way: the user PICKS, they never
    elaborate. Answers are saved via settings (plus clip.setup_done=true) and
    never asked again."""
    style = ws.effective_settings()["clip"]["caption_style"]
    return {
        "ask_once": True,
        "instructions": (
            "First clip in this workspace: ask the user these once (as "
            "multiple choice — one message, not an interview), save each "
            "answer with the settings tool, then set clip.setup_done=true. "
            "If they skip, set setup_done=true anyway and keep defaults — "
            "never ask twice."),
        "questions": [
            {"setting": "clip.export_dir",
             "question": "Where should your clips land?",
             "options": [
                 {"value": None, "label": "Inside .openpod — the library "
                  "keeps every clip filed with its episode (default)"},
                 {"value": ".", "label": "The working directory — clips "
                  "appear right here, next to your files"},
                 {"value": "ask", "label": "A folder of yours — name it once "
                  "and every export lands there"},
             ]},
            {"setting": "clip.captions",
             "question": "Captions on your clips?",
             "options": [
                 {"value": "burn", "label": "Burned into the video — always "
                  "visible, no player toggle (what social feeds expect)"},
                 {"value": "soft", "label": "Sidecar file (.srt) next to the "
                  "clip — players and editors toggle them"},
                 {"value": "off", "label": "No captions unless I ask"},
             ]},
            {"setting": "clip.caption_style.color",
             "question": "Caption color?",
             "options": [
                 {"value": style["color"], "label": "Clean white (default)"},
                 {"value": "#FFE14D", "label": "Bold yellow — the classic "
                  "social-caption look"},
                 {"value": "custom", "label": "A brand color (give a hex)"},
             ]},
            {"setting": "clip.caption_style.boxed",
             "question": "Caption style?",
             "options": [
                 {"value": True, "label": "Boxed — a dark strip behind the "
                  "text (the social standard, default)"},
                 {"value": False, "label": "Outlined — floating text with a "
                  "dark contour, no strip"},
             ]},
            {"setting": "clip.aspect",
             "question": "Clip dimensions for shareable exports?",
             "options": [
                 {"value": "original", "label": "Keep the source dimensions"},
                 {"value": "vertical", "label": "Vertical 9:16 — TikTok, "
                  "Reels, Shorts"},
                 {"value": "square", "label": "Square 1:1 — feed posts"},
                 {"value": "wide", "label": "Wide 16:9 — YouTube, X"},
             ]},
            {"setting": "clip.label",
             "question": "Burn a headline plate at the top?",
             "options": [
                 {"value": False, "label": "No headline (default)"},
                 {"value": True, "label": "Yes — speaker and episode title "
                  "on a plate, from structured metadata "
                  "(clip.label_template: '{name}, {title}')"},
             ]},
        ],
    }


def _apply_presentation(result: ClipResult, entry, ws: Workspace, *,
                        captions_mode: Optional[str], lang: Optional[str],
                        captions_file: Optional[str], word_level: bool,
                        label_flag: Optional[bool], label_text: Optional[str],
                        hook: Optional[str], aspect: Optional[str],
                        style: Optional[str], out_dir: Optional[str], source,
                        meta_path: Optional[Path] = None,
                        transcript=None) -> None:
    """The presentation layer: caption data, sidecars, label, export, burn.

    Everything here is a *derivative*. The library master (`result.path`)
    is complete before this runs and is never touched by it."""
    settings = ws.effective_settings()["clip"]
    mode = captions_mode or settings["captions"]
    if mode not in ("off", "soft", "burn"):
        raise ValueError(f"captions must be off|soft|burn, got {mode!r}")
    result.aspect = aspect or settings["aspect"]
    if result.aspect not in ASPECT_FILTERS:
        raise ValueError(
            f"aspect must be one of {sorted(ASPECT_FILTERS)}, got {result.aspect!r}")
    from .ass import STYLES

    style_mode = style or settings["style"]
    if style_mode not in STYLES:
        raise ValueError(
            f"style must be one of {STYLES}, got {style_mode!r}")
    result.style = style_mode

    # Label: explicit text > structured meta (never agent memory).
    speakers = resolve_speakers(source, ws) if source is not None else None
    want_label = label_flag if label_flag is not None else settings["label"]
    the_label = label_text
    if the_label is None and (want_label or label_text) and speakers:
        labeled = SourceRef.from_dict({**(source.to_dict() if source else {"kind": "podcast"}),
                                       "speakers": speakers}) if source else None
        the_label = speaker_label(labeled, settings["label_template"]) \
            if labeled else None
    if label_text or want_label:
        result.label = the_label
        if the_label is None:
            note = ("no structured speakers on this episode's meta — set "
                    "source.speakers (name/title), add the show to "
                    "speakers.yaml, or pass label_text; refusing to guess a name")
            result.capability_note = _append_note(result.capability_note, note)

    # Captions — data first (phrases with break semantics on the clip json),
    # sidecar second, pixels last.
    caption_result = None
    words = None
    if mode in ("soft", "burn"):
        from .captions import captions_block, export_captions

        caption_result = export_captions(entry, result.start, result.end,
                                         lang=lang)
        result.captions_path = caption_result.path
        result.captions = caption_result.to_dict()
        if word_level:
            words = _word_track(result)
        if caption_result.translation_needed:
            result.capability_note = _append_note(
                result.capability_note,
                f"captions are in {caption_result.language or 'source language'}; "
                f"user prefers {caption_result.requested_language} — translate the "
                "sidecar line-by-line (keep timings, use ‖ for forced breaks) "
                "and re-verify coverage before any burn")

        if transcript is not None and meta_path is not None:
            block = captions_block(transcript, result.start, result.end,
                                   words=words)
            _update_clip_meta(meta_path, captions=block, speakers=speakers,
                              hook=hook)
    elif meta_path is not None and (speakers or hook):
        _update_clip_meta(meta_path, speakers=speakers, hook=hook)

    # Export dir: the working-folder package. "ask" is the first-use
    # sentinel meaning the interface still owes the user a question —
    # never a folder name.
    dest_setting = out_dir or settings["export_dir"]
    if dest_setting == "ask":
        dest_setting = None
    if dest_setting:
        import shutil as _shutil

        dest = Path(dest_setting).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        result.export_dir = dest
        for artifact in (result.path, result.captions_path, result.card_path):
            if artifact is not None and Path(artifact).exists():
                copied = dest / Path(artifact).name
                _shutil.copy2(artifact, copied)
                result.export_paths.append(copied)
        if result.deeplink:
            dl = dest / "deeplink.txt"
            dl.write_text(result.deeplink + "\n", encoding="utf-8")
            result.export_paths.append(dl)
        if words:
            wj = dest / f"{result.path.stem}.words.json"
            wj.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
            result.export_paths.append(wj)
        if the_label or hook:
            lj = dest / "label.json"
            lj.write_text(json.dumps({"label": the_label, "hook": hook},
                                     ensure_ascii=False), encoding="utf-8")
            result.export_paths.append(lj)

        if mode == "burn" or result.aspect != "original":
            burn_captions = None
            if mode == "burn":
                burn_captions = _gate_burn_captions(result, entry,
                                                    caption_result,
                                                    captions_file)
            burned = _burn(result, burn_captions, the_label, dest,
                           aspect=result.aspect,
                           style=settings["caption_style"],
                           style_mode=style_mode, words=words)
            if burned is not None:
                result.export_paths.append(burned)
                stills = _verify_stills(burned, result, dest)
                if stills is not None:
                    result.export_paths.append(stills)
    elif mode == "burn":
        result.capability_note = _append_note(
            result.capability_note,
            "captions=burn needs an export destination (--out or "
            "clip.export_dir) — the library master is never burned; "
            "delivered soft captions instead")


def _word_track(result: ClipResult) -> Optional[list]:
    """Best-effort word-level timings from the already-cut clip file."""
    from .asr import DependencyMissing
    from .captions import word_timings_for_clip

    try:
        return word_timings_for_clip(result.path, 0.0,
                                     result.end - result.start) or None
    except DependencyMissing as e:
        result.capability_note = _append_note(result.capability_note, str(e))
    except Exception:
        result.capability_note = _append_note(
            result.capability_note,
            "word-level pass failed; phrase captions are still exact per cue")
    return None


def _gate_burn_captions(result: ClipResult, entry, caption_result,
                        captions_file: Optional[str]):
    """The burn gate: only verified captions reach pixels.

    An agent-translated file must pass the coverage check against the
    transcript window; a translation-needed sidecar without a verified
    replacement refuses to burn (soft sidecar already delivered)."""
    from .captions import parse_srt, verify_coverage

    if captions_file:
        transcript = entry.read_transcript()
        lines = parse_srt(Path(captions_file).read_text(encoding="utf-8"))
        report = verify_coverage(transcript, result.start, result.end, lines)
        if not report.ok:
            result.capability_note = _append_note(
                result.capability_note,
                f"refusing to burn {Path(captions_file).name}: coverage "
                f"{report.ratio:.0%} — {len(report.gaps)} spoken line(s) "
                "missing; restore them and re-verify")
            return None
        return captions_file
    if caption_result is not None and caption_result.translation_needed:
        if caption_result.language is None:
            # Unknown source language: can't prove a mismatch, so burn —
            # but say the check is on the user/agent.
            result.capability_note = _append_note(
                result.capability_note,
                "caption language is unlabeled — check the burned text "
                f"matches the user's preferred {caption_result.requested_language} "
                "(verify.png shows the frames)")
            return str(result.captions_path) if result.captions_path else None
        result.capability_note = _append_note(
            result.capability_note,
            f"refusing to burn {caption_result.language} captions when the "
            f"user prefers {caption_result.requested_language} — translate "
            "the sidecar, verify it (captions --verify), and pass it as "
            "captions_file")
        return None
    return str(result.captions_path) if result.captions_path else None


def _hex_to_ass(hex_color: str, alpha: str = "00") -> str:
    """``#RRGGBB`` -> libass ``&HAABBGGRR``. Lives in ass.py now; kept as an
    alias for its old callers."""
    from .ass import hex_to_ass

    return hex_to_ass(hex_color, alpha)


def _write_burn_ass(result: ClipResult, captions_path, dest: Path, *,
                    style: dict, style_mode: str,
                    words: Optional[list]) -> Path:
    """Build the ``.ass`` the burn renders — part of the export package, so
    the exact styling that produced the derivative is inspectable."""
    from . import transcript as tx
    from .ass import build_ass

    cues = tx.load_file(Path(captions_path)).cues
    lines = [(c.start, c.end if c.end is not None else c.start + 2.0, c.text)
             for c in cues]
    ass_path = dest / f"{result.path.stem}.ass"
    ass_path.write_text(
        build_ass(lines, style=style, mode=style_mode, words=words),
        encoding="utf-8")
    result.export_paths.append(ass_path)
    return ass_path


def _force_style(style: dict) -> str:
    """The libass force_style string for the user's caption styling."""
    boxed = style.get("boxed", True)
    outline = _hex_to_ass(style.get("outline", "#000000"),
                          alpha="60" if boxed else "00")
    parts = [
        f"PrimaryColour={_hex_to_ass(style.get('color', '#FFFFFF'))}",
        f"OutlineColour={outline}",
        "BorderStyle=4" if boxed else "BorderStyle=1",
        "Alignment=8" if style.get("position") == "top" else "Alignment=2",
        "MarginV=40",
    ]
    if style.get("font"):
        parts.insert(0, f"FontName={style['font']}")
    return ",".join(parts)


def _burn(result: ClipResult, captions_path: Optional[str],
          label: Optional[str], dest: Path, *,
          aspect: str = "original",
          style: Optional[dict] = None,
          style_mode: str = "plain",
          words: Optional[list] = None) -> Optional[Path]:
    """Render the social derivative into the export dir: aspect crop,
    hard subs (an ASS built per clip.caption_style + clip.style — per-word
    keyword color and karaoke need inline override tags a plain SRT can't
    carry), name-plate label.

    Capability-gated: no libass -> no subtitle burn, no drawtext -> no label
    burn; each degrade is reported, nothing fails mid-encode. RTL caption
    files get a font warning — libass shapes RTL, but only with a face that
    actually carries the glyphs."""
    from .asr import ffmpeg_capabilities

    if not result.has_video:
        result.capability_note = _append_note(
            result.capability_note,
            "burn-in / social framing needs a video track; this clip is "
            "audio-only — use the soft sidecar with a player that renders it")
        return None

    style = style or {}
    caps = ffmpeg_capabilities()
    filters = []
    if ASPECT_FILTERS.get(aspect):
        filters.append(ASPECT_FILTERS[aspect])
    if captions_path:
        if caps["subtitles"]:
            if style_mode == "karaoke" and not words:
                result.capability_note = _append_note(
                    result.capability_note,
                    "karaoke needs the word-level track (word_level=True, "
                    "openpod[asr]) — burned with keyword styling instead")
            ass_path = _write_burn_ass(result, captions_path, dest,
                                       style=style, style_mode=style_mode,
                                       words=words)
            esc_path = str(ass_path).replace("'", r"\'")
            filters.append(f"subtitles='{esc_path}'")
            from .captions import is_rtl
            lang = (result.captions or {}).get("requested_language") \
                or (result.captions or {}).get("language")
            if is_rtl(lang):
                result.capability_note = _append_note(
                    result.capability_note,
                    "RTL captions: check the burned frames (verify.png) — "
                    "the default font may lack Hebrew/Arabic glyphs; install "
                    "a dedicated face if boxes appear")
        else:
            result.capability_note = _append_note(
                result.capability_note,
                "this ffmpeg build lacks the subtitles filter (libass) — "
                "no captions were burned; the .srt sidecar is in the export "
                "folder (run `openpod doctor` for the capability report)")
    if label:
        if caps["drawtext"]:
            esc = label.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
            filters.append(
                "drawtext=text='" + esc + "':x=(w-text_w)/2:y=h*0.06:"
                "fontsize=h*0.045:fontcolor=white:box=1:"
                "boxcolor=black@0.5:boxborderw=12")
        else:
            result.capability_note = _append_note(
                result.capability_note,
                "this ffmpeg build lacks drawtext — the label was not "
                "burned (label text is in label.json in the export folder)")

    if not filters:
        return None

    social = dest / f"{result.path.stem}-social{result.path.suffix}"
    cmd = ["ffmpeg", "-y", "-i", str(result.path), "-vf", ",".join(filters),
           "-c:a", "copy", str(social)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        result.capability_note = _append_note(
            result.capability_note,
            f"burn-in encode failed ({proc.stderr[-200:].strip()}); "
            "the clean master and soft captions are still in the export folder")
        return None
    return social


def _verify_stills(burned: Path, result: ClipResult,
                   dest: Path) -> Optional[Path]:
    """verify.png — start/mid/end frames of the burned derivative, tiled.

    "Never claim done without looking": the agent (and user) eyeball the
    burned text before anything ships. Best-effort; a still failure never
    fails the export."""
    duration = max(result.end - result.start, 0.1)
    frames = []
    try:
        for i, t in enumerate((duration * 0.02, duration * 0.5,
                               duration * 0.95)):
            f = dest / f".verify-{i}.png"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(burned),
                 "-frames:v", "1", str(f)], capture_output=True)
            if proc.returncode != 0 or not f.exists():
                return None
            frames.append(f)
        out = dest / "verify.png"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(frames[0]), "-i", str(frames[1]),
             "-i", str(frames[2]), "-filter_complex", "hstack=inputs=3",
             str(out)], capture_output=True)
        if proc.returncode != 0:
            return None
        return out
    finally:
        for f in frames:
            f.unlink(missing_ok=True)


def _update_clip_meta(meta_path: Path, **blocks) -> None:
    """Fold presentation data (captions track, speakers, hook) into the
    clip's .json sidecar — captions are data on the clip, not just pixels."""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    meta.update({k: v for k, v in blocks.items() if v is not None})
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                         encoding="utf-8")


def _append_note(existing: Optional[str], note: str) -> str:
    return f"{existing}; {note}" if existing else note


def _ffmpeg_cut(media: str, start: float, end: float, out: Path,
                *, reencode: bool) -> None:
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", media]
    if reencode:
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-c", "copy"]
    cmd += [str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and not reencode:
        # Stream copy can fail on some containers; retry with a re-encode.
        _ffmpeg_cut(media, start, end, out, reencode=True)
        return
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-500:]}")
