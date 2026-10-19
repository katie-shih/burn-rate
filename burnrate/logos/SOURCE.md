# Where these came from

`claude_code.svg` and `codex.svg` are the Claude and OpenAI brand marks, taken
from [Simple Icons](https://github.com/simple-icons/simple-icons) (the icon
files are released under CC0 1.0).

Two things worth being clear about:

- **CC0 covers the icon files, not the brands.** The Claude and OpenAI marks
  are trademarks of Anthropic and OpenAI. Using them to label which tool a row
  of usage came from is ordinary descriptive use; they are not endorsements,
  and Burn Rate is not affiliated with either company.
- They are **vendored**, not fetched. Like the font, nothing is downloaded
  while the dashboard runs.

To change one, replace the file. `burnrate/theme.py` reads whatever is here and
recolours it to the tile's ink, so an SVG with its own hard-coded colours will
ignore the palette — strip `fill` attributes from the paths first.
