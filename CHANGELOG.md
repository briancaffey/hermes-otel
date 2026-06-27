# Changelog

## [0.10.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.9.0...hermes-otel-v0.10.0) (2026-06-27)


### Features

* **dashboard:** complete rebuild — full Traces + Metrics + Logs, native theme ([7d2bcdf](https://github.com/briancaffey/hermes-otel/commit/7d2bcdfeb58006d3d837950e2ccade55fed10cc6))
* **dashboard:** esbuild build + tabbed shell + live "Live" view ([9824809](https://github.com/briancaffey/hermes-otel/commit/982480986dfa945b20f2e429dc8efe346d3a63fc))
* **dashboard:** esbuild build + tabbed shell + live "Live" view ([c732052](https://github.com/briancaffey/hermes-otel/commit/c732052aff5b8dfd4d9808e7e6cc46d377f50170))
* **dashboard:** in-process live telemetry store + /live API (zero-config foundation) ([fec9101](https://github.com/briancaffey/hermes-otel/commit/fec91018544a587c25012c33ac56c8b5e42f160a))
* **dashboard:** in-process live telemetry store + /live API (zero-config foundation) ([2e3f80a](https://github.com/briancaffey/hermes-otel/commit/2e3f80aab931ed60f2abbd45d69e3fdc210c8e06))
* **dashboard:** traces from live store, turn-grouped Live, quieter Logs ([3ef378e](https://github.com/briancaffey/hermes-otel/commit/3ef378e199cef69f6d29fb4f103939e47d572f29))
* **metrics:** emit OTel-standard GenAI metrics (gen_ai.client.*/gen_ai.agent.*) alongside hermes.* ([791ae35](https://github.com/briancaffey/hermes-otel/commit/791ae3581870ed3ae7d54c2946f2e2be1c5ff5ec))
* **metrics:** emit OTel-standard GenAI metrics alongside hermes.* ([70cc4ba](https://github.com/briancaffey/hermes-otel/commit/70cc4ba1d8db12c0bd2f466e3be282a8b6ff82aa)), closes [#38](https://github.com/briancaffey/hermes-otel/issues/38)
* **skills:** dev skills + self-registering observability skill ([2e5b4ba](https://github.com/briancaffey/hermes-otel/commit/2e5b4ba5f6e3332a1c0289b4086b6d6b2d766ea1))
* **spans:** capture API errors & retries (api_request_error) ([c2a036d](https://github.com/briancaffey/hermes-otel/commit/c2a036d80351d2f4350a3335b86c81f00d13362a))
* **spans:** capture API errors & retries (api_request_error) ([18b1aa1](https://github.com/briancaffey/hermes-otel/commit/18b1aa1beadd54824f608819ab8980fdfb629e2c))
* **spans:** human-in-the-loop approval spans (approval.&lt;pattern_key&gt;) ([d8ec5b1](https://github.com/briancaffey/hermes-otel/commit/d8ec5b1bb7915d21c52cde94d9ca5cf9a7953503))
* **spans:** human-in-the-loop approval spans (approval.&lt;pattern_key&gt;) ([1723784](https://github.com/briancaffey/hermes-otel/commit/172378435be25a7d4eecba2972a0f2f04245d158)), closes [#30](https://github.com/briancaffey/hermes-otel/issues/30)
* **spans:** model skills as execution-window spans (skill.&lt;name&gt;) ([517bd4c](https://github.com/briancaffey/hermes-otel/commit/517bd4c011453cb363cacda5df83fe25f092548e)), closes [#39](https://github.com/briancaffey/hermes-otel/issues/39)
* **spans:** model sub-agent delegation as a linked trace tree ([5dfe885](https://github.com/briancaffey/hermes-otel/commit/5dfe885e6a0bccf018d4b11fee3b742e93858bd4))
* **spans:** skill execution-window spans (skill.&lt;name&gt;) + observability skills ([67c644d](https://github.com/briancaffey/hermes-otel/commit/67c644d80982263ec19e86a6fd2e8fc477861140))
* **spans:** sub-agent delegation tree (subagent_start / subagent_stop) ([0c74460](https://github.com/briancaffey/hermes-otel/commit/0c744606496fd5c7c4928d27c58a41ebc80c994c))
* **usage:** capture reasoning output tokens ([a3ce136](https://github.com/briancaffey/hermes-otel/commit/a3ce136b4eb4d08bb0bc84229fd87ae9b6ceaff7)), closes [#40](https://github.com/briancaffey/hermes-otel/issues/40)
* **usage:** capture reasoning output tokens (gen_ai.usage.reasoning.output_tokens) ([b0148be](https://github.com/briancaffey/hermes-otel/commit/b0148be03ece14905876f9721574b206caabdeda))


### Bug Fixes

* **ci:** black formatting + make orphan-sweep subagent test deterministic ([3ccb952](https://github.com/briancaffey/hermes-otel/commit/3ccb95275c8586286a967fd74704a819912f8685))
* **dashboard:** back the live store with shared SQLite (cross-process) ([8316648](https://github.com/briancaffey/hermes-otel/commit/8316648dc5be4d8488f45e796b9dc4e4310bfb7d))
* **dashboard:** flamegraph as a top-line on each full-width span card ([ac98c7e](https://github.com/briancaffey/hermes-otel/commit/ac98c7e5271f14d2caa4ba7e0485a1ce50167270))
* **dashboard:** grid-aligned span waterfall with a shared time axis ([44e5e26](https://github.com/briancaffey/hermes-otel/commit/44e5e26425c7db1adce3c513b515989bc70ba474))
* **dashboard:** span detail as collapsible cards with a kind-colour top strip ([26c4036](https://github.com/briancaffey/hermes-otel/commit/26c403605f8036a1344578d208631c7b57ac24d4))
* **metrics:** label agent token usage with the real LLM provider ([93496d7](https://github.com/briancaffey/hermes-otel/commit/93496d76a7fc7946a7340f8604fbca7c5118dea4))

## [0.9.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.8.0...hermes-otel-v0.9.0) (2026-06-26)


### Features

* **hooks:** W3C traceparent propagation to MCP servers ([2ca6700](https://github.com/briancaffey/hermes-otel/commit/2ca6700e7e0f70bf46f605dbb6122721b2ade2ad))
* **hooks:** W3C traceparent propagation to MCP servers ([b794604](https://github.com/briancaffey/hermes-otel/commit/b794604554e0718fec4fc5c8d0fa7ac0565ad516))

## [0.8.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.7.0...hermes-otel-v0.8.0) (2026-06-24)


### Features

* **backends:** add first-class Honeycomb backend type ([3de6828](https://github.com/briancaffey/hermes-otel/commit/3de682873e4b9e3ba701d5686206e2691067b894))
* **backends:** add first-class Honeycomb backend type ([57ea5a5](https://github.com/briancaffey/hermes-otel/commit/57ea5a5dc851805e5694d444dbedd99c22086263)), closes [#20](https://github.com/briancaffey/hermes-otel/issues/20)

## [0.7.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.6.0...hermes-otel-v0.7.0) (2026-06-23)


### Features

* **hooks:** add GenAI semantic convention attributes ([c40012c](https://github.com/briancaffey/hermes-otel/commit/c40012c706092925b4f53b6bc2b46184c46e586f))
* **hooks:** add GenAI semantic convention attributes ([719e790](https://github.com/briancaffey/hermes-otel/commit/719e7907719dc03724d63afd828cb3994d71e408))

## [0.6.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.5.0...hermes-otel-v0.6.0) (2026-06-21)


### Features

* **config:** apply per-category preview_max_chars truncation limits ([f181591](https://github.com/briancaffey/hermes-otel/commit/f181591a323c55b80ee54d276c5c9132734b7473))
* **config:** apply per-category preview_max_chars truncation limits ([4688201](https://github.com/briancaffey/hermes-otel/commit/46882013b7db69fd454d00cbe87d0b064a157c3b))

## [0.5.0](https://github.com/briancaffey/hermes-otel/compare/hermes-otel-v0.4.0...hermes-otel-v0.5.0) (2026-06-07)


### Features

* allow disabling trace export per backend ([31ff25f](https://github.com/briancaffey/hermes-otel/commit/31ff25f1cfce9f2725ae2c0db347a91b467889e4))
* allow disabling trace export per backend ([4f6898e](https://github.com/briancaffey/hermes-otel/commit/4f6898e01755207cecdd7a1307244b22c1a15e31))

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
