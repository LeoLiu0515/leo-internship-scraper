import asia_scan as a
a.J104_KWS = ["實習"]
a.J104_MAX_PAGES = 6
rows = a.load_104()
print("104 raw rows", len(rows))
kept = [r for r in rows if a.title_ok(r["title"], r["company"], True) and a.term_ok(r["title"])]
print("104 kept after filters", len(kept))
import collections
print("by age<=14:", sum(1 for r in kept if r["age"] <= 14))
for r in kept[:25]:
    print("  ", r["age"], "d |", r["company"][:20], "|", r["title"][:60], "|", r["loc"][:12])
print("--- dropped sample ---")
for r in [r for r in rows if r not in kept][:10]:
    print("  x", r["company"][:18], "|", r["title"][:60])
