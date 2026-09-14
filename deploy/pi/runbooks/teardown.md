# Teardown runbook — Experiment E physical bench

Ordered command reverse of bring-up. Leaves the bench reproducible; steps 1–4
are non-destructive to measurements (collect first!).

## 1. Stop and disable node services

```bash
ansible edges,producers,llm -i deploy/pi/inventory.ini -b \
  -m systemd -a "name=picn-node.service state=stopped enabled=false"
ansible intake -i deploy/pi/inventory.ini -b \
  -m systemd -a "name=picn-intake.service state=stopped enabled=false"
```

## 2. Collect leftovers (if step 5 of bring-up was skipped)

```bash
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml \
  --tags collect --extra-vars "picn_run_id=${PICN_RUN_ID}"
```

## 3. Archive results off-bench, then wipe node results

```bash
rsync -av pi-07:/var/lib/picn/results/physical/ ./demo/results/physical/
rsync -av pi-07:/var/lib/picn/results/all/ ./demo/results/physical/all-$(date +%F)/

ansible picn -i deploy/pi/inventory.ini -b \
  -m file -a "path=/var/lib/picn/results state=absent"
```

## 4. Remove chrony discipline (optional — keep for repeated campaigns)

```bash
ansible picn -i deploy/pi/inventory.ini -b \
  -m systemd -a "name=chrony state=stopped enabled=false"
```

## 5. Remove the deployment (full clean)

```bash
ansible picn -i deploy/pi/inventory.ini -b \
  -m file -a "path=/etc/picn state=absent"
ansible picn -i deploy/pi/inventory.ini -b \
  -m file -a "path=/etc/systemd/system/picn-node.service state=absent"
ansible intake -i deploy/pi/inventory.ini -b \
  -m file -a "path=/etc/systemd/system/picn-intake.service state=absent"
ansible picn -i deploy/pi/inventory.ini -b \
  -m shell -a "systemctl daemon-reload"
ansible picn -i deploy/pi/inventory.ini -b \
  -m file -a "path={{ picn_venv_path | default('/opt/picn-venv') }} state=absent"
ansible picn -i deploy/pi/inventory.ini -b \
  -m file -a "path={{ picn_repo_dir | default('/opt/PiCN') }} state=absent"
```

The picn user/group, CPython 3.14, and packages stay installed by design;
wiping them is a full SD-card reflash decision, not a playbook step.
