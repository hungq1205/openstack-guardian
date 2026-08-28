# Unverified operational behavior

Points where the OpenAPI spec and schema don't say enough, and the answer
was "not sure" rather than a guess. Each is called out in the affected
operation's `usage_note` too -- this file is the single place to check what
still needs a real answer, and to update once one arrives (both here and in
the annotation file).

## `resize_server` (server.json)

Does resizing a server (`POST .../servers/{id}/resize/`) apply immediately
with a reboot, or some other way? There's no `confirm_resize`/`revert_resize`
operation in this spec, unlike vanilla OpenStack Nova's two-phase resize.
Currently annotated `risk_level: "high"` as a conservative default regardless
of the answer -- a flavor change is significant either way.

## `disable_compute_node` / `enable_compute_node` / `set_down_compute_node` / `unset_down_compute_node` (server.json)

In vanilla OpenStack these only affect future scheduling (new servers), not
servers already running on the host. Not confirmed whether this CMP's
implementation matches that. Currently annotated assuming vanilla OpenStack
semantics apply (`disable`/`enable` medium/low; `set_down`/`unset_down`
higher, since `set_down` is normally a precursor to `evacuate_compute_node`
and implies the host is being treated as failed) -- revise if this deployment
behaves differently.

## `delete_backup_policy` / `delete_volume_qos` (block_storage.json)

Deleting the policy/profile definition -- unconfirmed whether volumes
currently governed by it are affected (existing backups deleted? QoS limits
reverted to a default, or left as last-applied?). Annotated `risk_level:
"medium"` pending an answer, with the uncertainty stated in the usage_note.
