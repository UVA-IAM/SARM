#/usr/bin/env bash
#
docker stack rm sarm
sleep 10
docker build -t sarm-ss-bridge ./sarm-ss-bridge
docker volume rm sarm_bridge-data
docker stack deploy -c docker-compose.yml sarm
