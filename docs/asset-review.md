# Asset review Agent

GitHub Actions is the automatic entry for Asset requests. Creator edits/reopens
and creator comments containing `/asset-review` on its own line use the same
global queue as the twice-daily fallback. The fallback runs at 00:00 and 12:00
Asia/Shanghai (`0 4,16 * * *` UTC), replacing the former independent AIP schedule.
Keep that AIP schedule paused so two schedulers cannot publish the same review.

The gateway handles only one eligible Issue per execution, keeps the existing
30-minute per-Issue cooldown, and preserves `asset-review-state` artifacts.
Scheduled scans skip requests still in cooldown rather than waiting. There is
no new state namespace when moving the Agent between Profiles: fingerprints,
completed outcomes and the concurrency group remain unchanged. An unfinished
accepted run must be resolved against its original Agent before changing the
call URL.

## Identity and credentials

The current Agent uses the Subscan GitHub App and installation tokens limited
to `subscan-explorer/chain-asset`: Contents, Issues and Pull requests read/write,
Metadata read-only. No personal PAT, SSH signing key, workflow permission or
organization-management permission is required. The App's overall installation
ceiling still applies to its private key; per-token scoping is not isolation
between independently owned Apps.

Mount the App PEM, configuration and `scripts/asset_review_github.py` under
`~/.config/chain-asset/` as `github-app.pem`, `github-app.json` and
`github_api.py`. The JSON configuration supplies `app_id`, `installation_id`,
`owner: subscan-explorer`, `repositories: [chain-asset]`, the exact permission
map, and `private_key_file: ~/.config/chain-asset/github-app.pem`.

The normal read command is:

```sh
python3 /root/.config/chain-asset/github_api.py rest /repos/subscan-explorer/chain-asset
```

Use `--method POST/PUT/PATCH` and `--input request.json` for a permitted REST
mutation, or `graphql --input request.json` for GraphQL. The latter file contains
`query` and optional `variables`. The helper keeps JWTs and installation tokens
in process memory, rejects authenticated redirects, bounds responses, and
reports GraphQL errors as failures. It exposes no token-output mode and does not
pass credentials to npm, Git, environment variables or external evidence sites.

Set repository variable `CHAIN_ASSET_AGENT_LOGIN` to the verified App login
(`subscan-aip-research[bot]`). Set `CHAIN_ASSET_LEGACY_REVIEWERS` to the JSON array
`["freehere107"]` when retaining that former reviewer's public outcomes. The
historical allowlist only recognizes old feedback/deduplication evidence; every
new comment or draft PR receipt must belong to the current reviewer. This keeps
old owner-authored Agent feedback out of fingerprints after the identity change.
Do not add submitter-provided identities to this trusted configuration.

Keep `HAST_ASSET_AGENT_HOOK_URL` only in the existing repository Actions Secret.
Never commit, print or put the credential-bearing URL in a prompt. Preserve the
`chain-assets` environment and `ASSET_REVIEW_ENABLED` switch during cutover.

## Verification and cutover

1. Save and read back the complete target Agent configuration. Preserve the
   model, reasoning, isolation and field-verification/scam/draft-only rules.
2. Verify the App's GraphQL `viewer.login`, exact repository token scope and a
   repository read from the saved Agent's sandbox. Do not publish an Asset merely
   to test credentials.
3. Verify the latest trusted state artifact has no unfinished run. Disable
   automatic admission and pause the old AIP schedule before changing the
   reviewer variables and hook Secret; do not delete or reset recovery state.
4. Run `asset-review.yml` with `dry_run=true` and `connectivity_check=true` for a
   known eligible Issue. Require the completed, verified structured outcome;
   accepted HTTP alone is insufficient. This check performs no Asset research,
   credential reads, Issue reply or Asset PR creation.
5. Restore automatic admission after the verified result. Leave the old Agent
   for the operator to remove. A real Asset review/publication is a separate
   business acceptance step.

Run the helper and gateway boundary tests with:

```sh
python3 -m unittest discover -s tests -p 'test_asset_review*.py' -v
git diff --check
```

Asset data and package builds are unchanged by this control-plane migration.
Real accepted submissions must still validate all JSON, the intended diff and
safe logo, then pass `npm run build` before creating a draft PR.
