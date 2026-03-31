# X4 Foundations Source Repository

## LLM Guidelines

If unsure about something, or considering doing extra work beyond what was explicitly requested, **ask first**.

**Only add content that is explicitly defined in game source code.** Do not guess or assume additions based on descriptions, lore, or inferences. If something isn't in the code, don't include it.

## Version Naming

Source directories under `source/` use short version codes. In the version number, **B** = Beta, **H** = Hotfix (e.g., `9.00B1` = 9.00 Beta 1, `8.00H4` = 8.00 Hotfix 4).

## Directory Structure

### `source/` — Game Source Files

Extracted game data files used as the authoritative reference.

- Each version directory contains extracted data (libraries, maps, aiscripts, etc.)
- DLC content lives under `source/{version}/extensions/ego_dlc_*`. Each DLC mirrors the base game structure (assets/, libraries/, md/, maps/, index/). DLCs:
  - `ego_dlc_split` — Cradle of Humanity (Split faction, ships, stations)
  - `ego_dlc_terran` — Cradle of Humanity (Terran/Yaki/ATF factions, Solar System sectors)
  - `ego_dlc_boron` — Kingdom End (Boron faction, terraforming mechanics)
  - `ego_dlc_pirate` — Tides of Avarice (pirate faction; only DLC with `aiscripts/`)
  - `ego_dlc_timelines` — Timelines (story/scenario content, cluster environments)
  - `ego_dlc_ventures` — Ventures (seasonal content under `assets/season_01/`, chapters/coalitions)
  - `ego_dlc_mini_01` — Hyperion (single ship + story)
  - `ego_dlc_mini_02` — Envoy (Envoy + Cypher ships + story)

## Source Code Guide

### Key Files

- `libraries/wares.xml` — All wares: pricing (min/avg/max), production recipes, resources, equipment
- `libraries/parameters.xml` — Economy tuning, build pricing factors, game constants
- `libraries/scriptproperties.xml` — All game object properties accessible to scripts (prices, stock levels, relations, etc.)
- `libraries/jobs.xml` — NPC job definitions (ship spawning, roles, quotas)
- `libraries/ships.xml` — Ship macro definitions and properties
- `libraries/loadouts.xml` — Ship equipment loadouts
- `libraries/loadoutrules.xml` — Rules governing loadout generation
- `libraries/defaults.xml` — Default values and fallbacks
- `libraries/gamestarts.xml` — Game start scenarios
- `libraries/characters.xml` — NPC character definitions
- `libraries/factions.xml` — Faction definitions, diplomacy signals, response modes, faction relationships
- `libraries/modules.xml` — Station production module definitions: recipes, limits, faction compatibility
- `libraries/stations.xml` — Station categorization and faction mapping (shipyards, wharfs, equipment docks)
- `libraries/races.xml` — NPC race definitions with character models, movement speeds, appearances
- `libraries/diplomacy.xml` — Diplomatic agent actions, costs, rewards, duration mechanics
- `libraries/region_definitions.xml` — Region environmental definitions (nebulae, fog, resources, boundaries)
- `libraries/constructionplans.xml` — Station construction blueprints with module positioning
- `libraries/drops.xml` — Lockbox loot tables, ammunition baskets, destruction drops
- `libraries/quotas.xml` — Mission offer weights/quotas per zone (trade, fight, build, explore, etc.)
- `libraries/stock.xml` — NPC inventory definitions (what traders/pirates carry, quantities, chances)
- `libraries/people.xml` — NPC crew composition and skill distribution by faction/role
- `libraries/roles.xml` — NPC character roles (service, marine, passenger, worker, trainee) with skill tiers
- `libraries/posts.xml` — NPC post/job definitions (pilot, engineer, manager, trader) with control types
- `libraries/purposes.xml` — Ship/module purpose tags (trade, fight, build, mine) mapped to ship classes
- `libraries/shipgroups.xml` — Faction-specific ship selections for NPC spawning
- `libraries/baskets.xml` — Ware groupings used by stock, drops, and inventory systems
- `libraries/unlocks.xml` — Discount/commission modifiers based on conditions (relations, scanner level, etc.)
- `libraries/terraforming.xml` — Terraforming mechanics (temperature, pressure, oxygen, humidity states)

### Directory Conventions

