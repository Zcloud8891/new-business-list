# Ops notes (not published; repo root is public though, so no secrets here)
Live: https://zcloud8891.github.io/new-business-list/  (GitHub Pages, account Zcloud8891, source main:/docs)

## Monthly rebuild, unattended
Option A (box routine, works today with existing gh login):
  cd /workspace/lane-new-biz/site && python3 build.py && git add -A summaries docs && \
  (git diff --cached --quiet || (git -c user.name=new-business-list-bot -c user.email=41898282+github-actions[bot]@users.noreply.github.com commit -m "Rebuild $(date +%F)" && git push))
  cron: 0 8 5-18 * *  (America/Los_Angeles). Quiet when nothing changed.
Option B (no box needed): copy ops/monthly-rebuild.yml to .github/workflows/ in the GitHub web UI
  (signed in as Zcloud8891), or run `gh auth refresh -s workflow` (browser device code), then push it.

## Competitor swap test (home H1). Rival: Oregon New Businesses (oregonnewbusinesses.com/pricing, checked 2026-11-10:
## $49/mo major metro incl. Eugene, $29 mid, $19 smaller city, $199/mo all Oregon, weekly CSV by email)
v1 "Every new Oregon business, delivered fresh."  -> swap: still true for them (weekly, fresher). Nothing breaks. Rejected.
v2 "471 new Lane County businesses registered in October 2026." -> swap: same state data, still true. Rejected.
v3 (live) "471 new Lane County businesses registered in October 2026. Get every one for $9 a month."
   + sub "All of Oregon is $19 a month ... built from the state's own records by a dad in Veneta"
   -> swap breaks: $9 for all of Lane (they charge $49 for Eugene alone, +$19 per smaller town); $19 statewide (theirs $199);
      "dad in Veneta". Weak spot: price can be matched; Veneta-dad local angle can't.
