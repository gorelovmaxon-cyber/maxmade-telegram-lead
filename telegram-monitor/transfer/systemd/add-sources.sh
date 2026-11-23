#!/bin/sh
# Add 9 approved public order feeds to the MAXMADE monitor on the VPS.
# Run as maxmade-admin: sudo sh /tmp/add-sources.sh
# Backs up monitor.env, resolves peer IDs (no join, no messages), writes the
# three comma lists in the same pass, restarts the reader and shows its state.
set -eu
E=/etc/maxmade-monitor/monitor.env
cp -a "$E" "$E.bak-channels-20261110"
cd /opt/maxmade-monitor/current
set -a
. "$E"
set +a
/opt/maxmade-monitor/venv/bin/python /tmp/add_sources.py
systemctl restart maxmade-monitor-reader
sleep 20
systemctl is-active maxmade-monitor-reader
journalctl -u maxmade-monitor-reader -n 20 --no-pager -o cat
