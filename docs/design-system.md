# VRAM Radar interface system

This document owns the product-wide interface hierarchy for the Windows and
macOS desktop application. It adapts the quiet editorial discipline of
`ui-skills` and the product-fit, accessibility, and consistency checks from
`ui-ux-pro-max-skill`; repository and user requirements remain the final
authority.

## Product brief

- Audience: researchers and engineers who need a quick read of GPU capacity
  and task state, including people who do not know SSH configuration details.
- Primary job: answer "where is usable GPU memory right now?" immediately.
- Secondary jobs: inspect one server's processes/tasks, find a suitable GPU,
  and repair a connection.
- Safety promise: the interface is read-only and local-first. It never suggests
  that viewing or matching reserves a GPU.

## Information hierarchy

An empty Profile uses a separate first-run hierarchy instead of an empty
dashboard:

1. Explain the read-only purpose and the result the app will provide.
2. Start a local-only automatic discovery scan when the discovery step opens;
   keep one rerun action and the complete Windows/macOS lookup tutorial one
   disclosure level below it.
3. Review imported or manually entered servers, then reveal authentication and
   advanced fields only when needed.
4. Show the monitoring dashboard only after at least one server is configured.

For a configured Profile, the dashboard hierarchy is:

1. Current aggregate free VRAM, online server count, and online GPU count.
2. Server connection state and the live or cached data boundary.
3. Per-server login account and home-directory location in the server title row
   on wide layouts, followed by GPU, process, node, and Slurm task details.
4. An on-demand folder tree below each server; directory children stay nested
   and individually collapsible rather than expanding the first view.
5. Optional matching inputs, configuration discovery, authentication, and
   advanced connection settings.

The mini navigator starts on the right edge. Its explicit six-dot handle can be
dragged across the window or activated from the keyboard to snap the navigator
to either edge. The owning Profile persists that preference outside the app
package so restarts and upgrades do not reset it. Below 900 CSS pixels it stays
available as the same compact edge rail, moves toward the lower safe-area edge,
and expands over content on demand instead of being removed. The navigator
keeps its existing content in the all, available, and issue views. Only the task
filter is personal: its result set and task line use the current login account's
active Slurm jobs or direct GPU processes, so other-user activity never places
a server in that category.

The first level stays visible. Later levels use meaningful disclosure controls;
collapsing content must never remove it from the product.

Inside each server card, heading levels and disclosure depth are fixed rather
than chosen ad hoc:

1. The server name is the card's primary heading (`h3`, 21 px).
2. Process, task, and folder modules are inset secondary disclosures (`h4`,
   16 px) with a restrained left accent and a closed default state.
3. "我的" and "其他用户" groups are tertiary disclosures (`h5`, 14.5 px)
   on an indented guide rail. When a secondary module opens, only the current
   account's group opens by default.
4. Time partitions inside a task group use compact section headings (`h6`,
   13 px); they organize tables without competing with the group title.

Do not repeat a primary table as a second topology or summary view inside an
expanded module. Put collection scope and interpretation details in a compact,
default-collapsed "说明" disclosure after the data. The first expanded screen
therefore starts with the requested values, while the full explanation remains
available on demand.

## Visual language

- The named direction is **Precision Radar**: a calm technical instrument with
  editorial hierarchy, not a generic dashboard or marketing surface.
- `shell`: a warm neutral field that supports long monitoring sessions.
- `surface`: crisp data planes separated primarily by spacing and thin rules;
  shadows are reserved for modal elevation.
- `signal`: one restrained teal accent for live monitoring and primary focus.
- `green`: healthy/available state.
- `amber`: stale, queued, or attention state.
- `red`: offline, blocked, or failed state.
- `--ui-font` carries Chinese and English interface copy through platform-native
  UI families, `--display-font` carries product and hierarchy headings with the
  same CJK fallback order, and `--mono-font` carries measurements, timestamps,
  IDs, paths, and commands. `--data-font` is a compatibility alias for the mono
  stack; no component defines an independent code-font stack.
- Body copy starts at 16 px. Persistent captions and compact controls do not go
  below 12 px; nested labels step down through weight, color, and spacing as well
  as size so information remains readable under Windows/macOS display scaling.
- The signature elements are the square radar mark, the segmented aggregate
  VRAM tape, and the indexed server equipment rail. The capacity tape shows the
  actual available-to-total ratio; it must not use an ornamental radar, fake
  history, or another shape whose geometry is unrelated to the displayed data.
- Use 4/6/10 px radii. Pills are reserved for genuine compact statuses; normal
  controls, cards, and facts use restrained rectangular geometry.
- Do not use decorative gradients, glow, glass blur, floating-card stacks, or
  ornamental charts. One view gets one accent; semantic colors communicate
  actual state.

## Interaction rules

- Use plain action labels that describe the result: "查找可用服务器",
  "自动发现并导入", and "保存并连接".
- Keep visible copy minimal. Lead with the value or action; do not repeat
  generic reassurance when it does not change the user's decision. Preserve
  detailed explanations only where they prevent a configuration, privacy, or
  data-interpretation mistake.
- Use indentation, a guide rail, type scale, and spacing together to communicate
  nesting. Borders alone are not a sufficient heading hierarchy. Reduce table
  and metadata type one step inside nested disclosures, but keep body text at a
  readable size and preserve 44 px targets on compact/touch layouts.
- Do not show an empty recommendation container. Reveal matching results only
  after the user clicks the matching action, and emphasize server, free memory,
  partition, GPU type, node/device, and free-GPU count over prose.
- Keep common server fields visible. Put passwords, ports, usernames, private
  keys, and local IDs under a clearly named second level.
- Keep the login account and home path in the server heading band on wide
  layouts. On narrow layouts, place them together on the next row without
  creating a separate card section. Load folder structure only when its
  disclosure opens, cap the result, show folders as nested disclosures, and
  expose metadata rather than file contents.
- Screens containing multiple disclosure levels provide one restrained
  "一键收起" action. Users expand only the detail they need; bulk-expand and
  restore controls do not compete with the primary task.
- First-run steps expose only the content required for the current decision.
  "稍后设置" returns to the compact setup landing page, not an empty dashboard.
- Show errors near the affected flow and provide the next action.
- Preserve keyboard focus, visible labels, responsive layout, reduced-motion
  support, and at least 44 px targets on compact/touch layouts.
- Header actions wrap instead of overflowing under a narrow viewport or high
  display scale. Paths, diagnostics, errors, and toast messages use bounded
  containers and safe breaking, while command blocks and wide data tables keep
  intentional horizontal scrolling.
- Use one thin tokenized scrollbar treatment for the document, dialogs,
  navigator, tables, and code blocks. Animate only compositor-friendly
  `transform` and `opacity`; disclosure height and padding changes are immediate,
  and `prefers-reduced-motion` disables the remaining motion.
- Every convenience action remains available as a visible button or menu item.
  Do not register fixed system-wide shortcuts. If configurable in-app shortcuts
  are added later, they must be editable and disableable, ignore text inputs and
  IME composition, warn about duplicate or known reserved combinations, and
  show their current binding beside the action and in its tooltip instead of on
  a separate shortcut-learning page.
- Never rely on color alone: every state also has text, an icon, or a label.

## References

- <https://github.com/ibelick/ui-skills>
- <https://github.com/nextlevelbuilder/ui-ux-pro-max-skill>
