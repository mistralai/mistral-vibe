# 0021 Registry Skills Resolution and Store

## Decision

Behind `experimental_enable_registry_skills`, skills pinned from the AI Registry
follow these rules:

- **Resolution is scope-first.** A skill name loads the nearest scope's skill,
  and within a scope a file beats a pin: project file, project pin, global
  file, global pin. The server marks the pin each name loads as `active` in
  `skills/installed`; a client renders that mark and never ranks skills itself.
- **One store per machine.** Every pinned version lives once in
  `skills-registry-cache/store/<skill_id>/<version>`, shared by every process
  and session. A version is staged and renamed into place under its version
  lock, and never changes once stored.
- **Sessions keep the store current.** A session syncs the versions its
  manifests pin when it opens and when its project roots change. It holds a
  lease on the versions it loads, and marks a version used when it stores,
  loads or drops it. A load is marked without a lock, before it is checked, so
  a cleanup that moves a version aside as a session marks it puts it back. A
  sandboxed session copies the versions it resolves into its sandbox before
  every derivation it adopts.
- **The store cleans itself, conservatively.** After a sync that succeeded, at
  most once a day per machine, a version goes only if no session used it for
  30 days, it is not its skill's newest, neither the global manifest nor any
  project's on record pins it, no alias last resolved to it, no lease holds it,
  and the registry still serves it. The cleanup checks again under the
  version's lock, then moves it aside in one rename. Its walks and deletes
  run in the background worker ([0019](0019-process-services.md)); the served
  check runs in the app server, with the session's credentials.
- **Locks never stall a session.** Writers wait ten seconds at most for a
  lock; the cleanup never waits, it skips. The records the cleanup reads
  (resolved aliases, project manifests on record, leases) are written to disk
  and replaced in one rename, the shared ones under their lock. An alias or
  manifest record that cannot be read stops the cleanup.

## Rationale

One store keeps a version once however many repositories pin it. Deleting
from it is safe only because the cleanup takes nothing a session, a manifest
or an alias may still need, and only what a later sync can fetch again: a kept
version costs disk, while a missing one breaks a session. Releases from before
the cleanup neither mark nor hold what they load, which is why the served check
stays.

## Agent Guidance

- Reach the store's layout, locks and markers through
  `vibe/core/skills/registry/_store.py`, and the records the cleanup reads
  through `_records.py`.
- Copy a sandboxed session's skills before every derivation it adopts: route
  it through `derive_configuration`, or copy first as `_derive_candidate` does
  for an org config.
- Delete store versions only in `_eviction.py`, and reach it from the app
  server only through the `skill_store` jobs of the background worker. Add a
  keep rule with a test that fails without it.

## Flag To User When

- A change would let the cleanup wait for a lock, delete a version the
  registry no longer serves, or delete without the version's lock.
- A client would start ranking skills itself instead of reading `active`.
