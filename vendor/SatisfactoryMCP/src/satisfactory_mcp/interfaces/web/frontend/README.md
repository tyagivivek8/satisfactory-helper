# The map page

TypeScript, built by Vite into `../static/`, which is the directory `app.py` mounts at `/`.

**The built bundle is NOT committed.** `static/` is gitignored: `app.js` is a dependency's
compiled code -- Leaflet, minified -- and the repository does not carry or redistribute it.
A fresh clone therefore has no page until the build below has run once; until then the
server answers `/` with exactly that instruction (and the JSON API works regardless).
`tests/test_architecture.py` enforces both halves: nothing under `static/` may ever be
tracked, and every file a build puts there carries the build banner -- build output is
exactly the kind of file someone edits in place, and the next build silently throws that
edit away.

## Building, changing anything

```
cd src/satisfactory_mcp/interfaces/web/frontend
npm ci            # exact versions from package-lock.json
npm run build     # build the page into ../static/, which app.py serves
npm run dev       # Vite on :5173, hot reload, /api proxied to :8712
npm run check     # tsc --noEmit
npm run typegen   # regenerate src/api-schema.d.ts from a running server
```

The artifact is never in a diff and never in a merge: what is reviewed is `src/`, and
`static/` resolves by rebuild, always -- after pulling a frontend change, run
`npm run build` again and the served page matches the sources by construction.

## The dev loop

`npm run dev` serves the page from source with hot module replacement and proxies `/api` to
whatever `satisfactory-mcp-web` is already running on `127.0.0.1:8712` — so the dev server
needs no save files, no game install and no Python of its own. Start the real server first:

```
uv run satisfactory-mcp-web      # terminal 1, :8712
npm run dev                      # terminal 2, :5173
```

**`/api/events` is Server-Sent Events, and the proxy passes it through.** This was measured
rather than assumed, because a proxy that buffers a response body turns a live stream into a
page that hangs on the first event: with the dev server up, `curl -N
http://127.0.0.1:5173/api/events` delivers `event: save` frames as the watcher emits them,
the `content-type` stays `text/event-stream`, and the live dot in the header goes green.
Vite's proxy is `http-proxy` in streaming mode and adds no compression, which is what makes
that work; if a compression plugin is ever added here, this is the thing it will break.

The page reads `/api` at absolute paths, so nothing has to be configured for dev versus
production — in production it is the same origin, in dev the proxy makes it look like one.

## Layout

| file | what it is | lines |
| --- | --- | --- |
| `index.html` | the page. Vite's entry; the built copy lands in `../static/` | 55 |
| `src/main.ts` | the entry: which map events are listened for, in what order, which modules fetch, and what happens on load | 157 |
| `src/leaflet.ts` | the one `import`, and where the private-field declarations attach | 19 |
| `src/state.ts` | the current selection, the base-map mode, the epoch, the layer registry. Imports nothing | 164 |
| `src/registry.ts` | what the page fetches, declared by the module that draws it. Imports nothing at runtime | 131 |
| `src/dom.ts` | `el`, and the escaping `popup()` every popup builder goes through | 67 |
| `src/toast.ts` | the message strip: failures, and the one non-failure note | 68 |
| `src/format.ts` | resource short name, region line, phase name | 37 |
| `src/palette.ts` | every colour chosen against the others, in one table | 121 |
| `src/api.ts` | `get()`, the two query parameters every endpoint takes, and the error branch no schema describes | 116 |
| `src/api-shapes.ts` | the response shapes, one line each, re-exported from the generated schema | 162 |
| `src/geometry.ts` | the page's own words for what it draws with: points, boxes, route curves. Imports nothing | 53 |
| `src/api-schema.d.ts` | generated from `/openapi.json`; paths, query parameters and every response body | 3133 |
| `src/leaflet-private.d.ts` | the fields this page hangs off Leaflet objects | 124 |
| `src/map.ts` | the map, the CRS, the panes, the fragment, and the `[-y, x]` rule | 219 |
| `src/fragment.ts` | the address bar as an input: re-reading `#…` when it changes under an open tab | 125 |
| `src/layers.ts` | named layer groups, kept across a refetch | 87 |
| `src/layercontrol.ts` | the folded control: floor picker, base-map modes, legend, filter, tri-state families, focus | 784 |
| `src/regions.ts` | the biome raster, how it shares the screen with a render, and whose choice it is | 176 |
| `src/tiles.ts` | the base map: four modes, one tile layer, and the probing behind both | 426 |
| `src/routes.ts` | belts and pipes: runs, lifts, junctions, chevrons | 911 |
| `src/placements.ts` | the floor plan, the machines standing on it, and the containers among them | 299 |
| `src/power.ts` | the wires and the poles they are strung between | 235 |
| `src/floors.ts` | floor mode: one storey at a time, by filtering what is already drawn | 896 |
| `src/markers.ts` | nodes, pickups, the player — and the node-dot raise | 224 |
| `src/labels.ts` | factory labels, the flight, the card's one action, and the declutter pass | 388 |
| `src/header.ts` | the identity line and the you-are-here dot: `/api/summary`'s two consumers | 81 |
| `src/inspector.ts` | the right-click answer: the one thing here that is not a layer | 155 |
| `src/load.ts` | when the page fetches, and what a reply is allowed to do; the epoch guard | 142 |
| `src/worlds.ts` | the two pickers, and keeping a selection through a rescan | 205 |
| `src/sse.ts` | one EventSource, and what a save write means | 61 |
| `src/style.css` | the page's own stylesheet, imported after Leaflet's so it wins on order | 604 |
| `vite.config.ts` | where the build writes, the banner it stamps, the licence it copies out of `node_modules/leaflet/`, the dev proxy | |
| `node-fs.d.ts` | the one Node module the config imports, declared by hand instead of installing `@types/node` | |
| `scripts/stamp-schema.mjs` | re-applies the generated schema's provenance header | |

