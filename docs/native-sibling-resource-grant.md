# Native sibling clone resource grant repair

PR44 `a8d668a` can reject the real native sibling compositor transaction with
`commit-context-stale`. The staging transaction deep-copies the native identity
state. Its resource grant still refers to the source state object: isolated
production-chain diagnostics found the binding differs only at tuple index 8,
the state object identity. The existing opacity compositor test also reproduces
this on the untouched PR44 checkout.

The repair is limited to `native_sibling_transaction.py`:

- Transfer the existing grant to the exact cloned binding only when every field
  except the object identity matches, including state generation. Preserve the
  original validation timestamp; do not renew the 500 ms lease.
- Keep resource/capture/mode scope in the transaction guards. Legitimate member
  staging may change state identity/generation, but cannot change these scopes.
- Require resources ready for source authorization and final commit.
- Rollback restores the original grant only if the transaction still owns the
  current grant and the restored source has its exact original binding. It does
  not replace a newer callback's grant. Recheck final source authorization after
  restoration and hide/close an unauthorized source even if the earlier staged
  scope was current.

Production resource binding, asynchronous validation, frame/round guards and
identity/pose gates are unchanged. This commit does not depend on the fallback
consumer integration and is intended to be independently cherry-picked onto
main after PR44.

Validation uses the existing real compositor/identity transaction with
synthetic registered resource files and controlled native surface sinks:

- Original isolated probe: `commit-context-stale` on `a8d668a`; repaired:
  `alignment-v2-committed`, success true.
- Native atomic suite: source/target success, rejection/exception rollback,
  stale frame/request/cancel/floor, both mode registries, resource/capture/mode
  changes during paint, expired lease, unchanged lease on clone transfer, and
  preservation of a newer callback grant with source hiding.
- Both opacity suites must be run with this transaction suite; the original
  failing actual compositor case now succeeds. This is not game/HWND or
  long-running stability acceptance.

The earlier two-mode fixture changed only the host mode after issuing a grant.
It now writes a synthetic registry for each requested mode before proposal and
real validation. A separate test retains the late mode-change rejection case.
