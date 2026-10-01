# VRAM Radar dialog & notice design

Every popup of VRAM Radar's own (consent, revoke confirmation, "not installed /
not signed in" notices, the "max 4" limit, "reading…" progress) is drawn by one
component in `src/vram_radar/ui_dialogs.py`. Content is a small spec dict
(`consent_spec`, `revoke_spec`, `notice_spec`, `toast_spec`); never build
ad-hoc `MessageBox`/`Form` dialogs.

## Components

| Component | Use | Behaviour |
|---|---|---|
| `show_dialog(spec)` | a decision or an explanation that needs acknowledgement | modal, centred, Enter = primary, Esc = cancel id, Tab/←/→ move focus, drag by the body |
| `show_toast(spec, anchor)` | progress / confirmation / limits | non-activating, above the strip, closes after 3.5 s or on click, one at a time |

## Anatomy (dialog)

```
┌──────────────────────────────────────────────┐
│ [icon 40]  Headline (bold, the key question) │
│            • short line (≤ 24 CJK chars)     │
│            • short line                      │
│            note (secondary, small)           │
├──────────────────────────────────────────────┤  footer (subtle fill)
│ [ Primary (accent) ]  [ Secondary ]          │  2+ buttons share the row,
└──────────────────────────────────────────────┘  primary first; 1 button sits right
```

Icon = the provider app's own icon (`provider_icon`: `PrivateExtractIconsW` on
the detected exe at the exact pixel size, then `resources\icon.ico/png`), else a
rounded letter tile. Cached per (path, px).

## Tokens (DIP; × DPI/96 at runtime, per monitor)

| Token | Value | Token | Value |
|---|---|---|---|
| width | 420 | pad | 24 |
| icon | 40 | icon_gap | 16 |
| headline | 16 px bold | body | 13 px |
| note | 12 px | headline_gap / line_gap / note_gap | 10 / 6 / 12 |
| bullet | 5 (accent dot) | bullet_gap | 10 |
| footer_pad | 20 | button | h 32, radius 4, gap 8, min w 104 |
| corner | 8 (DWM round corners) | toast | w 320, pad 14, icon 24, 13/12 px, 3.5 s |

Font: Microsoft YaHei UI (Latin + CJK), grayscale anti-aliased text.

## Colours

| Role | Light | Dark |
|---|---|---|
| surface | #FFFFFF | #2C2C2C |
| footer | #F3F3F3 | #202020 |
| border | #E0E0E0 | #404040 |
| text / secondary | #1A1A1A / #606060 | #FFFFFF / #C8C8C8 |
| secondary button | #FDFDFD, border #D0D0D0 | #383838, border #4E4E4E |
| primary button | accent darkened 12 % | accent lightened 35 % |

Theme follows `AppsUseLightTheme`; accent follows `DWM\AccentColor`. Primary
text is black or white, whichever contrasts with the fill.

## Writing

Headline first, phrased as the decision ("允许显存雷达自动读取 Grok 额度？").
Then 2–3 short lines, no paragraphs. Buttons are verbs: 允许 / 暂不,
关闭 / 取消, 知道了.

Render all variants for review: `python tools/render_dialogs.py OUTDIR`.