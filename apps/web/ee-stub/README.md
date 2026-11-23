# ee-stub

Open-source stand-ins for the Enterprise Edition web modules.

OSS code imports EE modules through the `@ee/*` alias. At build time
`next.config.js` points that alias at `apps/web/ee` only when its known
`services/tenancy/resolveMulti.server.ts` entrypoint exists and EE is not forced
off. `LEARNHOUSE_DISABLE_EE=1` or `LEARNHOUSE_PUBLIC=true` forces this folder
even when EE exists. A broken EE symlink fails the build rather than silently
selecting stubs. Community builds compile without EE.

Every module imported from `@ee/*` outside of `ee/` must have a file here with
the same path and the same exports. Stubs render nothing or fall back to the
single-org behavior.