- `md/` — Mission Director scripts: game logic, missions, faction behavior
- `aiscripts/` — AI behavior scripts: ship orders, combat, trading, restocking
- `libraries/` — Static data definitions: wares, ships, parameters, jobs
- `t/` — Localization text files (16 languages). Files named `0001-lXXX.xml` where XXX is a language code (e.g., l044=English, l007=Russian, l049=German). Text IDs are hierarchical: `<language>` → `<page id="NNNN">` → `<t id="N">` where page IDs group by category (1001=Interface, 1002=Player Choices, etc.)
- `maps/` — Sector/zone/cluster layout data. `xu_ep2_universe/` is the main playable game map. Hierarchy: Galaxy → Clusters → Sectors → Zones, defined across `galaxy.xml`, `clusters.xml`, `sectors.xml`, `zones.xml`, `zonehighways.xml`, `sechighways.xml`. Other subdirectories are testing/demo universes (demo_universe, blackgalaxy, effectuniverse) or utility (editor, mainmenuscene, unittests)
- `shadergl/` — OpenGL shader parameter definitions in `high_spec/`: `default.xml` (base PBR material properties) and `enforced.xml` (runtime-mutable parameters for glow, animation, planet rendering, dynamic effects)
- `assets/` — Game object definitions using two-file architecture: component files (geometry, lights, connections) + macro files (gameplay properties, stats). Naming: `[type]_[faction]_[size]_[role]_[variant]` (e.g., `ship_arg_s_fighter_01`). Faction codes: `arg` (Argon), `par` (Paranid), `tel` (Teladi), `xen` (Xenon), `kha` (Kha'ak), `spl` (Split), `gen` (generic). Sizes: `xs/s/m/l/xl`
  - `units/` — Ship/drone definitions by size class (size_xs through size_xl). Components define geometry; macros contain actual ship stats: hull HP, mass, inertia, drag, thrust, jerk, crew capacity, missile storage, software slots, steering curves. Covers fighters, corvettes, destroyers, carriers, miners, drones, spacesuits
  - `structures/` — Station module definitions: production factories (40+ ware types), habitats, defense modules, docking bays, storage containers, connection pieces, landmarks. Macros contain hull HP, workforce capacity, cargo limits, production recipes, build permissions
  - `props/` — Equipment and world objects (1050 files): engines (with thrust/boost/travel stats), weapons/turrets (rotation speed, reload, bullet refs), shields (by faction/size/mark), gates, highway elements, storage lockboxes, docking bays, scanners, satellites, interactive objects. Engine macros are authoritative for propulsion stats
  - `environments/` — Sector visuals (849 files): asteroids by material/size (crystal/ore/ice, XS-XXL), cluster skyboxes with positioned celestial bodies, debris fields, fog/nebulae, rendered sector meshes (planets/moons/suns), nav beacons. Destructible asteroids have hull HP, wreck refs, and drop positions
  - `interiors/` — Station/ship interior layouts (283 files): faction-specific rooms and corridors (`room_arg_*`, `room_bor_*`), ship bridges by size, dockarea props, trader corners, event monitor scenes (story cinematics), Teladi prison construction set, reusable xref props by tech level (hightech/lowtech/standard)
  - `characters/` — Player entity, BETTY AI computer, NPC body definitions with face/bone modifiers for customization, platform interaction entities (enter/exit/undock). Race-specific body geometry in subfolders
  - `wares/` — Visual 3D representations of tradeable items (193 files): geometry, bounding boxes, materials for all raw materials, equipment, missiles, inventory items. Paired with `libraries/wares.xml` which has the economy data (pricing/recipes)
  - `fx/` — Visual effects (451 files): weapon bullets/impacts/muzzles by faction and size, explosions (distortion/light/smoke), engine boost/exhaust, lens flares, EMP, data leaks. Weapon macros here contain reload/damage/heat properties alongside visual definitions
  - `cutscenecore/` — Cutscene scene compositions: NPC conversation rooms, main menu backgrounds. Components define camera positioning, character spawn events, lighting rigs; macros connect to ships/stations/rooms
  - `map/` — Editor utility only (2 files: axis gizmo, map parts template). All actual game map data is in the top-level `maps/` directory
  - `ui/` — 3D HUD spatial positioning (7 files): screen anchor points for radar, crosshair, speedbar, panels, infobars. Defines WHERE UI elements appear in 3D space; top-level `ui/` defines HOW they behave (Lua)
  - `legacy/` — Actively indexed base-game assets (484 files, not deprecated): explosion FX chains, boost effects, advertisement signs, storage/cargo modules, highway elements. All registered in `index/` lookup tables
  - `system/` — Engine utility components: graphics primitives (XGfx), physics test objects (XPhys), dummy components, lighting setups. Not production game content — useful as reference for the component/macro architecture pattern
- `cutscenes/` — Cutscene definitions: camera sequences (orbit, pan, follow, lookat), keyframed animations, environment setup, and event timing for cinematics
- `ui/` — UI framework and addons: `core/` has XSD schemas and Lua runtime for HUD elements; `addons/ego_*` define menus/panels (each with `ui.xml` manifest + Lua scripts); `widget/` has reusable components
- `index/` — Master lookup tables: `macros.xml` and `components.xml` map names to file paths across all assets

### Mission Director Naming

- `md/gm_*.xml` — Generic mission implementations (gameplay logic)
- `md/rml_*.xml` — Runtime mission libraries (reusable mission building blocks)
- `md/gmc_*.xml` — Generic mission chains (multi-step missions)
- `md/*_subscriptions.xml` — Mission subscription managers: define when/where missions spawn (e.g., `x4ep1_trade_subscriptions.xml`, `x4ep1_pirates_subscriptions.xml`)
- `md/factionlogic*.xml` — Faction AI decision-making
- `md/factiongoal_*.xml` — Faction strategic goals
- `md/factionsubgoal_*.xml` — Faction tactical sub-goals
- `md/gs_*.xml` — Game start scenario scripts (intro, tutorial, trade, fight, etc.)
- `md/lib_*.xml` — Reusable MD libraries (ship creation, dialogs, factions, rewards)
- `md/npc_*.xml` — NPC management systems (spawning, trading, state machines, use cases)
- `md/tutorial_*.xml` — Tutorial sequences (flight, mining, boarding, stations, map, etc.)
- `md/story_*.xml` — Story/campaign content (faction storylines, diplomacy, research)
- `md/scenario_*.xml` — Game scenario definitions (combat, advanced, tutorials)
- Standalone system files: `boarding.xml`, `diplomacy.xml`, `encounters.xml`, `trade.xml`, `terraforming.xml`, `fleet_reconstitution.xml`, `inituniverse.xml`, etc.

### AI Script Naming

- `aiscripts/order.*.xml` — Assignable orders (player-issuable commands)
- `aiscripts/interrupt.*.xml` — Reactive behaviors (restocking, attacked responses)
- `aiscripts/move.*.xml` — Movement/navigation (29 files): autopilot, flee variants, gate transit, docking, parking, idle, evasion, claiming
- `aiscripts/fight.*.xml` — Combat targeting by ship class: bigtarget, capital, fighter, medium, station
- `aiscripts/lib.*.xml` — Reusable utility libraries: ammo management, mining efficiency, fleet defense, target selection, weapon modes
- `aiscripts/masstraffic.*.xml` — Background traffic system: generic behavior, police, flee, attack handling
- `aiscripts/mining.*.xml` — Mining operations: drone/ship collection by type (solid, liquid, capital)
- `aiscripts/boarding.*.xml` — Boarding pod operations and return
- `aiscripts/trade.*.xml` — Trade utilities: find commander, find free trader, station trading
- `aiscripts/player.interaction.*.xml` — Player event handlers (budget, trade results)
- `aiscripts/build.*.xml` — Construction: build storage, ship trader

### How Things Connect

- **Mission flow:** Subscription files (`*_subscriptions.xml`) define mission availability and pass parameters to `gm_` files, which use `rml_` building blocks for objectives
- **Ship spawning:** `jobs.xml` defines NPC fleet composition; `get_ship_definition` in scripts selects ships by size/faction/tags; `wares.xml` production entries define build costs
- **Pricing:** Wares have min/avg/max in `wares.xml`; equipment at wharfs/equipment docks is priced by resource cost (`buildprice`) with a `buildpricefactor` multiplier based on build queue size; NPC stations vary 0.9x–1.15x
- **AI behavior:** `aiscripts/order.*.xml` are assignable orders; `aiscripts/interrupt.*.xml` handle reactive behaviors; `move.*.xml` handles navigation/pathfinding; `fight.*.xml` handles combat by target class; `lib.*.xml` provides shared utilities
- **Localization:** Text referenced by page+ID (e.g., page 1001, id 5) across `t/0001-lXXX.xml` language files; scripts use `{1001,5}` style references

