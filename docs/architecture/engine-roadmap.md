# PathfinderEngine architecture roadmap

PathfinderEngine should reuse the architectural lessons of `DnDControllerV1` without
copying D&D 3.5e mechanics into Pathfinder 2e.

## Layer 0 — published reference library

Status: **started**

Responsibilities:

- complete Archives of Nethys index snapshot;
- lossless normalized records;
- stable references and provenance;
- source/category indexes;
- Legacy/Remaster relationships and aliases;
- integrity verification;
- no executable-mechanics claims.

Primary paths:

- `scripts/sync_aon.py`
- `scripts/verify_source_data.py`
- `schema/source-definition-v1.schema.json`
- `data/source/`

## Layer 1 — PF2e rules kernel

Planned responsibilities:

- three-action economy and reactions/free actions;
- activity/action costs;
- checks, DCs, degrees of success, natural 20/1 shifts;
- proficiency ranks and level-based proficiency;
- bonuses/penalties and stacking types;
- multiple attack penalty;
- traits as executable rule hooks;
- damage, resistance, weakness, immunity, persistent damage;
- conditions and condition values;
- dying, wounded, doomed, recovery, Hero Points;
- movement, reach, cover, concealment, detection and senses;
- spell traditions, spell ranks, focus points and refocus;
- counteract checks;
- afflictions;
- encounter XP and creature adjustments.

This layer should consume normalized source references but implement mechanics in
versioned code/data separate from the published text.

## Layer 2 — character construction/state

Planned responsibilities:

- ancestry, heritage and versatile heritage;
- background;
- class and class features;
- archetypes and dedications;
- feats and prerequisites;
- skills and skill increases;
- attributes and boosts;
- equipment, runes, invested items and bulk;
- spells, slots, repertoires, preparations and focus spells;
- level-up validation and retraining;
- optional rules profiles such as Free Archetype.

## Layer 3 — encounter/combat controller

Planned responsibilities:

- initiative by arbitrary skill/perception;
- encounter turn order;
- action/reaction tracking;
- MAP-aware strikes;
- targeting, range and areas;
- saves and basic saves;
- conditions and timed effects;
- persistent damage/recovery;
- death and stabilization;
- hazards;
- encounter budgets and XP awards;
- deterministic RNG/event log for reproducibility.

## Layer 4 — campaign/world systems

Planned responsibilities:

- campaign state and chronology;
- quests and procedural generation;
- settlements, factions, merchants and inventory;
- travel/exploration activities;
- downtime;
- reputation and subsystems;
- encounter generation;
- world packages and maps;
- narrative description references for continuity.

## Layer 5 — application surface

The end state can mirror the useful `DnDControllerV1` separation:

- core IDs/errors/RNG/time;
- PF2e rules;
- compendium/reference retrieval;
- character construction;
- combat;
- encounter AI;
- campaign state;
- generation;
- persistence;
- server/API;
- MCP/controller integration;
- optional UI.

## Rules-resolution policy

PathfinderEngine should default to current Remastered material when AoN explicitly
links a Legacy entry to a replacement, but it must retain Legacy content and allow a
campaign profile to opt into it.

A name match alone is never enough to declare two entries the same rule. Explicit AoN
Legacy/Remaster links are authoritative identity evidence; other suspected overlaps
should be reviewable rather than silently merged.

## Data/mechanics boundary

The published reference library answers **what the source says**.

The rules kernel answers **what the engine can execute**.

Keeping those separate prevents a large indexed corpus from creating the false
impression that every feat, spell, archetype, creature ability, or subsystem has
already been implemented mechanically.
