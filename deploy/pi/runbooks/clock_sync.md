# Clock-sync runbook — Experiment E

The intake's clock is the reference domain for every `t_*` measurement.
Producer-side service times are measured in **producer clock domains** and
corrected into the intake domain by `demo.collect_service_records` using the
per-record `clock_offset_ms` field. This runbook measures that offset with
chrony and documents where it lands.

## 1. Reference and clients

After `--tags base,clock` bring-up, `pi-01` runs the chrony reference
(`local stratum 8`, `allow 10.0.0.0/24`) and every other node follows it
(`server 10.0.0.11 prefer iburst`, `makestep 1.0 3`).

## 2. Measure the residual offset per node

```bash
ansible picn -i deploy/pi/inventory.ini -b -m command \
  -a "chronyc -n tracking"
```

`System time` in `chronyc tracking` is the node's residual offset against its
sources: `0.000…seconds fast/slow` means the node's clock tracks the
reference within the printed tolerance.

CSV form (script-friendly, offset in ns on the `System time` line):

```bash
ansible picn -i deploy/pi/inventory.ini -m shell \
  -a "chronyc -c tracking | cut -d, -f1-15"   # field 14/15 = fast/slow offset ns
```

## 3. Thresholds

- Target: residual `|offset|` well below the collector's
  `--skew-threshold-ms` (default `1.0 ms`); chrony on a switched LAN reaches
  single-digit microseconds.
- `chronyc sourcestats` / `chronyc tracking` wedged above threshold:
  re-run `makestep` windows (`systemctl restart chrony`, keep nodes idle),
  then re-measure. Do not start a campaign with a follower in `step` state.

## 4. Recording the offset into service records

Producer-side observers stamp `clock_offset_ms` into each service record at
run time (`{run_id, parent, leaf_index, correlation, t_service_ns,
clock_offset_ms, …}`). The measured residual from step 2 is the value
stamped; keep the measurement fresh (re-run step 2 at campaign start and
after any reboot).

The collector then

1. computes `service_corrected_ms = service_measured_ms - clock_offset_ms`,
2. flags `skew_exceeded` for `|clock_offset_ms| > --skew-threshold-ms`,
3. prints the residual skew (`worst absolute offset`) and the excluded
   record count — the same numbers the `physical_publishable` gate's
   condition 6 consumes (`clock.skew_measured_ms`, `clock.excluded_runs`).

## 5. Verification (one host by hand)

```bash
ssh pi-02 chronyc tracking | grep -E 'System time|Last offset'
ssh pi-01 /opt/picn-venv/bin/python - <<'PY'
from demo.collect_service_records import residual_skew_ms
print(residual_skew_ms([{"clock_offset_ms": 0.002}, {"clock_offset_ms": 0.5}]))
PY
```

`0.5` here is under the 1.0 ms threshold; any record at or above it is
flagged and counted, never dropped.
