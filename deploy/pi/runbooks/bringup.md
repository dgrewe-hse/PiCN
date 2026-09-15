# Bring-up runbook — Experiment E physical bench (design v4 §5.6)

Ordered commands to provision, deploy, verify, run a campaign, and collect.
Run from the repository root on the operator workstation; `ssh` access to
`pi-01 … pi-08` with your admin user is assumed (no credentials live in this
repository). Placeholders to fill before starting:

- `PICN_COMMIT` — deployed commit, e.g. `$(git rev-parse HEAD)`
- `PICN_RUN_ID` — campaign id, e.g. `e2-det-$(date +%s)` (deterministic)
  or `e2-llm-$(date +%s)` (LLM)

## 0. Preflight (controller)

```bash
git fetch origin && test -z "$(git status --porcelain)"   # clean tree
ansible-galaxy collection install ansible.posix           # chrony helpers
```

## 1. Base + clock (`tags base,clock`)

```bash
PICN_COMMIT=$(git rev-parse HEAD)
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml \
  --tags base,clock --extra-vars "picn_commit=${PICN_COMMIT}"
```

Installs CPython 3.14 from source, clones PiCN at `PICN_COMMIT`, builds the
venv (`pip install -e ".[dev]"`), and points every node's chrony at the
intake (`pi-01`) as reference. Verify the offsets before proceeding:

```bash
ansible picn -i deploy/pi/inventory.ini -b -m command \
  -a "chronyc -n tracking" --tags clock
```

The measured per-node offsets feed the clock-offset correction
(`runbooks/clock_sync.md`).

## 2. Deploy node services (`tags deploy`)

```bash
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml \
  --tags deploy
```

Renders `/etc/picn/picn-node.env` (env contract verbatim from
`demo/physical_node.py`) and the systemd unit on every node; serving roles
(`edge`, `producer`, `llm`) start immediately. The intake unit
(`picn-intake.service`, oneshot) is started per campaign in step 4.

## 3. Healthcheck (`tags healthcheck`)

```bash
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml \
  --tags healthcheck
```

Checks the deployed interpreter version, the env-contract imports
(`demo.physical_node`, `demo.collect_service_records`, `run_physical`,
`physical_publishable`), and that every serving unit is active.

## 4. Run the campaign (from `pi-01`)

Deterministic 2-edge cell (co-located variant, intake -> edge1/edge2),
invoking the runner exactly as its CLI defines:

```bash
ssh pi-01
export PICN_RUN_ID=e2-det-$(date +%s)
sudo -u picn env PICN_COMMIT=<commit-from-step-1> \
  /opt/picn-venv/bin/python -m demo.run_physical \
  --backend deterministic --edges 2 --seeds 1 --k 8 \
  --leaf-latency-s 0.05 --runs-per-cell 30 \
  --run-id "${PICN_RUN_ID}" \
  --out /var/lib/picn/results/physical/${PICN_RUN_ID}.jsonl
```

> **Known delta (flagged, not silently absorbed):** the committed runner
> builds the cell topology in-process (loopback edge endpoints). Injecting
> the real bench endpoints (`picn_edge_endpoints` from `group_vars/intake.yml`
> — `10.0.0.12:9001`, `10.0.0.13:9002`, or `10.0.0.16:9103` for LLM cells)
> into `run_physical_cell` is the pending follow-up; the per-node services
> deployed in step 2 already speak the env contract it will target.

Equivalent systemd path (uses the node entrypoint's intake delegation):

```bash
sudo systemctl set-environment PICN_RUN_ID=${PICN_RUN_ID}
sudo systemctl start picn-intake.service   # PICN_ROLE=intake -> run_physical
```

LLM cells drop `--leaf-latency-s` (Finding 9) and pass
`--backend llm --llm-placement on-pi --model-config /var/lib/picn/results/model.toml`;
LLM cells need `n >= 15` runs per cell (deterministic: `n >= 30`).

## 5. Collect (`tags collect`)

```bash
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml \
  --tags collect --extra-vars "picn_run_id=${PICN_RUN_ID}"
```

Fetches every node's `markers/` and `service_records/` to the observer
(`pi-07`, `/var/lib/picn/results/all/<host>/…`), then runs
`python -m demo.collect_service_records` to

1. correct each producer's `service_measured_ms` into the intake clock
   domain (`service_corrected_ms = service_measured_ms - clock_offset_ms`),
2. flag and count records with residual skew above
   `skew_threshold_ms` (`1.0 ms` default) as `skew_exceeded`,
3. merge every node's run marker into the campaign JSONL as
   `node_markers` for the `physical_publishable` gate.

Outputs on `pi-07`:

- `/var/lib/picn/results/physical/${PICN_RUN_ID}.jsonl` — campaign records
  (with `node_markers`)
- `/var/lib/picn/results/physical/${PICN_RUN_ID}-corrected.jsonl` —
  consolidated, clock-corrected service records

## 6. Gate the cell

```bash
ssh pi-07 /opt/picn-venv/bin/python - <<'PY'
import json
from agentic.benchmark.physical import build_physical_cell, physical_publishable
runs = [json.loads(l) for l in open("/var/lib/picn/results/physical/<RUN_ID>.jsonl")]
cell = build_physical_cell(runs)
print(cell["n"], physical_publishable(cell))
PY
```
