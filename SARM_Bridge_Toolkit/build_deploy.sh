#/usr/bin/env bash
#
docker build -t sarm-ss-bridge ./sarm-ss-bridge
docker stack deploy -c docker-compose.yml sarm
