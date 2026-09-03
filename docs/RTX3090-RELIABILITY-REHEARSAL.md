# RTX 3090 Forge reliability rehearsal

This is the physical release gate for the Forge reliability contract. Run it on the Debian 13
RTX 3090 reference appliance after both coordinated pull requests pass CI and are deployed to
the test environment.

## Preconditions

- Forge boots in UEFI mode and `/forge-data` is a persistent filesystem.
- `forge.local`, NVIDIA, ComfyUI, Forge web, worker and sync report ready.
- The Forge is paired with `https://test.ooc.melbourne` and commissioned.
- The test operator can inspect the OOC ProductionJob and Candidate records.

Record the ISO checksum, Forge source ref, OOC System version, NVIDIA driver, workflow digest,
model digest and test timestamps in the acceptance evidence.

## A. Normal multi-asset execution

1. Queue one image job requiring the `image` capability.
2. Confirm the claim is present in Forge SQLite before ComfyUI begins.
3. Produce at least two output assets.
4. Confirm every local asset SHA-256 matches its OOC MediaAsset record.
5. Confirm exactly one Candidate exists for the ProductionJob.

Pass: one attempt, all assets received, one Candidate, complete provenance.

## B. Network loss after rendering

1. Queue a job and wait until rendering has completed locally.
2. Disconnect Ethernet and disable Wi-Fi before upload acknowledgement.
3. Confirm the local job remains `READY_TO_UPLOAD` or `READY_TO_COMPLETE`.
4. Leave the network unavailable beyond one ordinary poll interval.
5. Restore one network interface.
6. Confirm upload and completion resume without another render.

Pass: no lost output, no duplicate MediaAsset and exactly one Candidate.

## C. Reboot after asset upload

1. Queue a job and wait until at least one asset is acknowledged by OOC.
2. Power off the Forge before Candidate completion is acknowledged.
3. Boot normally and confirm `/forge-data` is mounted before Forge services start.
4. Confirm already acknowledged assets are not uploaded again.
5. Confirm completion resumes and returns the existing Candidate on retry.

Pass: no re-render, no duplicate upload and exactly one Candidate.

## D. Lease safety

1. Run a job longer than the original 15-minute lease.
2. Confirm lease heartbeats extend the active attempt while queued, rendering and uploading.
3. Attempt completion with an incorrect or superseded attempt token.

Pass: the valid attempt completes and the stale attempt is rejected.

## E. Local Study submission

1. Create a Study through `forge.local` while disconnected from OOC.
2. Reconnect, open the completed local job and select **Submit Study to OOC**.
3. Interrupt the connection once during submission, then restore it.
4. Repeat the submission action.

Pass: the local Study produces exactly one OOC Candidate, never an automatically published Work.

## F. Forge loss isolation

1. Confirm all accepted MediaAssets and provenance are readable from OOC.
2. Power the Forge off.
3. Exercise OOC public, Lens, commerce and admin health checks.

Pass: OOC remains healthy and the Forge is shown as offline without loss of canonical records.

The release is rejected if any case creates ambiguous state, duplicate Candidates, duplicate
MediaAssets, an automatic Work, or an OOC availability dependency on the Forge.
