# Changelog

## [0.4.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.3.0...hermes-otel-v0.4.0) (2026-04-26)


### Features

* **dashboard:** add dashboard page for otel ([983814d](https://github.com/briancaffey/hermes-otel/commit/983814d48bad6fe4b0e2c145ea81772803395fed))
* **hook:** optional hook settings ([c86a583](https://github.com/briancaffey/hermes-otel/commit/c86a58391ca75910d76ab847e0928e789c7faee3))
* **hooks:** capture gateway sender id ([27a1fad](https://github.com/briancaffey/hermes-otel/commit/27a1fadeaf98ef750676ab55d91a52ea5b9acdea))
* **hooks:** capture gateway sender identity ([5db08d1](https://github.com/briancaffey/hermes-otel/commit/5db08d1a749d450b1c28fe7fd7204f77af28cb80))
* **hooks:** map sender id to user.id ([d6140c0](https://github.com/briancaffey/hermes-otel/commit/d6140c0098debd7024b19bd44d8b8c1ce4f0b59b))


### Bug Fixes

* **lint:** format code ([7721056](https://github.com/briancaffey/hermes-otel/commit/77210563592fbc73613d2cac12f04536b02b5b3d))

## [0.3.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.2.0...hermes-otel-v0.3.0) (2026-04-22)


### Features

* **backend:** remove generic backend, add uptrace and openobserve backends ([2b49e3d](https://github.com/briancaffey/hermes-otel/commit/2b49e3db986768b25f0280b855790159690eb8b6))
* **logs,lgtm:** add OTel logs pipeline and LGTM docker stack ([64cbf4d](https://github.com/briancaffey/hermes-otel/commit/64cbf4d70ff26c86a453846e8c88f755a9292c13))

## [0.2.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.1.0...hermes-otel-v0.2.0) (2026-04-19)


### Features

* **docs:** add docs site using docusaurus ([8f4caa0](https://github.com/briancaffey/hermes-otel/commit/8f4caa0ca3eae5be12543e099d1215072ac506d8))


### Bug Fixes

* **docs:** unblock build with webpackbar override and MDX escape ([eb00673](https://github.com/briancaffey/hermes-otel/commit/eb00673db3b639830e5925b814e2d05af0a9819f))
* **docs:** unblock Docusaurus build & deploy site ([9ac3fab](https://github.com/briancaffey/hermes-otel/commit/9ac3fabe20ce0980cbeb9aadb2ff02b85bdc89f4))

## 0.1.0 (2026-04-19)


### Features

* **batch:** add batching for multiple otel backends ([df6cce0](https://github.com/briancaffey/hermes-otel/commit/df6cce076dd0405684f62d9eda2d1630f5e297aa))
* **black:** format with black ([c4a7e83](https://github.com/briancaffey/hermes-otel/commit/c4a7e8339fb4b0c2ee1f8b7af6c8d958a4b1a018))
* **config:** add config details ([7a776b7](https://github.com/briancaffey/hermes-otel/commit/7a776b712e93d43137b31d7448951f26c51024de))
* **config:** add yaml/env config, per-turn summaries, orphan sweep, jaeger/tempo support ([efc1f26](https://github.com/briancaffey/hermes-otel/commit/efc1f26be5a63fcb0692934928fda609d11366bc))
* **contextvar:** replace threading.local with contextvar ([0fca6f6](https://github.com/briancaffey/hermes-otel/commit/0fca6f688b747740bf9006984ef95d5b49e5c85b))
* **contextvar:** replace threading.local with contextvar ([ea0fdb0](https://github.com/briancaffey/hermes-otel/commit/ea0fdb0664738fb6a514dace614bf0fff496016a))
* **gha:** add github actions for unit tests and various fixes ([4b8b751](https://github.com/briancaffey/hermes-otel/commit/4b8b7514605d148782f888884a4631320b323858))
* **metrics:** add otlp metrics ([6b2650b](https://github.com/briancaffey/hermes-otel/commit/6b2650bf5d06ee9afd281c4c803b196e8dccf76a))
* **otel:** add otel plugin for hermes agent ([9383853](https://github.com/briancaffey/hermes-otel/commit/9383853abae3db7623bbaacc19fef654a78d6577))
* **refactor:** phase 0 refactor ([008511d](https://github.com/briancaffey/hermes-otel/commit/008511d55e43b30550b88d87069a157521976342))
* **signoz:** add signoz support ([5d842b3](https://github.com/briancaffey/hermes-otel/commit/5d842b369a0cb26e92b4b87ad61dadfdd7ae5413))
* **tests:** add tests and refactor ([d7a97f9](https://github.com/briancaffey/hermes-otel/commit/d7a97f92e8f42e24f6ed028bdc49f786e026cb6a))


### Bug Fixes

* **gha:** defer relative imports in plugin __init__ to register() ([b70ca8b](https://github.com/briancaffey/hermes-otel/commit/b70ca8b93b0a068ba145cbdf458ca1868f268d4e))
* **gha:** fix for gha ([c215516](https://github.com/briancaffey/hermes-otel/commit/c215516b7905ff21469c22614fdefba1fff07905))
* **gha:** fix gha tests ([355bc01](https://github.com/briancaffey/hermes-otel/commit/355bc018b7f3b1721381b7164ec9d3585f912f2e))
* **gha:** use importlib import mode in pytest ([b84924e](https://github.com/briancaffey/hermes-otel/commit/b84924e801428e0333018ed6c0667ddceb7a6752))
* **misc:** various fixes ([2704676](https://github.com/briancaffey/hermes-otel/commit/2704676b2054ca8118a9f1c5bad9f56fe0fe4fba))
* **misc:** various fixes for span names ([cb44380](https://github.com/briancaffey/hermes-otel/commit/cb443809fac3bc0fa867eeaf5707e149f8bf7326))