Four things about the graph are deliberate and easy to undo by accident.

`load.ts` imports no module that fetches, and that is the newest of the four. It used to import
all seven drawing modules and call each draw function by name; they now declare what they want
fetched through `registry.ts` and `load.ts` runs the list knowing none of the names. The catch
is that a module nothing imports is a module the build leaves out, and a registration that
never ran is a layer that is simply never fetched — no compile error, no runtime error, just an
absence. So `main.ts` names every one of them in its FEATURES block, and
`tests/test_architecture.py` checks that block against the set of modules calling
`registerFetch` in both directions, plus the rule that keeps it load-bearing: `load.ts` and
`registry.ts` may not import any of them. `regions.ts` is the exception `load.ts` still names,
because `/api/regions` is geography — no world to scope it to, fetched once — so it is in
neither wave and registers nothing.

`state.ts` imports nothing. `map.ts` reads `BOOT` while it is building the map, so anything
`state.ts` imported would have to be evaluated before the map exists. It is also where
`BaseMode` is declared, for that reason and no other: the modes belong to what the page is
currently showing, and declaring the union beside the tile layers would make this file import
the module that fetches tiles.

`layercontrol.ts` does not import `labels.ts`. `batch()` used to end by calling `declutter()`
by name, which put three files in a ring — control imports labels imports layers imports
control — to say "the list has stopped changing". It now offers `onSettled`, and `main.ts`
registers the pass.

`layercontrol.ts` does not import `tiles.ts` either, and the same shape fixes it: the control
draws the four base-map radios and `tiles.ts` registers what a click on one means, through
`onModePick`. The arrow can only point that way — `tiles.ts` reaches the control through
`layers.ts` already — and the seam is what keeps "which picture is the base map" out of a
widget that otherwise knows nothing about pyramids.

