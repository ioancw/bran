# bran UI design system

bran is a one-person agent workbench: you talk to an orchestrator, schedule
runner agents, and read what they produce (briefings, research, documents).
The chrome is a tool; the agent output is reading material. Everything in this
file follows from that split.

## Tokens (see `src/styles/themes.css`)

Light (default when the OS prefers light):

| token        | value     | use                                   |
| ------------ | --------- | ------------------------------------- |
| `--bg`       | `#fafaf9` | app canvas                            |
| `--surface`  | `#ffffff` | cards, sidebar, header                |
| `--surface2` | `#f4f3f1` | hover rows, inputs, quiet panels      |
| `--border`   | `#e8e6e3` | hairline between surfaces             |
| `--border2`  | `#d9d6d2` | control borders                       |
| `--fg`       | `#1c1917` | body text                             |
| `--fg-dim`   | `#57534e` | secondary text                        |
| `--muted`    | `#79746e` | labels, meta, placeholders            |
| `--accent`   | `#b0405e` | bran rose: primary action, active nav |

Midnight (dark) uses the same roles on a neutral near-black (`#0e0e10`) with the
rose lifted to `#d9748e`. Dracula, Solarized and Nord keep their own palettes.
Neutrals are stone (slightly warm), not zinc, so the app does not read as a
generic admin template.

## Type

- **Schibsted Grotesk** is the only typeface in the chrome. Sizes: 11 (meta),
  12 (labels, table headers), 13 (body, nav, cells), 14 (card titles, inputs),
  15 (chat), 17 (page title), 22 (empty-state headings). Weights 400/500/600.
- **Geist Mono** only for identifiers: run ids, session ids, cron strings,
  code, and tabular numbers (cost, duration).
- **Source Serif 4** only inside `.prose-md.reading`: the rendered output of an
  agent on the Outputs page and the run result on RunDetail. This is the one
  deliberately bookish moment: briefings read like print, the tool around
  them reads like a tool.
- Sentence case everywhere. No uppercase tracking, no italic display type.

## Layout

- Sidebar 240px (56px icon rail when collapsed). Icons always visible.
- Page header: 52px sticky bar. Title 17px/600, optional subtitle 13px muted
  on the same baseline, actions right-aligned. No scroll animation.
- Content padding 24px 28px. Section gap 24px. Card padding 16px.
- Reading column (`--chat-col`) 760px for chat and rendered output.
- Radii: 6px controls and chips, 10px cards and inputs, 12px composer and
  dialogs, 999px pills.

## Principles

1. **Chrome is a tool, output is reading material.** Every label the app writes
   is sans and sentence case; everything an agent wrote gets the reading
   column.
2. **Hairline over shadow.** Resting surfaces use a 1px border. Shadows only on
   floating layers (menus, dialogs, toasts, the composer).
3. **One accent, spent carefully.** Primary button, active nav item, focus
   ring, links. Status is a coloured dot plus a word, never a filled pill.
4. **Icons carry the nav, words carry the data.** Use `components/Icon.svelte`
   (16px Lucide outlines). Never append an arrow glyph to link text.
5. **Motion answers the user.** Expand, open, confirm. No page-load stagger,
   no scroll-driven masthead, no hover lift on cards.

## Shared classes (global.css)

- Buttons: `.btn-primary`, `.btn-outline`, `.btn-ghost`, `.btn-danger`,
  `.btn-text`. All 13px/500, 32px tall, radius 6. Put an `<Icon>` before the
  label when an icon helps; never a text glyph (`+`, `→`, `↻`, `×`).
- `.label-cap`: section label, 12px/500 muted, sentence case (first letter is
  capitalised by CSS, so write labels lowercase or in sentence case).
- `.pill` with `.ok/.warn/.err/.info/.mute/.accent`: soft tinted tag, 12px.
- `.src` with `.src-runner/.src-spawn/.src-manual/.src-chat/.src-synthesis`:
  how a run was triggered. Sentence case.
- `.chip`: neutral tag for tool names (mono 11px).
- `.card`, `.card-quiet` (flat `surface2` panel, no border).
- `table.bran-table`: 12px sentence-case headers, 13px cells, 40px rows.
- `.field`: inputs, selects, textareas. 13px, radius 8.
- `.composer`: the shared input surface.
- `.prose-md` (sans, chat) and `.prose-md.reading` (serif, outputs).
- `.tool-block`, `.tool-group`: chat tool rows.

## Writing

- Buttons say what happens: "New runner", "Save", "Run now", "Delete runner".
- Confirm dialogs state the consequence: "Delete runner morning-brief? Its
  schedule stops. Past runs and outputs are kept."
- Empty states explain what will appear here and offer the action that fills
  it. No brackets, no italic poetry.
- Meta lines use spacing and colour to separate facts; a middle dot is fine
  between two short facts, never three or more.
