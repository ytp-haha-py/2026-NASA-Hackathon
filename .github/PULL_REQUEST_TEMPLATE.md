## Closes

Closes #

## Summary

<!-- What changed and why. Link the issue and keep the scope reviewable. -->

## Tests run (paste observed output)

<!-- Do not claim a check passed without the command and its output. -->

- [ ] `bash scripts/verify.sh` (or `--backend-only` with a reason)
- [ ] `bash scripts/smoke.sh`
- [ ] Focused tests:

```text
$ <command>
<observed output / summary>
```

## Screenshots / demo link

<!-- UI changes require a screenshot or a short recording. Write "N/A" for backend-only changes. -->

## Dataset and provenance changes

<!-- New/changed manifests, credentials-free source URLs, licenses, access timestamps, checksums,
     synthetic vs observed classification. Write "None" if untouched. -->

- Data kind: `observed` / `synthetic` / none
- Sources:
- Manifests:
- Provenance/limitations updated in:

## API compatibility

<!-- Note changes to contracts/openapi.json, request/response fields, error codes, defaults, or
     `method_version` / `contract_version`. State whether the frontend generated types were refreshed. -->

- [ ] `contracts/openapi.json` regenerated and committed (if schemas changed)
- [ ] Breaking change? Yes / No - explanation:

## Limitations and risks

<!-- Remaining uncertainty, coverage gaps, follow-up work, rollback plan. -->

## Scientific honesty checklist

- [ ] Synthetic fixtures are still labelled `synthetic` / TEST-ONLY and are not presented as observations.
- [ ] Results are described as matches within the indexed subset, not a global survey or base-site recommendation.
- [ ] Units, scales, method versions, provenance, and limitations remain visible where users see results.
- [ ] No invented data, uncited sources, credentials, paid services, or large raster binaries were added.
- [ ] Dataset licenses and access timestamps are recorded where data provenance changed.
