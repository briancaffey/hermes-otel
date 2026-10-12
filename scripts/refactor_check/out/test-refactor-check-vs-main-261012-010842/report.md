# refactor-check

- base: `main @ bebbc41` (batch `R261012-010842-base`)
- branch: `working tree @ 1d94751` (batch `R261012-010842-branch`)
- profile: `minimal`, prompts: `shell-echo,no-tools`

## Verdict

No differences outside the allowlist.


## check_batch matrices

### main @ bebbc41

# Batch R261012-010842-base · profile minimal

2 turn(s) started 2026-10-12T05:08:43.353208+00:00; sources: live, jaeger-local.

Cell: spans/tools/logs found for the turn in that source; `–` = the source does not serve logs; ✗ = not found or a failed expectation.

| prompt | live | jaeger-local |
|---|---|---|
| shell-echo | ✓ 6/1/10 | ✓ 6/1/– |
| no-tools | ✓ 4/0/5 | ✓ 4/0/– |

## Findings

- **WARN** `shell-echo` @ jaeger-local: free-text search returned 2 traces; matched by content
- **WARN** `no-tools` @ jaeger-local: free-text search returned 2 traces; matched by content

## Runs

- `shell-echo` · exit 0 · 13.6s · marker `R261012-010842-base-shell-echo`
- `no-tools` · exit 0 · 7.5s · marker `R261012-010842-base-no-tools`

### working tree @ 1d94751

# Batch R261012-010842-branch · profile minimal

2 turn(s) started 2026-10-12T05:09:40.436468+00:00; sources: live, jaeger-local.

Cell: spans/tools/logs found for the turn in that source; `–` = the source does not serve logs; ✗ = not found or a failed expectation.

| prompt | live | jaeger-local |
|---|---|---|
| shell-echo | ✓ 6/1/10 | ✓ 6/1/– |
| no-tools | ✓ 4/0/5 | ✓ 4/0/– |

## Findings

- **WARN** `shell-echo` @ jaeger-local: free-text search returned 4 traces; matched by content
- **WARN** `no-tools` @ jaeger-local: free-text search returned 4 traces; matched by content

## Runs

- `shell-echo` · exit 0 · 9.3s · marker `R261012-010842-branch-shell-echo`
- `no-tools` · exit 0 · 8.6s · marker `R261012-010842-branch-no-tools`

