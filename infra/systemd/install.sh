#!/usr/bin/env bash
# Render the unit templates and install them. Run on the server only, as root:
#   sudo infra/systemd/install.sh /opt/scores_research /etc/study/study.env study
# Prints the rendered units instead with --print.
set -euo pipefail

PRINT=0
if [[ "${1:-}" == "--print" ]]; then PRINT=1; shift; fi
REPO_DIR="${1:?repo dir}"
ENV_FILE="${2:?env file}"
RUN_USER="${3:?user}"
HERE="$(cd "$(dirname "$0")" && pwd)"

for unit in "$HERE"/study-*.service "$HERE"/study-*.timer; do
  name="$(basename "$unit")"
  rendered="$(sed -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@ENV_FILE@|$ENV_FILE|g" -e "s|@USER@|$RUN_USER|g" "$unit")"
  if [[ $PRINT == 1 ]]; then
    printf '### %s\n%s\n\n' "$name" "$rendered"
  else
    printf '%s\n' "$rendered" > "/etc/systemd/system/$name"
  fi
done

if [[ $PRINT == 0 ]]; then
  systemctl daemon-reload
  systemctl enable --now study-health.timer study-restart.timer study-waves.timer
  systemctl list-timers 'study-*'
fi
