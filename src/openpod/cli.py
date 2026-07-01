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
from .config import Workspace
from .models import format_timestamp


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
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

    result = catch(
        args.link, workspace=_ws(args), kind=args.kind,
        transcript_path=args.transcript, k_ideas=args.ideas,
        prefer_captions=not args.force_asr,
    )
    if args.json:
        print(json.dumps({
            "entry_id": result.entry_id,
            "title": result.source.title,
            "show": result.source.show,
            "cues": len(result.transcript),
            "ideas": [i.to_dict() for i in result.ideas],
        }, indent=2, ensure_ascii=False))
        return 0

    print(f"caught: {result.entry_id}")
    print(f"  title:      {result.source.title or '(untitled)'}")
    print(f"  transcript: {len(result.transcript)} cues ({result.transcript.source})")
    print(f"  artifacts:  {result.entry.dir}")
    print(f"  {len(result.ideas)} key ideas extracted → ideas.md, briefing.md")
    return 0


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
        ts = format_timestamp(h.start)
        loc = f"{h.show} · {h.episode}"
        print(f"[{h.score:.3f}] {loc} @ {ts}")
        print(f"    {h.text.strip()}")
        if h.deeplink:
            print(f"    → {h.deeplink}")
    return 0


def _cmd_clip(args) -> int:
    from .clip import clip

    result = clip(args.entry_id, args.start, args.end, workspace=_ws(args),
                  snap=not args.no_snap, audio_path=args.audio,
                  reencode=args.reencode)
    print(f"clip: {result.path}")
    print(f"  span: {format_timestamp(result.start)} – {format_timestamp(result.end)}")
    if result.deeplink:
        print(f"  deep-link: {result.deeplink}")
    if result.card_path:
        print(f"  share card: {result.card_path}")
    return 0


def _cmd_export(args) -> int:
    from .exports import export_timestamps

    out = export_timestamps(args.entry_id, workspace=_ws(args), fmt=args.format,
                            segments=args.segments)
    print(out)
    return 0


def _cmd_follow(args) -> int:
    from .follows import Follows

    f = Follows(_ws(args)).add(args.url, title=args.title)
    print(f"following: {f.title or f.url} ({f.kind})")
    return 0


def _cmd_unfollow(args) -> int:
    from .follows import Follows

    ok = Follows(_ws(args)).remove(args.url)
    print("unfollowed" if ok else "not in follows list")
    return 0 if ok else 1


def _cmd_follows(args) -> int:
    from .follows import Follows

    follows = Follows(_ws(args)).list()
    if not follows:
        print("no follows yet — add one with `openpod follow <url>`")
        return 0
    for f in follows:
        print(f"- [{f.kind}] {f.title or f.url}")
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
        print(f"- {i.show}: {i.title}{when}")
        if i.link:
            print(f"    {i.link}")
    return 0


def _cmd_persona(args) -> int:
    from .persona import Persona

    persona = Persona(_ws(args))
    if args.action == "init":
        path = persona.init(force=args.force)
        print(f"persona at: {path}")
    elif args.action == "interview":
        for i, q in enumerate(persona.interview(), 1):
            print(f"{i}. {q}")
    elif args.action == "derive":
        print(persona.derive())
    elif args.action == "show":
        print(persona.read() or "(no persona yet — run `openpod persona init`)")
    return 0


def _cmd_list(args) -> int:
    from .library import Library

    library = Library(_ws(args))
    entries = list(library)
    if args.json:
        print(json.dumps([{"entry_id": e.entry_id, "show": e.show(),
                            "title": e.title()} for e in entries], indent=2,
                         ensure_ascii=False))
        return 0
    if not entries:
        print("library is empty — `openpod catch <link>` to start")
        return 0
    for e in entries:
        print(f"{e.entry_id}\n    {e.show()} · {e.title()}")
    return 0


def _cmd_reindex(args) -> int:
    from .search import reindex

    n = reindex(_ws(args))
    print(f"reindexed {n} cues")
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
    fl.set_defaults(func=_cmd_follows)

    d = sub.add_parser("digest", help="what's new across your follows")
    d.add_argument("--per-feed", type=int, default=5)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_digest)

    pe = sub.add_parser("persona", help="manage the local persona.md")
    pe.add_argument("action", choices=["init", "interview", "derive", "show"],
                    nargs="?", default="show")
    pe.add_argument("--force", action="store_true")
    pe.set_defaults(func=_cmd_persona)

    ls = sub.add_parser("list", help="list caught episodes")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    ri = sub.add_parser("reindex", help="rebuild the search index from artifacts")
    ri.set_defaults(func=_cmd_reindex)

    v = sub.add_parser("version", help="print version")
    v.set_defaults(func=_cmd_version)

    return p


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
