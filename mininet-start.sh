#!/bin/bash
#
# mininet-start.sh
#
# Runs Mininet for testing purposes 

sudo mn --controller=remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13 --topo=tree,5
