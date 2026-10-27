# Researching entities from public sources

Entities are our running notes on things the vault tracks: teams, orgs, projects, products, hosts, and people. This procedure keeps them current from public sources.

## Scope rules (always on)

- **Public information only.** Nothing behind a login, nothing obtained by deception, no scraping that breaks a site's terms.
- **For people, only their professional footprint that bears on the vault's purpose:** public repos and commits, talks, write-ups, blog posts, conference and team bios, competition results, and public posts about the domain.
- **Never collect** home addresses, family members, phone numbers, personal accounts unrelated to the domain, photos of people, or anything about minors beyond a public roster listing.
- **Every person entity states `osint_scope`:** why they're tracked and which footprint is in scope. If you can't state a reason tied to the vault's purpose, don't create the entity.
- **Linking two accounts or names as the same person needs a cited basis** (a profile that links the other account, the same commit email, a roster naming both). Until then, mark it `[inferred: basis]` and leave it in `## Open questions`. Never merge people with common names on similarity alone.
- **An organization assessed under rules of engagement** (e.g. an engagement client) is researched only within those rules.

## Procedure

1. **Create or open the entity** (`entities/<kind>/<id>.md`):
   - `kind`;
   - `handles` (`github:<login>`, `domain:<name>`, `site:<url>`);
   - `osint_scope` for people;
   - `collectors` (e.g. `[github, crtsh, site]`);
   - `watch` targets for fast-changing things.
2. **Collect:** `kb collect <id>`. Collectors cache dated results under `.kb/collect/` and print what's new. They never edit the note.
3. **Write the note** (your part):
   - `## Summary`: who or what it is, why we track it, the one thing to know.
   - `## Facts`: each fact dated and cited. Collector facts as `(collector: github, 2026-10-20)`. Facts that matter for a decision are promoted: capture the page or repo as a source (`kb capture`) and cite its excerpt instead.
   - `## Timeline`: dated, newest first. Watch appends here automatically.
   - `## Links`, `## Open questions`.
4. **Related entities:** create entities for related entities (members, maintainers, parent orgs) only when they're in scope. Otherwise mention them by role ("three other contributors").
5. **Before relying on a note,** check `last_checked` and the dates on its facts. Say how old the information is when you use it.
