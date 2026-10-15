# OpenClaw agent runtime setup

Last verified: 2026-10-15. This describes the agent configuration, not a new
Gateway installation or a change of service owner.

## Desired configuration

Mira uses the built-in OpenClaw runtime while retaining the selected model.
Merge this fragment into the existing configuration; do not replace unrelated
agents, model settings, or plugin configuration.

```json
{
  "agents": {
    "entries": {
      "mira": {
        "models": {
          "openai/gpt-6.1-sol": {
            "agentRuntime": { "id": "openclaw" }
          }
        }
      }
    }
  }
}
```

A saved default does not replace a runtime already selected by a session.
Select OpenClaw Default for that session through the supported session controls,
retaining the full provider/model ID. Verify on a subsequent turn: a status
showing a pending live switch is not proof that the new runtime has executed.

## X-search configuration

The xAI plugin has an explicit cross-provider opt-in:

```json
{
  "plugins": {
    "entries": {
      "xai": {
        "config": { "xSearch": { "enabled": true } }
      }
    }
  }
}
```

This flag is not an authentication credential or proof of tool availability.
Use the supported xAI OAuth or API-key setup. Keep credentials in the managed
auth store or approved secret references, never in dotfiles, skill text,
command output, or Git commits. An absent plugin API-key field alone does not
prove that managed OAuth is absent.

## Verification receipt

- The saved per-agent runtime is `openclaw` for the unchanged model.
- A subsequent session-status check reported OpenClaw Default.
- The saved `xSearch.enabled` flag is true, but X search is not yet verified.
- The attempted plugin reload was rejected with `FORBIDDEN`, missing
  `operator.admin`. No successful reload is claimed.
- The last completed auth-status check reported an expired xAI OAuth profile.
  A later bounded probe timed out without a result; this does not prove a
  refresh succeeded or that the credential is irrecoverable.

## Remaining recovery

Start a fresh OpenClaw conversation if system controls report that the inference
route cannot be reverified. Use an authorized operator connection for the
plugin reload. Do not replace an explicit admin-scope denial with root SSH,
alternate tokens, or a direct backend call. Do not restart the hosting Gateway
through shell commands as a substitute.

Then verify managed xAI authentication, reload only as authorized, confirm
`x_search` appears in the new turn, and run one bounded real search. Record the
result separately from configuration and plugin activation. No Gateway binary
update, service-owner change, or new secret provisioning was completed as part
of this receipt.