`layercontrol.ts` does not import `floors.ts` for the third time round the same shape:
`onFloorPick` and `onFloorExit` are the seam, and the control draws a floor picker without
knowing what a storey is. `floors.ts` is imported by `labels.ts` (the card's action),
`fragment.ts` and `main.ts` (the address bar and the Esc key), `load.ts` (a redraw replaces a
layer's contents, and the floor filter is a fact about contents) and `placements.ts` (a save
write changes what is built, so `/api/machines` re-asks for the decomposition) — so it must
import none of those five, and does not.

Leaflet is the `leaflet` npm package pinned to **1.9.4** — the exact version that used to sit
in `static/vendor/leaflet.js` — and it is compiled into the bundle together with its own
stylesheet. Its three icon PNGs are inlined as data URIs, so the built page makes no network
request to anything but this server: no CDN, no fonts, no map tiles anyone else hosts. That
is a licence and privacy posture, not a preference, and `npm run build` is expected to keep
it. If a dependency ever emits an `assets/` directory of its own, check what is in it.

The licence half of that posture has two ends, and the repository is only one of them. The
repo redistributes no Leaflet at all — the bundle is untracked, which is the point of not
committing it. But a *build* compiles Leaflet in, and BSD-2-Clause requires the notice to
travel with that binary form for whoever ever distributes one, so every build carries it:
`vite.config.ts` copies `node_modules/leaflet/LICENSE` — the npm package is the source of
truth, there is no vendored copy to drift — into `static/vendor/LEAFLET-LICENSE`, the page
names it in a comment, and the bundle's own banner states what is compiled in.

## Types

`npm run check` is `tsc --noEmit` and it is clean under **full `strict`**, plus
`noUncheckedIndexedAccess`, `noUnusedLocals` and `noUnusedParameters`. It needs no running
server: everything it reads is committed.

`noUncheckedIndexedAccess` is the one worth calling out, because it is the setting most
projects leave off. It cost five call sites here, and each was a real "this index can miss"
that the old code happened to answer correctly: a fragment with no `z`, a section key nobody
has folded yet, a session name seen once. They are now written down as answers rather than
left as luck.

One file carries the API, and it is generated. There used to be two.

- **`src/api-schema.d.ts` is generated** by `npm run typegen` from the server's own
  `/openapi.json`, and committed. It is the authority for which paths exist, which query
  parameters each takes, what a validation error looks like — `get()` only accepts a path the
  server actually serves — **and now for every response body too**: each router under
  `interfaces/web/routers/` declares a `response_model`, so its whole payload is described
  here. Adding a field to an endpoint therefore means running `typegen`, not editing two files.
  Regenerating rewrites the file whole, so its provenance header is re-stamped by
  `scripts/stamp-schema.mjs`, which `typegen` chains.
- **`src/api-shapes.ts` re-exports those components** under the names the page already used,
  one line apiece and no fields of its own. The indirection is so that a change to an endpoint
  moves one line in one file rather than every module that draws its payload, and so the page's
  names stay the page's while their definitions come from the server. `floors.ts` predates it
  and reaches into the schema itself.
- **`src/api-types.ts` is gone.** It held the frontend's claim about the API — response shapes
  read off real payloads — and it died when the last endpoint started describing itself. What
  was in it that was never a payload lives with the code that uses it: the drawing tuples in
  `geometry.ts` and `ApiError` in `api.ts`.
- **`/api/worlds` was the last exception, and its conversion was a body change — made as
  one.** It forwarded the loader's own save headers, so the useful response model deleted the
  eight keys per row nobody read; the server now declares `WorldsResponse` like everything
  else, `state.ts` and `worlds.ts` import the rows from `api-shapes.ts`, and the page has no
  hand-written payload claims left. `worlds()` in `routers/world.py` names the deleted keys.

What is still `any`, in full:

- `L.Class.extend()` returns `any` in `@types/leaflet`, so `PyramidLayer` in `tiles.ts` is
  asserted back to a `new (url, options) => TileLayer` at the point of definition. That
  assertion is the only place the tile layer's shape is stated.
- `L.Control.Layers.sortFunction` and a few Leaflet option bags are typed by `@types/leaflet`
  as loosely as Leaflet itself defines them; nothing here widens them further.
- `/api/summary`'s `header` is an open map — `{[key: string]: unknown}` by the server's own
  declaration, because it forwards the sidecar's save header rather than restating its thirteen
  keys (see `SummaryResponse` in `routers/world.py`); the page reads the one key it uses,
  `session_name`, out of that map. The rest of the endpoint is generated in full. The sentence
  that used to stand here — "typed for the four branches this page reads and no further" — was
  written when every endpoint answered `-> dict` and typing more meant inventing a contract;
  that stopped being true when the routers declared their response models.

`src/leaflet-private.d.ts` declares the fields this page hangs off Leaflet objects. It keeps
two kinds apart on purpose: the page's own marks (`_rank`, `_chevron`, `_labelWeight`), and
three real Leaflet internals it deliberately uses (`_handlingClick`, `_update`, `layerId`).
The second list is what to read before upgrading Leaflet.
