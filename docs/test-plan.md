# test-plan.md

Run all automated tests: `python -m unittest discover -s tests -v` (22 tests, ~20 s, stdlib only).

## Automated
| Area | Test | Proves |
|---|---|---|
| Protocol | `test_guarded_detection` | only requests with id + guarded method are intercepted |
| Protocol | `test_denial_shapes` | isError result for tools/call; `-32001` otherwise; id preserved |
| Protocol | `test_describe` | tool/arguments extraction, junk params safe |
| Policy | `test_policy_block_beats_allow` | rule precedence |
| UI safety | `test_sanitize_strips_bidi_and_controls` | no spoofing chars |
| Install | `test_install_roundtrip` | wrap/unwrap idempotent, env preserved, remote skipped |
| Stdio | `test_allow_executes_and_passthrough_untouched` | allow path, un-guarded passthrough, audit |
| Stdio | `test_block_never_reaches_server` | blocked call not executed |
| Stdio | `test_policy_rules_block_specific_tool` | `auto_block` globs |
| Stdio | `test_many_concurrent_requests_all_answered_correctly` | 20 parallel, correct id mapping |
| Stdio | `test_fails_closed_when_daemon_unreachable` | fail-closed |
| Stdio | `test_cancel_notification_drops_pending_request` | cancellation |
| Stdio | `test_server_exit_terminates_proxy` | no hang/crash (regression for stdin abort) |
| Stdio | `test_bad_server_command_reports_error` | exit 127 |
| Gateway | `test_allow_forwards_json_and_sse` | JSON + chunked SSE streaming, header injection |
| Gateway | `test_block_returns_denial_and_upstream_untouched` | blocked, upstream silent |
| Gateway | `test_unguarded_methods_pass_without_approval` | no popup for `tools/list` |
| Gateway | `test_batch_with_one_denied_denies_all` | fail-safe batches |
| Gateway | `test_secret_path_required` | 404 w/o secret; secret stripped |
| Gateway | `test_concurrent_requests` | approvals overlap |
| Gateway | `test_fail_closed_without_daemon` | fail-closed |
| Gateway | `test_upstream_down_gives_502` | upstream error handling |

## Manual (needs a desktop)
See `requirements.md §9`, M-1…M-6, plus: run 5 simultaneous requests and confirm 5 cascaded windows; leave a popup open and kill the proxy → popup disappears; close laptop lid/kill daemon → next request restarts it.

## Exit criteria
0 failing automated tests; all manual items ticked; demo recorded from a clean run.
