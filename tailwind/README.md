# Tailwind — optional CSS engine

**The default needs no Node and no build step.** `assets/css/site.css` is hand-authored, ships
compiled, and serves all 1,020 skeleton × style × theme combinations at 36 KB. If you never touch
this folder, nothing here runs and nothing here is required.

This folder exists because Tailwind is genuinely good at the thing the hand-authored sheet is bad
at: **ad-hoc utilities while you are editing a template.** Adding `mt-8 grid gap-4 md:grid-cols-3`
to a partial is faster than opening the stylesheet, naming a class, and writing the rule.

## What it does

`tailwind/src.css` produces a **drop-in replacement** for `assets/css/site.css`:

1. `@import "tailwindcss"` — every utility, tree-shaken against the templates
2. `@theme` — the design tokens as Tailwind theme values, so `bg-brand`, `text-ink`,
   `rounded-card` and `font-display` resolve to the *same* custom properties the themes set at
   runtime. A utility and a hand-written rule cannot disagree about what "brand" means.
3. the existing hand-authored stylesheet, imported whole — so every skeleton, pack and variant
   keeps working exactly as before

You get utilities **in addition to** what exists, not instead of it.

## Use it

```bash
python3 foundry.py css tailwind     # compile -> assets/css/site.tailwind.css
python3 foundry.py css check        # which engine each site is set to
```

Then in a site's YAML:

```yaml
css_engine: tailwind    # default is `builtin`
```

The renderer copies whichever file that names. Both can coexist: one site on Tailwind, the rest
on the built-in sheet.

## The honest trade

| | built-in | tailwind |
|---|---|---|
| Node required | no | **yes** |
| Build step after a template edit | none | **`foundry css tailwind`** |
| Size | 36 KB, fixed | 36 KB + the utilities you actually use |
| Ad-hoc utility classes | no | yes |

That middle row is the one that bit Engine A. Its templates and its compiled bundle could drift,
and a section that used a class the compiler had not seen rendered **silently unstyled** — no
error, no warning. `foundry css check` reports drift rather than letting you find it on a live
page, but the risk is real and it is the reason the built-in sheet stays the default.
