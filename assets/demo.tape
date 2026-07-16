# demo.tape — the source for assets/demo.gif
#
# The GIF used to be a hand-recorded binary with no source, so when the banner
# copy changed nothing caught it and the GIF kept demoing a retired tagline.
# This file is the fix: the GIF is now regenerable output, not an artifact.
#
#   brew install vhs        # pulls ttyd + ffmpeg
#   vhs assets/demo.tape    # run from the repo root; rewrites assets/demo.gif
#
# The README points at raw.githubusercontent, so pushing the new GIF to main
# updates both the README and the PyPI page. No release needed.
#
# Colors come from src/openpod/brand/brand-tokens.json. Blue (#58A6FF) is
# reserved for the moment — timestamps, deep-links, the jump affordance.
# Never let it leak onto ordinary output.

Output assets/demo.gif

Require openpod

Set Shell zsh
Set FontFamily "IBM Plex Mono"
Set FontSize 18
Set Width 1440
Set Height 1000
Set Padding 30
Set PlaybackSpeed 1.0
Set TypingSpeed 45ms

Set Theme { "name": "OpenPod", "background": "#0E0E0E", "foreground": "#FCFCFB", "cursor": "#58A6FF", "selection": "#2A3F5F", "black": "#0E0E0E", "red": "#F85149", "green": "#3FB950", "yellow": "#D29922", "blue": "#58A6FF", "magenta": "#BC8CFF", "cyan": "#39C5CF", "white": "#FCFCFB", "brightBlack": "#6E7681", "brightRed": "#FF7B72", "brightGreen": "#56D364", "brightYellow": "#E3B341", "brightBlue": "#79C0FF", "brightMagenta": "#D2A8FF", "brightCyan": "#56D4DD", "brightWhite": "#FFFFFF" }

# Start clean — no shell noise, no stale prompt in frame one.
Hide
Type "clear" Enter
Sleep 500ms
Show

# 1. Catch an episode. Prints the banner + what was extracted.
#    Tune the Sleep to the real runtime; too short truncates the output,
#    too long wastes seconds against the 25s budget in the design brief.
Type "openpod catch 'https://feeds.megaphone.fm/hubermanlab'"
Sleep 800ms
Enter
Sleep 6s

# 2. Search across the corpus — breadth is the value, not one-episode summary.
Type "openpod search 'dopamine baseline after cold exposure'"
Sleep 800ms
Enter
Sleep 5s

# 3. Hold on the hits so the blue ▸ timestamps land. This is the moment.
Sleep 3s
