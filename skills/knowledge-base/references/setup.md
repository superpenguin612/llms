# Setting up a new vault: the interview

A vault's profile and README decide everything downstream: what gets captured, how it's typed, which tags exist, who can read it. Don't guess them. Interview the owner first, then write `kb.yaml` and `README.md` from the answers.

## How to interview

- One topic at a time, a few questions per round. Offer concrete options with a recommended default ("Recommended: …") so answering is quick; always allow a free answer.
- Use what already exists before asking: existing notes, folders, an older corpus, the repo's AGENTS.md. Ask about what you can't infer, and say what you inferred.
- Play answers back as the profile and README you're about to write, and get a yes before writing.
- Record decisions and their reasons in the README (Conventions, Status) so the next agent doesn't re-ask.

## What to ask

1. **Purpose.** What decisions, tools, reports or preparation will this vault feed? Who acts on it? What would make it worth maintaining?
2. **Scope and coverage.** What's in, what's deliberately out? Time range (seasons, years, versions)? What does "complete" look like?
3. **Consumers.** People, agents (cloud, local), domain skills, tools that read it. Teammates who contribute? (→ `inbox/<name>/` flow, who owns triage.)
4. **Access.** Can cloud agents read it, or local models only? (→ `access: shared | local`.) Anything sensitive that needs its own vault?
5. **Sources.** Where does material come from: web pages, PDFs, videos, repos, packets, chat exports, our own screenshots and logs? Any existing corpus to seed from? (→ types and capture plan.)
6. **Types.** Which record types does the domain need beyond the starter's (source, entity, evidence, topic, playbook, output)? For each new one: external or ours, kept original or not, quoted verbatim or not, input/synthesis/output, refreshed or watched, claims cited? (→ `types:` traits, folders, required keys.)
7. **Trust tiers.** Which source roles matter here, and what does each mean in this domain? (→ `tiers:` and the Conventions section.)
8. **Scope fields, contexts, events.** How is material scoped (season, event, region, version)? What events or engagements produce evidence, and when are they? (→ `scope_fields`, `contexts`, `events`, `before-event` refresh.)
9. **Tags.** Propose a small starting vocabulary (8-15) of *aspects* of the domain, each with a one-line meaning. Not the domain's own name, not one-off topics. (→ `tags:`; more are added later with `kb tags add`.)
10. **Entities and watching.** What does the vault track over time: teams, orgs, people, projects, hosts? Which repos, accounts, pages or feeds should be watched, and how often? Where should alerts go? (→ entity kinds, `watch_every`, `alerts`.)
11. **Outputs and downstream.** What leaves the vault (findings, requirements, briefs), and what consumes it (a report tool, an issue tracker, a domain skill)? (→ output types and their keys.)
12. **Layout.** Where does the vault live (usually `knowledge-base/` in the domain repo)? Which existing folders move in, which stay out (code, tools)?

## Then

1. `kb init <dir> --profile <closest starter> --name <name>`.
2. Edit `kb.yaml` from the answers (types, tiers, tags with meanings, scope fields, contexts, events, alerts); `kb build` validates it.
3. Fill `README.md`'s Purpose, Scope and coverage, Consumers, Conventions and Status from the answers (no `<!-- kb:todo -->` left).
4. `kb registry add <name> <dir>`; set up watches; seed the capture queue (`kb queue add`) from any existing corpus.
5. `kb lint && kb build`, commit, and show the owner the README and profile.
