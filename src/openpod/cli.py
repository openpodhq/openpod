"""The ``openpod`` command-line interface.

Pull-only, local-pure. Every command operates on the ``.openpod/`` library in
the current workspace (or ``$OPENPOD_HOME``). Uses only the standard library so
the CLI itself has no import cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from . import theme
from .config import Workspace
from .models import format_timestamp


def main(argv: Optional[list[str]] = None) -> int:
    theme.enable_safe_output()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        print(theme.banner())
        print()
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\naborted", file=sys.stderr)
        return 130
    except Exception as e:  # surface a clean message, not a traceback
        from .errors import OpenPodError

        if isinstance(e, OpenPodError):
            # Structured errors are data: print the dict so agents and
            # scripts can branch on it (needs_confirmation etc.).
            print(json.dumps(e.to_dict(), indent=2, ensure_ascii=False))
            return 2
        print(f"error: {e}", file=sys.stderr)
        return 1


def _ws(args) -> Workspace:
    return Workspace(getattr(args, "home", None))


_DEFAULT_BRIDGE_PORT = 8788


def _cmd_player_bridge(args) -> int:
    """Run the local player bridge and block until interrupted."""
    import threading

    from .bridge import DEFAULT_PORT, PlayerBridge

    ws = _ws(args)
    bridge = PlayerBridge(ws, port=args.port or DEFAULT_PORT, code=args.code).start()
    print(theme.banner())
    print()
    print(f"Player bridge listening on {theme.path(f'http://127.0.0.1:{bridge.port}')}")
    print()
    print("  Pair it once in the player: Settings → AI control → Connect a local bridge")
    print(f"    port:  {bridge.port}")
    print(f"    code:  {bridge.code}")
    print()
    print("  Point your agent's MCP config at `openpod-mcp` to get the player_* tools.")
    print("  Commands you issue while no tab is open are queued and run on next connect.")
    print("  Ctrl-C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nstopping bridge…", file=sys.stderr)
    finally:
        bridge.stop()
    return 0


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _cmd_catch(args) -> int:
    from .catch import catch
    from .persona import Persona

    ws = _ws(args)
    asr = "auto"
    if getattr(args, "asr_now", False) or args.force_asr:
        asr = "now"
    elif getattr(args, "no_asr", False):
        asr = "never"
    result = catch(
        args.link, workspace=ws, kind=args.kind,
        transcript_path=args.transcript, k_ideas=args.ideas,
        prefer_captions=not args.force_asr,
        confirmed=getattr(args, "confirmed", False),
        asr=asr,
        progress=lambda msg: print(f"… {msg}", file=sys.stderr),
    )
    _p = Persona(ws)
    next_step = None if (_p.exists() or _p.global_exists()) else (
        "this briefing is generic — ask your agent to \"Set Up My Persona\" "
        "(or run `openpod persona init`) and the next one is written for you"
    )
    if args.json:
        payload = _catch_dict(result)
        if next_step:
            payload["next_step"] = next_step
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(theme.banner())
    print()
    print(theme.ok(f"caught: {result.entry_id}"))
    print(f"  title:      {result.source.title or '(untitled)'}")
    print(f"  transcript: {len(result.transcript)} cues ({result.transcript.source})")
    print(f"  artifacts:  {theme.path(str(result.entry.dir))}")
    print(f"  {len(result.ideas)} key ideas extracted → ideas.md, briefing.md")
    if next_step:
        print(f"  next: {next_step}")
    return 0


def _catch_dict(result) -> dict:
    """The one catch schema, shared verbatim with the MCP tool."""
    d = {
        "entry_id": result.entry_id,
        "title": result.source.title,
        "show": result.source.show,
        "source_kind": result.source.kind,
        "transcript_cues": len(result.transcript),
        "transcript_source": result.transcript.source,
        "artifacts_dir": str(result.entry.dir),
        "ideas": [i.to_dict() for i in result.ideas],
        "toc": [i.to_dict() for i in result.toc],
        "segments": [s.to_dict() for s in result.segments],
        "chapters": [c.to_dict() for c in result.chapters],
    }
    if result.transcript.notes:
        d["transcript_notes"] = result.transcript.notes
    # Origin/source decoupling: what the user pasted is product state.
    if result.origin is not None:
        d["origin_kind"] = result.origin.kind
        if result.origin.url:
            d["origin_url"] = result.origin.url
    ident = getattr(result, "identity", None)
    if ident is not None and getattr(ident, "episode_key", ""):
        d["episode_key"] = ident.episode_key
        d["match_confidence"] = ident.match_confidence
    return d


def _cmd_search(args) -> int:
    from .search import search

    hits = search(args.query, workspace=_ws(args), limit=args.limit,
                  semantic=not args.no_semantic)
    if args.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False))
        return 0
    if not hits:
        print("no matches (try `openpod reindex` if you just caught something)")
        return 0
    for h in hits:
        loc = f"{h.show} · {h.episode}"
        print(f"[{h.score:.3f}] {loc} {theme.moment(h.start, h.deeplink)}")
        print(f"    {h.text.strip()}")
        if h.chapter_start is not None:
            title = f" — {h.chapter_title}" if h.chapter_title else ""
            print(f"    ↳ chapter {theme.moment(h.chapter_start, h.chapter_deeplink)}{title}")
        if h.segment_start is not None and h.segment_start < h.start:
            title = f" — {h.segment_title}" if h.segment_title else ""
            print(f"    ↳ beat starts {theme.moment(h.segment_start, h.segment_deeplink)}{title}")
    return 0


def _cmd_clip(args) -> int:
    from .clip import clip

    video = None
    if getattr(args, "audio_only", False):
        video = False
    elif getattr(args, "video", False):
        video = True
    result = clip(args.entry_id, args.start, args.end, workspace=_ws(args),
                  snap=not args.no_snap, audio_path=args.audio,
                  reencode=args.reencode, video=video,
                  captions=args.captions, lang=args.lang,
                  captions_file=args.captions_file,
                  word_level=args.word_level,
                  label=True if (args.label_from_meta or args.label) else None,
                  label_text=args.label, hook=args.hook,
                  aspect=args.aspect, out_dir=args.out)
    if args.json:
        print(json.dumps({
            "path": str(result.path),
            "start": result.start,
            "end": result.end,
            "quote": result.quote,
            "deeplink": result.deeplink,
            "has_video": result.has_video,
            "capability_note": result.capability_note,
            "captions_path": str(result.captions_path) if result.captions_path else None,
            "captions": result.captions,
            "label": result.label,
            "aspect": result.aspect,
            "export_dir": str(result.export_dir) if result.export_dir else None,
            "export_paths": [str(p) for p in result.export_paths],
            "card_path": str(result.card_path) if result.card_path else None,
            "card_png_path": str(result.card_png_path) if result.card_png_path else None,
            "first_use": result.first_use,
        }, indent=2, ensure_ascii=False))
        return 0
    print(f"clip: {theme.path(str(result.path))}")
    print(f"  span: {theme.moment(result.start, result.deeplink)} – {format_timestamp(result.end)}")
    if result.captions_path:
        cov = (result.captions or {}).get("coverage", {})
        print(f"  captions: {theme.path(str(result.captions_path))} "
              f"(coverage {cov.get('ratio', '?')})")
    if result.label:
        print(f"  label: {result.label}")
    if result.export_dir:
        print(f"  exported: {theme.path(str(result.export_dir))} "
              f"({len(result.export_paths)} files)")
    if result.capability_note:
        print(f"  note: {result.capability_note}")
    if result.first_use:
        print("  first clip here — set your defaults once "
              "(where clips land, captions, color & style, dimensions, "
              "headline):\n"
              "    openpod settings clip.export_dir .    # etc., then\n"
              "    openpod settings clip.setup_done true")
    if result.card_path:
        print(f"  share card: {theme.path(str(result.card_path))}")
        if result.card_png_path:
            print(f"  card image: {theme.path(str(result.card_png_path))}")
        else:
            print("  card image: to get a PNG, open the card in a browser and "
                  "screenshot it — or `pip install 'openpod[card-png]'`")
    return 0


def _cmd_captions(args) -> int:
    from .captions import export_captions, parse_srt, verify_coverage
    from .library import Library

    entry = Library(_ws(args)).get(args.entry_id)
    if entry is None:
        print(f"error: no caught episode with id {args.entry_id!r}", file=sys.stderr)
        return 1

    if args.verify:
        transcript = entry.read_transcript()
        lines = parse_srt(Path(args.verify).read_text(encoding="utf-8"))
        report = verify_coverage(transcript, args.start, args.end, lines)
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0 if report.ok else 2

    r = export_captions(entry, args.start, args.end, lang=args.lang,
                        fmt=args.format,
                        out_dir=Path(args.out) if args.out else None)
    payload = r.to_dict()
    if args.word_level:
        from .captions import word_timings_for_clip
        from .media import get_media

        m = get_media(entry.entry_id, entry.source(), workspace=_ws(args))
        words = word_timings_for_clip(m.path, args.start, args.end)
        wpath = r.path.with_suffix(".words.json")
        wpath.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        payload["words_path"] = str(wpath)
        payload["words"] = len(words)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if r.coverage.ok else 2


def _cmd_settings(args) -> int:
    ws = _ws(args)
    if args.key and args.value is not None:
        value: object = args.value
        if value in ("true", "false"):
            value = value == "true"
        ws.set_setting(args.key, value)
        print(f"{args.key} = {value}")
        return 0
    if args.key:
        print(json.dumps(ws.get_setting(args.key), indent=2, ensure_ascii=False))
        return 0
    print(json.dumps(ws.effective_settings(), indent=2, ensure_ascii=False))
    return 0


def _cmd_doctor(args) -> int:
    from .doctor import check

    report = check(_ws(args), fix=args.fix)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        ff = report["ffmpeg"]
        print(theme.banner())
        print()
        print(f"ffmpeg: {'ok' if ff['ffmpeg'] else 'MISSING'}"
              f"  subtitles(libass): {'yes' if ff['subtitles'] else 'no'}"
              f"  drawtext: {'yes' if ff['drawtext'] else 'no'}")
        st = report["settings"]
        print(f"settings: {st['path']}" + ("" if st["exists"] else " (not created — defaults apply)"))
        lib = report["library"]
        print(f"library: {len(lib['foreign'])} foreign item(s)"
              + (" — quarantined to _scratch/" if lib["quarantined"] and lib["foreign"] else ""))
        for f in lib["foreign"][:20]:
            print(f"  - {f['path']}" + (f" → {f['moved_to']}" if f.get("moved_to") else ""))
        for section in (ff, st, lib):
            for n in section["notes"]:
                print(f"  note: {n}")
    foreign_pending = report["library"]["foreign"] and not args.fix
    return 1 if foreign_pending else 0


def _cmd_summary(args) -> int:
    from .library import Library

    library = Library(_ws(args))

    if not args.entry_id:
        # Recall listing across the library.
        rows = []
        for entry in library:
            body = entry.read_summary(body_only=True)
            if body:
                rows.append((entry.entry_id, body.strip().splitlines()[0][:80]))
        if not rows:
            print("no summaries yet — agents write them with save_summary "
                  "(or: openpod summary <entry> --from notes.md)")
            return 0
        for entry_id, first in rows:
            print(f"{entry_id}\n    {first}")
        return 0

    entry = library.get(args.entry_id)
    if entry is None:
        print(f"error: no caught episode with id {args.entry_id!r}", file=sys.stderr)
        return 1

    if args.from_file:
        text = (sys.stdin.read() if args.from_file == "-"
                else Path(args.from_file).read_text(encoding="utf-8"))
        path = entry.write_summary(text, append=args.append)
        print(f"summary: {theme.path(str(path))}")
        return 0

    body = entry.read_summary(body_only=not args.raw)
    if body is None:
        print(f"no summary for {args.entry_id} yet")
        return 1
    print(body.strip())
    return 0


def _cmd_export(args) -> int:
    from .exports import export_timestamps

    out = export_timestamps(args.entry_id, workspace=_ws(args), fmt=args.format,
                            segments=args.segments)
    print(out)
    return 0


def _cmd_follow(args) -> int:
    from .follows import Follows

    ws = _ws(args)
    f = Follows(ws).add(args.url, title=args.title)
    print(f"following: {f.title or f.url} ({f.kind})")
    print(f"  wrote: {theme.path(str(ws.follows_file))}")
    return 0


def _cmd_unfollow(args) -> int:
    from .follows import Follows

    ws = _ws(args)
    removed = Follows(ws).remove(args.url)
    if removed:
        print(theme.ok("unfollowed"))
        print(f"  wrote: {theme.path(str(ws.follows_file))}")
    else:
        print(theme.fail("not in follows list — `openpod follows` shows what you follow"))
    return 0 if removed else 1


def _cmd_follows(args) -> int:
    from .follows import Follows

    follows = Follows(_ws(args)).list()
    if args.json:
        print(json.dumps([f.to_dict() for f in follows], indent=2,
                         ensure_ascii=False))
        return 0
    if not follows:
        print("no follows yet — add one with `openpod follow <url>` "
              "or import them with `openpod import <file.opml>`")
        return 0
    for f in follows:
        tag = f" ← {f.source}" if f.source else ""
        print(f"- [{f.kind}] {f.title or f.url}{tag}")
    return 0


def _cmd_digest(args) -> int:
    from .follows import Follows

    items = Follows(_ws(args)).poll(per_feed=args.per_feed)
    if args.json:
        print(json.dumps([vars(i) for i in items], indent=2, ensure_ascii=False))
        return 0
    if not items:
        print("nothing new (or no follows configured)")
        return 0
    for i in items:
        when = f" · {i.published}" if i.published else ""
        tag = "" if i.in_rotation else " · new to you"
        print(f"- {i.show}: {i.title}{when}{tag}")
        if i.link:
            print(f"    {i.link}")
    return 0


def _cmd_persona(args) -> int:
    from .persona import Persona

    persona = Persona(_ws(args))
    if args.action == "init":
        if getattr(args, "global_layer", False):
            p = persona.init_global(force=args.force)
        else:
            p = persona.init(force=args.force)
        print(f"persona at: {theme.path(str(p))}")
    elif args.action == "interview":
        for i, q in enumerate(persona.interview(), 1):
            print(f"{i}. {q}")
        print("\n(guess-and-confirm: run `openpod persona scan --json` first "
              "and turn the evidence into multi-select options)")
    elif args.action == "scan":
        from .scan import scan_workspace

        evidence = scan_workspace(_ws(args), extra_roots=args.roots)
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
    elif args.action == "derive":
        print(persona.derive())
        print(f"\nwrote: {theme.path(str(persona.path))} (Derived block only — your sections untouched)")
    elif args.action == "show":
        if getattr(args, "json", False):
            print(json.dumps(persona.read_layers(), indent=2, ensure_ascii=False))
            return 0
        if getattr(args, "global_layer", False):
            print(persona.read_global()
                  or "(no global persona yet — run `openpod persona init --global`)")
            return 0
        layers = persona.read_layers()
        shown = False
        for name in ("global", "workspace"):
            if layers[name]["content"]:
                print(f"═══ {name} · {layers[name]['path']} ═══\n")
                print(layers[name]["content"])
                shown = True
        if not shown:
            print("(no persona yet — run `openpod persona init`)")
    elif args.action == "split":
        if args.to_global:
            result = persona.apply_split(args.to_global)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        proposal = persona.propose_split()
        if proposal is None:
            print("nothing to split (no workspace persona, or the global "
                  "layer already exists)")
            return 0
        print(json.dumps(proposal, indent=2, ensure_ascii=False))
        print("\napply with: openpod persona split --to-global "
              "\"Role\" \"Language & style\" …", file=sys.stderr)
    return 0


def _cmd_import(args) -> int:
    from .imports import import_opml

    result = import_opml(args.file, workspace=_ws(args), label=args.label)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"imported: {result.source_label}")
    print(f"  staged:  {theme.path(str(result.staged_path))}")
    print(f"  follows: +{len(result.added)} added, {result.skipped} already "
          f"followed → {theme.path(str(result.follows_path))}")
    print(f"  persona: refreshed '## Imported interests (opt-in)' → "
          f"{theme.path(str(result.persona_path))}")
    return 0


def _cmd_note(args) -> int:
    from .library import Library

    entry = Library(_ws(args)).get(args.entry_id)
    if entry is None:
        raise ValueError(
            f"no caught episode with id {args.entry_id!r} — "
            "`openpod list` shows what's in the library"
        )
    entry.append_note(args.text)
    print(f"noted → {theme.path(str(entry.notes_path))}")
    return 0


def _cmd_skills(args) -> int:
    from .skills import get_skill, list_skills

    if args.slug:
        skill = get_skill(args.slug)
        if skill is None:
            known = ", ".join(s.slug for s in list_skills())
            raise ValueError(f"no skill named {args.slug!r} — one of: {known}")
        if args.json:
            print(json.dumps(skill.to_dict(include_body=True), indent=2,
                             ensure_ascii=False))
        else:
            print(skill.body)
        return 0
    skills = list_skills()
    if args.json:
        print(json.dumps([s.to_dict() for s in skills], indent=2,
                         ensure_ascii=False))
        return 0
    for s in skills:
        print(f"{s.slug}\n    {s.name} — {s.description}")
    return 0


def _cmd_list(args) -> int:
    from .library import Library

    library = Library(_ws(args))
    entries = list(library)
    if args.json:
        print(json.dumps([{"entry_id": e.entry_id, "show": e.show(),
                            "title": e.title(), "dir": str(e.dir)}
                          for e in entries], indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print("library is empty — `openpod catch <link>` to start")
        return 0
    for e in entries:
        print(f"{e.entry_id}\n    {e.show()} · {e.title()}")
    return 0


def _cmd_reindex(args) -> int:
    from .search import reindex

    ws = _ws(args)
    n = reindex(ws)
    print(f"reindexed {n} cues\n  wrote: {theme.path(str(ws.index_db))}")
    return 0


def _cmd_sync(args) -> int:
    from . import sync as sync_mod

    ws = _ws(args)
    action = getattr(args, "sync_action", None)

    if action == "login":
        def _prompt(user_code: str, uri: str) -> None:
            print(theme.banner())
            print()
            print("To authorize this device, open:")
            print(f"  {uri}")
            print(f"and enter the code: {theme.ok(user_code)}")
            print("waiting for approval…")

        creds = sync_mod.login(ws, base_url=args.base, on_prompt=_prompt)
        path = sync_mod._credentials_path(ws)
        who = f" as {creds.email}" if creds.email else ""
        print(theme.ok(f"logged in{who}"))
        print(f"  token stored: {theme.path(str(path))}")
        print("  note: this file holds a secret bearer token (chmod 600, "
              "git-ignored). Never commit or share it.")
        return 0

    # Default `sync push`: follows → entry-map → segments → transcripts.
    r_follows = sync_mod.push_follows(ws)
    print(theme.ok(f"pushed {r_follows['follows']} follows"))
    r_map = sync_mod.push_entry_map(ws)
    print(theme.ok(f"pushed entry map: {r_map.get('entries', 0)} entries"))
    r_seg = sync_mod.push_segments(ws)
    print(theme.ok(f"pushed segments: {r_seg['emitted']} emitted, "
                   f"{r_seg['accepted']} accepted, {r_seg['duplicates']} duplicate"))
    r_tx = sync_mod.push_transcripts(ws)
    print(theme.ok(f"pushed {r_tx['transcripts']} transcripts"))
    return 0


def _cmd_pull(args) -> int:
    from . import sync as sync_mod

    ws = _ws(args)
    if getattr(args, "heard", False):
        r = sync_mod.pull_heard(ws)
        if r.get("unavailable"):
            print(r["unavailable"])
            return 0
        print(theme.ok(f"pulled {r['heard']} heard cues → "
                       f"{r['entries_written']} episodes"))
        for p in r["paths"]:
            print(f"  wrote: {theme.path(p)}")
        if r["orphans"]:
            print(f"  {len(r['orphans'])} heard cues had no local episode "
                  "(heard on the player but never caught here)")
        return 0

    r = sync_mod.pull_bookmarks(ws)
    print(theme.ok(f"pulled {r['bookmarks']} bookmarks → "
                   f"{r['entries_written']} episodes"))
    for p in r["paths"]:
        print(f"  wrote: {theme.path(p)}")
    for bm in r["orphans"]:
        eid = bm.get("openpodEntryId") or bm.get("feedUrl") or "?"
        print(f"  bookmark for uncaught episode {eid} — "
              "`openpod catch <link>` to bring it into the library")
    return 0


def _cmd_import_heard(args) -> int:
    from . import sync as sync_mod

    ws = _ws(args)
    r = sync_mod.import_heard(ws, args.file)
    print(theme.ok(f"imported from {r['source']}"))
    print(f"  heard cues: {r['heard']} → {r['entries_written']} episodes")
    print(f"  follows:    +{r['follows_added']} added")
    for p in r["paths"]:
        print(f"  wrote: {theme.path(p)}")
    if r["orphans"]:
        print(f"  {len(r['orphans'])} heard cues had no local episode "
              "(catch them to make heard content searchable)")
    return 0


def _cmd_version(args) -> int:
    print(f"openpod {__version__}")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openpod",
        description="Local-first briefings for long-form audio & video. "
                    "Nothing leaves your machine.",
    )
    p.add_argument("--home", help="workspace root (default: $OPENPOD_HOME or cwd)")
    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("catch", help="ingest a link into the library")
    c.add_argument("link", help="podcast/RSS/YouTube link, or a local file")
    c.add_argument("--kind", choices=["youtube", "podcast", "spotify", "apple", "file"])
    c.add_argument("--transcript", help="use a local caption file instead of fetching")
    c.add_argument("--ideas", type=int, default=8, help="number of key ideas to extract")
    c.add_argument("--force-asr", action="store_true", help="skip captions, transcribe audio")
    c.add_argument("--asr-now", action="store_true",
                   help="captions rate-limited? transcribe locally now instead "
                        "of waiting (the estimate is shown before it starts)")
    c.add_argument("--no-asr", action="store_true",
                   help="never transcribe locally — fail with options instead")
    c.add_argument("--confirmed", action="store_true",
                   help="the user confirmed a fuzzy episode match; proceed with it")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_catch)

    s = sub.add_parser("search", help="search the local library")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--no-semantic", action="store_true", help="keyword-only")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_search)

    cl = sub.add_parser("clip", help="cut a local clip from a caught episode")
    cl.add_argument("entry_id", help="library entry id (show/episode)")
    cl.add_argument("start", type=float, help="start time in seconds")
    cl.add_argument("end", type=float, help="end time in seconds")
    cl.add_argument("--no-snap", action="store_true", help="don't snap to cue edges")
    cl.add_argument("--audio", help="local media file to cut instead of downloading")
    cl.add_argument("--reencode", action="store_true")
    cl.add_argument("--video", action="store_true",
                    help="require video (reported honestly if unavailable)")
    cl.add_argument("--audio-only", action="store_true",
                    help="force audio-only even when the source has video")
    cl.add_argument("--captions", choices=["off", "soft", "burn"],
                    help="caption derivative: soft = .srt sidecar, burn = "
                         "hard-subbed copy in the export dir (default: settings)")
    cl.add_argument("--lang", help="caption target language (BCP-47); "
                                   "default: settings/locale")
    cl.add_argument("--captions-file",
                    help="verified (translated) .srt to burn — must pass the "
                         "coverage check or the burn is refused")
    cl.add_argument("--word-level", action="store_true",
                    help="add word timings from the cut clip (asr extra) — "
                         "the karaoke-highlight track")
    cl.add_argument("--label", help="name-plate text for the export derivative")
    cl.add_argument("--label-from-meta", action="store_true",
                    help="label from the episode's structured speakers "
                         "(or workspace speakers.yaml)")
    cl.add_argument("--hook", help="one-line hook stored in the export "
                                   "package (label.json); rendered by "
                                   "downstream tools, not burned here")
    cl.add_argument("--aspect",
                    choices=["original", "vertical", "square", "wide"],
                    help="export derivative shape: vertical 9:16 (TikTok/"
                         "Reels/Shorts), square 1:1, wide 16:9; the library "
                         "master always keeps source dimensions "
                         "(default: clip.aspect setting)")
    cl.add_argument("--out", help="working folder: copy master + sidecars there "
                                  "(burn derivatives land only there); "
                                  "default: clip.export_dir setting")
    cl.add_argument("--json", action="store_true")
    cl.set_defaults(func=_cmd_clip)

    cap = sub.add_parser("captions",
                         help="export caption sidecar (srt/vtt) for a clip "
                              "window, with a transcript coverage report")
    cap.add_argument("entry_id")
    cap.add_argument("start", type=float)
    cap.add_argument("end", type=float)
    cap.add_argument("--lang", help="target language (translation flagged, "
                                    "never guessed)")
    cap.add_argument("--format", choices=["srt", "vtt"], default="srt")
    cap.add_argument("--out", help="write the sidecar here instead of clips/")
    cap.add_argument("--word-level", action="store_true",
                     help="also emit word timings for the window "
                          "(karaoke highlight; needs the asr extra)")
    cap.add_argument("--verify", metavar="SRT_FILE",
                     help="verify an (edited/translated) caption file's "
                          "coverage against the transcript window instead of exporting")
    cap.set_defaults(func=_cmd_captions)

    se = sub.add_parser("settings", help="get or set settings.yaml keys")
    se.add_argument("key", nargs="?", help="dotted key, e.g. clip.export_dir")
    se.add_argument("value", nargs="?", help="new value (omit to read)")
    se.set_defaults(func=_cmd_settings)

    dr = sub.add_parser("doctor", help="capability report + library hygiene check")
    dr.add_argument("--fix", action="store_true",
                    help="quarantine foreign files under library/_scratch/")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=_cmd_doctor)

    su = sub.add_parser("summary",
                        help="read/write an episode's summary.md — the "
                             "cross-session memory record")
    su.add_argument("entry_id", nargs="?",
                    help="entry to read; omit to list all summaries")
    su.add_argument("--from", dest="from_file", metavar="FILE",
                    help="write the summary from FILE ('-' for stdin)")
    su.add_argument("--append", action="store_true",
                    help="append after the existing body instead of replacing")
    su.add_argument("--raw", action="store_true",
                    help="include the product-owned frontmatter when reading")
    su.set_defaults(func=_cmd_summary)

    e = sub.add_parser("export-timestamps", help="emit timed segments + deep-links")
    e.add_argument("entry_id")
    e.add_argument("--format", choices=["markdown", "json"], default="markdown")
    e.add_argument("--segments", type=int, default=12)
    e.set_defaults(func=_cmd_export)

    fo = sub.add_parser("follow", help="add a podcast RSS / YouTube channel")
    fo.add_argument("url")
    fo.add_argument("--title")
    fo.set_defaults(func=_cmd_follow)

    uf = sub.add_parser("unfollow", help="remove a follow")
    uf.add_argument("url")
    uf.set_defaults(func=_cmd_unfollow)

    fl = sub.add_parser("follows", help="list follows")
    fl.add_argument("--json", action="store_true")
    fl.set_defaults(func=_cmd_follows)

    im = sub.add_parser(
        "import",
        help="import subscriptions from a file you exported elsewhere (OPML)",
    )
    im.add_argument("file", help="path to an .opml subscription export")
    im.add_argument("--label", help="provenance tag (default: opml:<filename>)")
    im.add_argument("--json", action="store_true")
    im.set_defaults(func=_cmd_import)

    no = sub.add_parser("note", help="append a note to a caught episode")
    no.add_argument("entry_id", help="library entry id (show/episode)")
    no.add_argument("text", help="the note text")
    no.set_defaults(func=_cmd_note)

    sk = sub.add_parser("skills", help="list the packaged skills (features)")
    sk.add_argument("slug", nargs="?", help="show one skill's instructions")
    sk.add_argument("--json", action="store_true")
    sk.set_defaults(func=_cmd_skills)

    d = sub.add_parser("digest", help="what's new across your follows")
    d.add_argument("--per-feed", type=int, default=5)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_digest)

    pe = sub.add_parser("persona", help="manage the local persona.md")
    pe.add_argument("action",
                    choices=["init", "interview", "scan", "derive", "show",
                             "split"],
                    nargs="?", default="show")
    pe.add_argument("--global", dest="global_layer", action="store_true",
                    help="operate on the global layer (~/.openpod/persona.md)")
    pe.add_argument("--to-global", nargs="*", metavar="SECTION",
                    help="split: move these sections to the global persona "
                         "(omit to print the proposal)")
    pe.add_argument("--force", action="store_true")
    pe.add_argument("--json", action="store_true")
    pe.add_argument("--roots", nargs="*",
                    help="extra folders to scan for evidence (opt-in)")
    pe.set_defaults(func=_cmd_persona)

    ls = sub.add_parser("list", help="list caught episodes")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    ri = sub.add_parser("reindex", help="rebuild the search index from artifacts")
    ri.set_defaults(func=_cmd_reindex)

    sy = sub.add_parser(
        "sync",
        help="push local artifacts to a paired player (opt-in; needs login)",
    )
    sy_sub = sy.add_subparsers(dest="sync_action")
    sy_login = sy_sub.add_parser("login", help="authorize this device (device flow)")
    sy_login.add_argument("--base", help="player API base URL "
                          "(default: $OPENPOD_PLAYER_API or http://localhost:8787)")
    sy_login.set_defaults(func=_cmd_sync)
    sy_push = sy_sub.add_parser(
        "push",
        help="push follows, entry-map, segment recommendations, transcripts",
    )
    sy_push.set_defaults(func=_cmd_sync)
    sy.set_defaults(func=_cmd_sync)

    pl = sub.add_parser(
        "pull",
        help="pull bookmarks (→ notes.md) or heard cues (→ listened.json) back",
    )
    pl.add_argument("--heard", action="store_true",
                    help="pull heard cues into listened.json instead of bookmarks")
    pl.set_defaults(func=_cmd_pull)

    ih = sub.add_parser(
        "import-heard",
        help="import a player export file (heard cues + follows), fully offline",
    )
    ih.add_argument("file", help="path to an openpod_export JSON file")
    ih.set_defaults(func=_cmd_import_heard)

    pb = sub.add_parser(
        "player-bridge",
        help="run the local bridge so an AI agent can operate the browser player",
    )
    pb.add_argument("--port", type=int, default=None,
                    help=f"loopback port (default {_DEFAULT_BRIDGE_PORT})")
    pb.add_argument("--code", default=None,
                    help="fixed 6-digit pairing code (default: random each run)")
    pb.set_defaults(func=_cmd_player_bridge)

    v = sub.add_parser("version", help="print version")
    v.set_defaults(func=_cmd_version)

    return p


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
