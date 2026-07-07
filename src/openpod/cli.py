"""The ``openpod`` command-line interface.

Pull-only, local-pure. Every command operates on the ``.openpod/`` library in
the current workspace (or ``$OPENPOD_HOME``). Uses only the standard library so
the CLI itself has no import cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import __version__
from . import theme
from .config import Workspace
from .models import format_timestamp


def main(argv: Optional[list[str]] = None) -> int:
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
        print(f"error: {e}", file=sys.stderr)
        return 1


def _ws(args) -> Workspace:
    return Workspace(getattr(args, "home", None))


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _cmd_catch(args) -> int:
    from .catch import catch
    from .persona import Persona

    ws = _ws(args)
    result = catch(
        args.link, workspace=ws, kind=args.kind,
        transcript_path=args.transcript, k_ideas=args.ideas,
        prefer_captions=not args.force_asr,
    )
    next_step = None if Persona(ws).exists() else (
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
    return {
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

    result = clip(args.entry_id, args.start, args.end, workspace=_ws(args),
                  snap=not args.no_snap, audio_path=args.audio,
                  reencode=args.reencode)
    if args.json:
        print(json.dumps({
            "path": str(result.path),
            "start": result.start,
            "end": result.end,
            "quote": result.quote,
            "deeplink": result.deeplink,
            "card_path": str(result.card_path) if result.card_path else None,
            "card_png_path": str(result.card_png_path) if result.card_png_path else None,
        }, indent=2, ensure_ascii=False))
        return 0
    print(f"clip: {theme.path(str(result.path))}")
    print(f"  span: {theme.moment(result.start, result.deeplink)} – {format_timestamp(result.end)}")
    if result.card_path:
        print(f"  share card: {theme.path(str(result.card_path))}")
        if result.card_png_path:
            print(f"  card image: {theme.path(str(result.card_png_path))}")
        else:
            print("  card image: to get a PNG, open the card in a browser and "
                  "screenshot it — or `pip install 'openpod[card-png]'`")
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
            print(json.dumps({
                "path": str(persona.path),
                "exists": persona.exists(),
                "content": persona.read(),
            }, indent=2, ensure_ascii=False))
            return 0
        print(persona.read() or "(no persona yet — run `openpod persona init`)")
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
    c.add_argument("--kind", choices=["youtube", "podcast", "spotify", "file"])
    c.add_argument("--transcript", help="use a local caption file instead of fetching")
    c.add_argument("--ideas", type=int, default=8, help="number of key ideas to extract")
    c.add_argument("--force-asr", action="store_true", help="skip captions, transcribe audio")
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
    cl.add_argument("--json", action="store_true")
    cl.set_defaults(func=_cmd_clip)

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
                    choices=["init", "interview", "scan", "derive", "show"],
                    nargs="?", default="show")
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

    v = sub.add_parser("version", help="print version")
    v.set_defaults(func=_cmd_version)

    return p


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
