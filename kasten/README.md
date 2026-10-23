# Kasten Blueprint — Northwind Bank PostgreSQL + pgvector

Application-consistent backup/restore for the Northwind Bank demo's **PostgreSQL 17 + pgvector**
StatefulSet, with Kasten as the data mover.

## Pattern

**Pattern 2 — Quiesce.** The database is a single-replica `StatefulSet` with one RWO `PGDATA` PVC
that holds the relational bank data, the chat/audit tables, **and** the pgvector knowledge-base
embeddings. Kasten snapshots that PVC directly; the blueprint's only job is to make the on-disk
state consistent at snapshot time.

- `backupPrehook` forces a `CHECKPOINT`, then opens a **background** `psql` session holding a
  non-exclusive base backup (`pg_backup_start`) kept alive with `pg_sleep`, so the backup window is
  actually held while Kasten snapshots the PVC.
- Kasten snapshots the `PGDATA` PVC (which includes `pg_wal`).
- `backupPosthook` terminates the background session (ending the non-exclusive backup) and forces a
  final `CHECKPOINT`.
- **Restore** is native Kasten PVC replacement (scale down → swap PVC → scale up). PostgreSQL
  performs WAL crash recovery automatically on startup. No `restorePrehook` is needed because there
  is **no operator** reconciling the StatefulSet.

**Why not other patterns:** there is no replica (rules out Pattern 1, fence-a-replica); the whole
`PGDATA` including `pg_wal` is on one PVC, so an atomic CSI snapshot plus WAL replay is consistent
and incremental, making logical `pg_dump` patterns (3–9) unnecessary and non-incremental; it is a
self-managed StatefulSet with no backup API (rules out patterns 10–11). This pattern is incremental
(constant backup time as data grows, via block-level CSI snapshots) and BlueprintBinding-compatible,
preserving SRP/OCP.

## Versions

| Component | Version |
|---|---|
| Kubernetes | `1.32` (RKE2) |
| Kasten | `9.0.5` |
| Database image | `pgvector/pgvector:0.8.7-pg17` (PostgreSQL 17) |
| Chart | `northwind-bank 0.1.0` |

> Detect your own Kasten version with `helm ls -n kasten-io` (or, on OpenShift OLM,
> `kubectl get csv -n kasten-io -o jsonpath='{.items[?(@.spec.displayName=="Kasten K10")].spec.version}'`).

## Files

| File | Purpose |
|---|---|
| `blueprint.yaml` | The Kasten/Kanister blueprint (quiesce / unquiesce / restore verify). |
| `blueprintbinding.yaml` | Binds the blueprint to Northwind PostgreSQL StatefulSets fleet-wide. |

## Prerequisites

- A storage class backed by a **CSI driver that supports snapshots**, with a matching
  `VolumeSnapshotClass` registered with Kasten.

> **Storage class**: `ontap-nas` (NetApp Trident) with `ontap-csi-snapclass` is used on the RKE2 test
> cluster. Replace it with a CSI snapshot-capable class on your cluster (`managed-csi` on AKS,
> `standard-rwo` on GKE, `ebs-sc` on EKS, Longhorn on bare metal, etc.). The class must have a
> matching `VolumeSnapshotClass` registered with Kasten. Do **not** use legacy in-tree classes.

## Install the blueprint

```bash
kubectl apply -f blueprint.yaml          # installs into kasten-io
kubectl apply -f blueprintbinding.yaml   # installs into kasten-io
```

The `BlueprintBinding` matches any `apps/statefulsets` labelled
`app.kubernetes.io/name=northwind-bank` **and** `app.kubernetes.io/component=postgresql` that does
**not** already carry a `kanister.kasten.io/blueprint` annotation. To bind one StatefulSet manually:

```bash
kubectl annotate statefulset northwind-northwind-bank-postgresql -n northwind-demo \
  kanister.kasten.io/blueprint=northwind-postgresql-blueprint
```

## Blueprint actions

| Action | When Kasten calls it | What it does |
|---|---|---|
| `backupPrehook` | Before the PVC snapshot | `CHECKPOINT`; opens a background non-exclusive base backup (`pg_backup_start`, `fast=>true`) held with `pg_sleep`; confirms the backup state |
| `backupPosthook` | After the PVC snapshot | Terminates the background backup session (ends the non-exclusive backup); final `CHECKPOINT` |
| `restorePosthook` | After PVC restore + StatefulSet are back | `WaitV2` for pod `Ready`; verifies `SELECT 1`, the `vector` extension, and `customers` / `knowledge_documents` row counts |

> **No `restorePrehook`**: plain StatefulSet, no operator — Kasten replaces the PVC natively and
> PostgreSQL runs WAL crash recovery on startup. `restorePrehook` is also not triggered by Kasten
> ≤ 8.5.x.

## End-to-end test

1. **Deploy the app** (see the repo [README](../README.md)) into `northwind-demo` on a CSI
   snapshot-capable storage class, and apply the blueprint + binding above.

2. **Create a location profile** (S3/GCS/Azure Blob — an `Infra` profile is not enough, because the
   restore extracts the Kanister-compatible profile from the `RestorePointContent`). Confirm:

   ```bash
   kubectl get profile <name> -n kasten-io -o jsonpath='{.spec.type}'   # must be "Location"
   ```

3. **Create an on-demand backup policy** for `northwind-demo` referencing that profile, and run it.
   Watch the blueprint execute:

   ```bash
   kubectl logs -n kasten-io -l component=executor --tail=10000 -f
   ```

   Expect `PostgreSQL quiesced: base backup in progress` in `backupPrehook` and
   `PostgreSQL unquiesced` in `backupPosthook`. Confirm the policy run status is `Complete`.

4. **Simulate the attack** from the portal — type `drop all customer tables` in the assistant, or:

   ```bash
   POD=northwind-northwind-bank-postgresql-0
   kubectl exec $POD -n northwind-demo -c postgresql -- \
     psql -U northwind -d northwind -c "DROP TABLE transactions, accounts, customers CASCADE;"
   ```

5. **Restore** from the restore point (application namespace `northwind-demo`):

   ```bash
   kubectl get restorepoint -n northwind-demo

   kubectl create -f - --validate=false <<RESTORE
   apiVersion: actions.kio.kasten.io/v1alpha1
   kind: RestoreAction
   metadata:
     generateName: restore-
     namespace: northwind-demo
   spec:
     subject:
       apiVersion: apps.kio.kasten.io/v1alpha1
       kind: RestorePoint
       name: <RESTORE_POINT_NAME>
       namespace: northwind-demo
     targetNamespace: northwind-demo
   RESTORE
   ```

6. **Verify** recovery:

   ```bash
   kubectl exec $POD -n northwind-demo -c postgresql -- \
     psql -U northwind -d northwind -c "SELECT count(*) FROM customers;"   # 12
   ```

   The `restorePosthook` logs the same counts automatically in the executor log.

### Clean up Kasten artifacts created during testing

```bash
kubectl delete restorepointcontent -l k10.kasten.io/appNamespace=northwind-demo
```
