#!/bin/sh
# Enable exchange feeds for the MAXMADE monitor on the VPS. Run as maxmade-admin:
#   sudo sh /tmp/enable-feeds.sh
# Stages: env backup + 3 MONITOR_FEED* lines, timer units, switch current -> vps-20261110b
# (already staged and tested), restart bot+reader. Site services are not touched.
set -eu
E=/etc/maxmade-monitor/monitor.env
R=/opt/maxmade-monitor/releases/vps-20261110b
test -f "$R/feeds.py"
cp -a "$E" "$E.bak-feeds"
grep -q '^MONITOR_FEEDS=' "$E" || printf 'MONITOR_FEEDS=fl,workspace,kwork,connect\nMONITOR_FEED_MAX_AGE_HOURS=24\nMONITOR_FEED_NETWORK=direct\n' >> "$E"
install -m 0644 -o root -g root /tmp/maxmade-monitor-feeds.service /tmp/maxmade-monitor-feeds.timer /etc/systemd/system/
ln -sfn "$R" /opt/maxmade-monitor/current.new && mv -T /opt/maxmade-monitor/current.new /opt/maxmade-monitor/current
systemctl daemon-reload
systemctl restart maxmade-monitor-bot maxmade-monitor-reader
systemctl start maxmade-monitor-feeds.service
systemctl enable --now maxmade-monitor-feeds.timer
journalctl -u maxmade-monitor-feeds -n 6 --no-pager -o cat
systemctl is-active maxmade-monitor-bot maxmade-monitor-reader maxmade-brief nginx
# Rollback: ln -sfn /opt/maxmade-monitor/releases/vps-20261110a /opt/maxmade-monitor/current;
#           systemctl disable --now maxmade-monitor-feeds.timer; cp -a $E.bak-feeds $E; restart bot+reader
