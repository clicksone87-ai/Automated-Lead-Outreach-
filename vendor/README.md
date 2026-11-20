# vendor

Third-party code is pinned here by manifest, not copied into the repository.

`scout.lock.json` pins [Scout](https://github.com/kiryano/Scout) (MIT). The parts the system
uses are ported into `backend/app/agents/scout/` (with the upstream licence in
`LICENSE.scout`); the upstream tree is only needed to compare against a newer release:

```
git clone https://github.com/kiryano/Scout.git vendor/scout
git -C vendor/scout checkout 183868d4fe4b9800d649d814ad8685750cc5637d
```

`vendor/scout/` is git-ignored.
